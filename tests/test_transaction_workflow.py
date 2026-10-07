from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, init_db

client = TestClient(app)


def test_transaction_flow_updates_linen_status_and_blocks_duplicates():
    init_db()

    linen_payload = {
        "epc": "EPC-TEST-UNIT-001",
        "kategori": "Bed Cover",
        "nama_linen": "Selimut Unit",
        "lokasi": "Storage",
        "total_cuci": 1,
        "status": "tersedia",
    }
    linen_response = client.post("/api/master/linen", json=linen_payload)
    assert linen_response.status_code == 201, linen_response.text
    linen_id = linen_response.json()["id"]

    transaksi_response = client.post(
        "/api/transaksi",
        json={"jenis_transaksi": "keluar", "kode_transaksi": "TRX-UNIT-001", "catatan": "uji unit"},
    )
    assert transaksi_response.status_code == 201, transaksi_response.text
    transaksi_id = transaksi_response.json()["id"]

    detail_response = client.post(
        "/api/transaksi/detail",
        json={"transaksi_id": transaksi_id, "linen_id": linen_id, "epc": linen_payload["epc"], "keterangan": "detail uji"},
    )
    assert detail_response.status_code == 201, detail_response.text

    history_response = client.post(
        "/api/transaksi/riwayat",
        json={
            "linen_id": linen_id,
            "transaksi_id": transaksi_id,
            "event_type": "keluar",
            "lokasi_sebelumnya": "Storage",
            "lokasi_sesudah": "Ruangan",
        },
    )
    assert history_response.status_code == 201, history_response.text

    duplicate_response = client.post(
        "/api/transaksi/detail",
        json={"transaksi_id": transaksi_id, "linen_id": linen_id, "epc": linen_payload["epc"], "keterangan": "duplikat"},
    )
    assert duplicate_response.status_code == 400

    linen_after = client.get(f"/api/master/linen").json()
    created = next(item for item in linen_after if item["id"] == linen_id)
    assert created["status"] == "dipakai"
    assert created["lokasi"] == "Ruangan"


def test_batch_transaction_workflow():
    init_db()

    payload = {
        "jenis_transaksi": "masuk",
        "kode_transaksi": "TRX-BATCH-001",
        "petugas": "Siti Laundry",
        "ruangan": "R. Laundry",
        "catatan": "Batch cuci dari ruangan",
        "epc_list": ["EPC-BATCH-001", "EPC-BATCH-002"],
    }
    response = client.post("/api/transaksi/batch", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["kode_transaksi"] == "TRX-BATCH-001"
    assert data["petugas"] == "Siti Laundry"
    assert data["total_linen"] == 2

    # Verify Linen master statuses were updated to dicuci
    linen_list = client.get("/api/master/linen?status=dicuci").json()
    epcs_dicuci = [item["epc"] for item in linen_list]
    assert "EPC-BATCH-001" in epcs_dicuci
    assert "EPC-BATCH-002" in epcs_dicuci


def test_rfid_port_status_api():
    status_res = client.get("/api/rfid/status")
    assert status_res.status_code == 200
    assert "port" in status_res.json()
    assert "available_ports" in status_res.json()

    port_res = client.post("/api/rfid/port", json={"port": "COM4"})
    assert port_res.status_code == 200
    assert port_res.json()["port"] == "COM4"

