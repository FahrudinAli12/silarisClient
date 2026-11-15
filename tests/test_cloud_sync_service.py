from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import httpx
import pytest

from app.main import app
from app.services.cloud_sync_service import CloudSyncService

client = TestClient(app)


def test_cloud_sync_service_configuration_behavior():
    """Memastikan fallback credentials tidak tertanam dan konfigurasi eksplisit berfungsi."""
    service_empty = CloudSyncService(url="", key="")
    assert service_empty.is_configured() is False

    service_configured = CloudSyncService(url="https://test.supabase.co", key="test_key_123")
    assert service_configured.is_configured() is True
    assert service_configured.url == "https://test.supabase.co"
    assert service_configured.key == "test_key_123"


def test_direct_patch_prohibited():
    """F8: Memastikan HTTP PATCH langsung ke tabel pengiriman_linen ditolak keras."""
    service = CloudSyncService(url="https://test.supabase.co", key="test_key_123")
    with pytest.raises(RuntimeError, match="DIRECT_PATCH_PROHIBITED"):
        service.mark_paket_received(101)


@patch("httpx.get")
def test_get_data_cloud_endpoint_lockdown(mock_get):
    """
    Tahap 3B: Jalur pengambilan paket lama ditutup sementara dengan 503
    dan dipastikan tidak mengirim request HTTP ke cloud.
    """
    response = client.get("/api/setting/get-data?kode=PKG-720728")
    assert response.status_code == 503
    data = response.json()
    assert "Penerimaan paket belum tersedia" in data["detail"]
    mock_get.assert_not_called()
