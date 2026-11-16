"""
=============================================================================
TEST SUITE: tests/test_r2_9_supabase_integration.py
STAGE: TAHAP 4D.2A.3-R2.9 (AUTOMATED REGRESSION & INTEGRATION TEST SUITE)
DESKRIPSI:
    Menguji integrasi ClientS dengan Supabase RPC secara menyeluruh pada
    database SQLite terisolasi temporer, mencakup 18 skenario wajib:
    1.  Adapter RPC Claim: payload sesuai kontrak DDL R2.9
    2.  Adapter RPC Confirm: 6 parameter eksak (tanpa permit_id)
    3.  Adapter Status: mengembalikan status lengkap dengan audit fields
    4.  Authentication Headers: apikey dan Bearer token
    5.  Expired Token: HTTP 401 raises SupabaseAuthenticationError
    6.  HTTP Timeout: timeout menghasilkan claim_uncertain tanpa commit lokal
    7.  Invalid JSON Response: penanganan aman SupabaseRPCError
    8.  Manifest Mismatch: respons CONFLICT ditangani tanpa commit lokal
    9.  Tenant Mismatch: HTTP 403 raises SupabaseAuthorizationError
    10. Idempotent Claim: replay klaim ditangani aman
    11. Idempotent Confirm: replay confirm ditangani aman
    12. Claim Uncertain Recovery: rekonsiliasi memulihkan state ke claim_verified
    13. Restart Recovery: startup recovery memulihkan claiming ke claim_held_reconciliation
    14. Duplicate Local Transaction Prevention: jaminan tidak ada duplikasi TransaksiLinen
    15. API Operations History Tenant Isolation: filter riwayat operasi per hospital_id
    16. Verification Code Exposure Prevention: verification_code tidak pernah diekspos di API
    17. Production Adapter Selection: APP_ENV=production memaksa SupabaseCloudPackageAdapter
    18. Legacy PATCH Prohibition: mark_paket_received memicu exception DIRECT_PATCH_PROHIBITED
=============================================================================
"""

import asyncio
import os
import shutil
import tempfile
from typing import Any, Optional

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.database import Base
from app.models.linen import Linen
from app.models.paket_receiving_operation import PaketReceivingOperation
from app.models.ruangan import Ruangan
from app.models.rumah_sakit import RumahSakit
from app.models.transaksi_linen import TransaksiLinen
from app.routers.auth_router import get_default_session_store
from app.routers.receiving_router import (
    get_default_cloud_adapter,
    set_custom_cloud_adapter,
)
from app.services.cloud_sync_service import CloudSyncService
from app.services.hospital_binding_service import (
    provision_hospital_binding,
    verify_hospital_binding,
)
from app.services.package_receiving_service import (
    ActivePackageOperationConflictError,
    HospitalMismatchError,
    OperationCircuitBreaker,
    PackageReceivingService,
)
from app.services.recovery_service import execute_startup_recovery
from app.services.rfid_service import RFIDScanSession, RFIDServiceManager
from app.services.session_store import SessionData, SessionStore
from app.services.supabase_cloud_adapter import (
    SupabaseAuthenticationError,
    SupabaseAuthorizationError,
    SupabaseCloudPackageAdapter,
    SupabaseRPCError,
)


@pytest.fixture
def isolated_db_env():
    """Membuat database SQLite temporer yang sepenuhnya terisolasi dalam direktori terkelola."""
    managed_dir = os.environ.get("CLIENTS_TEST_MANAGED_DIR")
    fd, db_path = tempfile.mkstemp(suffix="_r29_integration.db", dir=managed_dir)
    os.close(fd)

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

    # Inisialisasi Rumah Sakit ID 101 dan master ruangan
    with session_factory() as db:
        provision_hospital_binding(db, hospital_id=101, nama_rs="RS Uji R2.9", kode_rs="RS_R29", alamat="Jl. Uji 1")
        r1 = Ruangan(kode_ruangan="R1", nama_ruangan="Ruang Mawar", is_active=True)
        r2 = Ruangan(kode_ruangan="R2", nama_ruangan="Ruang Melati", is_active=True)
        db.add_all([r1, r2])
        db.commit()

    yield {
        "engine": engine,
        "db_path": db_path,
        "session_factory": session_factory,
    }

    engine.dispose()
    try:
        if os.path.exists(db_path):
            os.unlink(db_path)
    except Exception:
        pass


class DummyUIDProvider:
    def __init__(self, uid: str = "inst_r29_node_jakarta_01"):
        self.uid = uid

    def get_installation_uid(self) -> str:
        return self.uid


async def make_test_session(store: SessionStore, hospital_id: int = 101, email: str = "operator@rs.id") -> SessionData:
    return await store.create_session(
        user_id="user_test_r29",
        email=email,
        hospital_id=hospital_id,
        hospital_name="RS Uji R2.9",
        hospital_code="RS_R29",
        role="operator",
        access_token="tok_test_r29",
        refresh_token="ref_test_r29",
        token_expires_in=28800,
    )


# =============================================================================
# SKENARIO 1: Adapter RPC Claim (Payload sesuai kontrak DDL R2.9)
# =============================================================================
@pytest.mark.anyio
async def test_scen01_adapter_rpc_claim_contract():
    captured_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["url"] = str(request.url)
        captured_request["method"] = request.method
        captured_request["json"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "status": "CLAIMED",
                "package_id": 201,
                "claimed_by_operation_id": "op_test_01",
                "claimed_by_installation_uid": "inst_01",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key_test",
        client=client,
    )

    payload = {
        "paket_id": 201,
        "hospital_id": 101,
        "operation_id": "op_test_01",
        "installation_uid": "inst_01",
        "verification_code": "VC-201",
        "expected_manifest_hash": "a" * 64,
    }
    res = await adapter.claim_package("user_token_valid", payload)

    assert "rpc/claim_package" in captured_request["url"]
    assert captured_request["method"] == "POST"
    import json
    body = json.loads(captured_request["json"])
    assert body["p_package_id"] == 201
    assert body["p_hospital_id"] == 101
    assert body["p_operation_id"] == "op_test_01"
    assert body["p_installation_uid"] == "inst_01"
    assert body["p_verification_code"] == "VC-201"
    assert body["p_computed_manifest_hash"] == "a" * 64
    assert res["status"] == "CLAIMED"


# =============================================================================
# SKENARIO 2: Adapter RPC Confirm (6 parameter eksak, tanpa p_permit_id)
# =============================================================================
@pytest.mark.anyio
async def test_scen02_adapter_rpc_confirm_exact_six_params():
    captured_request = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_request["json"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"status": "CONFIRMED", "package_id": 202})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key_test",
        client=client,
    )

    payload = {
        "paket_id": 202,
        "hospital_id": 101,
        "operation_id": "op_test_02",
        "installation_uid": "inst_02",
        "verification_code": "VC-202",
        "manifest_hash": "b" * 64,
        "permit_id": "permit_local_only",  # Harus diabaikan / tidak dikirim ke RPC
    }
    await adapter.confirm_package("user_token_valid", payload)

    import json
    body = json.loads(captured_request["json"])
    assert len(body) == 6
    assert set(body.keys()) == {
        "p_package_id",
        "p_hospital_id",
        "p_operation_id",
        "p_installation_uid",
        "p_verification_code",
        "p_manifest_hash",
    }
    assert "p_permit_id" not in body
    assert "permit_id" not in body


# =============================================================================
# SKENARIO 3: Adapter Status (Audit trail fields)
# =============================================================================
@pytest.mark.anyio
async def test_scen03_adapter_status_audit_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "package_status": "claimed",
                "claimed_by_operation_id": "op_audit_01",
                "claimed_by_installation_uid": "inst_node_01",
                "claimed_at": "2026-11-14T10:00:00Z",
                "confirmed_by_operation_id": None,
                "confirmed_by_installation_uid": None,
                "confirmed_at": None,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key_test",
        client=client,
    )

    status_data = await adapter.get_package_claim_status("token", 203, 101)
    assert status_data["package_status"] == "claimed"
    assert status_data["claimed_by_operation_id"] == "op_audit_01"
    assert status_data["claimed_by_installation_uid"] == "inst_node_01"
    assert status_data["claimed_at"] is not None


# =============================================================================
# SKENARIO 4: Authentication Headers (apikey & Bearer token enforcement)
# =============================================================================
@pytest.mark.anyio
async def test_scen04_auth_headers_and_security_guard():
    captured_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(request.headers)
        return httpx.Response(200, json={"status": "OK"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="valid_pub_key_123",
        client=client,
    )

    # 1. Sukses dengan token pengguna sah
    await adapter._send_rpc("valid_user_jwt_token", "dummy_rpc", {})
    assert captured_headers["apikey"] == "valid_pub_key_123"
    assert captured_headers["authorization"] == "Bearer valid_user_jwt_token"

    # 2. Ditolak jika user_token kosong
    with pytest.raises(SupabaseAuthenticationError):
        await adapter._send_rpc("", "dummy_rpc", {})

    # 3. Ditolak jika publishable key ditukar sebagai user token
    with pytest.raises(SupabaseAuthenticationError):
        await adapter._send_rpc("valid_pub_key_123", "dummy_rpc", {})


# =============================================================================
# SKENARIO 5: Expired Token (HTTP 401 raises SupabaseAuthenticationError)
# =============================================================================
@pytest.mark.anyio
async def test_scen05_expired_token_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "JWT expired"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key",
        client=client,
    )

    with pytest.raises(SupabaseAuthenticationError) as exc:
        await adapter.claim_package("expired_jwt", {"paket_id": 205, "hospital_id": 101, "operation_id": "op", "installation_uid": "inst", "verification_code": "VC", "expected_manifest_hash": "h"})
    assert "401" in str(exc.value)


# =============================================================================
# SKENARIO 6: HTTP Timeout (Mencatat claim_uncertain tanpa commit transaksi)
# =============================================================================
@pytest.mark.anyio
async def test_scen06_http_timeout_maintains_claim_uncertain(isolated_db_env):
    env = isolated_db_env
    store = SessionStore(default_session_duration_seconds=28800)
    sess = await make_test_session(store, hospital_id=101)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Supabase gateway timeout after 10s")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key",
        client=client,
    )

    coord = PackageReceivingService(
        session_store=store,
        rfid_service_manager=RFIDServiceManager(),
        cloud_adapter=adapter,
        circuit_breaker=OperationCircuitBreaker(),
        installation_uid_provider=DummyUIDProvider(),
        db_session_factory=env["session_factory"],
    )

    scan_sess = RFIDScanSession(operation_id="op_timeout_test", paket_id=206)
    scan_sess.add_epc("E28011606000020496000001")
    manifest = [{"epc": "E28011606000020496000001", "kategori": "Linen", "nama_linen": "Sprei"}]

    res = await coord.process_package_receiving(
        session_id=sess.session_id,
        paket_id=206,
        verification_code="VC-206",
        manifest_items=manifest,
        scan_session=scan_sess,
        operation_id="op_timeout_test",
    )

    assert res["status"] == "uncertain"
    assert res["lifecycle_phase"] == "claim_uncertain"

    with env["session_factory"]() as db:
        # Transaksi linen lokal TIDAK boleh ada
        assert db.query(TransaksiLinen).count() == 0
        # Operasi tercatat sebagai claim_uncertain
        op = db.query(PaketReceivingOperation).filter_by(operation_id="op_timeout_test").first()
        assert op is not None
        assert op.lifecycle_phase == "claim_uncertain"


# =============================================================================
# SKENARIO 7: Invalid JSON Response (SupabaseRPCError ditangani aman)
# =============================================================================
@pytest.mark.anyio
async def test_scen07_invalid_json_handled_safely():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<HTML>502 Bad Gateway</HTML>", headers={"Content-Type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key",
        client=client,
    )

    with pytest.raises(SupabaseRPCError) as exc:
        await adapter.claim_package("token", {"paket_id": 207, "hospital_id": 101, "operation_id": "op", "installation_uid": "inst", "verification_code": "VC", "expected_manifest_hash": "h"})
    assert "bukan format JSON" in str(exc.value)


# =============================================================================
# SKENARIO 8: Manifest Mismatch (Cloud CONFLICT menghasilkan claim_uncertain/rejected)
# =============================================================================
@pytest.mark.anyio
async def test_scen08_manifest_mismatch_no_local_commit(isolated_db_env):
    env = isolated_db_env
    store = SessionStore(default_session_duration_seconds=28800)
    sess = await make_test_session(store, hospital_id=101)

    def handler(request: httpx.Request) -> httpx.Response:
        # Cloud mengembalikan respons hash berbeda
        return httpx.Response(
            200,
            json={
                "status": "MISMATCH",
                "paket_id": 208,
                "operation_id": "op_mismatch_test",
                "installation_uid": "inst_r29_node_jakarta_01",
                "status_paket": "claimed",
                "manifest_hash": "c" * 64,  # Berbeda dari kalkulasi lokal
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key",
        client=client,
    )

    coord = PackageReceivingService(
        session_store=store,
        rfid_service_manager=RFIDServiceManager(),
        cloud_adapter=adapter,
        circuit_breaker=OperationCircuitBreaker(),
        installation_uid_provider=DummyUIDProvider(),
        db_session_factory=env["session_factory"],
    )

    scan_sess = RFIDScanSession(operation_id="op_mismatch_test", paket_id=208)
    scan_sess.add_epc("E28011606000020496000001")
    manifest = [{"epc": "E28011606000020496000001", "kategori": "Linen", "nama_linen": "Sprei"}]

    res = await coord.process_package_receiving(
        session_id=sess.session_id,
        paket_id=208,
        verification_code="VC-208",
        manifest_items=manifest,
        scan_session=scan_sess,
        operation_id="op_mismatch_test",
    )

    assert res["status"] == "mismatch"
    assert res["lifecycle_phase"] == "claim_uncertain"

    with env["session_factory"]() as db:
        # Pastikan tidak ada transaksi lokal yang bocor
        assert db.query(TransaksiLinen).count() == 0


# =============================================================================
# SKENARIO 9: Tenant Mismatch (HTTP 403 raises SupabaseAuthorizationError)
# =============================================================================
@pytest.mark.anyio
async def test_scen09_tenant_mismatch_raises_authorization_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Paket bukan milik rumah sakit Anda"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key",
        client=client,
    )

    with pytest.raises(SupabaseAuthorizationError) as exc:
        await adapter.claim_package("token", {"paket_id": 209, "hospital_id": 102, "operation_id": "op", "installation_uid": "inst", "verification_code": "VC", "expected_manifest_hash": "h"})
    assert "403" in str(exc.value)


# =============================================================================
# SKENARIO 10: Idempotent Claim (Replay klaim ditangani aman)
# =============================================================================
@pytest.mark.anyio
async def test_scen10_idempotent_claim_handling():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "ALREADY_CLAIMED_BY_SELF",
                "paket_id": 210,
                "operation_id": "op_idemp_claim",
                "installation_uid": "inst_01",
                "status_paket": "claimed",
                "manifest_hash": "e" * 64,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key",
        client=client,
    )

    res = await adapter.claim_package("token", {
        "paket_id": 210,
        "hospital_id": 101,
        "operation_id": "op_idemp_claim",
        "installation_uid": "inst_01",
        "verification_code": "VC-210",
        "expected_manifest_hash": "e" * 64,
    })
    assert res["status"] == "ALREADY_CLAIMED_BY_SELF"


# =============================================================================
# SKENARIO 11: Idempotent Confirm (Replay confirm ditangani aman)
# =============================================================================
@pytest.mark.anyio
async def test_scen11_idempotent_confirm_handling():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "ALREADY_CONFIRMED_BY_SELF",
                "package_id": 211,
                "confirmed_by_operation_id": "op_idemp_confirm",
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key",
        client=client,
    )

    res = await adapter.confirm_package("token", {
        "paket_id": 211,
        "hospital_id": 101,
        "operation_id": "op_idemp_confirm",
        "installation_uid": "inst_01",
        "verification_code": "VC-211",
        "manifest_hash": "f" * 64,
    })
    assert res["status"] == "ALREADY_CONFIRMED_BY_SELF"


# =============================================================================
# SKENARIO 12: Claim Uncertain Recovery (Rekonsiliasi via Status RPC)
# =============================================================================
@pytest.mark.anyio
async def test_scen12_claim_uncertain_reconciliation(isolated_db_env):
    env = isolated_db_env
    store = SessionStore(default_session_duration_seconds=28800)
    sess = await make_test_session(store, hospital_id=101)

    def handler(request: httpx.Request) -> httpx.Response:
        if "rpc/get_package_claim_status" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "package_status": "claimed",
                    "package_id": 212,
                    "hospital_id": 101,
                    "claimed_by_operation_id": "op_recon_12",
                    "claimed_by_installation_uid": "inst_r29_node_jakarta_01",
                    "manifest_hash": "0" * 64,
                },
            )
        return httpx.Response(200, json={"status": "OK"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(
        supabase_url="https://xyz.supabase.co",
        publishable_key="pub_key",
        client=client,
    )

    coord = PackageReceivingService(
        session_store=store,
        rfid_service_manager=RFIDServiceManager(),
        cloud_adapter=adapter,
        circuit_breaker=OperationCircuitBreaker(),
        installation_uid_provider=DummyUIDProvider(),
        db_session_factory=env["session_factory"],
    )

    with env["session_factory"]() as db:
        db.add(
            PaketReceivingOperation(
                operation_id="op_recon_12",
                paket_id=212,
                hospital_id=101,
                installation_uid="inst_r29_node_jakarta_01",
                verification_code="VC-212",
                manifest_hash="0" * 64,
                scan_count=1,
                lifecycle_phase="claim_held_reconciliation",
                sync_state="claim_held_reconciliation",
            )
        )
        db.commit()

    recon_res = await coord.reconcile_held_operation("op_recon_12", sess.session_id, "token", 101)
    assert recon_res["status"] == "reconciled"
    assert recon_res["lifecycle_phase"] == "claim_verified"

    with env["session_factory"]() as db:
        op = db.query(PaketReceivingOperation).filter_by(operation_id="op_recon_12").first()
        assert op.lifecycle_phase == "claim_verified"


# =============================================================================
# SKENARIO 13: Restart Recovery (Crash claiming dipulihkan ke claim_held_reconciliation)
# =============================================================================
def test_scen13_restart_recovery_crash_in_claiming(isolated_db_env):
    env = isolated_db_env
    with env["session_factory"]() as db:
        db.add(
            PaketReceivingOperation(
                operation_id="op_crash_reboot",
                paket_id=213,
                hospital_id=101,
                installation_uid="inst_r29_node_jakarta_01",
                verification_code="VC-213",
                manifest_hash="h" * 64,
                scan_count=1,
                lifecycle_phase="claiming",
                sync_state="pending_claim",
            )
        )
        db.commit()

        # Eksekusi startup recovery
        execute_startup_recovery(db)

        op = db.query(PaketReceivingOperation).filter_by(operation_id="op_crash_reboot").first()
        assert op.lifecycle_phase == "claim_held_reconciliation"
        assert op.sync_state == "claim_held_reconciliation"


# =============================================================================
# SKENARIO 14: Duplicate Local Transaction Prevention (Tepat 1 TransaksiLinen)
# =============================================================================
@pytest.mark.anyio
async def test_scen14_prevent_duplicate_local_transactions(isolated_db_env):
    env = isolated_db_env
    store = SessionStore(default_session_duration_seconds=28800)
    sess = await make_test_session(store, hospital_id=101)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "rpc/claim_package" in url:
            import json
            body = json.loads(request.read().decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "status": "CLAIMED",
                    "paket_id": 214,
                    "operation_id": body.get("p_operation_id"),
                    "installation_uid": body.get("p_installation_uid"),
                    "status_paket": "claimed",
                    "manifest_hash": body.get("p_computed_manifest_hash"),
                },
            )
        if "rpc/confirm_package" in url:
            import json
            body = json.loads(request.read().decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "status": "confirmed",
                    "paket_id": 214,
                    "operation_id": "op_no_dup",
                    "hospital_id": 101,
                    "installation_uid": body.get("p_installation_uid", "inst_r29_node_jakarta_01"),
                    "manifest_hash": body.get("p_manifest_hash"),
                },
            )
        return httpx.Response(200, json={"status": "OK"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SupabaseCloudPackageAdapter(supabase_url="https://xyz.supabase.co", publishable_key="pub", client=client)

    coord = PackageReceivingService(
        session_store=store,
        rfid_service_manager=RFIDServiceManager(),
        cloud_adapter=adapter,
        circuit_breaker=OperationCircuitBreaker(),
        installation_uid_provider=DummyUIDProvider(),
        db_session_factory=env["session_factory"],
    )

    scan_sess = RFIDScanSession(operation_id="op_no_dup", paket_id=214)
    scan_sess.add_epc("E28011606000020496000001")
    manifest = [{"epc": "E28011606000020496000001", "kategori": "Linen", "nama_linen": "Sprei"}]

    # Panggilan pertama: berhasil
    res1 = await coord.process_package_receiving(
        session_id=sess.session_id,
        paket_id=214,
        verification_code="VC-214",
        manifest_items=manifest,
        scan_session=scan_sess,
        operation_id="op_no_dup",
    )
    assert res1["lifecycle_phase"] == "confirmed"

    # Panggilan kedua (idempotent replay dengan operation_id sama)
    res2 = await coord.process_package_receiving(
        session_id=sess.session_id,
        paket_id=214,
        verification_code="VC-214",
        manifest_items=manifest,
        scan_session=scan_sess,
        operation_id="op_no_dup",
    )
    assert res2["lifecycle_phase"] == "confirmed"
    assert res2.get("idempotent") is True

    # Panggilan ketiga (mencoba paket sama dengan operation_id berbeda -> DITOLAK)
    scan_competing = RFIDScanSession(operation_id="op_competing_diff", paket_id=214)
    scan_competing.add_epc("E28011606000020496000001")
    with pytest.raises(ActivePackageOperationConflictError):
        await coord.process_package_receiving(
            session_id=sess.session_id,
            paket_id=214,
            verification_code="VC-214",
            manifest_items=manifest,
            scan_session=scan_competing,
            operation_id="op_competing_diff",
        )

    with env["session_factory"]() as db:
        # Buktikan di database SQLite HANYA ada tepat 1 TransaksiLinen
        assert db.query(TransaksiLinen).count() == 1


# =============================================================================
# SKENARIO 15: API Operations History Tenant Isolation
# =============================================================================
def test_scen15_api_operations_history_tenant_isolation(isolated_db_env, monkeypatch):
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    from app.main import app, auth_config
    from app.database import get_db

    env = isolated_db_env
    store = get_default_session_store()

    # Buat 2 record operasi pada DB lokal: satu milik RS 101, satu milik RS 999
    with env["session_factory"]() as db:
        db.add(PaketReceivingOperation(
            operation_id="op_rs101",
            paket_id=215,
            hospital_id=101,
            installation_uid="inst_101",
            verification_code="VC",
            manifest_hash="h",
            lifecycle_phase="confirmed",
        ))
        db.add(PaketReceivingOperation(
            operation_id="op_rs999",
            paket_id=215,
            hospital_id=999,
            installation_uid="inst_999",
            verification_code="VC",
            manifest_hash="h",
            lifecycle_phase="confirmed",
        ))
        db.commit()

    sess = asyncio.run(store.create_session(
        user_id="user_15",
        email="op@rs101.id",
        hospital_id=101,
        hospital_name="RS Uji R2.9",
        hospital_code="RS_R29",
        role="operator",
        access_token="tok_15",
        refresh_token="ref_15",
        token_expires_in=28800,
    ))

    def _override_db():
        with env["session_factory"]() as s:
            yield s

    # Hapus override autouse dari conftest agar otentikasi nyata diuji
    from app.services.auth_dependencies import require_active_session
    app.dependency_overrides.pop(require_active_session, None)
    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)
        cookies = {auth_config.cookie_name: sess.session_id}
        headers = {
            auth_config.custom_header_name: auth_config.custom_header_value,
            "Origin": "http://testserver",
        }

        res = client.get("/api/receiving/operations/215", cookies=cookies, headers=headers)
        assert res.status_code == 200
        ops = res.json()["operations"]
        assert len(ops) == 1
        assert ops[0]["operation_id"] == "op_rs101"
        assert ops[0]["hospital_id"] == 101
    finally:
        app.dependency_overrides.clear()


# =============================================================================
# SKENARIO 16: Verification Code Exposure Prevention
# =============================================================================
def test_scen16_verification_code_exposure_prevention(isolated_db_env, monkeypatch):
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    from app.main import app, auth_config
    from app.database import get_db

    env = isolated_db_env
    store = get_default_session_store()
    sess = asyncio.run(store.create_session(
        user_id="user_16",
        email="op@rs101.id",
        hospital_id=101,
        hospital_name="RS Uji R2.9",
        hospital_code="RS_R29",
        role="operator",
        access_token="tok_16",
        refresh_token="ref_16",
        token_expires_in=28800,
    ))

    # Mock adapter info paket
    class MockAdapterWithInfo:
        async def get_package_info(self, token, paket_id):
            return {
                "paket_id": paket_id,
                "hospital_id": 101,
                "status": "pending",
                "manifest_items": [],
                "verification_code": "SECRET_SHOULD_BE_STRIPPED",
            }

    set_custom_cloud_adapter(MockAdapterWithInfo())

    def _override_db():
        with env["session_factory"]() as s:
            yield s

    # Hapus override autouse dari conftest agar otentikasi nyata diuji
    from app.services.auth_dependencies import require_active_session
    app.dependency_overrides.pop(require_active_session, None)
    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)
        cookies = {auth_config.cookie_name: sess.session_id}
        headers = {
            auth_config.custom_header_name: auth_config.custom_header_value,
            "Origin": "http://testserver",
        }

        res = client.get("/api/receiving/package/216", cookies=cookies, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "verification_code" not in data or data.get("verification_code") is None
        assert "SECRET_SHOULD_BE_STRIPPED" not in res.text
    finally:
        set_custom_cloud_adapter(None)
        app.dependency_overrides.clear()


# =============================================================================
# SKENARIO 17: Production Adapter Selection (APP_ENV=production enforces SupabaseCloudPackageAdapter)
# =============================================================================
def test_scen17_production_adapter_selection_guard(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)

    # Di production tanpa konfigurasi URL/Key -> wajib fail-fast
    with pytest.raises(RuntimeError) as exc:
        get_default_cloud_adapter()
    assert "PRODUCTION_CONFIG_ERROR" in str(exc.value)

    # Ketika kredensial ada di production -> menghasilkan SupabaseCloudPackageAdapter asli
    monkeypatch.setenv("SUPABASE_URL", "https://prod.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "prod_pub_key")
    adapter = get_default_cloud_adapter()
    assert isinstance(adapter, SupabaseCloudPackageAdapter)
    assert adapter.url == "https://prod.supabase.co"


# =============================================================================
# SKENARIO 18: Legacy PATCH Prohibition (mark_paket_received throws DIRECT_PATCH_PROHIBITED)
# =============================================================================
def test_scen18_legacy_patch_prohibited_throws_runtime_error(isolated_db_env):
    service = CloudSyncService(url="https://xyz.supabase.co", key="anon_key")
    with pytest.raises(RuntimeError) as exc:
        service.mark_paket_received(paket_id=218)
    assert "DIRECT_PATCH_PROHIBITED" in str(exc.value)


# =============================================================================
# SKENARIO 19: Strict Confirm Response (P1 Mandat R2.10)
# =============================================================================
@pytest.mark.anyio
async def test_scen19_strict_confirm_response_validation(isolated_db_env, monkeypatch):
    """
    Validasi ketat bahwa respons Confirm wajib memiliki seluruh 6 field non-null:
    paket_id, hospital_id, operation_id, installation_uid, manifest_hash, status.
    Jika ada field yang hilang/NULL, status lokal dipertahankan local_committed dan tidak disinkronkan.
    """
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    env = isolated_db_env

    # 1. Respons dengan hospital_id NULL -> ditolak, status tetap local_committed
    class MockAdapterNullHospital:
        async def claim_package(self, token, payload):
            return {
                "status": "SUCCESS",
                "status_paket": "claimed",
                "paket_id": 219,
                "operation_id": payload["operation_id"],
                "installation_uid": "INST_TEST",
                "manifest_hash": payload["expected_manifest_hash"],
            }
        async def confirm_package(self, token, payload):
            return {
                "paket_id": 219,
                "hospital_id": None,  # ILLEGAL NULL
                "operation_id": payload["operation_id"],
                "installation_uid": payload["installation_uid"],
                "manifest_hash": payload["manifest_hash"],
                "status": "confirmed",
            }

    store = SessionStore(default_session_duration_seconds=28800)
    sess = await store.create_session("u19", "u19@test.id", 101, "RS Uji", "RS_UJI", "operator", "tok19", "ref19", 3600)
    rfid_mgr = RFIDServiceManager()
    scan_sess = RFIDScanSession(operation_id="op_scen19", paket_id=219)
    scan_sess.add_epc("E280116060000219ABCDEF19")

    coord = PackageReceivingService(
        db_session_factory=env["session_factory"],
        cloud_adapter=MockAdapterNullHospital(),
        session_store=store,
        rfid_service_manager=rfid_mgr,
        installation_uid_provider=DummyUIDProvider("INST_TEST"),
    )

    res = await coord.process_package_receiving(
        session_id=sess.session_id,
        paket_id=219,
        verification_code="VC_VALID_19",
        operation_id="op_scen19",
        ruangan_tujuan="Ruang Mawar",
        manifest_items=[{"epc": "E280116060000219ABCDEF19", "kategori": "Sprei", "nama_linen": "Sprei Standard"}],
        scan_session=scan_sess,
    )
    assert res["status"] == "confirm_unverified"
    assert res["lifecycle_phase"] == "local_committed"
    assert res["sync_state"] == "pending_confirm"

    # Verifikasi database lokal: sync_state tetap pending_confirm
    with env["session_factory"]() as db:
        row = db.query(PaketReceivingOperation).filter_by(paket_id=219).first()
        assert row is not None
        assert row.lifecycle_phase == "local_committed"
        assert row.sync_state == "pending_confirm"


# =============================================================================
# SKENARIO 20: Operational Reconcile Endpoint (P0 Mandat R2.10)
# =============================================================================
def test_scen20_operational_reconcile_endpoint_e2e(isolated_db_env, monkeypatch):
    """
    Menguji endpoint operasional POST /receiving/operations/{operation_id}/reconcile
    termasuk active session, tenant isolation (mencegah rekonsiliasi lintas RS),
    installation UID check, dan pemulihan operasi held/uncertain.
    """
    from app.main import app, auth_config
    from app.database import get_db
    from app.services.installation_service import PersistentFileInstallationUIDProvider

    env = isolated_db_env
    host_inst_uid = PersistentFileInstallationUIDProvider().get_installation_uid()

    # Siapkan session store dan 2 user dari rumah sakit berbeda
    store = get_default_session_store()
    sess_rs101 = asyncio.run(store.create_session(
        user_id="user_rs101",
        email="op@rs101.id",
        hospital_id=101,
        hospital_name="RS Uji R2.10",
        hospital_code="RS_R210",
        role="operator",
        access_token="tok_rs101",
        refresh_token="ref_rs101",
        token_expires_in=28800,
    ))

    sess_rs202 = asyncio.run(store.create_session(
        user_id="user_rs202",
        email="op@rs202.id",
        hospital_id=202,
        hospital_name="RS Lain",
        hospital_code="RS_LAIN",
        role="operator",
        access_token="tok_rs202",
        refresh_token="ref_rs202",
        token_expires_in=28800,
    ))

    # Masukkan record operasi lokal milik RS 101 dalam status claim_uncertain
    op_id = "OP_REC_TEST_20"
    with env["session_factory"]() as db:
        rec = PaketReceivingOperation(
            operation_id=op_id,
            paket_id=220,
            hospital_id=101,
            installation_uid=host_inst_uid,
            verification_code="VC_TEST_20",
            manifest_hash="hash_test_20",
            lifecycle_phase="claim_uncertain",
            sync_state="claim_held_reconciliation",
        )
        rec_bad_inst = PaketReceivingOperation(
            operation_id="OP_BAD_INST_20",
            paket_id=221,
            hospital_id=101,
            installation_uid="INST_OTHER_FOREIGN",
            verification_code="VC_TEST_20B",
            manifest_hash="hash_test_20b",
            lifecycle_phase="claim_uncertain",
            sync_state="claim_held_reconciliation",
        )
        db.add_all([rec, rec_bad_inst])
        db.commit()

    def _override_db():
        with env["session_factory"]() as s:
            yield s

    # Hapus override autouse dari conftest agar otentikasi nyata diuji
    from app.services.auth_dependencies import require_active_session
    app.dependency_overrides.pop(require_active_session, None)
    app.dependency_overrides[get_db] = _override_db

    try:
        client = TestClient(app)

        # 1. Feature Gate Disabled -> Ditolak 503 Service Unavailable
        monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "0")
        res_gate = client.post(
            f"/api/receiving/operations/{op_id}/reconcile",
            cookies={auth_config.cookie_name: sess_rs101.session_id},
            headers={auth_config.custom_header_name: auth_config.custom_header_value, "Origin": "http://testserver"},
        )
        assert res_gate.status_code == 503

        # Aktifkan feature gate
        monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
        client.cookies.clear()

        # 2. Tanpa Sesi -> Ditolak 401 Unauthorized
        res_unauth = client.post(
            f"/api/receiving/operations/{op_id}/reconcile",
            headers={auth_config.custom_header_name: auth_config.custom_header_value, "Origin": "http://testserver"},
        )
        assert res_unauth.status_code == 401
        client.cookies.clear()

        # 3. Rekonsiliasi Lintas Rumah Sakit (Tenant Isolation) -> Ditolak 403
        res_cross = client.post(
            f"/api/receiving/operations/{op_id}/reconcile",
            cookies={auth_config.cookie_name: sess_rs202.session_id},
            headers={auth_config.custom_header_name: auth_config.custom_header_value, "Origin": "http://testserver"},
        )
        assert res_cross.status_code == 403
        assert "101" in res_cross.text and "202" in res_cross.text and "akses ditolak" in res_cross.text.lower()

        # 4. Mismatch Installation UID -> Ditolak 403
        res_inst_mismatch = client.post(
            "/api/receiving/operations/OP_BAD_INST_20/reconcile",
            cookies={auth_config.cookie_name: sess_rs101.session_id},
            headers={auth_config.custom_header_name: auth_config.custom_header_value, "Origin": "http://testserver"},
        )
        assert res_inst_mismatch.status_code == 403
        assert "INST_OTHER_FOREIGN" in res_inst_mismatch.text

        # 5. Operasi tidak ditemukan -> 404
        res_404 = client.post(
            "/api/receiving/operations/NON_EXISTENT_OP/reconcile",
            cookies={auth_config.cookie_name: sess_rs101.session_id},
            headers={auth_config.custom_header_name: auth_config.custom_header_value, "Origin": "http://testserver"},
        )
        assert res_404.status_code == 404

        # 6. Rekonsiliasi Sukses dari Pemilik yang Sah (RS 101)
        class MockReconcileCloudAdapter:
            async def get_package_claim_status(self, token, paket_id, hospital_id=None):
                return {
                    "package_id": 220,
                    "status_paket": "claimed",
                    "package_status": "claimed",
                    "hospital_id": 101,
                    "claimed_by_operation_id": op_id,
                    "claimed_by_installation_uid": host_inst_uid,
                    "manifest_hash": "hash_test_20",
                }

        set_custom_cloud_adapter(MockReconcileCloudAdapter())
        from app.routers.receiving_router import get_package_receiving_service
        from app.services.package_receiving_service import PackageReceivingService
        from app.services.rfid_service import RFIDServiceManager

        app.dependency_overrides[get_package_receiving_service] = lambda: PackageReceivingService(
            db_session_factory=env["session_factory"],
            cloud_adapter=MockReconcileCloudAdapter(),
            session_store=store,
            rfid_service_manager=RFIDServiceManager(),
            installation_uid_provider=PersistentFileInstallationUIDProvider(),
        )
        res_ok = client.post(
            f"/api/receiving/operations/{op_id}/reconcile",
            cookies={auth_config.cookie_name: sess_rs101.session_id},
            headers={auth_config.custom_header_name: auth_config.custom_header_value, "Origin": "http://testserver"},
        )
        assert res_ok.status_code == 200
        data = res_ok.json()
        assert data.get("status") == "reconciled"
        assert data.get("lifecycle_phase") == "claim_verified"

        # Verifikasi record database telah terefleksi ke claim_verified
        with env["session_factory"]() as db:
            updated_row = db.query(PaketReceivingOperation).filter_by(operation_id=op_id).first()
            assert updated_row.lifecycle_phase == "claim_verified"
            assert updated_row.sync_state == "pending_claim"

    finally:
        set_custom_cloud_adapter(None)
        app.dependency_overrides.clear()


# =============================================================================
# SKENARIO 21: Crash Recovery Claimed Validation Strict (R2.10.1 P0)
# =============================================================================
@pytest.mark.anyio
async def test_scen21_crash_recovery_claimed_validation_strict(isolated_db_env):
    """
    Menguji validasi ketat pada cabang status cloud 'claimed':
    Wajib memeriksa package_id, hospital_id, claimed_by_operation_id, claimed_by_installation_uid, manifest_hash.
    Jika hospital_id atau package_id tidak cocok -> ditolak (conflict).
    """
    env = isolated_db_env
    inst_uid = "INST_TEST_REC21"
    store = SessionStore(default_session_duration_seconds=28800)
    rfid_mgr = RFIDServiceManager()

    # Siapkan record operasi held
    op_id = "OP_REC_21"
    with env["session_factory"]() as db:
        rec = PaketReceivingOperation(
            operation_id=op_id,
            paket_id=221,
            hospital_id=101,
            installation_uid=inst_uid,
            verification_code="VC_21",
            manifest_hash="hash_21",
            lifecycle_phase="claim_uncertain",
            sync_state="claim_held_reconciliation",
        )
        db.add(rec)
        db.commit()

    # 1. Cloud status claimed tetapi hospital_id mismatch (202 vs 101) -> CONFLICT
    class MockAdapterHospMismatch:
        async def get_package_claim_status(self, token, paket_id, hospital_id=None):
            return {
                "package_id": 221,
                "status_paket": "claimed",
                "hospital_id": 202,  # MISMATCH
                "claimed_by_operation_id": op_id,
                "claimed_by_installation_uid": inst_uid,
                "manifest_hash": "hash_21",
            }

    coord = PackageReceivingService(
        db_session_factory=env["session_factory"],
        cloud_adapter=MockAdapterHospMismatch(),
        session_store=store,
        rfid_service_manager=rfid_mgr,
        installation_uid_provider=DummyUIDProvider(inst_uid),
    )
    res_mismatch = await coord.reconcile_held_operation(op_id, "sess_x", "tok_x", 101)
    assert res_mismatch["status"] == "conflict"
    assert res_mismatch["lifecycle_phase"] == "claim_rejected"

    # 2. Cloud status claimed dengan seluruh 5 identitas cocok -> RECONCILED (claim_verified)
    with env["session_factory"]() as db:
        row = db.query(PaketReceivingOperation).filter_by(operation_id=op_id).first()
        row.lifecycle_phase = "claim_held_reconciliation"
        row.sync_state = "claim_held_reconciliation"
        db.commit()

    class MockAdapterClaimedOk:
        async def get_package_claim_status(self, token, paket_id, hospital_id=None):
            return {
                "package_id": 221,
                "status_paket": "claimed",
                "hospital_id": 101,
                "claimed_by_operation_id": op_id,
                "claimed_by_installation_uid": inst_uid,
                "manifest_hash": "hash_21",
            }

    coord_ok = PackageReceivingService(
        db_session_factory=env["session_factory"],
        cloud_adapter=MockAdapterClaimedOk(),
        session_store=store,
        rfid_service_manager=rfid_mgr,
        installation_uid_provider=DummyUIDProvider(inst_uid),
    )
    res_ok = await coord_ok.reconcile_held_operation(op_id, "sess_x", "tok_x", 101)
    assert res_ok["status"] == "reconciled"
    assert res_ok["lifecycle_phase"] == "claim_verified"


# =============================================================================
# SKENARIO 22: Crash Recovery Confirmed Local Transaction Integrity (R2.10.1 P0)
# =============================================================================
@pytest.mark.anyio
async def test_scen22_crash_recovery_confirmed_local_transaction_integrity(isolated_db_env):
    """
    Menguji verifikasi ketat transaksi lokal pada pemulihan cabang 'confirmed':
    - Jika transaksi_id tidak ada di DB lokal -> held_reconciliation (TIDAK confirmed).
    - Jika transaksi_id terikat pada paket berbeda -> conflict (TIDAK confirmed).
    - Jika transaksi_id valid dan terikat pada paket ini -> confirmed.
    """
    env = isolated_db_env
    inst_uid = "INST_TEST_REC22"
    store = SessionStore(default_session_duration_seconds=28800)
    rfid_mgr = RFIDServiceManager()

    class MockAdapterConfirmed:
        async def get_package_claim_status(self, token, paket_id, hospital_id=None):
            return {
                "package_id": 222,
                "status_paket": "confirmed",
                "hospital_id": 101,
                "confirmed_by_operation_id": "OP_REC_22",
                "confirmed_by_installation_uid": inst_uid,
                "manifest_hash": "hash_22",
            }

    # Kasus A: Transaksi lokal ID tidak ditemukan
    with env["session_factory"]() as db:
        rec_missing_trx = PaketReceivingOperation(
            operation_id="OP_REC_22",
            paket_id=222,
            hospital_id=101,
            installation_uid=inst_uid,
            verification_code="VC_22",
            manifest_hash="hash_22",
            transaksi_id=999999,  # ID tidak ada di tabel transaksi_linen
            lifecycle_phase="local_committed",
            sync_state="pending_confirm",
        )
        db.add(rec_missing_trx)
        db.commit()

    coord = PackageReceivingService(
        db_session_factory=env["session_factory"],
        cloud_adapter=MockAdapterConfirmed(),
        session_store=store,
        rfid_service_manager=rfid_mgr,
        installation_uid_provider=DummyUIDProvider(inst_uid),
    )
    res_a = await coord.reconcile_held_operation("OP_REC_22", "sess_x", "tok_x", 101)
    assert res_a["status"] == "held_reconciliation"
    assert res_a["lifecycle_phase"] == "held_reconciliation"

    # Kasus B: Transaksi lokal ada tetapi terikat pada paket berbeda (Paket: 888, bukan 222)
    with env["session_factory"]() as db:
        wrong_trx = TransaksiLinen(
            id=777,
            jenis_transaksi="penerimaan",
            kode_transaksi="TRX-DIFF-PKG-888",
            total_linen=1,
            status="selesai",
            catatan="Penerimaan dari cloud | Paket: 888",  # PAKET BERBEDA
        )
        db.add(wrong_trx)
        row = db.query(PaketReceivingOperation).filter_by(operation_id="OP_REC_22").first()
        row.transaksi_id = 777
        db.commit()

    res_b = await coord.reconcile_held_operation("OP_REC_22", "sess_x", "tok_x", 101)
    assert res_b["status"] == "conflict"
    assert res_b["lifecycle_phase"] == "conflict"
    assert "tidak terikat valid pada paket 222" in res_b["detail"]

    # Kasus C: Transaksi lokal valid dan terikat pada paket 222 -> Sukses Reconciled Confirmed
    with env["session_factory"]() as db:
        correct_trx = TransaksiLinen(
            id=888,
            jenis_transaksi="penerimaan",
            kode_transaksi="TRX-CORRECT-PKG-222",
            total_linen=1,
            status="selesai",
            catatan="Penerimaan dari cloud | Paket: 222",  # PAKET COCOK
        )
        db.add(correct_trx)
        row = db.query(PaketReceivingOperation).filter_by(operation_id="OP_REC_22").first()
        row.transaksi_id = 888
        row.lifecycle_phase = "local_committed"
        row.sync_state = "pending_confirm"
        db.commit()

    res_c = await coord.reconcile_held_operation("OP_REC_22", "sess_x", "tok_x", 101)
    assert res_c["status"] == "reconciled"
    assert res_c["lifecycle_phase"] == "confirmed"
    assert res_c["transaksi_id"] == 888

    # Kasus D: False-Positive Substring Check (Target Paket 22, Catatan "Paket: 222" tidak boleh cocok)
    with env["session_factory"]() as db:
        rec_fp = PaketReceivingOperation(
            operation_id="OP_REC_FP_22",
            paket_id=22,  # TARGET 22
            hospital_id=101,
            installation_uid=inst_uid,
            verification_code="VC_22_FP",
            manifest_hash="hash_22",
            transaksi_id=888,  # Transaksi ID 888 memiliki catatan 'Paket: 222' (bukan 22!)
            lifecycle_phase="local_committed",
            sync_state="pending_confirm",
        )
        db.add(rec_fp)
        db.commit()

    class MockAdapterFP:
        def __init__(self, op_id="OP_REC_FP_22"):
            self.op_id = op_id

        async def get_package_claim_status(self, token, paket_id, hospital_id=None):
            return {
                "package_id": 22,
                "status_paket": "confirmed",
                "hospital_id": 101,
                "confirmed_by_operation_id": self.op_id,
                "confirmed_by_installation_uid": inst_uid,
                "manifest_hash": "hash_22",
            }

    coord_fp = PackageReceivingService(
        db_session_factory=env["session_factory"],
        cloud_adapter=MockAdapterFP("OP_REC_FP_22"),
        session_store=store,
        rfid_service_manager=rfid_mgr,
        installation_uid_provider=DummyUIDProvider(inst_uid),
    )
    res_d = await coord_fp.reconcile_held_operation("OP_REC_FP_22", "sess_x", "tok_x", 101)
    assert res_d["status"] == "conflict"
    assert res_d["lifecycle_phase"] == "conflict"
    assert "tidak terikat valid pada paket 22" in res_d["detail"]

    # Kasus E: Malformed Record / Catatan Tanpa Nomor Numerik
    with env["session_factory"]() as db:
        malformed_trx = TransaksiLinen(
            id=555,
            jenis_transaksi="penerimaan",
            kode_transaksi="TRX-MALFORMED",
            total_linen=1,
            status="selesai",
            catatan="Penerimaan dari cloud tanpa id paket",
        )
        db.add(malformed_trx)
        rec_malformed = PaketReceivingOperation(
            operation_id="OP_REC_MALFORMED",
            paket_id=22,
            hospital_id=101,
            installation_uid=inst_uid,
            verification_code="VC_22_MAL",
            manifest_hash="hash_22",
            transaksi_id=555,
            lifecycle_phase="local_committed",
            sync_state="pending_confirm",
        )
        db.add(rec_malformed)
        db.commit()

    coord_malformed = PackageReceivingService(
        db_session_factory=env["session_factory"],
        cloud_adapter=MockAdapterFP("OP_REC_MALFORMED"),
        session_store=store,
        rfid_service_manager=rfid_mgr,
        installation_uid_provider=DummyUIDProvider(inst_uid),
    )
    res_e = await coord_malformed.reconcile_held_operation("OP_REC_MALFORMED", "sess_x", "tok_x", 101)
    assert res_e["status"] == "conflict"
    assert res_e["lifecycle_phase"] == "conflict"
    assert "tidak terikat valid pada paket 22" in res_e["detail"]


