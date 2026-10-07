from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.pengiriman_temp import PengirimanTemp
from app.services.supabase_service import SupabaseService
from app.services.sync_service import SyncService

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status")
def sync_status(db: Session = Depends(get_db)) -> dict[str, object]:
    """Status sinkronisasi saat ini."""
    pending = db.query(PengirimanTemp).count()
    service = SupabaseService()
    if service.is_configured():
        return {"status": "ready", "message": "Sinkronisasi siap ke Supabase", "pending_buffer": pending, "supabase": "connected"}
    return {"status": "offline", "message": "Supabase belum dikonfigurasi, mode buffer lokal aktif", "pending_buffer": pending, "supabase": "not_configured"}


@router.post("/manual")
def manual_sync() -> dict[str, str]:
    """Endpoint manual untuk memulai sinkronisasi."""
    service = SyncService()
    return service.sync_now()


@router.get("/buffer")
def list_buffer(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    """Lihat data yang tertahan di buffer offline."""
    rows = db.query(PengirimanTemp).all()
    return [{"id": row.id, "payload": row.payload, "status": row.status, "created_at": row.created_at} for row in rows]
