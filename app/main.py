from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.database import SessionLocal, init_db
from app.routers.auth_router import (
    AuthRouterConfig,
    auth_router,
    get_default_config,
    get_default_session_store,
    register_ws_revocation_hook,
    set_default_config,
)
from app.routers.master_router import router as master_router
from app.routers.receiving_router import router as receiving_router
from app.routers.setting_router import router as setting_router
from app.routers.sync_router import router as sync_router
from app.routers.transaksi_router import router as transaksi_router
from app.services.auth_dependencies import (
    register_active_websocket,
    require_active_session,
    require_page_session,
    start_session_expiry_monitor,
    stop_session_expiry_monitor,
    unregister_active_websocket,
    ws_revocation_handler,
)
from app.services.hospital_binding_service import verify_hospital_binding
from app.services.rfid_service import RFIDService
from app.services.session_store import SessionData
from app.services.sync_service import SyncService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("client_rumah_sakit")

DEFAULT_ALLOWED_ORIGINS = [
    o.strip().rstrip("/")
    for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
    if o.strip()
]

auth_config = AuthRouterConfig(
    session_duration_seconds=28800,
    cookie_name="clients_session",
    cookie_secure=os.getenv("COOKIE_SECURE", "0") == "1",
    cookie_samesite="lax",
    cookie_path="/",
    allowed_origins=DEFAULT_ALLOWED_ORIGINS,
    require_custom_header=True,
    custom_header_name="X-ClientS-Request",
    custom_header_value="1",
)
set_default_config(auth_config)
register_ws_revocation_hook(ws_revocation_handler)

app = FastAPI(title="Sistem Informasi Client Rumah Sakit - Tracking Linen RFID")

# CORS terbatas pada origin yang diizinkan (tanpa wildcard credentials)
app.add_middleware(
    CORSMiddleware,
    allow_origins=auth_config.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_cache_headers(request: Request, call_next):
    """Pastikan data pengguna dan halaman terproteksi tidak dicache oleh browser."""
    response = await call_next(request)
    if not request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


# Pendaftaran Router
app.include_router(auth_router)
app.include_router(master_router, prefix="/api")
app.include_router(transaksi_router, prefix="/api")
app.include_router(sync_router, prefix="/api")
app.include_router(setting_router, prefix="/api")
app.include_router(receiving_router, prefix="/api")

app.state.rfid_service = RFIDService()
app.state.sync_service = SyncService()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def render_template(page_name: str) -> HTMLResponse:
    template_path = TEMPLATES_DIR / f"{page_name}.html"
    if not template_path.exists():
        return HTMLResponse(content=f"<h1>Halaman {page_name} belum tersedia</h1>", status_code=404)
    return HTMLResponse(content=template_path.read_text(encoding="utf-8"))


# =============================================================================
# RUTE HALAMAN ANTARMUKA (HTML)
# =============================================================================
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Any:
    """Halaman login akun rumah sakit. Jika sudah aktif, redirect ke dashboard."""
    session_id = request.cookies.get(auth_config.cookie_name)
    if session_id:
        store = get_default_session_store()
        session = await store.get_session(session_id)
        if session:
            return RedirectResponse(url="/dashboard", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    return render_template("login")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session: SessionData = Depends(require_page_session)) -> HTMLResponse:
    return render_template("dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(session: SessionData = Depends(require_page_session)) -> HTMLResponse:
    return render_template("dashboard")


@app.get("/master-data", response_class=HTMLResponse)
async def master_page(session: SessionData = Depends(require_page_session)) -> HTMLResponse:
    return render_template("master_data")


@app.get("/master-data/linen", response_class=HTMLResponse)
async def master_linen_page(session: SessionData = Depends(require_page_session)) -> HTMLResponse:
    return render_template("data_linen")


@app.get("/master-data/ruangan", response_class=HTMLResponse)
async def master_ruangan_page(session: SessionData = Depends(require_page_session)) -> HTMLResponse:
    return render_template("data_ruangan")


@app.get("/laundry", response_class=HTMLResponse)
async def laundry_page(session: SessionData = Depends(require_page_session)) -> HTMLResponse:
    return render_template("laundry")


@app.get("/penerimaan", response_class=HTMLResponse)
async def penerimaan_page(session: SessionData = Depends(require_page_session)) -> HTMLResponse:
    return render_template("penerimaan_paket")


@app.get("/history")
async def history_page(session: SessionData = Depends(require_page_session)) -> RedirectResponse:
    return RedirectResponse(url="/laundry?tab=history")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(session: SessionData = Depends(require_page_session)) -> HTMLResponse:
    return render_template("settings")


@app.get("/sw.js")
async def service_worker() -> Response:
    sw_path = STATIC_DIR / "sw.js"
    if not sw_path.exists():
        return Response(content="", status_code=404)
    return Response(content=sw_path.read_bytes(), media_type="application/javascript")


@app.get("/api/health")
def health_check() -> dict[str, str]:
    """Health check endpoint untuk mendeteksi status ketersediaan server."""
    return {"status": "ok"}


# =============================================================================
# ENDPOINT OPERASIONAL RFID (DILINDUNGI SESI)
# =============================================================================
@app.get("/api/rfid/status")
def get_rfid_status(session: SessionData = Depends(require_active_session)) -> JSONResponse:
    """Status reader RFID dan daftar port COM yang tersedia."""
    content = app.state.rfid_service.get_status()
    return JSONResponse(content=content, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.post("/api/rfid/port")
async def change_rfid_port(
    request: Request,
    session: SessionData = Depends(require_active_session),
) -> dict[str, str]:
    """Ganti port COM RFID secara dinamis."""
    body = await request.json()
    new_port = body.get("port")
    if not new_port:
        return {"status": "error", "message": "Port tidak valid"}
    await asyncio.to_thread(app.state.rfid_service.change_port, new_port)
    return {"status": "ok", "port": new_port}


@app.post("/api/rfid/scan/start")
async def start_rfid_scan(
    request: Request,
    session: SessionData = Depends(require_active_session),
) -> dict[str, object]:
    try:
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        mode = body.get("mode") or body.get("target") or "LINEN_MASUK"
        result = app.state.rfid_service.start_scan(mode)
        return {"status": "ok", "message": f"Active scan switched to {result.get('active_scanner_label')}", "data": result}
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}


@app.post("/api/rfid/scan/stop")
async def stop_rfid_scan(
    session: SessionData = Depends(require_active_session),
) -> dict[str, str]:
    try:
        app.state.rfid_service.stop_inventory()
        return {"status": "ok", "message": "Scan RFID dihentikan"}
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}


# =============================================================================
# WEBSOCKET STREAM EPC (DILINDUNGI SESI & ORIGIN)
# =============================================================================
@app.websocket("/ws/epc-live")
async def websocket_epc_live(websocket: WebSocket) -> None:
    # 1. Validasi Origin WebSocket
    origin = websocket.headers.get("origin")
    if origin:
        target_origin = origin.strip().rstrip("/")
        normalized_allowed = {o.strip().rstrip("/") for o in auth_config.allowed_origins if o.strip()}
        if target_origin not in normalized_allowed:
            logger.warning("Koneksi WebSocket ditolak: Origin %s tidak diizinkan", origin)
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Origin tidak diizinkan.")
            return

    # 2. Validasi Sesi Cookie WebSocket
    session_id = websocket.cookies.get(auth_config.cookie_name)
    if not session_id:
        logger.warning("Koneksi WebSocket ditolak: Cookie sesi tidak ditemukan")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Sesi tidak valid atau telah berakhir.")
        return

    session_store = get_default_session_store()
    session = await session_store.get_session(session_id)
    if not session:
        logger.warning("Koneksi WebSocket ditolak: Sesi %s tidak aktif atau kedaluwarsa", session_id[:8])
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Sesi tidak valid atau telah berakhir.")
        return

    # 3. Terima koneksi & daftarkan ke registry aktif
    await websocket.accept()
    await register_active_websocket(session.session_id, websocket)
    app.state.rfid_service.register_client(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected secara normal")
    except Exception as exc:
        logger.debug("WebSocket koneksi terputus: %s", exc)
    finally:
        app.state.rfid_service.unregister_client(websocket)
        await unregister_active_websocket(session.session_id, websocket)


# =============================================================================
# LIFECYCLE STARTUP & SHUTDOWN
# =============================================================================
@app.on_event("startup")
async def startup_event() -> None:
    init_db()
    logger.info("Database initialized")

    # Verifikasi pengikatan rumah sakit lokal jika konfigurasi cloud aktif
    expected_hospital_id = os.getenv("HOSPITAL_CLOUD_ID")
    if expected_hospital_id:
        try:
            with SessionLocal() as db_session:
                bound = verify_hospital_binding(db_session, int(expected_hospital_id))
                logger.info("Verifikasi binding RS sukses: %s (ID %d)", bound.nama, bound.id)
        except Exception as exc:
            logger.critical("VERIFIKASI BINDING RUMAH SAKIT GAGAL: %s", exc)
            raise

    # Jalankan pemantau kedaluwarsa sesi (interval 1.0 detik)
    start_session_expiry_monitor(get_default_session_store(), interval_seconds=1.0)

    # Jalankan background services jika tidak dinonaktifkan dalam mode testing terisolasi
    if os.getenv("DISABLE_BACKGROUND_SERVICES_FOR_TESTS", "0") != "1":
        app.state.rfid_service.start()
        app.state.sync_service.start()
        logger.info("Background services started")
    else:
        logger.info("Background services disabled for isolated test environment")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await stop_session_expiry_monitor()
    if os.getenv("DISABLE_BACKGROUND_SERVICES_FOR_TESTS", "0") != "1":
        app.state.rfid_service.stop()
        app.state.sync_service.stop()
        logger.info("Background services stopped")
