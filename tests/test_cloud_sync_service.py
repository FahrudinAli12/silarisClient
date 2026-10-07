from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import httpx

from app.main import app
from app.services.cloud_sync_service import CloudSyncService

client = TestClient(app)


def test_cloud_sync_service_default_configuration():
    service = CloudSyncService()
    assert service.is_configured() is True
    assert service.url == "https://suhdejzcneeaozlmtguf.supabase.co"
    assert service.key == "sb_publishable_HB4P4zn44hazfOnEF_2imQ_iFsdWfrf"


@patch("httpx.get")
def test_get_data_cloud_endpoint_success(mock_get):
    def fake_get(url, headers=None, params=None, timeout=None):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        if "pengiriman_linen" in url and "detail" not in url:
            mock_resp.json.return_value = [
                {
                    "id": 10,
                    "verification_code": "PKG-720728",
                    "supplier": "Test Supplier",
                    "status": "sent",
                }
            ]
        else:
            mock_resp.json.return_value = [
                {
                    "id": 1,
                    "pengiriman_id": 10,
                    "epc": "EPC-TEST-001",
                    "kategori": "Sprei",
                    "nama_linen": "Sprei Test",
                }
            ]
        return mock_resp

    mock_get.side_effect = fake_get

    response = client.get("/api/setting/get-data?kode=PKG-720728")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["paket"]["verification_code"] == "PKG-720728"
    assert len(data["linen_items"]) == 1
    assert data["linen_items"][0]["epc"] == "EPC-TEST-001"
