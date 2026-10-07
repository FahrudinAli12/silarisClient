from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RiwayatLinen(Base):
    """Riwayat pergerakan linen dari waktu ke waktu."""

    __tablename__ = "riwayat_linen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    linen_id: Mapped[int] = mapped_column(ForeignKey("linen.id"), nullable=False)
    transaksi_id: Mapped[int | None] = mapped_column(ForeignKey("transaksi_linen.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    lokasi_sebelumnya: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lokasi_sesudah: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
