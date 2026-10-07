from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PengirimanTemp(Base):
    """Buffer offline untuk data yang belum berhasil dikirim ke cloud."""

    __tablename__ = "pengiriman_temp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    payload: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # TODO: tambahkan retry count, last_error, dan metadata sinkronisasi.
