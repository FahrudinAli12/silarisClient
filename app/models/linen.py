from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Linen(Base):
    """Master data linen yang dipantau dengan RFID."""

    __tablename__ = "linen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    epc: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    kategori: Mapped[str] = mapped_column(String(50), nullable=False)
    nama_linen: Mapped[str] = mapped_column(String(100), nullable=False)
    lokasi: Mapped[str] = mapped_column(String(100), default="Storage")
    total_cuci: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="tersedia")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # TODO: tambahkan relasi ke transaksi/riwayat pada tahap pengembangan lanjutan.
