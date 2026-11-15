from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Optional, Protocol

import httpx

from app.services.session_store import SessionData, SessionStore, validate_positive_int

logger = logging.getLogger("auth_service")


# =============================================================================
# HIERARKI EXCEPTION AUTENTIKASI
# =============================================================================
class AuthError(Exception):
    """Base exception untuk autentikasi dan otorisasi ClientS."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class InvalidCredentialsError(AuthError):
    """Kredensial email atau password salah."""

    def __init__(self, message: str = "Email atau password salah.") -> None:
        super().__init__(message, status_code=401)


class AuthNetworkError(AuthError):
    """Gangguan jaringan atau timeout saat menghubungi layanan cloud Supabase."""

    def __init__(self, message: str = "Gagal terhubung ke layanan autentikasi cloud.") -> None:
        super().__init__(message, status_code=502)


class MembershipNotFoundError(AuthError):
    """Akun pengguna terdaftar di auth.users namun tidak memiliki pemetaan di hospital_memberships."""

    def __init__(self, message: str = "Akun Anda belum terdaftar dalam keanggotaan rumah sakit.") -> None:
        super().__init__(message, status_code=403)


class MembershipAmbiguousError(AuthError):
    """Akun terdaftar pada lebih dari satu rumah sakit (inkonsistensi data)."""

    def __init__(self, message: str = "Konfigurasi akun tidak valid: akun terikat ke lebih dari satu rumah sakit.") -> None:
        super().__init__(message, status_code=403)


class MembershipInactiveError(AuthError):
    """Keanggotaan akun pada rumah sakit berstatus nonaktif (is_active = False)."""

    def __init__(self, message: str = "Keanggotaan akun rumah sakit sedang dinonaktifkan.") -> None:
        super().__init__(message, status_code=403)


class HospitalNotFoundError(AuthError):
    """Data rumah sakit tidak ditemukan atau tidak lolos policy RLS."""

    def __init__(self, message: str = "Data rumah sakit tidak ditemukan atau tidak dapat diakses.") -> None:
        super().__init__(message, status_code=403)


class HospitalInactiveError(AuthError):
    """Entitas rumah sakit berstatus nonaktif (is_active = False)."""

    def __init__(self, message: str = "Rumah sakit terkait sedang dinonaktifkan.") -> None:
        super().__init__(message, status_code=403)


class HospitalMismatchError(AuthError):
    """Akun terikat ke rumah sakit yang berbeda dari rumah sakit instalasi ClientS."""

    def __init__(self, message: str = "Akun Anda terdaftar untuk rumah sakit lain dan tidak memiliki izin mengakses instalasi ClientS ini.") -> None:
        super().__init__(message, status_code=403)


class InstallationConfigError(AuthError):
    """Konfigurasi identitas rumah sakit pada instalasi ClientS kosong atau tidak valid."""

    def __init__(self, message: str = "Identitas rumah sakit pada instalasi ini belum dikonfigurasi atau tidak valid.") -> None:
        super().__init__(message, status_code=503)


class TokenRefreshRejectedError(AuthError):
    """Permintaan refresh token ditolak oleh Supabase (token dicabut/kedaluwarsa)."""

    def __init__(self, message: str = "Sesi cloud telah kedaluwarsa atau dicabut.") -> None:
        super().__init__(message, status_code=401)


# =============================================================================
# PROTOKOL PENYEDIA IDENTITAS INSTALASI (DEPENDENCY INJECTION)
# =============================================================================
class InstallationIdentityProvider(Protocol):
    def get_installation_hospital_id(self) -> Optional[int]:
        """Kembalikan ID bigint rumah sakit resmi untuk instalasi ini, atau None jika belum dikonfigurasi."""
        ...


class EnvInstallationIdentityProvider:
    """Implementasi default pembaca identitas instalasi dari environment variable."""

    def __init__(self, env_var_name: str = "HOSPITAL_CLOUD_ID") -> None:
        self.env_var_name = env_var_name

    def get_installation_hospital_id(self) -> Optional[int]:
        val = os.getenv(self.env_var_name, "").strip()
        if not val:
            return None
        try:
            val_int = int(val)
            return val_int if val_int > 0 else None
        except ValueError:
            return None


# =============================================================================
# LAYANAN UTAMA AUTENTIKASI
# =============================================================================
class AuthService:
    """Layanan autentikasi Supabase Auth, validasi keanggotaan RS, dan pengikatan instalasi."""

    def __init__(
        self,
        session_store: SessionStore,
        installation_provider: Optional[InstallationIdentityProvider] = None,
        supabase_url: Optional[str] = None,
        supabase_anon_key: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        session_duration_seconds: Optional[Any] = None,
        time_provider: Callable[[], float] = time.time,
    ) -> None:
        self.session_store = session_store
        self.installation_provider = installation_provider or EnvInstallationIdentityProvider()
        self.supabase_url = (supabase_url or os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
        self.supabase_anon_key = (supabase_anon_key or os.getenv("SUPABASE_ANON_KEY", "")).strip()
        self._external_client = http_client
        self.time_provider = time_provider

        # Validasi ketat konfigurasi durasi sesi
        if session_duration_seconds is not None:
            self.session_duration_seconds = validate_positive_int(
                session_duration_seconds, "session_duration_seconds"
            )
        else:
            self.session_duration_seconds = session_store.default_session_duration_seconds

    def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None:
            return self._external_client
        return httpx.AsyncClient(timeout=10.0)

    async def login(
        self,
        email: str,
        password: str,
        session_duration_seconds: Optional[Any] = None,
    ) -> tuple[SessionData, dict[str, Any]]:
        """Autentikasi akun pengguna dengan Supabase Auth dan validasi kecocokan rumah sakit instalasi."""
        email_clean = (email or "").strip().lower()
        password_clean = password or ""

        if not email_clean or not password_clean:
            raise InvalidCredentialsError("Email dan password wajib diisi.")

        # Validasi ketat session_duration_seconds jika disediakan
        if session_duration_seconds is not None:
            duration = validate_positive_int(session_duration_seconds, "session_duration_seconds")
        else:
            duration = self.session_duration_seconds

        # 1. Validasi Prasyarat Identitas Instalasi Lokal
        installation_id = self.installation_provider.get_installation_hospital_id()
        if installation_id is None or installation_id <= 0:
            raise InstallationConfigError("Identitas rumah sakit pada instalasi ini belum dikonfigurasi atau tidak valid.")

        if not self.supabase_url or not self.supabase_anon_key:
            raise InstallationConfigError("Konfigurasi koneksi Supabase Cloud (SUPABASE_URL atau SUPABASE_ANON_KEY) belum disetel.")

        client = self._get_client()
        should_close_client = self._external_client is None

        try:
            # 2. Permintaan Login ke Supabase Auth (Password Grant)
            token_url = f"{self.supabase_url}/auth/v1/token?grant_type=password"
            headers = {
                "apikey": self.supabase_anon_key,
                "Content-Type": "application/json",
            }
            payload = {
                "email": email_clean,
                "password": password_clean,
            }

            try:
                auth_resp = await client.post(token_url, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
                raise AuthNetworkError("Gagal terhubung ke layanan autentikasi Supabase karena masalah jaringan.") from exc

            if auth_resp.status_code >= 500:
                raise AuthNetworkError(f"Layanan autentikasi Supabase mengalami gangguan (HTTP {auth_resp.status_code}).")
            elif auth_resp.status_code != 200:
                # Periksa isi respons error dari Supabase Auth / Kong Gateway
                try:
                    auth_err = auth_resp.json()
                except Exception:
                    auth_err = {}

                err_text = ""
                if isinstance(auth_err, dict):
                    parts = []
                    for k in ("msg", "message", "error_description", "error", "error_code"):
                        v = auth_err.get(k)
                        if isinstance(v, str) and v.strip():
                            parts.append(v.strip().lower())
                    err_text = " ".join(parts)
                elif isinstance(auth_err, str):
                    err_text = auth_err.strip().lower()

                # 1. Deteksi masalah API key atau konfigurasi layanan cloud
                api_key_indicators = (
                    "invalid api key",
                    "api key not found",
                    "no api key",
                    "invalid_api_key",
                    "bad_jwt",
                    "jwsinvalidsignature",
                    "jwsnosignature",
                    "invalid signature",
                    "jwt signature",
                )
                is_api_key_error = any(ind in err_text for ind in api_key_indicators)
                if not is_api_key_error and "api key" in err_text:
                    is_api_key_error = True

                if is_api_key_error:
                    raise InstallationConfigError("Konfigurasi autentikasi bermasalah.")

                # 2. Deteksi status email belum dikonfirmasi (jangan salahkan password)
                email_unconfirmed_indicators = (
                    "email not confirmed",
                    "email_not_confirmed",
                    "email not verified",
                )
                if any(ind in err_text for ind in email_unconfirmed_indicators):
                    raise AuthError("Login tidak dapat diselesaikan. Email belum dikonfirmasi atau akun belum aktif.", status_code=400)

                # 3. Deteksi kredensial salah (GoTrue invalid_grant / invalid_credentials / user_not_found)
                credential_indicators = (
                    "invalid login credentials",
                    "invalid_credentials",
                    "invalid credentials",
                    "user not found",
                    "user_not_found",
                )
                is_cred_error = (
                    any(ind in err_text for ind in credential_indicators)
                    or (isinstance(auth_err, dict) and auth_err.get("error") == "invalid_grant")
                )

                if is_cred_error:
                    raise InvalidCredentialsError("Email atau password salah.")

                # 4. Error lain yang tidak dikenali: pesan umum yang aman, jangan otomatis menyalahkan password
                raise AuthError("Terjadi kesalahan pada proses autentikasi.", status_code=auth_resp.status_code)

            try:
                auth_data = auth_resp.json()
            except Exception as exc:
                raise AuthNetworkError("Format respons Supabase Auth tidak valid.") from exc

            access_token = auth_data.get("access_token")
            refresh_token = auth_data.get("refresh_token")

            # Validasi ketat expires_in tanpa fallback statis
            raw_expires_in = auth_data.get("expires_in")
            try:
                token_expires_in = validate_positive_int(raw_expires_in, "expires_in")
            except ValueError as exc:
                raise AuthNetworkError(f"Masa berlaku token (expires_in) dari Supabase Auth tidak valid: {exc}") from exc

            user_info = auth_data.get("user") or {}
            user_id = user_info.get("id")

            if not access_token or not refresh_token or not user_id:
                raise AuthNetworkError("Respons Supabase Auth tidak memuat token atau identitas user yang lengkap.")

            # 3. Query Identitas Rumah Sakit dan Keanggotaan via User Token (RLS Cloud)
            mem_url = f"{self.supabase_url}/rest/v1/hospital_memberships?select=hospital_id,role,is_active,rumah_sakit(id,kode_rs,nama_rs,is_active)"
            mem_headers = {
                "apikey": self.supabase_anon_key,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }

            try:
                mem_resp = await client.get(mem_url, headers=mem_headers)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
                raise AuthNetworkError("Gagal memverifikasi keanggotaan rumah sakit ke cloud.") from exc

            if mem_resp.status_code >= 500:
                raise AuthNetworkError("Layanan database Supabase mengalami gangguan.")
            elif mem_resp.status_code != 200:
                try:
                    mem_err = mem_resp.json()
                except Exception:
                    mem_err = {}
                mem_err_text = ""
                if isinstance(mem_err, dict):
                    mem_err_text = " ".join(str(v).lower() for v in mem_err.values() if isinstance(v, str))
                if any(ind in mem_err_text for ind in ("invalid api key", "api key not found", "no api key", "invalid_api_key", "bad_jwt", "jwsinvalidsignature")):
                    raise InstallationConfigError("Konfigurasi autentikasi bermasalah.")
                raise AuthNetworkError(f"Gagal mengambil data keanggotaan (HTTP {mem_resp.status_code}).")

            try:
                memberships = mem_resp.json()
            except Exception as exc:
                raise AuthNetworkError("Format data keanggotaan rumah sakit tidak valid.") from exc

            # 4. Validasi Keanggotaan dan Relasi Rumah Sakit
            if not isinstance(memberships, list) or len(memberships) == 0:
                raise MembershipNotFoundError("Akun Anda belum terdaftar dalam keanggotaan rumah sakit mana pun.")

            if len(memberships) > 1:
                raise MembershipAmbiguousError("Konfigurasi akun tidak valid: akun terikat ke lebih dari satu rumah sakit.")

            membership = memberships[0]
            if not isinstance(membership, dict):
                raise MembershipNotFoundError("Format data membership tidak valid.")

            # 4.A. Status keaktifan keanggotaan
            if membership.get("is_active") is not True:
                raise MembershipInactiveError("Keanggotaan akun rumah sakit sedang dinonaktifkan.")

            # 4.B. Relasi rumah_sakit
            rs_data = membership.get("rumah_sakit")
            if not rs_data or not isinstance(rs_data, dict):
                raise HospitalNotFoundError("Data rumah sakit tidak ditemukan atau tidak dapat diakses.")

            rs_id = rs_data.get("id")
            rs_nama = rs_data.get("nama_rs")
            rs_kode = rs_data.get("kode_rs")
            rs_is_active = rs_data.get("is_active")

            if rs_id is None or not rs_nama or not rs_kode:
                raise HospitalNotFoundError("Data identitas rumah sakit tidak lengkap.")

            if rs_is_active is not True:
                raise HospitalInactiveError("Rumah sakit terkait sedang dinonaktifkan.")

            # 4.C. Validasi Kecocokan Rumah Sakit dengan Instalasi ClientS
            try:
                user_hospital_id = int(membership["hospital_id"])
                rs_rel_id = int(rs_id)
            except (TypeError, ValueError) as exc:
                raise HospitalNotFoundError("ID rumah sakit pada membership tidak valid.") from exc

            if user_hospital_id != rs_rel_id or user_hospital_id != int(installation_id):
                raise HospitalMismatchError("Akun Anda terdaftar untuk rumah sakit lain dan tidak memiliki izin mengakses instalasi ClientS ini.")

            # 5. Buat Sesi Lokal di Backend
            session = await self.session_store.create_session(
                user_id=str(user_id),
                email=email_clean,
                hospital_id=user_hospital_id,
                hospital_name=str(rs_nama),
                hospital_code=str(rs_kode),
                role=str(membership.get("role") or "operator"),
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_in=token_expires_in,
                session_duration_seconds=duration,
            )

            # Sanitasi data respons publik: TIDAK memuat access_token, refresh_token, atau password
            user_context = {
                "user": {
                    "id": session.user_id,
                    "email": session.email,
                    "role": session.role,
                },
                "hospital": {
                    "id": session.hospital_id,
                    "nama_rs": session.hospital_name,
                    "kode_rs": session.hospital_code,
                },
            }

            return session, user_context

        finally:
            if should_close_client:
                await client.aclose()

    async def refresh_tokens(self, session: SessionData) -> bool:
        """Segarkan JWT Supabase jika mendekati kedaluwarsa, dilindungi lock anti-konkurensi."""
        refresh_lock = self.session_store.get_refresh_lock(session)

        async with refresh_lock:
            # Periksa keberadaan dan validitas sesi di store segera setelah memperoleh lock
            current_session = await self.session_store.get_session(session.session_id)
            if not current_session:
                return False

            # Periksa apakah token sudah diperbarui oleh task lain saat mengantre lock
            if not self.session_store.is_token_near_expiry(current_session):
                return True

            now = self.time_provider()
            # Jika sesi lokal sudah habis, hapus sesi dan jangan perbarui token
            if now >= current_session.session_expires_at:
                await self.session_store.delete_session(current_session.session_id)
                return False

            client = self._get_client()
            should_close_client = self._external_client is None

            try:
                refresh_url = f"{self.supabase_url}/auth/v1/token?grant_type=refresh_token"
                headers = {
                    "apikey": self.supabase_anon_key,
                    "Content-Type": "application/json",
                }
                payload = {
                    "refresh_token": current_session.refresh_token,
                }

                try:
                    resp = await client.post(refresh_url, json=payload, headers=headers)
                except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
                    raise AuthNetworkError("Gangguan jaringan saat memperbarui token Supabase.") from exc

                if resp.status_code in (400, 401, 422):
                    # Token refresh ditolak oleh Supabase (misal refresh token dicabut)
                    await self.session_store.delete_session(current_session.session_id)
                    raise TokenRefreshRejectedError("Sesi cloud telah dicabut atau kedaluwarsa.")
                elif resp.status_code != 200:
                    raise AuthNetworkError(f"Supabase Auth refresh gagal dengan status HTTP {resp.status_code}.")

                data = resp.json()
                new_access_token = data.get("access_token")
                new_refresh_token = data.get("refresh_token") or current_session.refresh_token

                # Validasi ketat expires_in tanpa fallback statis
                raw_expires_in = data.get("expires_in")
                try:
                    new_expires_in = validate_positive_int(raw_expires_in, "expires_in")
                except ValueError as exc:
                    raise AuthNetworkError(f"Masa berlaku token refresh (expires_in) tidak valid: {exc}") from exc

                if not new_access_token:
                    raise AuthNetworkError("Respons refresh Supabase Auth tidak memuat token baru.")

                # Periksa apakah sesi kedaluwarsa saat request cloud berlangsung
                now_after_call = self.time_provider()
                if now_after_call >= current_session.session_expires_at:
                    await self.session_store.delete_session(current_session.session_id)
                    return False

                # Update token di store: jika sesi telah dihapus/logout saat refresh berlangsung,
                # update_tokens akan menolak dan tidak membangkitkan sesi yang sudah dihapus.
                updated = await self.session_store.update_tokens(
                    current_session.session_id,
                    new_access_token=new_access_token,
                    new_refresh_token=new_refresh_token,
                    token_expires_in=new_expires_in,
                )
                return updated

            finally:
                if should_close_client:
                    await client.aclose()

    async def logout(self, session_id: Optional[str]) -> bool:
        """Hapus sesi dari memori server dan lakukan pencabutan best-effort ke Supabase Auth."""
        if not session_id:
            return False

        session = await self.session_store.get_session(session_id)
        token_to_revoke = session.access_token if session else None

        # Hapus sesi lokal seketika
        deleted = await self.session_store.delete_session(session_id)

        # Best-effort panggilan logout ke Supabase Auth
        if token_to_revoke and self.supabase_url and self.supabase_anon_key:
            client = self._get_client()
            should_close = self._external_client is None
            try:
                logout_url = f"{self.supabase_url}/auth/v1/logout"
                headers = {
                    "apikey": self.supabase_anon_key,
                    "Authorization": f"Bearer {token_to_revoke}",
                }
                await client.post(logout_url, headers=headers)
            except Exception as exc:
                logger.debug("Pencabutan token Supabase saat logout gagal: %s", type(exc).__name__)
            finally:
                if should_close:
                    await client.aclose()

        return deleted

    async def get_current_user_context(self, session_id: Optional[str]) -> Optional[dict[str, Any]]:
        """Validasi sesi aktif dan kembalikan konteks pengguna yang bersih tanpa token mentah."""
        if not session_id:
            return None

        session = await self.session_store.get_session(session_id)
        if not session:
            return None

        # Jika token mendekati masa habis, jalankan refresh otomatis
        if self.session_store.is_token_near_expiry(session):
            try:
                refreshed = await self.refresh_tokens(session)
                if not refreshed:
                    return None
            except Exception as exc:
                # Gangguan refresh tidak boleh diam-diam dianggap sebagai keberhasilan online
                logger.warning("Gagal memperbarui token otomatis: %s", type(exc).__name__)
                return None

        # Setelah proses refresh/jaringan selesai, ambil ulang sesi dari SessionStore
        fresh_session = await self.session_store.get_session(session_id)
        if not fresh_session:
            return None

        return {
            "user": {
                "id": fresh_session.user_id,
                "email": fresh_session.email,
                "role": fresh_session.role,
            },
            "hospital": {
                "id": fresh_session.hospital_id,
                "nama_rs": fresh_session.hospital_name,
                "kode_rs": fresh_session.hospital_code,
            },
            "session_expires_at": fresh_session.session_expires_at,
        }

    async def revalidate_membership(self, session_id: str) -> bool:
        """Pemeriksaan ulang keanggotaan dan RS secara live ke cloud (misal sebelum sync/pengambilan paket).

        Memastikan bahwa jika status keanggotaan atau rumah sakit diubah/dinonaktifkan
        di cloud setelah login awal, sesi lokal langsung dicabut dan tidak dapat mengakses cloud.
        """
        if not session_id:
            return False

        session = await self.session_store.get_session(session_id)
        if not session:
            return False

        # Periksa identitas instalasi tepercaya saat ini
        installation_id = self.installation_provider.get_installation_hospital_id()
        if installation_id is None or installation_id <= 0:
            await self.session_store.delete_session(session_id)
            return False

        # Pastikan sesi terikat ke instalasi saat ini
        if int(session.hospital_id) != int(installation_id):
            await self.session_store.delete_session(session_id)
            return False

        # Perbarui token jika mendekati kedaluwarsa
        if self.session_store.is_token_near_expiry(session):
            try:
                refreshed = await self.refresh_tokens(session)
                if not refreshed:
                    return False
            except Exception:
                return False

        # Ambil ulang sesi setelah refresh
        current_session = await self.session_store.get_session(session_id)
        if not current_session:
            return False

        client = self._get_client()
        should_close = self._external_client is None
        try:
            mem_url = f"{self.supabase_url}/rest/v1/hospital_memberships?select=hospital_id,role,is_active,rumah_sakit(id,kode_rs,nama_rs,is_active)"
            mem_headers = {
                "apikey": self.supabase_anon_key,
                "Authorization": f"Bearer {current_session.access_token}",
                "Content-Type": "application/json",
            }
            try:
                resp = await client.get(mem_url, headers=mem_headers)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError):
                return False

            if resp.status_code != 200:
                if resp.status_code in (401, 403):
                    await self.session_store.delete_session(session_id)
                return False

            try:
                memberships = resp.json()
            except Exception:
                return False

            if not isinstance(memberships, list) or len(memberships) != 1:
                await self.session_store.delete_session(session_id)
                return False

            mem = memberships[0]
            if not isinstance(mem, dict) or mem.get("is_active") is not True:
                await self.session_store.delete_session(session_id)
                return False

            rs = mem.get("rumah_sakit")
            if not rs or not isinstance(rs, dict) or rs.get("is_active") is not True:
                await self.session_store.delete_session(session_id)
                return False

            raw_mem_rs_id = mem.get("hospital_id")
            raw_rs_id = rs.get("id")
            rs_nama = rs.get("nama_rs")
            rs_kode = rs.get("kode_rs")

            if raw_mem_rs_id is None or raw_rs_id is None:
                await self.session_store.delete_session(session_id)
                return False

            if not isinstance(rs_nama, str) or not rs_nama.strip():
                await self.session_store.delete_session(session_id)
                return False

            if not isinstance(rs_kode, str) or not rs_kode.strip():
                await self.session_store.delete_session(session_id)
                return False

            try:
                mem_rs_id = int(raw_mem_rs_id)
                rs_id = int(raw_rs_id)
            except (TypeError, ValueError):
                await self.session_store.delete_session(session_id)
                return False

            # Pastikan ke-4 identitas RS cocok:
            # 1. membership.hospital_id
            # 2. rumah_sakit.id
            # 3. current_session.hospital_id
            # 4. installation_id
            # Perubahan RS pada membership harus mencabut sesi lama!
            if not (mem_rs_id == rs_id == int(current_session.hospital_id) == int(installation_id)):
                await self.session_store.delete_session(session_id)
                return False

            # Setelah request cloud selesai, periksa ulang keberadaan dan kedaluwarsa sesi
            post_check_session = await self.session_store.get_session(session_id)
            if not post_check_session:
                return False

            return True
        except Exception as exc:
            logger.warning("Gagal revalidasi membership online: %s", type(exc).__name__)
            return False
        finally:
            if should_close:
                await client.aclose()
