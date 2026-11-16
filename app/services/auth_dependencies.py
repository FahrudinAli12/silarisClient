from __future__ import annotations

import asyncio
import logging
from typing import Optional, Set

from fastapi import Depends, HTTPException, Request, Response, WebSocket, status
from fastapi.responses import RedirectResponse

from app.routers.auth_router import (
    AuthRouterConfig,
    get_default_auth_service,
    get_default_config,
    get_default_session_store,
    validate_mutation_security,
)
from app.services.auth_service import AuthService
from app.services.session_store import SessionData, SessionStore

logger = logging.getLogger("auth_dependencies")

get_session_store = get_default_session_store

# Registry WebSocket aktif yang terikat ke session_id
_active_ws_connections: dict[str, Set[WebSocket]] = {}
_ws_lock = asyncio.Lock()
_monitor_task: Optional[asyncio.Task] = None


async def require_active_session(
    request: Request,
    auth_service: AuthService = Depends(get_default_auth_service),
    config: AuthRouterConfig = Depends(get_default_config),
) -> SessionData:
    """Dependency otorisasi backend untuk seluruh endpoint API operasional.

    Aturan:
    - Memeriksa keberadaan cookie sesi backend.
    - Memeriksa keabsahan dan masa berlaku absolut sesi (28.800s).
    - Memeriksa CSRF header dan Origin pada setiap mutasi (POST/PUT/DELETE/PATCH).
    - Menolak dengan HTTP 401 jika sesi tidak valid, kedaluwarsa, atau tidak ada.
    """
    session_id = request.cookies.get(config.cookie_name)
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesi tidak valid atau telah berakhir. Silakan login kembali.",
        )

    session = await auth_service.session_store.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesi tidak valid atau telah berakhir. Silakan login kembali.",
        )

    # Validasi CSRF & Origin untuk request mutasi berbasis cookie
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        custom_header = request.headers.get(config.custom_header_name)
        validate_mutation_security(request, config, custom_header)

    return session


async def require_page_session(
    request: Request,
    auth_service: AuthService = Depends(get_default_auth_service),
    config: AuthRouterConfig = Depends(get_default_config),
) -> SessionData:
    """Dependency otorisasi untuk halaman antarmuka web (HTML).

    Jika sesi tidak ada atau kedaluwarsa, lakukan HTTP 307 Redirect ke /login.
    """
    session_id = request.cookies.get(config.cookie_name)
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )

    session = await auth_service.session_store.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )

    return session


async def require_master_data_editor(
    session: Any = Depends(require_active_session),
) -> Any:
    """Dependency otorisasi backend untuk mutasi master data (POST, PUT, DELETE).

    Aturan RBAC:
    - Admin: Diizinkan mengelola master data ruangan.
    - Supervisor: Diizinkan mengelola master data ruangan.
    - Operator atau role lain: DITOLAK dengan HTTP 403 Forbidden.
    """
    if isinstance(session, dict):
        role_raw = session.get("role")
        email_raw = session.get("email")
    else:
        role_raw = getattr(session, "role", None)
        email_raw = getattr(session, "email", None)

    user_role = (role_raw or "").strip().lower()
    if user_role not in ("admin", "supervisor"):
        logger.warning(
            "Akses mutasi master data ditolak untuk user '%s' dengan role '%s'.",
            email_raw,
            role_raw,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Akses ditolak: role '{role_raw}' tidak memiliki izin mengelola master data ruangan.",
        )
    return session


async def register_active_websocket(session_id: str, websocket: WebSocket) -> None:
    """Catat WebSocket yang terhubung ke sesi aktif."""
    async with _ws_lock:
        if session_id not in _active_ws_connections:
            _active_ws_connections[session_id] = set()
        _active_ws_connections[session_id].add(websocket)


async def unregister_active_websocket(session_id: str, websocket: WebSocket) -> None:
    """Hapus WebSocket dari registry saat disconnect."""
    async with _ws_lock:
        if session_id in _active_ws_connections:
            _active_ws_connections[session_id].discard(websocket)
            if not _active_ws_connections[session_id]:
                _active_ws_connections.pop(session_id, None)


async def ws_revocation_handler(session_id: str) -> None:
    """Handler pencabutan WebSocket saat logout atau sesi kedaluwarsa.

    Memutus koneksi WebSocket klien secara seketika walaupun koneksi dalam keadaan diam.
    """
    async with _ws_lock:
        sockets = _active_ws_connections.pop(session_id, set())

    for ws in sockets:
        try:
            await ws.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Sesi telah dicabut atau batas waktu 8 jam telah berakhir.",
            )
            logger.info("WebSocket klien untuk sesi %s berhasil diputus (sesi berakhir/logout).", session_id[:8])
        except Exception as exc:
            logger.debug("Error saat menutup WebSocket sesi %s: %s", session_id[:8], exc)


async def session_expiry_monitor_loop(
    session_store: SessionStore,
    interval_seconds: float = 1.0,
) -> None:
    """Loop pemantau kedaluwarsa sesi secara berkala di latar belakang.

    Menjamin bahwa WebSocket idle tertutup seketika saat batas 8 jam tercapai,
    tanpa harus menunggu request HTTP baru masuk.
    """
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            await session_store.clean_expired_sessions()
    except asyncio.CancelledError:
        logger.info("Pemantau kedaluwarsa sesi dihentikan secara bersih.")
        raise


def start_session_expiry_monitor(
    session_store: SessionStore,
    interval_seconds: float = 1.0,
) -> asyncio.Task:
    """Mulai tugas latar belakang pemantau kedaluwarsa sesi."""
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(
            session_expiry_monitor_loop(session_store, interval_seconds)
        )
    return _monitor_task


def is_session_expiry_monitor_running() -> bool:
    """Periksa apakah tugas pemantau kedaluwarsa sesi sedang aktif."""
    global _monitor_task
    return _monitor_task is not None and not _monitor_task.done()


async def stop_session_expiry_monitor() -> None:
    """Hentikan tugas pemantau kedaluwarsa sesi saat shutdown."""
    global _monitor_task
    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
        _monitor_task = None

    # Tutup seluruh WebSocket yang masih tersisa saat shutdown
    async with _ws_lock:
        all_sockets = [ws for s_set in _active_ws_connections.values() for ws in s_set]
        _active_ws_connections.clear()

    for ws in all_sockets:
        try:
            await ws.close(code=status.WS_1001_GOING_AWAY, reason="Server shutdown.")
        except Exception:
            pass
