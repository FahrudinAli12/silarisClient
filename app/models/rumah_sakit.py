from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RumahSakit(Base):
    """Informasi rumah sakit / instansi terkait client."""

    __tablename__ = "rumah_sakit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nama: Mapped[str] = mapped_column(String(150), nullable=False)
    kode: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    alamat: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
