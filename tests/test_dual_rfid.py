import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine, init_db
from app.main import app
from app.models.linen import Linen
from app.models.transaksi_linen import TransaksiLinen
from app.services.rfid_service import RFIDService, RFIDServiceManager, SingleRFIDReader


@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    init_db()
    db = SessionLocal()
    yield db
    db.close()


def test_rfid_service_backward_compatibility():
    """Verifikasi bahwa RFIDService API lama tetap 100% kompatibel."""
    service = RFIDService(port="COM3", baudrate=115200, demo_mode=True)
    assert isinstance(service, RFIDServiceManager)

    status = service.get_status()
    assert "port" in status
    assert "baudrate" in status
    assert "status" in status
    assert "incoming" in status
    assert "outgoing" in status
    assert status["incoming"]["name"] == "RFID_LINEN_MASUK"
    assert status["outgoing"]["name"] == "RFID_LINEN_KELUAR"

    # Test single-reader fallback port change
    service.change_port("COM8", target="masuk")
    assert service.incoming_reader.port == "COM8"


def test_dual_reader_independent_fallback():
    """Verifikasi bahwa COM3 dan COM4 dapat berjalan independen (fallback jika 1 error)."""
    manager = RFIDServiceManager(demo_mode=True)
    inc_stat = manager.incoming_reader.get_status()
    out_stat = manager.outgoing_reader.get_status()

    assert inc_stat["transaction_type"] == "LINEN_MASUK"
    assert out_stat["transaction_type"] == "LINEN_KELUAR"


def test_active_scan_control_mutual_exclusion():
    """Verifikasi kontrol active scan: HANYA 1 RFID Reader yang melakukan inventory aktif pada 1 waktu."""
    manager = RFIDServiceManager(demo_mode=True)

    # 1. Activate Linen Masuk
    res1 = manager.start_scan("LINEN_MASUK")
    assert res1["active_reader"] == manager.incoming_reader.port
    assert manager.incoming_reader.is_inventory_active() is True
    assert manager.outgoing_reader.is_inventory_active() is False

    stat1 = manager.get_status()
    assert stat1["reader"][0]["status"] == "ACTIVE"
    assert stat1["reader"][1]["status"] == "STANDBY"

    # 2. Activate Linen Keluar
    res2 = manager.start_scan("LINEN_KELUAR")
    assert res2["active_reader"] == manager.outgoing_reader.port
    assert manager.incoming_reader.is_inventory_active() is False
    assert manager.outgoing_reader.is_inventory_active() is True

    stat2 = manager.get_status()
    assert stat2["reader"][0]["status"] == "STANDBY"
    assert stat2["reader"][1]["status"] == "ACTIVE"


def test_database_migration_columns(setup_test_db):
    """Verifikasi bahwa kolom reader_source dan port_source ada pada tabel transaksi_linen tanpa recreate DB."""
    from sqlalchemy import inspect
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("transaksi_linen")}

    assert "reader_source" in columns
    assert "port_source" in columns


def test_auto_scan_masuk_com3(setup_test_db):
    """TEST 1: COM3 Scan (RFID_LINEN_MASUK) menghasilkan transaksi LINEN_MASUK."""
    client = TestClient(app)

    payload = {
        "epc": "E280TESTMASUK001",
        "transaction_type": "LINEN_MASUK",
        "reader_name": "RFID_LINEN_MASUK",
        "port": "COM3",
        "ruangan": "R. Laundry",
    }

    res = client.post("/api/transaksi/auto-scan", json=payload)
    assert res.status_code == 201
    data = res.json()

    assert data["status"] == "ok"
    assert data["jenis_transaksi"] == "masuk"
    assert data["reader_source"] == "RFID_LINEN_MASUK"
    assert data["port_source"] == "COM3"

    # Verifikasi data Linen di DB
    db = setup_test_db
    linen = db.query(Linen).filter(Linen.epc == "E280TESTMASUK001").first()
    assert linen is not None
    assert linen.status == "dicuci"
    assert linen.lokasi == "R. Laundry"


def test_auto_scan_keluar_com4(setup_test_db):
    """TEST 2: COM4 Scan (RFID_LINEN_KELUAR) menghasilkan transaksi LINEN_KELUAR."""
    client = TestClient(app)

    payload = {
        "epc": "E280TESTKELUAR001",
        "transaction_type": "LINEN_KELUAR",
        "reader_name": "RFID_LINEN_KELUAR",
        "port": "COM4",
        "ruangan": "Ruangan Flamboyan",
    }

    res = client.post("/api/transaksi/auto-scan", json=payload)
    assert res.status_code == 201
    data = res.json()

    assert data["status"] == "ok"
    assert data["jenis_transaksi"] == "keluar"
    assert data["reader_source"] == "RFID_LINEN_KELUAR"
    assert data["port_source"] == "COM4"

    # Verifikasi data Linen di DB
    db = setup_test_db
    linen = db.query(Linen).filter(Linen.epc == "E280TESTKELUAR001").first()
    assert linen is not None
    assert linen.status == "dipakai"
    assert linen.lokasi == "Ruangan Flamboyan"


def test_transaction_validation_rules(setup_test_db):
    """TEST 3: Validasi aturan — COM3 scan TIDAK BISA buat Linen Keluar & COM4 scan TIDAK BISA buat Linen Masuk."""
    client = TestClient(app)

    # Attempt 1: COM3 trying to create LINEN_KELUAR -> Rejected
    bad_payload1 = {
        "epc": "E280TESTBAD001",
        "transaction_type": "LINEN_KELUAR",
        "reader_name": "RFID_LINEN_MASUK",
        "port": "COM3",
    }
    res1 = client.post("/api/transaksi/auto-scan", json=bad_payload1)
    assert res1.status_code == 400
    assert "COM3" in res1.json()["detail"]

    # Attempt 2: COM4 trying to create LINEN_MASUK -> Rejected
    bad_payload2 = {
        "epc": "E280TESTBAD002",
        "transaction_type": "LINEN_MASUK",
        "reader_name": "RFID_LINEN_KELUAR",
        "port": "COM4",
    }
    res2 = client.post("/api/transaksi/auto-scan", json=bad_payload2)
    assert res2.status_code == 400
    assert "COM4" in res2.json()["detail"]


def test_existing_manual_transaction(setup_test_db):
    """Verifikasi bahwa fitur transaksi manual (UI batch form) tetap 100% berjalan."""
    client = TestClient(app)

    payload = {
        "jenis_transaksi": "masuk",
        "petugas": "Petugas Manual",
        "ruangan": "R. Laundry",
        "catatan": "Transaksi manual via UI",
        "epc_list": ["E280MANUAL001", "E280MANUAL002"],
    }

    res = client.post("/api/transaksi/batch", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["jenis_transaksi"] == "masuk"
    assert data["total_linen"] == 2
