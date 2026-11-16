from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Optional
import pytest
from starlette.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app, auth_config
from app.models.paket_receiving_operation import PaketReceivingOperation
from app.models.transaksi_linen import TransaksiLinen
from app.routers.auth_router import get_default_auth_service, get_default_session_store
from app.services.auth_dependencies import require_active_session, require_page_session
from app.services.auth_service import AuthService
from app.routers.receiving_router import (
    CloudPackageAdapter,
    DefaultMockCloudPackageAdapter,
    get_default_cloud_adapter,
    get_default_rfid_manager,
    get_default_uid_provider,
    get_package_receiving_service,
    is_receiving_feature_enabled,
    require_receiving_feature_enabled,
)
from app.services.circuit_breaker import OperationCircuitBreaker
from app.services.hospital_binding_service import provision_hospital_binding
from app.services.package_receiving_service import PackageReceivingService
from app.services.rfid_service import RFIDScanSession, RFIDServiceManager
from app.services.session_store import SessionData, SessionStore


class StaticUIDProvider:
    def __init__(self, uid: str):
        self.uid = uid

    def get_installation_uid(self) -> str:
        return self.uid


@pytest.fixture
def api_test_env():
    """Environment terisolasi untuk pengujian API penerimaan paket."""
    managed_dir = os.environ.get("CLIENTS_TEST_MANAGED_DIR")
    fd, db_path = tempfile.mkstemp(suffix="_api_test.db", dir=managed_dir)
    os.close(fd)

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 15},
        future=True,
    )
    Base.metadata.create_all(engine)
    TestingSessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    with TestingSessionFactory() as db:
        provision_hospital_binding(db, hospital_id=101, nama_rs="RS API Test", kode_rs="RS_API", alamat="Jl. API 1")
        from app.models.ruangan import Ruangan
        db.add(Ruangan(kode_ruangan="R-TEST-001", nama_ruangan="Ruang Linen Mawar", keterangan="Ruang API Test"))
        db.commit()

    session_store = SessionStore(default_session_duration_seconds=28800)
    auth_service = AuthService(session_store=session_store, session_duration_seconds=28800)
    cloud_adapter = DefaultMockCloudPackageAdapter()
    rfid_manager = RFIDServiceManager(demo_mode=True)
    uid_provider = StaticUIDProvider("inst_api_test_node_101")

    coord = PackageReceivingService(
        session_store=session_store,
        rfid_service_manager=rfid_manager,
        cloud_adapter=cloud_adapter,
        circuit_breaker=OperationCircuitBreaker(),
        installation_uid_provider=uid_provider,
        db_session_factory=TestingSessionFactory,
    )

    def _override_get_db():
        with TestingSessionFactory() as session:
            yield session

    # Hapus override autouse dari conftest agar pengujian benar-benar memvalidasi autentikasi
    app.dependency_overrides.pop(require_active_session, None)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_default_cloud_adapter] = lambda: cloud_adapter
    app.dependency_overrides[get_default_rfid_manager] = lambda: rfid_manager
    app.dependency_overrides[get_default_uid_provider] = lambda: uid_provider
    app.dependency_overrides[get_package_receiving_service] = lambda: coord
    app.dependency_overrides[get_default_session_store] = lambda: session_store
    app.dependency_overrides[get_default_auth_service] = lambda: auth_service

    client = TestClient(app)

    yield {
        "db_path": db_path,
        "engine": engine,
        "session_factory": TestingSessionFactory,
        "session_store": session_store,
        "auth_service": auth_service,
        "cloud": cloud_adapter,
        "rfid_manager": rfid_manager,
        "uid_provider": uid_provider,
        "client": client,
        "coordinator": coord,
    }

    app.dependency_overrides.clear()
    engine.dispose()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


async def create_auth_cookie(session_store: SessionStore, hospital_id: int = 101) -> tuple[dict[str, str], dict[str, str]]:
    """Helper untuk membuat sesi valid dan mengembalikan cookies serta headers otorisasi."""
    session = await session_store.create_session(
        user_id="user_test_01",
        email="operator@rs.id",
        hospital_id=hospital_id,
        hospital_name="RS API Test",
        hospital_code="RS_API",
        role="operator",
        access_token="tok_valid_test",
        refresh_token="ref_valid_test",
        token_expires_in=28800,
    )
    cookies = {auth_config.cookie_name: session.session_id}
    headers = {
        auth_config.custom_header_name: auth_config.custom_header_value,
        "Origin": "http://testserver",
    }
    return cookies, headers


# =============================================================================
# 14 TEST SUITE PENERIMAAN PAKET API
# =============================================================================

def test_01_api_without_auth(api_test_env, monkeypatch):
    """1. API tanpa autentikasi: Ditolak dengan HTTP 401 Unauthorized."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]

    # Tanpa cookie sesi
    res = client.get("/api/receiving/package/101")
    assert res.status_code == 401
    assert "tidak valid" in res.json()["detail"].lower()


def test_02_api_with_invalid_membership(api_test_env, monkeypatch):
    """2. API dengan membership tidak valid (RS sesi beda dengan hospital binding): Ditolak HTTP 403."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]

    # Sesi pengguna terikat pada hospital_id 999 (bukan RS lokal 101)
    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=999))

    res = client.get("/api/receiving/package/101", cookies=cookies, headers=headers)
    assert res.status_code == 403
    assert "tidak valid" in res.json()["detail"].lower()


def test_03_cross_hospital_access_rejection(api_test_env, monkeypatch):
    """3. Akses lintas rumah sakit: Operator RS 101 mencoba mengambil paket RS 999 -> Ditolak HTTP 403."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))

    # Paket 102 pada mock cloud diset milik hospital_id 999
    res = client.get("/api/receiving/package/102", cookies=cookies, headers=headers)
    assert res.status_code == 403
    assert "lintas rumah sakit" in res.json()["detail"].lower()

    # P0-02 Router NULL Guard: Paket 103 dengan hospital_id None ditolak pada preview (403)
    res_unbound = client.get("/api/receiving/package/103", cookies=cookies, headers=headers)
    assert res_unbound.status_code == 403
    assert "belum terikat" in res_unbound.json()["detail"].lower()

    # P0-02 Router NULL Guard: Paket 103 dengan hospital_id None ditolak pada process receiving (403)
    res_proc_unbound = client.post(
        "/api/receiving/process",
        json={
            "paket_id": 103,
            "verification_code": "VC-103",
            "ruangan_tujuan": "Ruang Linen Mawar",
        },
        cookies=cookies,
        headers=headers,
    )
    assert res_proc_unbound.status_code == 403
    assert "belum terikat" in res_proc_unbound.json()["detail"].lower()

    # P0-02: Paket 102 (RS 999) ditolak pada process receiving (403)
    res_proc_diff = client.post(
        "/api/receiving/process",
        json={
            "paket_id": 102,
            "verification_code": "VC-102",
            "ruangan_tujuan": "Ruang Linen Mawar",
        },
        cookies=cookies,
        headers=headers,
    )
    assert res_proc_diff.status_code == 403
    assert "lintas rumah sakit" in res_proc_diff.json()["detail"].lower()


def test_04_package_not_found(api_test_env, monkeypatch):
    """4. Paket tidak ditemukan pada cloud supplier: Ditolak HTTP 404 Not Found."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))

    res = client.get("/api/receiving/package/99999", cookies=cookies, headers=headers)
    assert res.status_code == 404
    assert "tidak ditemukan" in res.json()["detail"].lower()


def test_05_rfid_proof_unavailable(api_test_env, monkeypatch):
    """5. Bukti RFID tidak tersedia (belum ada scan session yang dibekukan): Ditolak HTTP 400."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))

    payload = {
        "paket_id": 101,
        "verification_code": "VC-101",
        "ruangan_tujuan": "Ruang Linen Mawar",
    }
    res = client.post("/api/receiving/process", json=payload, cookies=cookies, headers=headers)
    assert res.status_code == 400
    assert "bukti scan rfid" in res.json()["detail"].lower()


def test_06_manifest_mismatch(api_test_env, monkeypatch):
    """6. Manifest tidak cocok (EPC yang discan tidak lengkap): Ditolak HTTP 409 Conflict."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]
    rfid_mgr = api_test_env["rfid_manager"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))

    # Paket 101 di cloud butuh 2 EPC: ...0001 dan ...0002. Kita hanya scan 1 EPC.
    scan_sess = RFIDScanSession(operation_id="op_mismatch_01", paket_id=101)
    scan_sess.add_epc("E28011606000020496000001")
    scan_sess.freeze_snapshot()
    rfid_mgr.register_scan_session(scan_sess)

    payload = {
        "paket_id": 101,
        "verification_code": "VC-101",
        "operation_id": "op_mismatch_01",
        "ruangan_tujuan": "Ruang Linen Mawar",
    }
    res = client.post("/api/receiving/process", json=payload, cookies=cookies, headers=headers)
    assert res.status_code == 409
    assert "ketidakcocokan manifest" in res.json()["detail"].lower()


def test_07_claim_timeout_yields_uncertain(api_test_env, monkeypatch):
    """7. Claim timeout: Koordinasi menangani timeout, operasi bertahan pada claim_uncertain."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]
    cloud = api_test_env["cloud"]
    rfid_mgr = api_test_env["rfid_manager"]
    session_factory = api_test_env["session_factory"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))

    # Atur mode claim timeout
    cloud.claim_mode = "timeout"

    scan_sess = RFIDScanSession(operation_id="op_claim_timeout_01", paket_id=101)
    scan_sess.add_epc("E28011606000020496000001")
    scan_sess.add_epc("E28011606000020496000002")
    scan_sess.freeze_snapshot()
    rfid_mgr.register_scan_session(scan_sess)

    payload = {
        "paket_id": 101,
        "verification_code": "VC-101",
        "operation_id": "op_claim_timeout_01",
    }
    res = client.post("/api/receiving/process", json=payload, cookies=cookies, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["lifecycle_phase"] == "claim_uncertain"
    assert data["sync_state"] == "claim_held_reconciliation"

    # Verifikasi di SQLite
    with session_factory() as db:
        row = db.query(PaketReceivingOperation).filter_by(operation_id="op_claim_timeout_01").first()
        assert row is not None
        assert row.lifecycle_phase == "claim_uncertain"


def test_08_local_committed_operation_idempotency(api_test_env, monkeypatch):
    """8. Operasi local_committed: Replay request melanjutkan konfirmasi tanpa membuat transaksi duplikat."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]
    cloud = api_test_env["cloud"]
    rfid_mgr = api_test_env["rfid_manager"]
    session_factory = api_test_env["session_factory"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))
    cloud.claim_mode = "success"
    cloud.confirm_mode = "success"

    scan_sess = RFIDScanSession(operation_id="op_idempotent_01", paket_id=101)
    scan_sess.add_epc("E28011606000020496000001")
    scan_sess.add_epc("E28011606000020496000002")
    scan_sess.freeze_snapshot()
    rfid_mgr.register_scan_session(scan_sess)

    payload = {
        "paket_id": 101,
        "verification_code": "VC-101",
        "operation_id": "op_idempotent_01",
    }
    res1 = client.post("/api/receiving/process", json=payload, cookies=cookies, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["lifecycle_phase"] == "confirmed"

    # Panggilan kedua (idempotent replay)
    res2 = client.post("/api/receiving/process", json=payload, cookies=cookies, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["lifecycle_phase"] == "confirmed"
    assert res2.json().get("idempotent") is True

    # Buktikan di SQLite tepat 1 TransaksiLinen
    with session_factory() as db:
        trx_count = db.query(TransaksiLinen).count()
        assert trx_count == 1, "Dilarang membuat transaksi linen duplikat saat replay!"


def test_09_confirm_timeout_maintains_local_committed(api_test_env, monkeypatch):
    """9. Confirm timeout: Transaksi lokal tetap tersimpan, status operasi tetap local_committed."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]
    cloud = api_test_env["cloud"]
    rfid_mgr = api_test_env["rfid_manager"]
    session_factory = api_test_env["session_factory"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))
    cloud.claim_mode = "success"
    cloud.confirm_mode = "timeout"

    scan_sess = RFIDScanSession(operation_id="op_confirm_timeout_01", paket_id=101)
    scan_sess.add_epc("E28011606000020496000001")
    scan_sess.add_epc("E28011606000020496000002")
    scan_sess.freeze_snapshot()
    rfid_mgr.register_scan_session(scan_sess)

    payload = {
        "paket_id": 101,
        "verification_code": "VC-101",
        "operation_id": "op_confirm_timeout_01",
    }
    res = client.post("/api/receiving/process", json=payload, cookies=cookies, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["lifecycle_phase"] == "local_committed"
    assert data["sync_state"] == "pending_confirm"

    with session_factory() as db:
        row = db.query(PaketReceivingOperation).filter_by(operation_id="op_confirm_timeout_01").first()
        assert row.lifecycle_phase == "local_committed"
        # Transaksi lokal tetap ada
        assert row.transaksi_id is not None


def test_10_claim_held_reconciliation_operation_locked(api_test_env, monkeypatch):
    """10. Operasi claim_held_reconciliation: Ditolak dan terkunci dari pemrosesan baru."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]
    session_factory = api_test_env["session_factory"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))

    # Masukkan operasi berstatus claim_held_reconciliation ke DB
    with session_factory() as db:
        db.add(
            PaketReceivingOperation(
                operation_id="OP-HELD-LOCKED",
                paket_id=101,
                hospital_id=101,
                installation_uid="inst_api_test_node_101",
                verification_code="VC-101",
                manifest_hash="hash",
                lifecycle_phase="claim_held_reconciliation",
                sync_state="claim_held_reconciliation",
            )
        )
        db.commit()

    # Percobaan mulai scan baru ditolak
    res_scan = client.post("/api/receiving/scan/start", json={"paket_id": 101}, cookies=cookies, headers=headers)
    assert res_scan.status_code == 409
    assert "claim_held_reconciliation" in res_scan.json()["detail"]


def test_11_auth_blocked_user_operation_rejection(api_test_env, monkeypatch):
    """11. Operasi auth_blocked_user: Ditolak dan diblokir dari pemrosesan ulang."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]
    session_factory = api_test_env["session_factory"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))

    with session_factory() as db:
        db.add(
            PaketReceivingOperation(
                operation_id="OP-BLOCKED-USER",
                paket_id=101,
                hospital_id=101,
                installation_uid="inst_api_test_node_101",
                verification_code="VC-101",
                manifest_hash="hash",
                lifecycle_phase="auth_blocked_user",
                sync_state="pending_claim",
            )
        )
        db.commit()

    res = client.post("/api/receiving/scan/start", json={"paket_id": 101}, cookies=cookies, headers=headers)
    assert res.status_code == 409
    assert "auth_blocked_user" in res.json()["detail"]


def test_12_prevent_parallel_duplicate_transactions(api_test_env, monkeypatch):
    """12. Pencegahan transaksi ganda: Paket sedang diproses oleh OP-1, request dengan OP-2 ditolak."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]
    session_factory = api_test_env["session_factory"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))

    with session_factory() as db:
        db.add(
            PaketReceivingOperation(
                operation_id="OP-ACTIVE-FIRST",
                paket_id=101,
                hospital_id=101,
                installation_uid="inst_api_test_node_101",
                verification_code="VC-101",
                manifest_hash="hash",
                lifecycle_phase="claiming",
                sync_state="pending_claim",
            )
        )
        db.commit()

    # Percobaan scan dengan operation_id berbeda ditolak
    res = client.post(
        "/api/receiving/scan/start",
        json={"paket_id": 101, "operation_id": "OP-COMPETING-SECOND"},
        cookies=cookies,
        headers=headers,
    )
    assert res.status_code == 409
    assert "operasi aktif lain" in res.json()["detail"].lower()


def test_13_ui_lifecycle_rendering(api_test_env, monkeypatch):
    """13. UI menampilkan status lifecycle dan komponen penerimaan paket dengan benar."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client = api_test_env["client"]
    store = api_test_env["session_store"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))

    res = client.get("/penerimaan", cookies=cookies)
    assert res.status_code == 200
    html = res.text

    assert "Penerimaan Paket Linen Supplier" in html
    assert "MENUNGGU PEMINDAIAN" in html
    assert "lifecycle-badge" in html
    assert "manifest-match-badge" in html
    assert "gate-banner" in html
    assert "history-table-body" in html
    assert "btn-process-receiving" in html


def test_14_endpoint_locked_when_feature_gate_disabled(api_test_env, monkeypatch):
    """14. Endpoint tetap terkunci ketika feature gate nonaktif (default): Mengembalikan HTTP 503."""
    # Pastikan feature gate mati (default)
    monkeypatch.delenv("ENABLE_PACKAGE_RECEIVING_API", raising=False)
    client = api_test_env["client"]
    store = api_test_env["session_store"]

    cookies, headers = asyncio.run(create_auth_cookie(store, hospital_id=101))

    # 1. Cek endpoint feature-status (dapat diakses untuk reporting)
    res_status = client.get("/api/receiving/feature-status")
    assert res_status.status_code == 200
    assert res_status.json()["feature_gate_enabled"] is False
    assert res_status.json()["status"] == "locked"

    # 2. Endpoint operasional wajib terkunci dengan HTTP 503
    res_pkg = client.get("/api/receiving/package/101", cookies=cookies, headers=headers)
    assert res_pkg.status_code == 503
    assert "terkunci" in res_pkg.json()["detail"].lower()

    res_proc = client.post(
        "/api/receiving/process",
        json={"paket_id": 101, "verification_code": "VC-101"},
        cookies=cookies,
        headers=headers,
    )
    assert res_proc.status_code == 503
    assert "terkunci" in res_proc.json()["detail"].lower()
