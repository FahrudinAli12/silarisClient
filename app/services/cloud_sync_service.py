from __future__ import annotations

import os
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def clean_epc(epc_str: str) -> str:
    clean = str(epc_str).strip().upper()
    if len(clean) == 28 and clean.startswith("3000"):
        return clean[4:]
    return clean


class CloudSyncService:
    """Service untuk mengambil data paket linen dari Supabase berdasarkan kode verifikasi."""

    def __init__(self) -> None:
        self.url = os.getenv("SUPABASE_URL", "https://suhdejzcneeaozlmtguf.supabase.co").strip().rstrip("/")
        self.key = os.getenv("SUPABASE_ANON_KEY", "sb_publishable_HB4P4zn44hazfOnEF_2imQ_iFsdWfrf").strip()

    def is_configured(self) -> bool:
        return bool(self.url and self.key)

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }

    def get_paket_by_kode(self, verification_code: str) -> dict[str, Any]:
        """
        Ambil data paket pengiriman dari cloud berdasarkan kode verifikasi.
        
        Mengharapkan tabel Supabase:
          - pengiriman_linen: id, verification_code, supplier, status, tanggal, catatan
          - detail_pengiriman_linen: id, pengiriman_id, epc, kategori, nama_linen
        
        Returns dict dengan keys: paket (header) dan linen_items (list detail).
        Raises RuntimeError jika Supabase belum dikonfigurasi.
        Raises httpx.HTTPError jika request gagal.
        Raises ValueError jika kode tidak ditemukan atau sudah diproses.
        """
        if not self.is_configured():
            raise RuntimeError(
                "Koneksi cloud (Supabase) belum dikonfigurasi. "
                "Harap isi SUPABASE_URL dan SUPABASE_ANON_KEY pada konfigurasi server."
            )

        # Ambil header paket berdasarkan kode
        url = f"{self.url}/rest/v1/pengiriman_linen"
        params = {"verification_code": f"eq.{verification_code}", "select": "*", "limit": "1"}
        resp = httpx.get(url, headers=self._headers(), params=params, timeout=10.0)
        resp.raise_for_status()
        rows = resp.json()

        if not isinstance(rows, list):
            raise ValueError("Response header paket cloud tidak valid.")
        if not rows:
            raise ValueError(f"Kode verifikasi '{verification_code}' tidak ditemukan di cloud.")

        paket = rows[0]

        if paket.get("status") == "received":
            raise ValueError(
                f"Kode verifikasi '{verification_code}' sudah pernah diterima dan diproses. "
                "Gunakan kode yang belum digunakan."
            )

        # Ambil detail linen dari paket tersebut
        paket_id = paket["id"]
        detail_url = f"{self.url}/rest/v1/detail_pengiriman_linen"
        detail_params = {"pengiriman_id": f"eq.{paket_id}", "select": "*"}
        detail_resp = httpx.get(detail_url, headers=self._headers(), params=detail_params, timeout=10.0)
        detail_resp.raise_for_status()
        linen_items = detail_resp.json()

        if not isinstance(linen_items, list):
            raise ValueError("Response detail paket cloud tidak valid.")
        if not linen_items:
            raise ValueError(
                f"Paket dengan kode '{verification_code}' tidak mempunyai data linen. "
                "Hubungi supplier untuk memastikan paket sudah terisi."
            )

        # Keep cloud fields available while presenting one stable client shape.
        normalized_items = []
        for item in linen_items:
            epc = item.get("epc")
            if not epc:
                raise ValueError("Response detail paket cloud tidak memiliki EPC yang valid.")
            clean = clean_epc(epc)
            normalized_items.append({
                **item,
                "id": item.get("id", item.get("linen_id")),
                "epc": clean,
                "kategori": item.get("kategori", ""),
                "nama_linen": item.get("nama_linen", ""),
            })

        return {"paket": paket, "linen_items": normalized_items}

    def mark_paket_received(self, paket_id: str | int) -> None:
        """Tandai paket sebagai sudah diterima di Supabase."""
        if not self.is_configured():
            return  # Silently skip if not configured — local save still succeeds

        url = f"{self.url}/rest/v1/pengiriman_linen?id=eq.{paket_id}"
        try:
            resp = httpx.patch(
                url,
                headers=self._headers(),
                json={"status": "received"},
                timeout=10.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Gagal menandai paket %s sebagai received: %s", paket_id, exc)
