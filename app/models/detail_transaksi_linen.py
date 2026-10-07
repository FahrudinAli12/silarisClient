from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DetailTransaksiLinen(Base):
    """Detail item linen per transaksi."""

    __tablename__ = "detail_transaksi_linen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    transaksi_id: Mapped[int] = mapped_column(ForeignKey("transaksi_linen.id"), nullable=False)
    linen_id: Mapped[int] = mapped_column(ForeignKey("linen.id"), nullable=False)
    epc: Mapped[str] = mapped_column(String(64), nullable=False)
    status_sebelum: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status_setelah: Mapped[str | None] = mapped_column(String(50), nullable=True)
    waktu_scan: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    keterangan: Mapped[str | None] = mapped_column(String(255), nullable=True)

