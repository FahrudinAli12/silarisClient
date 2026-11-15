from __future__ import annotations

import logging
import os
from typing import Any

import httpx

import re

logger = logging.getLogger(__name__)

_EPC_HEX_24_RE = re.compile(r"^[0-9A-F]{24}$")


def clean_epc(epc_str: str) -> str:
    clean = str(epc_str).strip().upper()
    if len(clean) == 28 and clean.startswith("3000"):
        clean = clean[4:]
    if not _EPC_HEX_24_RE.match(clean):
        raise ValueError(f"INVALID_EPC_FORMAT: Tag EPC '{epc_str}' tidak valid (wajib 24 karakter hex).")
    return clean


class CloudSyncService:
    """Service legacy untuk mengambil data paket linen dari Supabase berdasarkan kode verifikasi."""

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        # Menghapus fallback URL dan key yang tertanam di source code (F8 Fix)
        self.url = (url or os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
        self.key = (key or os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")).strip()

    def is_configured(self) -> bool:
        return bool(self.url and self.key)

    def _headers(self) -> dict[str, str]:
        if not self.is_configured():
            raise RuntimeError(
                "Koneksi cloud (Supabase) belum dikonfigurasi. "
                "Harap isi SUPABASE_URL dan SUPABASE_ANON_KEY pada konfigurasi environment."
            )
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

        if paket.get("status") in ("received", "confirmed"):
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
        """
        Tandai paket sebagai sudah diterima di Supabase.
        
        CATATAN INTEGRITAS R2.9 (F8):
        Jalur HTTP PATCH langsung ke tabel pengiriman_linen telah DINONAKTIFKAN secara permanen.
        Seluruh konfirmasi penerimaan paket wajib melalui Confirm RPC resmi
        (PackageReceivingService.confirm_package).
        """
        raise RuntimeError(
            "DIRECT_PATCH_PROHIBITED: Pembaruan status paket via HTTP PATCH langsung dilarang demi integritas multi-tenant "
            "dan state machine immutability. Seluruh konfirmasi penerimaan paket wajib menggunakan Confirm RPC resmi."
        )
