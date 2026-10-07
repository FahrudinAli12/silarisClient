from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Ruangan(Base):
    """Master lokasi ruangan tempat linen didistribusikan."""

    __tablename__ = "ruangan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    kode_ruangan: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    nama_ruangan: Mapped[str] = mapped_column(String(100), nullable=False)
    keterangan: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
