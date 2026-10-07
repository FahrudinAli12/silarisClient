from fastapi.testclient import TestClient

from app.main import app
from app.database import init_db

client = TestClient(app)


def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_service_worker():
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "application/javascript" in response.headers.get("content-type", "")


def test_master_crud_and_sync_buffer():
    init_db()

    create_response = client.post(
        "/api/master/linen",
        json={
            "epc": "EPC-CRUD-001",
            "kategori": "Bed Cover",
            "nama_linen": "Selimut CRUD",
            "lokasi": "Storage",
            "total_cuci": 1,
            "status": "tersedia",
        },
    )
    assert create_response.status_code == 201, create_response.text
    linen_id = create_response.json()["id"]

    search_response = client.get("/api/master/linen?q=CRUD")
    assert search_response.status_code == 200
    data = search_response.json()
    assert any(item["id"] == linen_id for item in data)

    update_response = client.put(
        f"/api/master/linen/{linen_id}",
        json={"epc": "EPC-CRUD-001", "kategori": "Bed Cover", "nama_linen": "Selimut CRUD", "lokasi": "Ruangan", "total_cuci": 2, "status": "dipakai"},
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["lokasi"] == "Ruangan"

    sync_response = client.post("/api/sync/manual")
    assert sync_response.status_code == 200
    assert sync_response.json()["status"] == "queued"

    buffer_response = client.get("/api/sync/buffer")
    assert buffer_response.status_code == 200
    assert len(buffer_response.json()) >= 1

    delete_response = client.delete(f"/api/master/linen/{linen_id}")
    assert delete_response.status_code == 200

    after_delete = client.get(f"/api/master/linen/{linen_id}")
    assert after_delete.status_code == 404


def test_simpan_data_creates_riwayat():
    from unittest.mock import patch
    init_db()

    mock_cloud_data = {
        "paket": {"id": 100, "kode": "PKT-TEST-99"},
        "linen_items": [
            {"epc": "E2801191A000000000000001", "kategori": "Handuk", "nama_linen": "Handuk Test"}
        ]
    }

    with patch("app.routers.setting_router.CloudSyncService.get_paket_by_kode", return_value=mock_cloud_data), \
         patch("app.routers.setting_router.CloudSyncService.mark_paket_received", return_value=True):
        payload = {
            "verification_code": "PKT-TEST-99",
            "petugas": "Petugas Test",
            "paket_id": 100,
            "linen_terdaftar": [
                {"id": 1, "epc": "E2801191A000000000000001", "kategori": "Handuk", "nama_linen": "Handuk Test"}
            ]
        }
        res = client.post("/api/setting/simpan-data", json=payload)
        assert res.status_code == 200, res.text

    riwayat_res = client.get("/api/transaksi/riwayat")
    assert riwayat_res.status_code == 200
    riwayat_items = riwayat_res.json()
    assert len(riwayat_items) > 0
    assert any(r["event_type"] == "penerimaan" for r in riwayat_items)

