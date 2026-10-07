from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers.master_router import router as master_router
from app.routers.transaksi_router import router as transaksi_router
from app.routers.sync_router import router as sync_router
from app.routers.setting_router import router as setting_router
from app.services.rfid_service import RFIDService
from app.services.sync_service import SyncService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("client_rumah_sakit")

app = FastAPI(title="Sistem Informasi Client Rumah Sakit - Tracking Linen RFID")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(master_router, prefix="/api")
app.include_router(transaksi_router, prefix="/api")
app.include_router(sync_router, prefix="/api")
app.include_router(setting_router, prefix="/api")

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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return render_template("dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page() -> HTMLResponse:
    return render_template("dashboard")


@app.get("/master-data", response_class=HTMLResponse)
async def master_page() -> HTMLResponse:
    return render_template("master_data")


@app.get("/master-data/linen", response_class=HTMLResponse)
async def master_linen_page() -> HTMLResponse:
    return render_template("data_linen")


@app.get("/master-data/ruangan", response_class=HTMLResponse)
async def master_ruangan_page() -> HTMLResponse:
    return render_template("data_ruangan")


@app.get("/laundry", response_class=HTMLResponse)
async def laundry_page() -> HTMLResponse:
    return render_template("laundry")


@app.get("/history")
async def history_page() -> RedirectResponse:
    return RedirectResponse(url="/laundry?tab=history")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page() -> HTMLResponse:
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


from fastapi.responses import JSONResponse

@app.get("/api/rfid/status")
def get_rfid_status() -> JSONResponse:
    """Status reader RFID dan daftar port COM yang tersedia."""
    content = app.state.rfid_service.get_status()
    return JSONResponse(content=content, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.post("/api/rfid/port")
async def change_rfid_port(request: Request) -> dict[str, str]:
    """Ganti port COM RFID secara dinamis."""
    body = await request.json()
    new_port = body.get("port")
    if not new_port:
        return {"status": "error", "message": "Port tidak valid"}
    import asyncio
    await asyncio.to_thread(app.state.rfid_service.change_port, new_port)
    return {"status": "ok", "port": new_port}


@app.post("/api/rfid/scan/start")
async def start_rfid_scan(request: Request) -> dict[str, object]:
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
async def stop_rfid_scan() -> dict[str, str]:
    try:
        app.state.rfid_service.stop_inventory()
        return {"status": "ok", "message": "Scan RFID dihentikan"}
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}


@app.websocket("/ws/epc-live")
async def websocket_epc_live(websocket: WebSocket) -> None:
    await websocket.accept()
    app.state.rfid_service.register_client(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        app.state.rfid_service.unregister_client(websocket)
        logger.info("WebSocket client disconnected")


@app.on_event("startup")
async def startup_event() -> None:
    init_db()
    logger.info("Database initialized")
    app.state.rfid_service.start()
    app.state.sync_service.start()
    logger.info("Background services started")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    app.state.rfid_service.stop()
    app.state.sync_service.stop()
    logger.info("Background services stopped")
