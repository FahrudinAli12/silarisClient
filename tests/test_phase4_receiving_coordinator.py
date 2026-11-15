from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Optional
import unicodedata
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.detail_transaksi_linen import DetailTransaksiLinen
from app.models.linen import Linen
from app.models.paket_receiving_operation import PaketReceivingOperation
from app.models.riwayat_linen import RiwayatLinen
from app.models.rumah_sakit import RumahSakit
from app.models.transaksi_linen import TransaksiLinen
from app.services.circuit_breaker import OperationCircuitBreaker
from app.services.hospital_binding_service import provision_hospital_binding
from app.services.installation_service import PersistentFileInstallationUIDProvider
from app.services.manifest_service import compute_canonical_manifest_hash
from app.services.package_receiving_service import (
    ActivePackageOperationConflictError,
    AuthenticationError,
    CircuitBreakerActiveError,
    CommitAuthorityError,
    ConfirmPermitError,
    HospitalMismatchError,
    ManifestCompletenessError,
    PackageReceivingService,
    StateTransitionError,
)
from app.services.recovery_service import execute_startup_recovery
from app.services.rfid_service import RFIDScanSession, RFIDServiceManager, SingleRFIDReader
from app.services.session_store import SessionData, SessionStore


# =============================================================================
# MOCK CLOUD ADAPTER UNTUK PENGUJIAN KOORDINATOR
# =============================================================================

class MockCloudClaimConfirmAdapter:
    def __init__(self) -> None:
        self.claim_mode: str = "success"  # "success", "timeout", "conflict", "mismatch", "malformed"
        self.confirm_mode: str = "success"  # "success", "timeout", "bad_identity", "server_error"
        self.last_claim_payload: Optional[dict[str, Any]] = None
        self.last_confirm_payload: Optional[dict[str, Any]] = None
        self.claim_call_count: int = 0
        self.confirm_call_count: int = 0

    async def claim_package(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.claim_call_count += 1
        self.last_claim_payload = payload

        if self.claim_mode == "timeout":
            raise asyncio.TimeoutError("Connection to Supabase claim RPC timed out after 10s")
        elif self.claim_mode == "conflict":
            return {
                "paket_id": payload["paket_id"],
                "operation_id": "op-competing-999",
                "installation_uid": "inst-foreign",
                "status_paket": "claimed",
                "manifest_hash": payload["expected_manifest_hash"],
            }
        elif self.claim_mode == "mismatch":
            return {
                "paket_id": payload["paket_id"],
                "operation_id": payload["operation_id"],
                "installation_uid": payload["installation_uid"],
                "status_paket": "claimed",
                "manifest_hash": "different_hash_from_cloud_1234567890",
            }
        elif self.claim_mode == "malformed":
            return {"error": "Invalid payload received"}
        else:
            return {
                "paket_id": payload["paket_id"],
                "operation_id": payload["operation_id"],
                "installation_uid": payload["installation_uid"],
                "status_paket": "claimed",
                "manifest_hash": payload["expected_manifest_hash"],
            }

    async def confirm_package(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.confirm_call_count += 1
        self.last_confirm_payload = payload

        if self.confirm_mode == "timeout":
            raise asyncio.TimeoutError("Confirm RPC timeout")
        elif self.confirm_mode == "bad_identity":
            return {
                "paket_id": payload.get("paket_id"),
                "hospital_id": payload.get("hospital_id"),
                "operation_id": "op_mismatched",
                "installation_uid": payload.get("installation_uid"),
                "manifest_hash": payload.get("manifest_hash"),
                "status": "confirmed",
            }
        elif self.confirm_mode == "server_error":
            raise RuntimeError("Cloud 500 Internal Error")
        else:
            return {
                "paket_id": payload.get("paket_id"),
                "hospital_id": payload.get("hospital_id"),
                "operation_id": payload.get("operation_id"),
                "installation_uid": payload.get("installation_uid"),
                "manifest_hash": payload.get("manifest_hash"),
                "status": "confirmed",
            }


class MockInstallationUIDProvider:
    def __init__(self, uid: str = "inst_test_fixed_12345") -> None:
        self.uid = uid

    def get_installation_uid(self) -> str:
        return self.uid


# =============================================================================
# FIXTURES TERISOLASI
# =============================================================================

@pytest.fixture
def isolated_coordinator_env():
    """Fixture lingkungan database, store, manager, dan coordinator terisolasi penuh dalam direktori terkelola."""
    managed_dir = os.environ.get("CLIENTS_TEST_MANAGED_DIR")
    fd, path = tempfile.mkstemp(suffix="_phase4_coord_test.db", dir=managed_dir)
    os.close(fd)

    db_url = f"sqlite:///{path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

    # Inisialisasi binding RS 101 dan Master Ruangan resmi
    with session_factory() as db:
        provision_hospital_binding(db, hospital_id=101, nama_rs="RS Uji Coba", kode_rs="RS_TEST", alamat="Jl. Uji 1")
        from app.models.ruangan import Ruangan
        db.add(Ruangan(kode_ruangan="R-TEST-001", nama_ruangan="Ruang Linen Mawar", keterangan="Ruang Uji Coba"))
        db.commit()

    session_store = SessionStore(default_session_duration_seconds=28800)
    rfid_mgr = RFIDServiceManager(demo_mode=True)
    cloud_adapter = MockCloudClaimConfirmAdapter()
    circuit_breaker = OperationCircuitBreaker()
    inst_provider = MockInstallationUIDProvider("inst_hospital_101_dev")

    coordinator = PackageReceivingService(
        session_store=session_store,
        rfid_service_manager=rfid_mgr,
        cloud_adapter=cloud_adapter,
        circuit_breaker=circuit_breaker,
        installation_uid_provider=inst_provider,
        db_session_factory=session_factory,
    )

    yield {
        "coordinator": coordinator,
        "session_store": session_store,
        "rfid_manager": rfid_mgr,
        "cloud_adapter": cloud_adapter,
        "circuit_breaker": circuit_breaker,
        "inst_provider": inst_provider,
        "session_factory": session_factory,
        "engine": engine,
    }

    engine.dispose()
    try:
        if os.path.exists(path):
            os.unlink(path)
    except Exception:
        pass


def make_valid_session(store: SessionStore, hospital_id: int = 101, email: str = "operator@rs101.id") -> SessionData:
    now = time.time()
    s_id = f"sess_{uuid.uuid4().hex}"
    session = SessionData(
        session_id=s_id,
        user_id="usr-test-1",
        email=email,
        hospital_id=hospital_id,
        hospital_name="RS Uji Coba",
        hospital_code="RS_TEST",
        role="operator",
        access_token=f"jwt_{uuid.uuid4().hex}",
        refresh_token="ref_123",
        token_expires_at=now + 3600,
        session_expires_at=now + 28800,
        created_at=now,
        last_activity_at=now,
    )
    store._sessions[s_id] = session
    return session


# =============================================================================
# 20 SKENARIO PENGUJIAN WAJIB KOORDINATOR (PHASE 4B.2)
# =============================================================================

@pytest.mark.anyio
async def test_scen01_normal_reception_full_lifecycle(isolated_coordinator_env):
    """SKENARIO 1: Penerimaan normal 19 langkah berhasil hingga status confirmed."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])

    manifest = [
        {"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei Putih"},
        {"epc": "E28000000000000000000002", "kategori": "Linen", "nama_linen": "Sarung Bantal"},
    ]

    scan_session = RFIDScanSession(operation_id="op-norm-1", paket_id=201)
    scan_session.add_epc("E28000000000000000000001")
    scan_session.add_epc("E28000000000000000000002")

    res = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=201,
        verification_code="VC-201",
        manifest_items=manifest,
        scan_session=scan_session,
        operation_id="op-norm-1",
    )

    assert res["status"] == "ok"
    assert res["lifecycle_phase"] == "confirmed"
    assert res["sync_state"] == "synced"
    assert res["transaksi_id"] is not None

    # P0-01 Assertion: Verifikasi keenam field kanonikal Confirm diperiksa
    last_confirm = env["cloud_adapter"].last_confirm_payload
    assert last_confirm is not None
    assert last_confirm["paket_id"] == 201
    assert last_confirm["hospital_id"] == 101
    assert last_confirm["operation_id"] == "op-norm-1"
    assert last_confirm["installation_uid"] == "inst_hospital_101_dev"
    assert "manifest_hash" in last_confirm and len(last_confirm["manifest_hash"]) == 64
    assert last_confirm["verification_code"] == "VC-201"

    # Verifikasi SQLite atomik: 4 entitas bisnis tersimpan
    with env["session_factory"]() as db:
        trx = db.query(TransaksiLinen).filter_by(id=res["transaksi_id"]).first()
        assert trx is not None
        assert trx.total_linen == 2

        details = db.query(DetailTransaksiLinen).filter_by(transaksi_id=trx.id).all()
        assert len(details) == 2

        riwayats = db.query(RiwayatLinen).filter_by(transaksi_id=trx.id).all()
        assert len(riwayats) == 2

        linens = db.query(Linen).all()
        assert len(linens) == 2
        for l in linens:
            assert l.status == "tersedia"


@pytest.mark.anyio
async def test_scen02_missing_epc_rejected(isolated_coordinator_env):
    """SKENARIO 2: EPC kurang dari manifest fisik ditolak sebelum Claim."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])

    manifest = [
        {"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei A"},
        {"epc": "E28000000000000000000002", "kategori": "Linen", "nama_linen": "Sprei B"},
    ]

    scan_session = RFIDScanSession(operation_id="op-missing-1", paket_id=202)
    scan_session.add_epc("E28000000000000000000001")  # Hanya 1 yang discan

    with pytest.raises(ManifestCompletenessError) as exc_info:
        await coord.process_package_receiving(
            session_id=session.session_id,
            paket_id=202,
            verification_code="VC-202",
            manifest_items=manifest,
            scan_session=scan_session,
            operation_id="op-missing-1",
        )

    assert "Missing" in str(exc_info.value)
    assert env["cloud_adapter"].claim_call_count == 0


@pytest.mark.anyio
async def test_scen03_foreign_epc_rejected(isolated_coordinator_env):
    """SKENARIO 3: Tag EPC asing terdeteksi ditolak."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])

    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]

    scan_session = RFIDScanSession(operation_id="op-foreign-1", paket_id=203)
    scan_session.add_epc("E28000000000000000000001")
    scan_session.add_epc("E28099999999999999999999")  # EPC asing

    with pytest.raises(ManifestCompletenessError) as exc_info:
        await coord.process_package_receiving(
            session_id=session.session_id,
            paket_id=203,
            verification_code="VC-203",
            manifest_items=manifest,
            scan_session=scan_session,
            operation_id="op-foreign-1",
        )

    assert "Unexpected" in str(exc_info.value)


@pytest.mark.anyio
async def test_scen04_duplicate_epc_in_manifest_rejected(isolated_coordinator_env):
    """SKENARIO 4: Duplikasi EPC dalam manifest ditolak."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])

    manifest_dup = [
        {"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei A"},
        {"epc": "3000E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei Dup"},
    ]

    scan_session = RFIDScanSession(operation_id="op-dup-1", paket_id=204)
    scan_session.add_epc("E28000000000000000000001")

    with pytest.raises(ValueError) as exc:
        await coord.process_package_receiving(
            session_id=session.session_id,
            paket_id=204,
            verification_code="VC-204",
            manifest_items=manifest_dup,
            scan_session=scan_session,
            operation_id="op-dup-1",
        )

    assert "DUPLICATE_MANIFEST_EPC" in str(exc.value)


@pytest.mark.anyio
async def test_scen05_manifest_mismatch_distinguished(isolated_coordinator_env):
    """SKENARIO 5: Manifest mismatch dari cloud dibedakan dan mempertahankan claim_uncertain."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])
    env["cloud_adapter"].claim_mode = "mismatch"

    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]
    scan_session = RFIDScanSession(operation_id="op-mismatch-1", paket_id=205)
    scan_session.add_epc("E28000000000000000000001")

    res = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=205,
        verification_code="VC-205",
        manifest_items=manifest,
        scan_session=scan_session,
        operation_id="op-mismatch-1",
    )

    assert res["status"] == "mismatch"
    assert res["lifecycle_phase"] == "claim_uncertain"

    with env["session_factory"]() as db:
        row = db.query(PaketReceivingOperation).filter_by(operation_id="op-mismatch-1").first()
        assert row.lifecycle_phase == "claim_uncertain"
        assert row.sync_state == "claim_held_reconciliation"


@pytest.mark.anyio
async def test_scen06_claim_timeout_yields_claim_uncertain(isolated_coordinator_env):
    """SKENARIO 6: Timeout pada RPC Claim mempertahankan claim_uncertain secara durable."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])
    env["cloud_adapter"].claim_mode = "timeout"

    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]
    scan_session = RFIDScanSession(operation_id="op-timeout-1", paket_id=206)
    scan_session.add_epc("E28000000000000000000001")

    res = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=206,
        verification_code="VC-206",
        manifest_items=manifest,
        scan_session=scan_session,
        operation_id="op-timeout-1",
    )

    assert res["status"] == "uncertain"
    assert res["lifecycle_phase"] == "claim_uncertain"

    with env["session_factory"]() as db:
        row = db.query(PaketReceivingOperation).filter_by(operation_id="op-timeout-1").first()
        assert row.lifecycle_phase == "claim_uncertain"
        assert "CLAIM_TIMEOUT" in row.failure_reason


@pytest.mark.anyio
async def test_scen07_replay_claim_uncertain_recovers_to_confirmed(isolated_coordinator_env):
    """SKENARIO 7: Replay dari status claim_uncertain dengan operation_id sama berhasil memulihkan alur."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])

    # 1. Simulasikan state awal claim_uncertain
    with env["session_factory"]() as db:
        db.add(
            PaketReceivingOperation(
                operation_id="op-replay-1",
                paket_id=207,
                hospital_id=101,
                installation_uid="inst_hospital_101_dev",
                verification_code="VC-207",
                manifest_hash="dummy_hash",
                scan_count=1,
                lifecycle_phase="claim_uncertain",
                sync_state="claim_held_reconciliation",
            )
        )
        db.commit()

    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]
    scan_session = RFIDScanSession(operation_id="op-replay-1", paket_id=207)
    scan_session.add_epc("E28000000000000000000001")

    env["cloud_adapter"].claim_mode = "success"
    res = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=207,
        verification_code="VC-207",
        manifest_items=manifest,
        scan_session=scan_session,
        operation_id="op-replay-1",
    )

    assert res["status"] == "ok"
    assert res["lifecycle_phase"] == "confirmed"


@pytest.mark.anyio
async def test_scen08_crash_after_intent_persisted(isolated_coordinator_env):
    """SKENARIO 8: Crash setelah intent tersimpan dipulihkan ke claim_held_reconciliation."""
    env = isolated_coordinator_env
    with env["session_factory"]() as db:
        db.add(
            PaketReceivingOperation(
                operation_id="op-crash-intent",
                paket_id=208,
                hospital_id=101,
                installation_uid="inst_hospital_101_dev",
                verification_code="VC-208",
                manifest_hash="hash",
                scan_count=1,
                lifecycle_phase="claiming",
                sync_state="pending_claim",
            )
        )
        db.commit()

        # Jalankan startup recovery
        execute_startup_recovery(db)

        row = db.query(PaketReceivingOperation).filter_by(operation_id="op-crash-intent").first()
        assert row.lifecycle_phase == "claim_held_reconciliation"
        assert row.sync_state == "claim_held_reconciliation"

    # Rekonsiliasi: status tertahan dilaporkan sebagai held_reconciliation
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])
    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]
    scan_sess = RFIDScanSession(operation_id="op-crash-intent", paket_id=208)
    scan_sess.add_epc("E28000000000000000000001")

    res = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=208,
        verification_code="VC-208",
        manifest_items=manifest,
        scan_session=scan_sess,
        operation_id="op-crash-intent",
    )
    assert res["status"] == "held_reconciliation"
    assert res["lifecycle_phase"] == "claim_held_reconciliation"

    # Operasi baru dengan operation_id berbeda ditolak dari paket tertahan ini
    scan_competing = RFIDScanSession(operation_id="op-competing-held", paket_id=208)
    scan_competing.add_epc("E28000000000000000000001")
    with pytest.raises(ActivePackageOperationConflictError):
        await coord.process_package_receiving(
            session_id=session.session_id,
            paket_id=208,
            verification_code="VC-208",
            manifest_items=manifest,
            scan_session=scan_competing,
            operation_id="op-competing-held",
        )


@pytest.mark.anyio
async def test_scen09_crash_after_claim_verified(isolated_coordinator_env):
    """SKENARIO 9: Crash saat claim_verified sebelum commit dipulihkan ke claim_held_reconciliation."""
    env = isolated_coordinator_env
    with env["session_factory"]() as db:
        db.add(
            PaketReceivingOperation(
                operation_id="op-crash-verified",
                paket_id=209,
                hospital_id=101,
                installation_uid="inst_hospital_101_dev",
                verification_code="VC-209",
                manifest_hash="hash",
                scan_count=1,
                lifecycle_phase="claim_verified",
                sync_state="pending_claim",
            )
        )
        db.commit()

        execute_startup_recovery(db)

        row = db.query(PaketReceivingOperation).filter_by(operation_id="op-crash-verified").first()
        assert row.lifecycle_phase == "claim_held_reconciliation"
        assert row.sync_state == "claim_held_reconciliation"


@pytest.mark.anyio
async def test_scen10_crash_after_local_committed_preserves_state(isolated_coordinator_env):
    """SKENARIO 10: Crash setelah local_committed dipulihkan ke awaiting_reauth dengan operation_id sama."""
    env = isolated_coordinator_env
    with env["session_factory"]() as db:
        db.add(
            PaketReceivingOperation(
                operation_id="op-crash-local-commit",
                paket_id=210,
                hospital_id=101,
                installation_uid="inst_hospital_101_dev",
                verification_code="VC-210",
                manifest_hash="hash",
                scan_count=1,
                lifecycle_phase="local_committed",
                sync_state="pending_confirm",
            )
        )
        db.commit()

        execute_startup_recovery(db)

        row = db.query(PaketReceivingOperation).filter_by(operation_id="op-crash-local-commit").first()
        assert row.lifecycle_phase == "awaiting_reauth"
        assert row.sync_state == "awaiting_reauth"

    # Rekonsiliasi pasca-restart: koordinator melanjutkan awaiting_reauth ke confirmed
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])
    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]
    scan_sess = RFIDScanSession(operation_id="op-crash-local-commit", paket_id=210)
    scan_sess.add_epc("E28000000000000000000001")

    res = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=210,
        verification_code="VC-210",
        manifest_items=manifest,
        scan_session=scan_sess,
        operation_id="op-crash-local-commit",
    )
    assert res["status"] == "ok"
    assert res["lifecycle_phase"] == "confirmed"

    with env["session_factory"]() as db:
        final_row = db.query(PaketReceivingOperation).filter_by(operation_id="op-crash-local-commit").first()
        assert final_row.lifecycle_phase == "confirmed"
        assert final_row.sync_state == "synced"


@pytest.mark.anyio
async def test_scen11_confirm_timeout_maintains_local_committed(isolated_coordinator_env):
    """SKENARIO 11: Timeout Confirm RPC mempertahankan local_committed di SQLite."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])
    env["cloud_adapter"].confirm_mode = "timeout"

    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]
    scan_session = RFIDScanSession(operation_id="op-conf-timeout", paket_id=211)
    scan_session.add_epc("E28000000000000000000001")

    res = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=211,
        verification_code="VC-211",
        manifest_items=manifest,
        scan_session=scan_session,
        operation_id="op-conf-timeout",
    )

    assert res["status"] == "confirm_pending"
    assert res["lifecycle_phase"] == "local_committed"
    assert res["transaksi_id"] is not None

    with env["session_factory"]() as db:
        row = db.query(PaketReceivingOperation).filter_by(operation_id="op-conf-timeout").first()
        assert row.lifecycle_phase == "local_committed"
        assert row.sync_state == "pending_confirm"


@pytest.mark.anyio
async def test_scen12_confirm_bad_identity_rejection(isolated_coordinator_env):
    """SKENARIO 12: Confirm response dengan identitas tidak cocok tidak di-confirm."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])
    env["cloud_adapter"].confirm_mode = "bad_identity"

    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]
    scan_session = RFIDScanSession(operation_id="op-conf-bad", paket_id=212)
    scan_session.add_epc("E28000000000000000000001")

    res = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=212,
        verification_code="VC-212",
        manifest_items=manifest,
        scan_session=scan_session,
        operation_id="op-conf-bad",
    )

    assert res["status"] == "confirm_unverified"
    assert res["lifecycle_phase"] == "local_committed"


@pytest.mark.anyio
async def test_scen13_logout_before_permit_consumed_blocks_dispatch(isolated_coordinator_env):
    """SKENARIO 13: Logout sebelum permit dikonsumsi menolak dispatch Confirm."""
    env = isolated_coordinator_env
    store: SessionStore = env["session_store"]
    session = make_valid_session(store)

    # 1. Dapatkan permit
    ok, token, p_id, _ = await store.acquire_confirm_dispatch_permit(
        session_id=session.session_id,
        operation_id="op-logout-test",
        paket_id=213,
        hospital_id=101,
    )
    assert ok is True

    # 2. Operator logout sebelum permit dikonsumsi
    await store.delete_session(session.session_id)

    # 3. Konsumsi permit ditolak
    c_ok, _, err = await store.consume_confirm_dispatch_permit(session.session_id, p_id)
    assert c_ok is False
    assert "revoked" in err or "missing" in err


@pytest.mark.anyio
async def test_scen14_two_operators_exclusive_commit_authority(isolated_coordinator_env):
    """SKENARIO 14: Dua operator tidak dapat commit serentak pada sesi yang sama."""
    env = isolated_coordinator_env
    store: SessionStore = env["session_store"]
    session = make_valid_session(store)

    auth1 = await store.acquire_commit_authority(session.session_id, "op-operator-1")
    assert auth1 is not None

    # Operator 2 pada sesi yang sama mencoba commit operasi lain
    auth2 = await store.acquire_commit_authority(session.session_id, "op-operator-2")
    assert auth2 is None

    # Setelah operator 1 melepas, operator 2 berhasil
    await store.release_commit_authority(auth1, "op-operator-1")
    auth2_retry = await store.acquire_commit_authority(session.session_id, "op-operator-2")
    assert auth2_retry is not None


@pytest.mark.anyio
async def test_scen15_two_installations_different_uids(isolated_coordinator_env):
    """SKENARIO 15: Dua instalasi memiliki installation_uid persisten yang berbeda."""
    provider1 = MockInstallationUIDProvider("inst_node_jakarta_01")
    provider2 = MockInstallationUIDProvider("inst_node_surabaya_02")

    assert provider1.get_installation_uid() != provider2.get_installation_uid()


@pytest.mark.anyio
async def test_scen16_two_hospitals_boundary_enforcement(isolated_coordinator_env):
    """SKENARIO 16: Operator RS 102 ditolak mengakses binding RS 101."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session_rs102 = make_valid_session(env["session_store"], hospital_id=102, email="operator@rs102.id")

    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]
    scan_session = RFIDScanSession(operation_id="op-hosp-diff", paket_id=216)
    scan_session.add_epc("E28000000000000000000001")

    with pytest.raises(HospitalMismatchError):
        await coord.process_package_receiving(
            session_id=session_rs102.session_id,
            paket_id=216,
            verification_code="VC-216",
            manifest_items=manifest,
            scan_session=scan_session,
            operation_id="op-hosp-diff",
        )


@pytest.mark.anyio
async def test_scen17_reader_disconnect_automatically_invalidates_session(isolated_coordinator_env):
    """SKENARIO 17: Event reader disconnect otomatis menginvalidasi RFIDScanSession aktif."""
    env = isolated_coordinator_env
    rfid_mgr: RFIDServiceManager = env["rfid_manager"]

    scan_session = RFIDScanSession(operation_id="op-reader-disc", paket_id=217, reader_name="RFID_LINEN_MASUK", port="COM3")
    rfid_mgr.register_scan_session(scan_session)
    assert scan_session.is_invalidated is False

    # Simulasikan reader masuk terputus (event callback otomatis dari reader)
    fake_reader = SingleRFIDReader(name="RFID_LINEN_MASUK", port="COM3", transaction_type="LINEN_MASUK")
    rfid_mgr._handle_reader_status(connected=False, reader=fake_reader)

    # Sesi harus terinvalidasi otomatis!
    assert scan_session.is_invalidated is True

    # Sesi yang invalidated tidak boleh digunakan untuk commit
    with pytest.raises(RuntimeError) as exc:
        scan_session.validate_for_commit()
    assert "invalidated" in str(exc.value)


@pytest.mark.anyio
async def test_scen18_com_port_change_invalidates_active_session(isolated_coordinator_env):
    """SKENARIO 18: Penggantian COM port dinamis menginvalidasi sesi aktif pada port terkait."""
    env = isolated_coordinator_env
    rfid_mgr: RFIDServiceManager = env["rfid_manager"]

    scan_session_masuk = RFIDScanSession(operation_id="op-port-chg", paket_id=218, reader_name="RFID_LINEN_MASUK", port="COM3")
    scan_session_keluar = RFIDScanSession(operation_id="op-unrelated", paket_id=218, reader_name="RFID_LINEN_KELUAR", port="COM4")

    rfid_mgr.register_scan_session(scan_session_masuk)
    rfid_mgr.register_scan_session(scan_session_keluar)

    # Ganti port reader masuk ke COM5
    rfid_mgr.change_port(new_port="COM5", target="masuk")

    # Sesi masuk terinvalidasi
    assert scan_session_masuk.is_invalidated is True
    # Sesi keluar TIDAK ikut terinvalidasi
    assert scan_session_keluar.is_invalidated is False


@pytest.mark.anyio
async def test_scen19_circuit_breaker_disk_full_blocks_commit(isolated_coordinator_env):
    """SKENARIO 19: Circuit breaker disk penuh memblokir transaksi commit secara aman."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    cb: OperationCircuitBreaker = env["circuit_breaker"]
    session = make_valid_session(env["session_store"])

    # Kunci operasi karena disk penuh
    cb.record_failure("op-disk-full", RuntimeError("database or disk is full"), paket_id=219)

    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]
    scan_session = RFIDScanSession(operation_id="op-disk-full", paket_id=219)
    scan_session.add_epc("E28000000000000000000001")

    with pytest.raises(CircuitBreakerActiveError):
        await coord.process_package_receiving(
            session_id=session.session_id,
            paket_id=219,
            verification_code="VC-219",
            manifest_items=manifest,
            scan_session=scan_session,
            operation_id="op-disk-full",
        )


@pytest.mark.anyio
async def test_scen20_two_parallel_requests_same_package_rejected(isolated_coordinator_env):
    """SKENARIO 20: Dua request paralel untuk paket yang sama ditolak (mencegah transaksi ganda)."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])

    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]

    # Simulasikan ada operasi 'op-first' yang sedang aktif untuk paket 220
    with env["session_factory"]() as db:
        db.add(
            PaketReceivingOperation(
                operation_id="op-first",
                paket_id=220,
                hospital_id=101,
                installation_uid="inst_hospital_101_dev",
                verification_code="VC-220",
                manifest_hash="hash",
                scan_count=1,
                lifecycle_phase="claiming",
                sync_state="pending_claim",
            )
        )
        db.commit()

    scan_session = RFIDScanSession(operation_id="op-second-competing", paket_id=220)
    scan_session.add_epc("E28000000000000000000001")

    # Operasi kedua untuk paket yang sama harus ditolak secara tegas
    with pytest.raises(ActivePackageOperationConflictError) as exc_info:
        await coord.process_package_receiving(
            session_id=session.session_id,
            paket_id=220,
            verification_code="VC-220",
            manifest_items=manifest,
            scan_session=scan_session,
            operation_id="op-second-competing",
        )

    assert "sedang diproses oleh operasi aktif lain" in str(exc_info.value)


def test_scen21_unicode_nfc_normalization_visual_equivalence():
    """SKENARIO 21: Normalisasi Unicode NFC menghasilkan hash identik untuk karakter ekivalen."""
    # Karakter e dengan aksen tirus:
    # 1. Precomposed: '\u00e9'
    # 2. Decomposed: 'e\u0301' (huruf e + combining acute accent)
    str_precomposed = "\u00e9"
    str_decomposed = "e\u0301"

    manifest_a = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": f"Sprei Caf{str_precomposed}"}]
    manifest_b = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": f"Sprei Caf{str_decomposed}"}]

    hash_a = compute_canonical_manifest_hash(manifest_a)
    hash_b = compute_canonical_manifest_hash(manifest_b)

    assert hash_a == hash_b


@pytest.mark.anyio
async def test_scen22_strict_confirm_all_six_fields_contract_and_rejection(isolated_coordinator_env):
    """SKENARIO 22 (P0-01): Validasi ketat bahwa keenam field respons Confirm diverifikasi eksak."""
    env = isolated_coordinator_env
    coord: PackageReceivingService = env["coordinator"]
    session = make_valid_session(env["session_store"])
    manifest = [{"epc": "E28000000000000000000001", "kategori": "Linen", "nama_linen": "Sprei"}]

    class ConfigurableConfirmAdapter(MockCloudClaimConfirmAdapter):
        def __init__(self):
            super().__init__()
            self.override_confirm_res = None

        async def confirm_package(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
            self.confirm_call_count += 1
            self.last_confirm_payload = payload
            if self.override_confirm_res is not None:
                return self.override_confirm_res(payload)
            return await super().confirm_package(token, payload)

    cfg_adapter = ConfigurableConfirmAdapter()
    coord.cloud_adapter = cfg_adapter

    # 1. hospital_id NULL -> ditolak (confirm_unverified)
    cfg_adapter.override_confirm_res = lambda p: {
        "paket_id": p["paket_id"],
        "hospital_id": None,
        "operation_id": p["operation_id"],
        "installation_uid": p["installation_uid"],
        "manifest_hash": p["manifest_hash"],
        "status": "confirmed",
    }
    scan_s = RFIDScanSession(operation_id="op-test-c1", paket_id=221)
    scan_s.add_epc("E28000000000000000000001")
    res1 = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=221,
        verification_code="VC-221",
        manifest_items=manifest,
        scan_session=scan_s,
        operation_id="op-test-c1",
    )
    assert res1["status"] == "confirm_unverified"
    assert res1["lifecycle_phase"] == "local_committed"

    # 2. installation_uid mismatch -> ditolak (confirm_unverified)
    cfg_adapter.override_confirm_res = lambda p: {
        "paket_id": p["paket_id"],
        "hospital_id": p["hospital_id"],
        "operation_id": p["operation_id"],
        "installation_uid": "inst_foreign_mismatched",
        "manifest_hash": p["manifest_hash"],
        "status": "confirmed",
    }
    scan_s = RFIDScanSession(operation_id="op-test-c2", paket_id=222)
    scan_s.add_epc("E28000000000000000000001")
    res2 = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=222,
        verification_code="VC-222",
        manifest_items=manifest,
        scan_session=scan_s,
        operation_id="op-test-c2",
    )
    assert res2["status"] == "confirm_unverified"
    assert res2["lifecycle_phase"] == "local_committed"

    # 3. manifest_hash mismatch -> ditolak (confirm_unverified)
    cfg_adapter.override_confirm_res = lambda p: {
        "paket_id": p["paket_id"],
        "hospital_id": p["hospital_id"],
        "operation_id": p["operation_id"],
        "installation_uid": p["installation_uid"],
        "manifest_hash": "a" * 64,
        "status": "confirmed",
    }
    scan_s = RFIDScanSession(operation_id="op-test-c3", paket_id=223)
    scan_s.add_epc("E28000000000000000000001")
    res3 = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=223,
        verification_code="VC-223",
        manifest_items=manifest,
        scan_session=scan_s,
        operation_id="op-test-c3",
    )
    assert res3["status"] == "confirm_unverified"
    assert res3["lifecycle_phase"] == "local_committed"

    # 4. status non-confirmed -> ditolak (confirm_unverified)
    cfg_adapter.override_confirm_res = lambda p: {
        "paket_id": p["paket_id"],
        "hospital_id": p["hospital_id"],
        "operation_id": p["operation_id"],
        "installation_uid": p["installation_uid"],
        "manifest_hash": p["manifest_hash"],
        "status": "rejected_unknown",
    }
    scan_s = RFIDScanSession(operation_id="op-test-c4", paket_id=224)
    scan_s.add_epc("E28000000000000000000001")
    res4 = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=224,
        verification_code="VC-224",
        manifest_items=manifest,
        scan_session=scan_s,
        operation_id="op-test-c4",
    )
    assert res4["status"] == "confirm_unverified"
    assert res4["lifecycle_phase"] == "local_committed"

    # 5. Seluruh 6 field valid dan cocok -> SUKSES confirmed
    cfg_adapter.override_confirm_res = None
    scan_s = RFIDScanSession(operation_id="op-test-c5", paket_id=225)
    scan_s.add_epc("E28000000000000000000001")
    res5 = await coord.process_package_receiving(
        session_id=session.session_id,
        paket_id=225,
        verification_code="VC-225",
        manifest_items=manifest,
        scan_session=scan_s,
        operation_id="op-test-c5",
    )
    assert res5["status"] == "ok"
    assert res5["lifecycle_phase"] == "confirmed"
    assert res5["sync_state"] == "synced"
