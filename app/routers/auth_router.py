from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, ValidationError

from app.services.auth_service import (
    AuthError,
    AuthNetworkError,
    AuthService,
    HospitalInactiveError,
    HospitalMismatchError,
    HospitalNotFoundError,
    InstallationConfigError,
    InvalidCredentialsError,
    MembershipAmbiguousError,
    MembershipInactiveError,
    MembershipNotFoundError,
)
from app.services.session_store import SessionStore, validate_positive_int

logger = logging.getLogger("auth_router")


# =============================================================================
# MODEL REQUEST & RESPONSE
# =============================================================================
class LoginRequest(BaseModel):
    """Payload login dari browser.

    PERHATIAN KEAMANAN:
    Field hospital_id sengaja diizinkan ada di payload tetapi DITOLAK / DIABAIKAN
    sebagai sumber otorisasi. Otorisasi RS sepenuhnya divalidasi dari Supabase RLS
    dan identitas instalasi tepercaya di backend.
    """

    email: str = Field(..., description="Email akun pengguna rumah sakit")
    password: str = Field(..., description="Kata sandi akun pengguna")
    hospital_id: Optional[Any] = Field(
        default=None,
        description="Field yang mungkin dikirim oleh klien browser lama/liar; diabaikan demi keamanan.",
    )


class UserInfo(BaseModel):
    id: str
    email: str
    role: str


class HospitalInfo(BaseModel):
    id: int
    nama_rs: str
    kode_rs: str


class AuthResponse(BaseModel):
    status: str = "ok"
    user: UserInfo
    hospital: HospitalInfo


class MeResponse(BaseModel):
    status: str = "ok"
    user: UserInfo
    hospital: HospitalInfo
    session_expires_at: Optional[float] = None


class MessageResponse(BaseModel):
    status: str = "ok"
    message: str


# =============================================================================
# KONFIGURASI ROUTER & KEAMANAN
# =============================================================================
@dataclass
class AuthRouterConfig:
    """Konfigurasi keamanan router autentikasi.

    CATATAN KEBIJAKAN KONFIGURASI:
    Durasi sesi (session_duration_seconds) adalah parameter wajib yang HARUS ditentukan
    secara eksplisit pada setiap instalasi dan TIDAK memiliki nilai default otomatis.
    """

    session_duration_seconds: Any  # Wajib diberikan secara eksplisit, tanpa nilai default
    cookie_name: str = "clients_session"
    cookie_secure: bool = False  # True untuk HTTPS di produksi, False untuk localhost/LAN HTTP
    cookie_samesite: str = "lax"
    cookie_path: str = "/"
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    require_custom_header: bool = True
    custom_header_name: str = "X-ClientS-Request"
    custom_header_value: str = "1"

    def __post_init__(self) -> None:
        self.session_duration_seconds = validate_positive_int(
            self.session_duration_seconds, "session_duration_seconds"
        )


# =============================================================================
# DEPENDENCY PROVIDERS DEFAULT
# =============================================================================
_default_config: Optional[AuthRouterConfig] = None
_default_session_store: Optional[SessionStore] = None
_default_auth_service: Optional[AuthService] = None


def set_default_config(config: AuthRouterConfig) -> None:
    """Setel konfigurasi router autentikasi default secara eksplisit."""
    global _default_config, _default_session_store, _default_auth_service
    _default_config = config
    _default_session_store = None
    _default_auth_service = None


def reset_default_config() -> None:
    """Reset konfigurasi default (khusus untuk isolasi pengujian)."""
    global _default_config, _default_session_store, _default_auth_service
    _default_config = None
    _default_session_store = None
    _default_auth_service = None


def get_default_config() -> AuthRouterConfig:
    """Ambil konfigurasi default; gagal jika konfigurasi belum disetel secara eksplisit."""
    global _default_config
    if _default_config is None:
        raise InstallationConfigError(
            "Konfigurasi router autentikasi belum disetel. Durasi sesi wajib ditentukan secara eksplisit."
        )
    return _default_config


_global_revocation_hooks: list[Callable[[str], Any]] = []


def get_default_session_store() -> SessionStore:
    global _default_session_store
    if _default_session_store is None:
        cfg = get_default_config()
        _default_session_store = SessionStore(default_session_duration_seconds=cfg.session_duration_seconds)
        for cb in _global_revocation_hooks:
            _default_session_store.register_revocation_callback(cb)
    return _default_session_store


def get_default_auth_service() -> AuthService:
    global _default_auth_service
    if _default_auth_service is None:
        cfg = get_default_config()
        _default_auth_service = AuthService(
            session_store=get_default_session_store(),
            session_duration_seconds=cfg.session_duration_seconds,
        )
    return _default_auth_service


def register_ws_revocation_hook(
    callback: Callable[[str], Any],
    session_store: Optional[SessionStore] = None,
) -> None:
    """Titik integrasi pencabutan sesi untuk WebSocket operasional.

    Memungkinkan modul WebSocket mendaftarkan handler untuk memutus koneksi
    klien saat sesinya dicabut atau logout tanpa mengubah logika WebSocket sekarang.
    """
    if callback not in _global_revocation_hooks:
        _global_revocation_hooks.append(callback)
    store = session_store or get_default_session_store()
    store.register_revocation_callback(callback)



# =============================================================================
# VALIDASI KEAMANAN (ORIGIN & CUSTOM CSRF HEADER)
# =============================================================================
def validate_mutation_security(
    request: Request,
    config: AuthRouterConfig,
    custom_header_val: Optional[str],
) -> None:
    """Periksa Origin allowlist dan custom header pada request mutasi auth (POST login/logout)."""
    # 1. Validasi Custom Request Header (Anti-CSRF dari standard form submissions)
    if config.require_custom_header:
        if not custom_header_val or custom_header_val.strip() != config.custom_header_value:
            logger.warning(
                "Percobaan mutasi auth ditolak: header %s tidak valid atau hilang",
                config.custom_header_name,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Header permintaan khusus {config.custom_header_name} tidak valid.",
            )

    # 2. Validasi Origin / Referer terhadap Allowlist eksplisit
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")

    target_origin: Optional[str] = None
    if origin:
        target_origin = origin.strip().rstrip("/")
    elif referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            target_origin = f"{parsed.scheme}://{parsed.netloc}"

    if not target_origin:
        logger.warning("Percobaan mutasi auth ditolak: header Origin dan Referer tidak tersedia")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Header Origin atau Referer wajib disertakan untuk mutasi autentikasi.",
        )

    # Normalisasi allowed origins
    normalized_allowed = {o.strip().rstrip("/") for o in config.allowed_origins if o.strip()}
    if target_origin not in normalized_allowed:
        logger.warning("Percobaan mutasi auth ditolak dari origin di luar allowlist")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin permintaan tidak diizinkan.",
        )


# =============================================================================
# FACTORY / BUILDER ROUTER
# =============================================================================
def create_auth_router(
    auth_service_provider: Callable[[], AuthService] = get_default_auth_service,
    config_provider: Callable[[], AuthRouterConfig] = get_default_config,
) -> APIRouter:
    """Buat instance router autentikasi terisolasi dengan dependency injection."""
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post(
        "/login",
        response_model=AuthResponse,
        summary="Login akun pengguna rumah sakit",
        description="Autentikasi dengan Supabase Auth, validasi identitas RS dan keanggotaan aktif.",
    )
    async def login(
        request: Request,
        response: Response,
        custom_header: Optional[str] = Header(None, alias="X-ClientS-Request"),
        auth_service: AuthService = Depends(auth_service_provider),
        config: AuthRouterConfig = Depends(config_provider),
    ) -> Any:
        # Keamanan: Periksa origin dan custom header (tanpa membocorkan header mentah ke log)
        validate_mutation_security(request, config, custom_header)

        # Parsing JSON secara manual untuk sanitasi terbatas pada modul auth:
        # Mencegah detail password bocor saat terjadi ValidationError pada request yang malformed
        try:
            raw_body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format JSON tidak valid.",
            )

        if not isinstance(raw_body, dict):
            raise HTTPException(
                status_code=422,
                detail="Payload login harus berupa objek JSON.",
            )

        try:
            payload = LoginRequest.model_validate(raw_body)
        except ValidationError as val_err:
            # Hapus nilai 'input' dari seluruh error agar password pengguna tidak ikut dikembalikan
            sanitized_errors = []
            for err in val_err.errors():
                err_dict = dict(err)
                err_dict.pop("input", None)
                sanitized_errors.append(err_dict)
            raise HTTPException(
                status_code=422,
                detail=sanitized_errors,
            )

        # CATATAN KEAMANAN:
        # payload.hospital_id sengaja diabaikan. Sumber otorisasi murni berasal dari
        # identitas instalasi tepercaya di backend dan token Supabase yang diverifikasi RLS.
        try:
            session, user_ctx = await auth_service.login(
                email=payload.email,
                password=payload.password,
                session_duration_seconds=config.session_duration_seconds,
            )
        except InvalidCredentialsError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message)
        except (
            MembershipNotFoundError,
            MembershipAmbiguousError,
            MembershipInactiveError,
            HospitalNotFoundError,
            HospitalInactiveError,
            HospitalMismatchError,
        ) as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=exc.message)
        except InstallationConfigError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.message)
        except AuthNetworkError as exc:
            # Bedakan kegagalan jaringan dari password salah (502 Bad Gateway)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.message)
        except AuthError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

        # Set Cookie sesi backend (HANYA memuat session_id acak kriptografis)
        # Token Supabase TIDAK PERNAH dikirim ke cookie maupun body respons!
        response.set_cookie(
            key=config.cookie_name,
            value=session.session_id,
            max_age=config.session_duration_seconds,
            path=config.cookie_path,
            httponly=True,
            secure=config.cookie_secure,
            samesite=config.cookie_samesite,  # type: ignore
        )

        return {
            "status": "ok",
            "user": user_ctx["user"],
            "hospital": user_ctx["hospital"],
        }

    @router.post(
        "/logout",
        response_model=MessageResponse,
        summary="Logout dan hapus sesi pengguna",
    )
    async def logout(
        request: Request,
        response: Response,
        custom_header: Optional[str] = Header(None, alias="X-ClientS-Request"),
        auth_service: AuthService = Depends(auth_service_provider),
        config: AuthRouterConfig = Depends(config_provider),
    ) -> Any:
        # Keamanan: Periksa origin dan custom header
        validate_mutation_security(request, config, custom_header)

        session_id = request.cookies.get(config.cookie_name)
        if session_id:
            await auth_service.logout(session_id)

        # Hapus cookie sesi dari browser klien
        response.delete_cookie(
            key=config.cookie_name,
            path=config.cookie_path,
            httponly=True,
            secure=config.cookie_secure,
            samesite=config.cookie_samesite,  # type: ignore
        )

        return {"status": "ok", "message": "Logout berhasil."}

    @router.get(
        "/me",
        response_model=MeResponse,
        summary="Cek status dan identitas sesi aktif pengguna",
    )
    async def me(
        request: Request,
        auth_service: AuthService = Depends(auth_service_provider),
        config: AuthRouterConfig = Depends(config_provider),
    ) -> Any:
        session_id = request.cookies.get(config.cookie_name)
        if not session_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sesi tidak ditemukan atau belum login.",
            )

        user_ctx = await auth_service.get_current_user_context(session_id)
        if not user_ctx:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sesi telah kedaluwarsa atau tidak valid.",
            )

        return {
            "status": "ok",
            "user": user_ctx["user"],
            "hospital": user_ctx["hospital"],
            "session_expires_at": user_ctx.get("session_expires_at"),
        }

    return router


# Router instance standar (siap diimpor atau dipasang pada aplikasi FastAPI sementara)
auth_router = create_auth_router()
