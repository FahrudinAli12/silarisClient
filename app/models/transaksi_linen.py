from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TransaksiLinen(Base):
    """Header transaksi linen: masuk, keluar, laundry, dll."""

    __tablename__ = "transaksi_linen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    jenis_transaksi: Mapped[str] = mapped_column(String(30), nullable=False)
    kode_transaksi: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    petugas: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ruangan: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_linen: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="selesai")
    catatan: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reader_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    port_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
