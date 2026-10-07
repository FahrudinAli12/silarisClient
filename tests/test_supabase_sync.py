import json

from app.services.supabase_service import SupabaseService


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = json.dumps(payload).encode("utf-8")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("request failed")

    def json(self):
        return self._payload


def test_supabase_service_pushes_payload_when_configured(monkeypatch):
    calls = {}

    def fake_request(method, url, headers=None, json=None, timeout=None):
        calls["method"] = method
        calls["url"] = url
        calls["headers"] = headers or {}
        calls["json"] = json
        return DummyResponse([{"id": 1, "event": "demo"}])

    monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "demo-key")
    monkeypatch.setattr("app.services.supabase_service.httpx.request", fake_request)

    service = SupabaseService()
    result = service.push_record("sync_events", {"event": "demo"})

    assert service.is_configured() is True
    assert calls["method"] == "POST"
    assert calls["url"].endswith("/rest/v1/sync_events")
    assert result == [{"id": 1, "event": "demo"}]
