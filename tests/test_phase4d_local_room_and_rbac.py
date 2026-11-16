from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from typing import Any
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base, check_schema_compatibility, init_db, get_db
from app.main import app
from app.models.linen import Linen
from app.models.ruangan import Ruangan
from app.models.transaksi_linen import TransaksiLinen
from app.routers.auth_router import (
    AuthRouterConfig,
    get_default_auth_service,
    get_default_config,
    get_default_session_store,
    set_default_config,
)
from app.services.auth_dependencies import require_active_session
from app.routers.receiving_router import (
    get_default_cloud_adapter,
    get_default_rfid_manager,
    get_default_uid_provider,
    get_package_receiving_service,
)
from app.services.auth_service import AuthService
from app.services.circuit_breaker import OperationCircuitBreaker
from app.services.hospital_binding_service import provision_hospital_binding
from app.services.package_receiving_service import (
    PackageReceivingService,
    RoomValidationError,
)
from app.services.rfid_service import RFIDScanSession, RFIDServiceManager
from app.services.session_store import SessionStore


# =============================================================================
# MOCKS
# =============================================================================
class MockCloudAdapter:
    def __init__(self) -> None:
        self.claim_call_count = 0
        self.confirm_call_count = 0

    async def get_package_info(self, token: str, paket_id: int) -> dict[str, Any]:
        return {
            "paket_id": paket_id,
            "verification_code": f"VC-{paket_id}",
            "supplier": "PT Supplier Linen",
            "hospital_id": 101,
            "status": "dikirim",
            "manifest_items": [
                {"epc": "E28011900000000000000001", "kategori": "Linen", "nama_linen": "Sprei A"},
            ],
        }

    async def claim_package(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.claim_call_count += 1
        return {
            "paket_id": payload["paket_id"],
            "operation_id": payload["operation_id"],
            "installation_uid": payload["installation_uid"],
            "status_paket": "claimed",
            "manifest_hash": payload["expected_manifest_hash"],
        }

    async def confirm_package(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.confirm_call_count += 1
        return {
            "paket_id": payload["paket_id"],
            "operation_id": payload["operation_id"],
            "status": "confirmed",
        }


class MockUIDProvider:
    def get_installation_uid(self) -> str:
        return "inst_uid_rbac_test_101"


# =============================================================================
# FIXTURES
# =============================================================================
@pytest.fixture
def rbac_env():
    managed_dir = os.environ.get("CLIENTS_TEST_MANAGED_DIR")
    fd, path = tempfile.mkstemp(suffix="_rbac_test.db", dir=managed_dir)
    os.close(fd)

    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

    with session_factory() as db:
        provision_hospital_binding(db, hospital_id=101, nama_rs="RS Uji RBAC", kode_rs="RS_RBAC", alamat="Jl. RS RBAC No. 1")

    store = SessionStore(default_session_duration_seconds=28800)
    cloud = MockCloudAdapter()
    circuit = OperationCircuitBreaker()
    rfid_mgr = RFIDServiceManager()
    uid_prov = MockUIDProvider()

    coordinator = PackageReceivingService(
        db_session_factory=session_factory,
        cloud_adapter=cloud,
        session_store=store,
        installation_uid_provider=uid_prov,
        circuit_breaker=circuit,
        rfid_service_manager=rfid_mgr,
    )

    auth_service = AuthService(
        session_store=store,
        installation_provider=uid_prov,
        supabase_url="http://mock.supabase.local",
        supabase_anon_key="mock_key",
    )

    config = AuthRouterConfig(
        session_duration_seconds=28800,
        cookie_name="clients_session_id",
        custom_header_name="X-ClientS-Request",
        allowed_origins=["http://localhost:8000", "http://testserver"],
    )

    def _override_db():
        with session_factory() as s:
            yield s

    app.dependency_overrides.pop(require_active_session, None)
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_default_cloud_adapter] = lambda: cloud
    app.dependency_overrides[get_default_rfid_manager] = lambda: rfid_mgr
    app.dependency_overrides[get_default_uid_provider] = lambda: uid_prov
    app.dependency_overrides[get_package_receiving_service] = lambda: coordinator
    app.dependency_overrides[get_default_session_store] = lambda: store
    app.dependency_overrides[get_default_auth_service] = lambda: auth_service
    app.dependency_overrides[get_default_config] = lambda: config

    client = TestClient(app, base_url="http://testserver")

    yield {
        "db_path": path,
        "engine": engine,
        "session_factory": session_factory,
        "store": store,
        "cloud": cloud,
        "coordinator": coordinator,
        "auth_service": auth_service,
        "config": config,
        "client": client,
    }

    app.dependency_overrides.clear()
    engine.dispose()
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


# Helper fungsi login dan membuat sesi uji untuk role tertentu
async def create_user_session(store: SessionStore, role: str, email: str = "user@rs.id") -> str:
    sess = await store.create_session(
        user_id=f"uid_{role}",
        email=email,
        hospital_id=101,
        hospital_name="RS Uji RBAC",
        hospital_code="RS_RBAC",
        role=role,
        access_token=f"tok_{role}",
        refresh_token=f"ref_{role}",
        token_expires_in=3600,
    )
    return sess.session_id


# =============================================================================
# TEST GROUP 1: MIGRATION DAN DATABASE SAFETY (Tes 1-4)
# =============================================================================

def test_01_migration_on_empty_database():
    """1. Migration pada database kosong: ALTER TABLE berhasil, skema sesuai."""
    managed_dir = os.environ.get("CLIENTS_TEST_MANAGED_DIR")
    fd, path = tempfile.mkstemp(suffix="_mig_empty.db", dir=managed_dir)
    os.close(fd)

    try:
        # Buat tabel ruangan versi lama (tanpa is_active)
        conn = sqlite3.connect(path)
        try:
            conn.execute("""
                CREATE TABLE ruangan (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kode_ruangan VARCHAR(20) NOT NULL UNIQUE,
                    nama_ruangan VARCHAR(100) NOT NULL,
                    keterangan VARCHAR(255),
                    created_at DATETIME
                );
            """)
            conn.commit()

            # Baca dan jalankan migrasi 02_add_is_active_to_ruangan.sql
            mig_sql_path = os.path.join("database_migrations", "sqlite_local", "02_add_is_active_to_ruangan.sql")
            assert os.path.exists(mig_sql_path)
            with open(mig_sql_path, "r", encoding="utf-8") as f:
                sql_script = f.read()

            conn.executescript(sql_script)
            conn.commit()

            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(ruangan);")
            columns = {row[1]: {"type": row[2], "notnull": row[3], "dflt": row[4]} for row in cursor.fetchall()}
            assert "is_active" in columns
            assert columns["is_active"]["notnull"] == 1
            assert str(columns["is_active"]["dflt"]) in ("1", "'1'")
            assert cursor.execute("SELECT COUNT(*) FROM ruangan").fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def test_02_migration_on_database_with_existing_rooms():
    """2. Migration pada database dengan ruangan lama: data tetap utuh, kolom terbuat."""
    managed_dir = os.environ.get("CLIENTS_TEST_MANAGED_DIR")
    fd, path = tempfile.mkstemp(suffix="_mig_existing.db", dir=managed_dir)
    os.close(fd)

    try:
        conn = sqlite3.connect(path)
        try:
            conn.execute("""
                CREATE TABLE ruangan (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kode_ruangan VARCHAR(20) NOT NULL UNIQUE,
                    nama_ruangan VARCHAR(100) NOT NULL,
                    keterangan VARCHAR(255),
                    created_at DATETIME
                );
            """)
            conn.execute("INSERT INTO ruangan (kode_ruangan, nama_ruangan, keterangan) VALUES ('R-IGD', 'IGD Darurat', 'Lama 1')")
            conn.execute("INSERT INTO ruangan (kode_ruangan, nama_ruangan, keterangan) VALUES ('R-ICU', 'ICU Sentral', 'Lama 2')")
            conn.commit()

            conn.execute("ALTER TABLE ruangan ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1;")
            conn.commit()

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM ruangan")
            assert cursor.fetchone()[0] == 2
            cursor.execute("SELECT kode_ruangan, nama_ruangan, is_active FROM ruangan ORDER BY id ASC")
            rows = cursor.fetchall()
            assert rows[0][0] == "R-IGD"
            assert rows[1][0] == "R-ICU"
        finally:
            conn.close()
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def test_03_existing_rooms_automatically_active():
    """3. Ruangan lama otomatis berstatus is_active = 1 (TRUE) tanpa nilai NULL."""
    managed_dir = os.environ.get("CLIENTS_TEST_MANAGED_DIR")
    fd, path = tempfile.mkstemp(suffix="_mig_auto_active.db", dir=managed_dir)
    os.close(fd)

    try:
        conn = sqlite3.connect(path)
        try:
            conn.execute("""
                CREATE TABLE ruangan (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kode_ruangan VARCHAR(20) NOT NULL UNIQUE,
                    nama_ruangan VARCHAR(100) NOT NULL,
                    keterangan VARCHAR(255)
                );
            """)
            conn.execute("INSERT INTO ruangan (kode_ruangan, nama_ruangan) VALUES ('R-01', 'Ruang Mawar')")
            conn.execute("INSERT INTO ruangan (kode_ruangan, nama_ruangan) VALUES ('R-02', 'Ruang Melati')")
            conn.execute("ALTER TABLE ruangan ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1;")
            conn.commit()

            cursor = conn.cursor()
            cursor.execute("SELECT is_active FROM ruangan")
            statuses = [row[0] for row in cursor.fetchall()]
            assert statuses == [1, 1]

            cursor.execute("SELECT COUNT(*) FROM ruangan WHERE is_active IS NULL")
            assert cursor.fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def test_04_new_room_active_by_default(rbac_env):
    """4. Ruangan baru aktif secara default via ORM dan penolakan nilai NULL."""
    session_factory = rbac_env["session_factory"]
    with session_factory() as db:
        new_room = Ruangan(kode_ruangan="R-BARU-01", nama_ruangan="Ruang Cendrawasih")
        db.add(new_room)
        db.commit()
        db.refresh(new_room)

        assert new_room.is_active is True
        assert new_room.id is not None

    # Pengujian constraint fisik: SQLite menolak insert NULL pada is_active
    with sqlite3.connect(rbac_env["db_path"]) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO ruangan (kode_ruangan, nama_ruangan, is_active) VALUES ('R-ERR', 'Ruang Error', NULL);")
            conn.commit()


# =============================================================================
# TEST GROUP 2: FILTER STATUS DAN DAFTAR RUANGAN (Tes 5-6)
# =============================================================================

@pytest.mark.anyio
async def test_05_active_rooms_in_filter(rbac_env):
    """5. Ruangan aktif tampil di dropdown / list filter ?is_active=true."""
    client: TestClient = rbac_env["client"]
    store: SessionStore = rbac_env["store"]
    session_factory = rbac_env["session_factory"]

    # Seed 1 ruangan aktif dan 1 ruangan nonaktif
    with session_factory() as db:
        db.add(Ruangan(kode_ruangan="R-ACT", nama_ruangan="Ruang Aktif", is_active=True))
        db.add(Ruangan(kode_ruangan="R-INACT", nama_ruangan="Ruang Nonaktif", is_active=False))
        db.commit()

    sid = await create_user_session(store, role="operator")
    res = client.get("/api/master/ruangan?is_active=true", cookies={"clients_session_id": sid})
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["kode_ruangan"] == "R-ACT"
    assert data[0]["is_active"] is True


@pytest.mark.anyio
async def test_06_inactive_rooms_filter_and_unfiltered_all(rbac_env):
    """6. Ruangan nonaktif tidak tampil di list aktif, namun tampil di filter nonaktif dan query all."""
    client: TestClient = rbac_env["client"]
    store: SessionStore = rbac_env["store"]
    session_factory = rbac_env["session_factory"]

    with session_factory() as db:
        db.add(Ruangan(kode_ruangan="R-ACT", nama_ruangan="Ruang Aktif", is_active=True))
        db.add(Ruangan(kode_ruangan="R-INACT", nama_ruangan="Ruang Nonaktif", is_active=False))
        db.commit()

    sid = await create_user_session(store, role="admin")

    # Filter nonaktif saja
    res_inact = client.get("/api/master/ruangan?is_active=false", cookies={"clients_session_id": sid})
    assert res_inact.status_code == 200
    data_inact = res_inact.json()
    assert len(data_inact) == 1
    assert data_inact[0]["kode_ruangan"] == "R-INACT"

    # Query all (tanpa filter) untuk admin
    res_all = client.get("/api/master/ruangan", cookies={"clients_session_id": sid})
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert len(data_all) == 2


# =============================================================================
# TEST GROUP 3: PACKAGE RECEIVING SERVICE ROOM VALIDATION (Tes 7, 16, 17)
# =============================================================================

@pytest.mark.anyio
async def test_07_inactive_room_rejected_by_service(rbac_env):
    """7. Ruangan nonaktif ditolak oleh PackageReceivingService sebelum Claim RPC."""
    coordinator: PackageReceivingService = rbac_env["coordinator"]
    cloud: MockCloudAdapter = rbac_env["cloud"]
    store: SessionStore = rbac_env["store"]
    session_factory = rbac_env["session_factory"]

    with session_factory() as db:
        db.add(Ruangan(kode_ruangan="R-DIS", nama_ruangan="Ruang Ditutup", is_active=False))
        db.commit()

    sess = await store.create_session("u1", "op@rs.id", 101, "RS", "RS", "operator", "t1", "r1", token_expires_in=3600)
    scan_sess = RFIDScanSession("op_rec_01", paket_id=101, reader_name="scanner_01", port="COM3")
    scan_sess.add_epc("E28011900000000000000001")
    scan_sess.freeze_snapshot()

    with pytest.raises(RoomValidationError) as exc:
        await coordinator.process_package_receiving(
            session_id=sess.session_id,
            paket_id=101,
            verification_code="VC-101",
            manifest_items=[{"epc": "E28011900000000000000001", "kategori": "Linen", "nama_linen": "Sprei A"}],
            scan_session=scan_sess,
            ruangan_tujuan="Ruang Ditutup",
        )

    assert "berstatus nonaktif" in str(exc.value)
    assert cloud.claim_call_count == 0


@pytest.mark.anyio
async def test_16_invalid_or_inactive_room_does_not_trigger_rpc_claim(rbac_env, monkeypatch):
    """16. Ruangan tidak valid / nonaktif tidak memicu RPC Claim (HTTP 400)."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client: TestClient = rbac_env["client"]
    cloud: MockCloudAdapter = rbac_env["cloud"]
    store: SessionStore = rbac_env["store"]
    session_factory = rbac_env["session_factory"]

    with session_factory() as db:
        db.add(Ruangan(kode_ruangan="R-OFF", nama_ruangan="Ruang Nonaktif", is_active=False))
        db.commit()

    sid = await create_user_session(store, role="operator")
    headers = {"X-ClientS-Request": "1", "Origin": "http://testserver"}
    cookies = {"clients_session_id": sid}

    # Request dengan ruangan nonaktif
    res = client.post(
        "/api/receiving/process",
        json={"paket_id": 101, "verification_code": "VC-101", "ruangan_tujuan": "Ruang Nonaktif"},
        cookies=cookies,
        headers=headers,
    )
    assert res.status_code == 400
    assert "berstatus nonaktif" in res.json()["detail"]
    assert cloud.claim_call_count == 0

    # Request dengan ruangan yang sama sekali tidak ada
    res2 = client.post(
        "/api/receiving/process",
        json={"paket_id": 101, "verification_code": "VC-101", "ruangan_tujuan": "Ruang Fiktif"},
        cookies=cookies,
        headers=headers,
    )
    assert res2.status_code == 400
    assert "tidak terdaftar" in res2.json()["detail"]
    assert cloud.claim_call_count == 0


@pytest.mark.anyio
async def test_17_empty_database_blocks_receiving_with_correct_message(rbac_env, monkeypatch):
    """17. Database kosong memblokir penerimaan paket dengan pesan yang benar."""
    monkeypatch.setenv("ENABLE_PACKAGE_RECEIVING_API", "1")
    client: TestClient = rbac_env["client"]
    cloud: MockCloudAdapter = rbac_env["cloud"]
    store: SessionStore = rbac_env["store"]

    sid = await create_user_session(store, role="operator")
    headers = {"X-ClientS-Request": "1", "Origin": "http://testserver"}
    cookies = {"clients_session_id": sid}

    res = client.post(
        "/api/receiving/process",
        json={"paket_id": 101, "verification_code": "VC-101", "ruangan_tujuan": "Ruang Mawar"},
        cookies=cookies,
        headers=headers,
    )
    assert res.status_code == 400
    assert "Master data ruangan rumah sakit masih kosong" in res.json()["detail"]
    assert cloud.claim_call_count == 0


# =============================================================================
# TEST GROUP 4: IMPLEMENTASI RBAC (Tes 8-12)
# =============================================================================

@pytest.mark.anyio
async def test_08_operator_cannot_add_room(rbac_env):
    """8. Operator tidak dapat menambah ruangan (HTTP 403 Forbidden)."""
    client: TestClient = rbac_env["client"]
    store: SessionStore = rbac_env["store"]

    sid = await create_user_session(store, role="operator")
    res = client.post(
        "/api/master/ruangan",
        json={"kode_ruangan": "R-OP-01", "nama_ruangan": "Ruang Operator"},
        cookies={"clients_session_id": sid},
        headers={"X-ClientS-Request": "1", "Origin": "http://testserver"},
    )
    assert res.status_code == 403
    assert "tidak memiliki izin" in res.json()["detail"]


@pytest.mark.anyio
async def test_09_operator_cannot_edit_room(rbac_env):
    """9. Operator tidak dapat mengubah ruangan (HTTP 403 Forbidden)."""
    client: TestClient = rbac_env["client"]
    store: SessionStore = rbac_env["store"]
    session_factory = rbac_env["session_factory"]

    with session_factory() as db:
        room = Ruangan(kode_ruangan="R-EX", nama_ruangan="Ruang Eksis")
        db.add(room)
        db.commit()
        db.refresh(room)
        room_id = room.id

    sid = await create_user_session(store, role="operator")
    res = client.put(
        f"/api/master/ruangan/{room_id}",
        json={"nama_ruangan": "Ruang Diganti"},
        cookies={"clients_session_id": sid},
        headers={"X-ClientS-Request": "1", "Origin": "http://testserver"},
    )
    assert res.status_code == 403
    assert "tidak memiliki izin" in res.json()["detail"]


@pytest.mark.anyio
async def test_10_operator_cannot_delete_room_but_can_read(rbac_env):
    """10. Operator tidak dapat menghapus ruangan (HTTP 403), tetapi memiliki akses baca (HTTP 200)."""
    client: TestClient = rbac_env["client"]
    store: SessionStore = rbac_env["store"]
    session_factory = rbac_env["session_factory"]

    with session_factory() as db:
        room = Ruangan(kode_ruangan="R-DEL-OP", nama_ruangan="Ruang Del OP")
        db.add(room)
        db.commit()
        db.refresh(room)
        room_id = room.id

    sid = await create_user_session(store, role="operator")

    # Delete ditolak 403
    res_del = client.delete(
        f"/api/master/ruangan/{room_id}",
        cookies={"clients_session_id": sid},
        headers={"X-ClientS-Request": "1", "Origin": "http://testserver"},
    )
    assert res_del.status_code == 403

    # Read diizinkan 200
    res_read = client.get("/api/master/ruangan", cookies={"clients_session_id": sid})
    assert res_read.status_code == 200
    assert any(r["id"] == room_id for r in res_read.json())


@pytest.mark.anyio
async def test_11_admin_can_manage_rooms(rbac_env):
    """11. Admin dapat mengelola ruangan (POST, PUT, DELETE) secara sah."""
    client: TestClient = rbac_env["client"]
    store: SessionStore = rbac_env["store"]

    sid = await create_user_session(store, role="admin", email="admin@rs.id")
    headers = {"X-ClientS-Request": "1", "Origin": "http://testserver"}
    cookies = {"clients_session_id": sid}

    # 1. POST Tambah ruangan baru
    res_post = client.post(
        "/api/master/ruangan",
        json={"kode_ruangan": "R-ADM-01", "nama_ruangan": "Ruang Operasi Admin", "keterangan": "Ket 1"},
        cookies=cookies,
        headers=headers,
    )
    assert res_post.status_code == 201
    room_id = res_post.json()["id"]
    assert res_post.json()["is_active"] is True

    # 2. PUT Update ruangan
    res_put = client.put(
        f"/api/master/ruangan/{room_id}",
        json={"nama_ruangan": "Ruang Operasi Admin Update"},
        cookies=cookies,
        headers=headers,
    )
    assert res_put.status_code == 200
    assert res_put.json()["nama_ruangan"] == "Ruang Operasi Admin Update"

    # 3. DELETE Hapus ruangan
    res_del = client.delete(f"/api/master/ruangan/{room_id}", cookies=cookies, headers=headers)
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "deleted"


@pytest.mark.anyio
async def test_12_supervisor_can_manage_rooms(rbac_env):
    """12. Supervisor dapat mengelola ruangan sesuai wewenang role aktual."""
    client: TestClient = rbac_env["client"]
    store: SessionStore = rbac_env["store"]

    sid = await create_user_session(store, role="supervisor", email="spv@rs.id")
    headers = {"X-ClientS-Request": "1", "Origin": "http://testserver"}
    cookies = {"clients_session_id": sid}

    # POST tambah ruangan
    res_post = client.post(
        "/api/master/ruangan",
        json={"kode_ruangan": "R-SPV-01", "nama_ruangan": "Ruang Supervisor"},
        cookies=cookies,
        headers=headers,
    )
    assert res_post.status_code == 201
    room_id = res_post.json()["id"]

    # PUT nonaktifkan ruangan
    res_put = client.put(
        f"/api/master/ruangan/{room_id}",
        json={"is_active": False},
        cookies=cookies,
        headers=headers,
    )
    assert res_put.status_code == 200
    assert res_put.json()["is_active"] is False


# =============================================================================
# TEST GROUP 5: LIFECYCLE, INTEGRITAS HISTORI, & SOFT-DISABLE (Tes 13-15)
# =============================================================================

@pytest.mark.anyio
async def test_13_physical_deletion_of_room_with_history_rejected(rbac_env):
    """13. Penghapusan ruangan beralih ke Soft-Delete Enforcement (is_active=False, tidak pernah terhapus fisik)."""
    client: TestClient = rbac_env["client"]
    store: SessionStore = rbac_env["store"]
    session_factory = rbac_env["session_factory"]

    with session_factory() as db:
        room = Ruangan(kode_ruangan="R-HIST-01", nama_ruangan="Ruang Rawat Inap 1")
        db.add(room)
        db.commit()
        db.refresh(room)
        room_id = room.id

        # Simpan riwayat transaksi yang mencatat ruangan tersebut
        trx = TransaksiLinen(
            jenis_transaksi="keluar",
            kode_transaksi="TRX-HIST-001",
            petugas="petugas@rs.id",
            ruangan="Ruang Rawat Inap 1",
            total_linen=5,
            status="selesai",
        )
        db.add(trx)
        db.commit()

    sid = await create_user_session(store, role="admin")
    res_del = client.delete(
        f"/api/master/ruangan/{room_id}",
        cookies={"clients_session_id": sid},
        headers={"X-ClientS-Request": "1", "Origin": "http://testserver"},
    )
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "deleted"
    assert res_del.json()["is_active"] is False

    with session_factory() as db:
        # Integritas Soft-Delete: record ruangan tetap tersimpan di SQLite, tidak terhapus fisik
        room_check = db.query(Ruangan).filter_by(id=room_id).first()
        assert room_check is not None
        assert room_check.is_active is False
        # Integritas histori transaksi: riwayat tetap utuh dan snapshot terjaga
        tx_check = db.query(TransaksiLinen).filter_by(kode_transaksi="TRX-HIST-001").first()
        assert tx_check is not None
        assert tx_check.ruangan == "Ruang Rawat Inap 1"


@pytest.mark.anyio
async def test_14_reactivation_of_room(rbac_env):
    """14. Penonaktifan dan aktivasi kembali ruangan (soft disable/enable cycle)."""
    client: TestClient = rbac_env["client"]
    store: SessionStore = rbac_env["store"]
    session_factory = rbac_env["session_factory"]

    with session_factory() as db:
        room = Ruangan(kode_ruangan="R-TOGGLE", nama_ruangan="Ruang ICU B", is_active=True)
        db.add(room)
        db.commit()
        db.refresh(room)
        room_id = room.id

    sid = await create_user_session(store, role="admin")
    headers = {"X-ClientS-Request": "1", "Origin": "http://testserver"}
    cookies = {"clients_session_id": sid}

    # 1. Nonaktifkan ruangan
    res_deact = client.put(
        f"/api/master/ruangan/{room_id}",
        json={"is_active": False},
        cookies=cookies,
        headers=headers,
    )
    assert res_deact.status_code == 200
    assert res_deact.json()["is_active"] is False

    # 2. Aktifkan kembali ruangan
    res_react = client.put(
        f"/api/master/ruangan/{room_id}",
        json={"is_active": True},
        cookies=cookies,
        headers=headers,
    )
    assert res_react.status_code == 200
    assert res_react.json()["is_active"] is True


@pytest.mark.anyio
async def test_15_rename_room_preserves_historical_transactions(rbac_env):
    """15. Perubahan nama master ruangan tidak mengubah snapshot riwayat transaksi lama."""
    client: TestClient = rbac_env["client"]
    store: SessionStore = rbac_env["store"]
    session_factory = rbac_env["session_factory"]

    with session_factory() as db:
        room = Ruangan(kode_ruangan="R-RENAME", nama_ruangan="Ruang Paviliun Lama")
        db.add(room)
        db.commit()
        db.refresh(room)
        room_id = room.id

        trx = TransaksiLinen(
            jenis_transaksi="masuk",
            kode_transaksi="TRX-RENAME-99",
            petugas="petugas@rs.id",
            ruangan="Ruang Paviliun Lama",
            total_linen=2,
            status="selesai",
        )
        db.add(trx)
        db.commit()

    sid = await create_user_session(store, role="admin")
    headers = {"X-ClientS-Request": "1", "Origin": "http://testserver"}
    cookies = {"clients_session_id": sid}

    # Rename master ruangan
    res_put = client.put(
        f"/api/master/ruangan/{room_id}",
        json={"nama_ruangan": "Ruang Paviliun Baru (Renovasi)"},
        cookies=cookies,
        headers=headers,
    )
    assert res_put.status_code == 200
    assert res_put.json()["nama_ruangan"] == "Ruang Paviliun Baru (Renovasi)"

    # Verifikasi riwayat transaksi lama: snapshot string ruangan TETAP tidak terubah
    with session_factory() as db:
        old_trx = db.query(TransaksiLinen).filter(TransaksiLinen.kode_transaksi == "TRX-RENAME-99").first()
        assert old_trx is not None
        assert old_trx.ruangan == "Ruang Paviliun Lama"
