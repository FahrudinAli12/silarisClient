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
        """Pemeriksaan antrean lokal di latar belakang.

        PERHATIAN KEAMANAN TAHAP 3B:
        Pengiriman cloud otomatis dari worker lama dinonaktifkan pada tahap ini sampai mekanisme
        otorisasi worker berbasis cloud yang jelas tersedia. Antrean lokal tetap dipertahankan aman di database
        tanpa memilih akun pengguna atau sesi secara sembarang.
        """
        db: Session = SessionLocal()
        try:
            pending_count = db.query(PengirimanTemp).filter(PengirimanTemp.status == "pending").count()
            if pending_count > 0:
                logger.debug(
                    "Worker sync: %d antrean lokal tersimpan. Pengiriman otomatis ke cloud ditangguhkan.",
                    pending_count,
                )
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
        """Proses sinkronisasi manual ke antrean lokal."""
        payload = {"event": "manual_sync", "timestamp": time.time()}
        self.enqueue_for_sync(payload)
        return {
            "status": "queued",
            "message": "Sinkronisasi diminta. Data tersimpan aman di antrean buffer lokal.",
        }

