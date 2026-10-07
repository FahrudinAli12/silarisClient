from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import declarative_base, sessionmaker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("client_rumah_sakit")

DB_PATH = Path(__file__).resolve().parent.parent / "Client_RumahSakit_Template.db"
SQLITE_DB_PATH = str(DB_PATH)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{SQLITE_DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def _schema_is_current() -> bool:
    """Periksa apakah tabel dan kolom yang dibutuhkan sudah sesuai skema saat ini."""
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())

        for table in Base.metadata.tables.values():
            if table.name not in tables:
                return False
            existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
            expected_columns = {column.name for column in table.columns}
            if not expected_columns.issubset(existing_columns):
                return False
        return True
    except Exception as exc:
        logger.warning("Gagal melakukan verifikasi skema: %s", exc)
        return False


def init_db() -> None:
    """Buat semua tabel, lalu perbarui skema jika ada ketidaksesuaian."""
    from app.models.linen import Linen
    from app.models.ruangan import Ruangan
    from app.models.transaksi_linen import TransaksiLinen
    from app.models.detail_transaksi_linen import DetailTransaksiLinen
    from app.models.riwayat_linen import RiwayatLinen
    from app.models.reader_device import ReaderDevice
    from app.models.pengiriman_temp import PengirimanTemp
    from app.models.rumah_sakit import RumahSakit

    if not _schema_is_current():
        logger.warning("Skema database tidak sesuai, membuat ulang tabel lokal")
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def get_db():
    """Generator sesi database untuk dependency injection."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
