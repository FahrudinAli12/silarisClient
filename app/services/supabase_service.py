from __future__ import annotations

import os
from typing import Any

import httpx


class SupabaseService:
    """Simple Supabase REST client for demo and real sync workflows."""

    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "").strip()
        self.key = os.getenv("SUPABASE_ANON_KEY", "").strip()

    def is_configured(self) -> bool:
        return bool(self.url and self.key)

    def push_record(self, table: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.is_configured():
            raise RuntimeError("Supabase credentials belum dikonfigurasi")

        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        response = httpx.request(
            "POST",
            f"{self.url.rstrip('/')}/rest/v1/{table}",
            headers=headers,
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()
