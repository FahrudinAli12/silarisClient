from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("session_store")


def validate_positive_int(value: Any, field_name: str) -> int:
    """Validasi ketat bilangan bulat positif.

    Aturan:
    - Tolak nilai hilang (None).
    - Tolak nilai boolean (True/False).
    - Tolak nilai float atau pecahan (misal 1.5, 1.0, float('inf'), float('nan')).
    - Tolak nilai nol (0) dan nilai negatif (< 0).
    - Terima bilangan bulat positif murni (int > 0).
    - Terima representasi string bilangan bulat positif murni (misal "3600"), tolak string pecahan/inf/nan.
    """
    if value is None:
        raise ValueError(f"{field_name} wajib diisi.")
    if isinstance(value, bool):
        raise ValueError(f"{field_name} tidak boleh bertipe boolean.")
    if isinstance(value, float):
        raise ValueError(f"{field_name} tidak boleh bertipe float atau pecahan.")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError(f"{field_name} harus bernilai bilangan bulat positif (> 0).")
        return value
    if isinstance(value, str):
        clean = value.strip()
        if not clean or not clean.isdigit():
            raise ValueError(f"{field_name} harus berupa representasi bilangan bulat positif murni.")
        val_int = int(clean)
        if val_int <= 0:
            raise ValueError(f"{field_name} harus bernilai bilangan bulat positif (> 0).")
        return val_int

    raise ValueError(f"{field_name} harus bertipe bilangan bulat positif.")


@dataclass
class ConfirmPermit:
    """Izin dispatch RPC Confirm terikat pada sesi, operasi, paket, dan rumah sakit."""

    permit_id: str
    session_id: str
    operation_id: str
    paket_id: int
    hospital_id: int
    issued_at: float
    is_used: bool = False


@dataclass
class SessionData:
    """Struktur data sesi terotentikasi di memori backend ClientS."""

    session_id: str = field(repr=False)
    user_id: str
    email: str
    hospital_id: int
    hospital_name: str
    hospital_code: str
    role: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    token_expires_at: float
    session_expires_at: float
    created_at: float
    last_activity_at: float
    is_revoked: bool = False
    commit_in_flight_op_id: Optional[str] = None
    confirm_permit: Optional[ConfirmPermit] = None
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def __repr__(self) -> str:
        """Representasi aman tanpa membocorkan session_id, access_token, atau refresh_token."""
        return (
            f"SessionData(user_id={self.user_id!r}, email={self.email!r}, "
            f"hospital_id={self.hospital_id!r}, hospital_name={self.hospital_name!r}, "
            f"hospital_code={self.hospital_code!r}, role={self.role!r}, "
            f"token_expires_at={self.token_expires_at!r}, "
            f"session_expires_at={self.session_expires_at!r}, "
            f"is_revoked={self.is_revoked!r})"
        )


class SessionStore:
    """Pengelola sesi pengguna di memori backend (In-Memory Session Store).

    PERINGATAN ARSITEKTUR:
    - SessionStore ini bersifat in-memory pada satu proses aplikasi.
    - Restart proses backend akan menghapus seluruh sesi aktif, sehingga pengguna
      harus melakukan login ulang.
    - Rancangan ini belum mendukung deployment multi-worker (seperti Uvicorn dengan
      workers > 1) atau load-balanced cluster tanpa penyimpanan sesi bersama (misal Redis).
    - Nilai durasi sesi adalah parameter konfigurasi wajib yang harus ditentukan secara eksplisit
      pada setiap instalasi, bukan keputusan durasi sesi operasional final.
    """

    def __init__(
        self,
        default_session_duration_seconds: Any,
        token_refresh_buffer_seconds: Any = 300,
        time_provider: Callable[[], float] = time.time,
    ) -> None:
        self.default_session_duration_seconds = validate_positive_int(
            default_session_duration_seconds, "default_session_duration_seconds"
        )
        self.token_refresh_buffer_seconds = validate_positive_int(
            token_refresh_buffer_seconds, "token_refresh_buffer_seconds"
        )
        self.time_provider = time_provider
        self._sessions: dict[str, SessionData] = {}
        self._lock = asyncio.Lock()
        self._revocation_callbacks: list[Callable[[str], Any]] = []

    def register_revocation_callback(self, callback: Callable[[str], Any]) -> None:
        """Daftarkan callback saat sebuah sesi dicabut atau kedaluwarsa (misal penutupan WebSocket)."""
        self._revocation_callbacks.append(callback)

    async def _notify_revocation(self, session_id: str) -> None:
        """Panggil seluruh callback pencabutan sesi secara aman tanpa mencetak kredensial ke log."""
        for callback in self._revocation_callbacks:
            try:
                res = callback(session_id)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:  # pragma: no cover
                logger.warning("Error saat memanggil revocation callback: %s", type(exc).__name__)

    async def create_session(
        self,
        user_id: str,
        email: str,
        hospital_id: int,
        hospital_name: str,
        hospital_code: str,
        role: str,
        access_token: str,
        refresh_token: str,
        token_expires_in: Any,
        session_duration_seconds: Optional[Any] = None,
    ) -> SessionData:
        """Buat sesi baru dengan ID acak kriptografis dan simpan di memori backend."""
        now = self.time_provider()

        # Validasi ketat token_expires_in (wajib positif, tanpa fallback diam-diam)
        expires_in_val = validate_positive_int(token_expires_in, "token_expires_in")

        # Validasi ketat durasi sesi (menghindari 'value or default' yang menyamarkan 0)
        if session_duration_seconds is not None:
            duration = validate_positive_int(session_duration_seconds, "session_duration_seconds")
        else:
            duration = self.default_session_duration_seconds

        session_id = secrets.token_urlsafe(32)
        token_expires_at = now + expires_in_val
        session_expires_at = now + duration

        session = SessionData(
            session_id=session_id,
            user_id=user_id,
            email=email,
            hospital_id=int(hospital_id),
            hospital_name=hospital_name,
            hospital_code=hospital_code,
            role=role,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
            session_expires_at=session_expires_at,
            created_at=now,
            last_activity_at=now,
            is_revoked=False,
        )

        async with self._lock:
            self._sessions[session_id] = session

        return session

    async def get_session(self, session_id: Optional[str]) -> Optional[SessionData]:
        """Ambil data sesi yang masih aktif dan belum kedaluwarsa."""
        if not session_id or not isinstance(session_id, str):
            return None

        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None

            now = self.time_provider()
            # Cek apakah sesi sudah kedaluwarsa secara absolut atau dicabut
            if session.is_revoked or now >= session.session_expires_at:
                self._sessions.pop(session_id, None)
                asyncio.create_task(self._notify_revocation(session_id))
                return None

            session.last_activity_at = now
            return session

    async def delete_session(self, session_id: Optional[str]) -> bool:
        """Hapus sesi dari memori (logout) dan tandai sebagai revoked."""
        if not session_id:
            return False

        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                session.is_revoked = True
                session.commit_in_flight_op_id = None
                session.confirm_permit = None

        if session:
            await self._notify_revocation(session_id)
            return True
        return False

    async def update_tokens(
        self,
        session_id: str,
        new_access_token: str,
        new_refresh_token: str,
        token_expires_in: Any,
    ) -> bool:
        """Perbarui token JWT Supabase tanpa memperpanjang batas session_expires_at lokal.

        Menjamin bahwa jika sesi telah dihapus/logout saat refresh berlangsung,
        sesi yang sudah musnah TIDAK AKAN dimunculkan kembali ke memori store.
        """
        # Validasi ketat token_expires_in
        expires_in_val = validate_positive_int(token_expires_in, "token_expires_in")

        now = self.time_provider()
        async with self._lock:
            session = self._sessions.get(session_id)
            # Jika sesi sudah tidak ada di store atau telah dicabut, tolak penyimpanan
            if not session or session.is_revoked or now >= session.session_expires_at:
                return False

            session.access_token = new_access_token
            session.refresh_token = new_refresh_token
            session.token_expires_at = now + expires_in_val
            # PENTING: session.session_expires_at SENGAJA TIDAK DIUBAH!
            return True

    def is_token_near_expiry(self, session: SessionData) -> bool:
        """Cek apakah JWT Supabase sudah dekat dengan batas kedaluwarsa."""
        now = self.time_provider()
        return now >= (session.token_expires_at - self.token_refresh_buffer_seconds)

    def get_refresh_lock(self, session: SessionData) -> asyncio.Lock:
        """Ambil lock per-sesi untuk mencegah refresh serentak pada sesi yang sama."""
        return session._refresh_lock

    async def clean_expired_sessions(self) -> int:
        """Bersihkan seluruh sesi yang telah kedaluwarsa."""
        now = self.time_provider()
        expired_ids: list[str] = []

        async with self._lock:
            for s_id, s in list(self._sessions.items()):
                if s.is_revoked or now >= s.session_expires_at:
                    expired_ids.append(s_id)
                    self._sessions.pop(s_id, None)

        for s_id in expired_ids:
            await self._notify_revocation(s_id)

        return len(expired_ids)

    async def acquire_commit_authority(
        self, session_id: Optional[str], operation_id: str
    ) -> Optional[SessionData]:
        """Akuisisi hak commit eksklusif untuk sebuah operasi di bawah asyncio.Lock."""
        if not session_id or not operation_id:
            return None

        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None

            now = self.time_provider()
            if session.is_revoked or now >= session.session_expires_at:
                self._sessions.pop(session_id, None)
                asyncio.create_task(self._notify_revocation(session_id))
                return None

            # Eksklusivitas: jika sedang dipegang operasi lain, tolak
            if session.commit_in_flight_op_id is not None and session.commit_in_flight_op_id != operation_id:
                return None

            session.commit_in_flight_op_id = operation_id
            session.last_activity_at = now
            return session

    async def release_commit_authority(
        self, session: Optional[SessionData], operation_id: str
    ) -> bool:
        """Pelepasan hak commit hanya oleh pemegang hak yang sah di bawah asyncio.Lock."""
        if not session or not operation_id:
            return False

        async with self._lock:
            live_session = self._sessions.get(session.session_id)
            target = live_session or session
            if target.commit_in_flight_op_id == operation_id:
                target.commit_in_flight_op_id = None
                return True
            return False

    async def acquire_confirm_dispatch_permit(
        self,
        session_id: Optional[str],
        operation_id: str,
        paket_id: int,
        hospital_id: int,
    ) -> tuple[bool, Optional[str], Optional[str], str]:
        """Penerbitan Confirm Dispatch Permit terikat 4 faktor dengan verifikasi token dan session expiry."""
        if not session_id or not operation_id:
            return False, None, None, "invalid_arguments"

        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False, None, None, "session_not_found"

            now = self.time_provider()
            if session.is_revoked or now >= session.session_expires_at:
                self._sessions.pop(session_id, None)
                asyncio.create_task(self._notify_revocation(session_id))
                return False, None, None, "session_expired"

            # Validasi rumah sakit
            if session.hospital_id != int(hospital_id):
                return False, None, None, "forbidden_hospital_mismatch"

            # Validasi access token expiry
            if now >= session.token_expires_at:
                return False, None, None, "token_expired"

            # Validasi permit in-flight pada sesi yang sama
            if session.confirm_permit is not None and not session.confirm_permit.is_used:
                return False, None, None, "dispatch_in_flight"

            permit_id = f"permit_{secrets.token_urlsafe(16)}"
            permit = ConfirmPermit(
                permit_id=permit_id,
                session_id=session.session_id,
                operation_id=operation_id,
                paket_id=int(paket_id),
                hospital_id=int(hospital_id),
                issued_at=now,
                is_used=False,
            )
            session.confirm_permit = permit
            session.last_activity_at = now
            return True, session.access_token, permit_id, "ok"

    async def consume_confirm_dispatch_permit(
        self, session_id: Optional[str], permit_id: str
    ) -> tuple[bool, Optional[str], str]:
        """Konsumsi permit secara atomik sebelum dispatch ke cloud.
        
        Membedakan status permit: diterbitkan vs dikonsumsi untuk dispatch.
        Jika logout terjadi sebelum permit dikonsumsi, dispatch ditolak.
        """
        if not session_id or not permit_id:
            return False, None, "invalid_arguments"

        async with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.is_revoked:
                return False, None, "session_revoked_or_missing"

            now = self.time_provider()
            if now >= session.session_expires_at:
                self._sessions.pop(session_id, None)
                asyncio.create_task(self._notify_revocation(session_id))
                return False, None, "session_expired"

            if session.confirm_permit is None or session.confirm_permit.permit_id != permit_id:
                return False, None, "permit_not_found"

            if session.confirm_permit.is_used:
                return False, None, "permit_already_consumed"

            session.confirm_permit.is_used = True
            session.last_activity_at = now
            return True, session.access_token, "ok"

    async def release_confirm_dispatch_permit(
        self, session: Optional[SessionData], permit_id: str
    ) -> bool:
        """Pelepasan Confirm Dispatch Permit hanya oleh pemegang permit_id yang cocok."""
        if not session or not permit_id:
            return False

        async with self._lock:
            live_session = self._sessions.get(session.session_id)
            target = live_session or session
            if target.confirm_permit is not None and target.confirm_permit.permit_id == permit_id:
                target.confirm_permit = None
                return True
            return False
