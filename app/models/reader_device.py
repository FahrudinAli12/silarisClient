from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReaderDevice(Base):
    """Konfigurasi reader RFID yang terhubung ke client."""

    __tablename__ = "reader_device"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nama_reader: Mapped[str] = mapped_column(String(100), nullable=False)
    port: Mapped[str] = mapped_column(String(50), nullable=False)
    baudrate: Mapped[int] = mapped_column(Integer, default=115200)
    status: Mapped[str] = mapped_column(String(30), default="disconnected")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
