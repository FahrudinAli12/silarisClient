from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.pengiriman_temp import PengirimanTemp
from app.services.supabase_service import SupabaseService

logger = logging.getLogger("sync_service")


class SyncService:
    """Sinkronisasi data lokal ke cloud dan buffer offline sederhana."""

    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Sync service started")

    def stop(self) -> None:
        self._running = False
        logger.info("Sync service stopped")

    def _run_loop(self) -> None:
        while self._running:
            self._drain_pending()
            time.sleep(5)

    def _drain_pending(self) -> None:
        db: Session = SessionLocal()
        try:
            rows = db.query(PengirimanTemp).filter(PengirimanTemp.status == "pending").all()
            for row in rows:
                payload = json.loads(row.payload)
                logger.info("Sinkronisasi payload %s", payload)
                try:
                    service = SupabaseService()
                    if service.is_configured():
                        service.push_record("sync_events", payload)
                        row.status = "sent"
                    else:
                        row.status = "pending"
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    logger.warning("Sinkronisasi gagal: %s", exc)
                    row.status = "pending"
            db.commit()
        finally:
            db.close()

    def enqueue_for_sync(self, payload: dict[str, Any]) -> None:
        """Simpan payload ke buffer offline saat sinkronisasi gagal."""
        db: Session = SessionLocal()
        try:
            row = PengirimanTemp(payload=json.dumps(payload), status="pending")
            db.add(row)
            db.commit()
        finally:
            db.close()
        logger.info("Payload masuk buffer offline: %s", payload)

    def sync_now(self) -> dict[str, Any]:
        """Proses sinkronisasi manual."""
        payload = {"event": "manual_sync", "timestamp": time.time()}
        self.enqueue_for_sync(payload)
        try:
            service = SupabaseService()
            if service.is_configured():
                service.push_record("sync_events", payload)
                return {"status": "sent", "message": "Sinkronisasi berhasil ke Supabase"}
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.warning("Sinkronisasi manual gagal: %s", exc)
        return {"status": "queued", "message": "Sinkronisasi diminta, akan diproses via buffer lokal"}
