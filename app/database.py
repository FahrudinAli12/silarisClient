from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import declarative_base, sessionmaker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("client_rumah_sakit")

DEFAULT_OLD_DB_PATH = (Path(__file__).resolve().parent.parent / "Client_RumahSakit_Template.db").resolve()
DEFAULT_NEW_DB_PATH = (Path(__file__).resolve().parent.parent / "Client_RumahSakit_Production.db").resolve()

DB_PATH = DEFAULT_NEW_DB_PATH
SQLITE_DB_PATH = str(DEFAULT_NEW_DB_PATH)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{SQLITE_DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def get_effective_sqlite_path(eng=None) -> Optional[Path]:
    """Ambil path file SQLite efektif dari engine yang aktif tanpa mencetak kredensial rahasia."""
    target_engine = eng or engine
    url = target_engine.url
    if url.get_backend_name() == "sqlite" and url.database:
        if url.database == ":memory:":
            return None
        return Path(url.database).resolve()
    return None

class DatabaseMigrationRequiredError(RuntimeError):
    """Exception yang dimunculkan jika skema database fisik tidak sesuai dengan model dan memerlukan migrasi."""
    pass


def check_schema_compatibility(eng=None) -> tuple[bool, list[str]]:
    """Periksa apakah tabel dan kolom yang dibutuhkan sudah sesuai skema saat ini.

    Catatan: Pemeriksaan mencakup keberadaan tabel dan kolom yang didefinisikan model,
    bukan validasi menyeluruh terhadap tipe data, constraint, atau indeks fisik.

    Mengembalikan tuple (is_compatible, list_of_issues).
    """
    target_engine = eng or engine
    issues: list[str] = []
    try:
        inspector = inspect(target_engine)
        existing_tables = set(inspector.get_table_names())

        for table in Base.metadata.tables.values():
            if table.name not in existing_tables:
                issues.append(f"Tabel '{table.name}' belum ada di database")
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
            expected_columns = {column.name for column in table.columns}
            missing_columns = expected_columns - existing_columns
            if missing_columns:
                issues.append(
                    f"Tabel '{table.name}' kehilangan kolom yang dibutuhkan: {sorted(list(missing_columns))}"
                )
        return (len(issues) == 0, issues)
    except Exception as exc:
        logger.warning("Gagal melakukan verifikasi skema: %s", exc)
        return (False, [f"Error saat inspeksi skema: {exc}"])


def _schema_is_current(eng=None) -> bool:
    """Periksa apakah tabel dan kolom yang dibutuhkan sudah sesuai skema saat ini."""
    is_compatible, _ = check_schema_compatibility(eng)
    return is_compatible


def init_db(engine_override=None) -> None:
    """Inisialisasi database lokal secara aman tanpa menghapus data.

    1. Mendaftarkan seluruh model ORM ke Base.metadata.
    2. Memeriksa keberadaan tabel fisik sebelum mengeksekusi DDL.
    3. Jika database baru/kosong (belum memiliki tabel pengguna), izinkan create_all().
    4. Jika database sudah memiliki tabel:
       - Periksa kelengkapan tabel dan kolom terlebih dahulu.
       - Jika tidak kompatibel, lempar DatabaseMigrationRequiredError tanpa menjalankan create_all() atau DDL lain.
       - Jika lengkap dan kompatibel, lanjutkan tanpa perubahan skema.
    5. Jika inspeksi database gagal, hentikan startup seketika (jangan menganggap database kosong).
    """
    eng = engine_override or engine

    # 1. Daftarkan seluruh model ke Base.metadata
    from app.models.linen import Linen
    from app.models.ruangan import Ruangan
    from app.models.transaksi_linen import TransaksiLinen
    from app.models.detail_transaksi_linen import DetailTransaksiLinen
    from app.models.riwayat_linen import RiwayatLinen
    from app.models.reader_device import ReaderDevice
    from app.models.pengiriman_temp import PengirimanTemp
    from app.models.rumah_sakit import RumahSakit

    # 2. Inspeksi keberadaan tabel sebelum eksekusi DDL apapun
    try:
        inspector = inspect(eng)
        existing_tables = set(inspector.get_table_names())
    except Exception as exc:
        error_msg = f"Gagal melakukan inspeksi database: {exc}. Startup dihentikan untuk mencegah kerusakan skema."
        logger.critical(error_msg)
        raise RuntimeError(error_msg) from exc

    # Filter tabel internal SQLite seperti sqlite_sequence
    user_tables = {t for t in existing_tables if not t.startswith("sqlite_")}

    # 3. Jika database benar-benar kosong, izinkan create_all untuk inisialisasi baru
    if len(user_tables) == 0:
        Base.metadata.create_all(bind=eng)
        is_compatible, issues = check_schema_compatibility(eng)
        if not is_compatible:
            error_msg = f"Inisialisasi database baru gagal membentuk skema yang sesuai: {'; '.join(issues)}."
            logger.critical(error_msg)
            raise DatabaseMigrationRequiredError(error_msg)
        logger.info("Inisialisasi database baru berhasil. Seluruh tabel terbentuk.")
        return

    # 4. Jika database sudah memiliki tabel, periksa kelengkapan TERLEBIH DAHULU sebelum DDL
    is_compatible, issues = check_schema_compatibility(eng)
    if not is_compatible:
        error_msg = (
            "Skema database lokal tidak kompatibel dengan model aplikasi saat ini. "
            f"Masalah terdeteksi: {'; '.join(issues)}. "
            "Migrasi database diperlukan sebelum aplikasi dapat dijalankan. "
            "Eksekusi DDL otomatis (create_all/drop_all) dibatalkan demi melindungi integritas data."
        )
        logger.critical(error_msg)
        raise DatabaseMigrationRequiredError(error_msg)

    # 5. Jika lengkap dan kompatibel, lanjutkan tanpa perubahan skema
    logger.info("Verifikasi skema database lokal berhasil. Skema kompatibel, tidak ada perubahan skema yang dilakukan.")


def get_db():
    """Generator sesi database untuk dependency injection."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
