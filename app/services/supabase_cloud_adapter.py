"""
=============================================================================
MODULE: app.services.supabase_cloud_adapter
REVISI: TAHAP 4D.2A.3-R2.9 (REAL SUPABASE REST RPC INTEGRATION ADAPTER)
DESKRIPSI:
    Implementasi asynchronous CloudPackageAdapter berbasis HTTPX untuk
    berkomunikasi langsung dengan Supabase REST RPC:
    1. get_package_info()       -> rpc/get_package_info
    2. claim_package()          -> rpc/claim_package
    3. confirm_package()        -> rpc/confirm_package (6 parameter eksak)
    4. get_package_claim_status()-> rpc/get_package_claim_status
    
    ATURAN KEAMANAN:
    - Header apikey: SUPABASE_PUBLISHABLE_KEY (anon key publik).
    - Header Authorization: Bearer <USER_JWT> (access token sesi pengguna).
    - Dilarang keras menggunakan publishable key sebagai Bearer token pengguna.
    - Dilarang keras menanam service_role key pada ClientS.
=============================================================================
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("supabase_cloud_adapter")


class SupabaseAuthenticationError(Exception):
    """Exception saat token pengguna kedaluwarsa atau tidak terautentikasi (HTTP 401)."""
    pass


class SupabaseAuthorizationError(Exception):
    """Exception saat hak akses tenant atau keanggotaan RS ditolak (HTTP 403)."""
    pass


class SupabaseRPCError(Exception):
    """Exception saat eksekusi RPC mengembalikan status error atau payload malformed."""
    pass


class SupabaseCloudPackageAdapter:
    """
    Adapter nyata komunikasi cloud Supabase REST RPC menggunakan HTTPX async client.
    Memenuhi kontrak CloudPackageAdapter dan CloudClaimConfirmAdapter.
    """

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        publishable_key: Optional[str] = None,
        timeout: float = 10.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.url = (supabase_url or os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
        self.publishable_key = (
            publishable_key or os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
        ).strip()
        self.timeout = timeout
        self._client = client

    def is_configured(self) -> bool:
        """Memeriksa apakah konfigurasi URL dan Publishable Key Supabase tersedia."""
        return bool(self.url and self.publishable_key)

    def _get_headers(self, user_token: str) -> dict[str, str]:
        """
        Menyusun header autentikasi HTTP sesuai standar Supabase REST API:
        - apikey: Publishable / anon key
        - Authorization: Bearer <user_token>
        """
        clean_token = (user_token or "").strip()
        if not clean_token:
            raise SupabaseAuthenticationError("User access token tidak boleh kosong.")

        if clean_token == self.publishable_key:
            raise SupabaseAuthenticationError(
                "Pelanggaran Keamanan: Publishable Key tidak boleh digunakan sebagai Bearer user token."
            )

        return {
            "apikey": self.publishable_key,
            "Authorization": f"Bearer {clean_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _send_rpc(self, token: str, rpc_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Helper internal untuk mengirimkan request POST ke endpoint REST RPC Supabase."""
        if not self.is_configured():
            raise RuntimeError(
                "Koneksi Supabase belum dikonfigurasi. Harap tentukan SUPABASE_URL dan SUPABASE_PUBLISHABLE_KEY."
            )

        headers = self._get_headers(token)
        endpoint = f"{self.url}/rest/v1/rpc/{rpc_name}"

        try:
            if self._client:
                resp = await self._client.post(endpoint, json=payload, headers=headers, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(endpoint, json=payload, headers=headers)
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            logger.warning("Supabase RPC '%s' mengalami timeout (%ss): %s", rpc_name, self.timeout, exc)
            raise asyncio.TimeoutError(f"Supabase RPC '{rpc_name}' timeout: {exc}") from exc
        except httpx.RequestError as exc:
            logger.warning("Supabase RPC '%s' mengalami kegagalan koneksi jaringan: %s", rpc_name, exc)
            raise RuntimeError(f"Supabase RPC network error: {exc}") from exc

        # Evaluasi HTTP Status Code
        if resp.status_code == 401:
            raise SupabaseAuthenticationError(f"Autentikasi cloud ditolak (HTTP 401): {resp.text}")
        if resp.status_code == 403:
            raise SupabaseAuthorizationError(f"Otorisasi tenant cloud ditolak (HTTP 403): {resp.text}")
        if resp.status_code == 404:
            raise SupabaseRPCError(f"RPC '{rpc_name}' atau resource tidak ditemukan (HTTP 404)")

        try:
            data = resp.json()
        except Exception as exc:
            raise SupabaseRPCError(f"Respons RPC '{rpc_name}' bukan format JSON valid: {resp.text}") from exc

        if resp.status_code >= 400:
            err_msg = data.get("message") if isinstance(data, dict) else resp.text
            raise SupabaseRPCError(f"RPC '{rpc_name}' mengembalikan error HTTP {resp.status_code}: {err_msg}")

        if not isinstance(data, dict):
            raise SupabaseRPCError(f"Format payload kembalian RPC '{rpc_name}' bukan dictionary JSON: {data}")

        return data

    async def get_package_info(self, token: str, paket_id: int) -> Optional[dict[str, Any]]:
        """
        Mengambil pratinjau metadata manifest paket melalui RPC aman get_package_info.
        Data verification_code TIDAK diekspos oleh RPC ini (Prinsip Least Privilege).
        """
        payload = {"p_package_id": int(paket_id)}
        try:
            res = await self._send_rpc(token, "get_package_info", payload)
            if res.get("status") == "NOT_FOUND" or res.get("code") == "PACKAGE_NOT_FOUND":
                return None
            if res.get("status") in ("FORBIDDEN", "ERROR"):
                logger.warning("Gagal mengambil info paket %s: %s", paket_id, res.get("message"))
                raise SupabaseAuthorizationError(res.get("message", "Akses paket ditolak"))
            return res
        except SupabaseRPCError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    async def claim_package(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Mengeksekusi RPC claim_package pada cloud Supabase.
        Memetakan payload ClientS ke parameter SQL kanonikal:
        - p_package_id
        - p_operation_id
        - p_installation_uid
        - p_verification_code
        - p_computed_manifest_hash
        - p_hospital_id
        """
        rpc_payload = {
            "p_package_id": int(payload["paket_id"]),
            "p_operation_id": str(payload["operation_id"]),
            "p_installation_uid": str(payload["installation_uid"]),
            "p_verification_code": str(payload["verification_code"]),
            "p_computed_manifest_hash": str(payload.get("expected_manifest_hash") or payload.get("manifest_hash")),
            "p_hospital_id": int(payload["hospital_id"]),
        }
        return await self._send_rpc(token, "claim_package", rpc_payload)

    async def confirm_package(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Mengeksekusi RPC confirm_package pada cloud Supabase dengan 6 parameter eksak.
        CATATAN: p_permit_id tidak dikirim ke cloud karena permit dikelola secara lokal di SessionStore.
        """
        rpc_payload = {
            "p_package_id": int(payload["paket_id"]),
            "p_hospital_id": int(payload["hospital_id"]),
            "p_operation_id": str(payload["operation_id"]),
            "p_installation_uid": str(payload["installation_uid"]),
            "p_verification_code": str(payload["verification_code"]),
            "p_manifest_hash": str(payload.get("manifest_hash") or payload.get("expected_manifest_hash")),
        }
        return await self._send_rpc(token, "confirm_package", rpc_payload)

    async def get_package_claim_status(
        self, token: str, paket_id: int, hospital_id: Optional[int] = None
    ) -> dict[str, Any]:
        """
        Memanggil RPC get_package_claim_status untuk memeriksa status klaim paket
        dan jejak audit identitas operasi dalam proses rekonsiliasi crash recovery.
        """
        rpc_payload: dict[str, Any] = {"p_package_id": int(paket_id)}
        if hospital_id is not None:
            rpc_payload["p_hospital_id"] = int(hospital_id)

        return await self._send_rpc(token, "get_package_claim_status", rpc_payload)

