from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.rumah_sakit import RumahSakit

logger = logging.getLogger("hospital_binding")


class HospitalBindingError(RuntimeError):
    """Base exception untuk masalah pengikatan rumah sakit lokal."""
    pass


class HospitalBindingMissingError(HospitalBindingError):
    """Database lokal belum terikat ke rumah sakit manapun."""
    pass


class HospitalBindingAmbiguousError(HospitalBindingError):
    """Database lokal memiliki lebih dari satu data rumah sakit (inkonsistensi data)."""
    pass


class HospitalBindingMismatchError(HospitalBindingError):
    """Rumah sakit yang terikat pada database lokal berbeda dari konfigurasi instalasi."""
    pass


def get_bound_hospital(db: Session) -> Optional[RumahSakit]:
    """Ambil data rumah sakit yang terikat pada database lokal saat ini.

    Aturan:
    - Jika tabel kosong (0 baris), kembalikan None.
    - Jika terdapat tepat 1 baris, kembalikan objek RumahSakit.
    - Jika terdapat > 1 baris, lempar HospitalBindingAmbiguousError karena instalasi
      ClientS secara tegas hanya boleh terikat ke tepat satu rumah sakit.
    """
    rows = db.query(RumahSakit).all()
    if len(rows) == 0:
        return None
    if len(rows) > 1:
        raise HospitalBindingAmbiguousError(
            f"Inkonsistensi database: ditemukan {len(rows)} data rumah sakit pada database lokal. "
            "Satu instalasi ClientS hanya boleh terikat ke tepat satu rumah sakit."
        )
    return rows[0]


def verify_hospital_binding(db: Session, expected_hospital_id: Optional[int]) -> RumahSakit:
    """Verifikasi bahwa database lokal telah terikat dan cocok dengan konfigurasi instalasi.

    Aturan:
    - Jika database lokal belum terikat (0 baris), lempar HospitalBindingMissingError.
    - Jika expected_hospital_id disediakan dan berbeda dari ID rumah sakit lokal,
      lempar HospitalBindingMismatchError.
    - Mengembalikan objek RumahSakit yang terverifikasi.
    """
    bound_rs = get_bound_hospital(db)
    if bound_rs is None:
        raise HospitalBindingMissingError(
            "Database lokal belum terikat ke rumah sakit manapun. "
            "Lakukan provisioning pengikatan rumah sakit terlebih dahulu."
        )

    if expected_hospital_id is not None:
        try:
            expected_id_int = int(expected_hospital_id)
        except (ValueError, TypeError) as exc:
            raise HospitalBindingMismatchError(
                f"Konfigurasi hospital_id instalasi ({expected_hospital_id!r}) tidak valid."
            ) from exc

        if int(bound_rs.id) != expected_id_int:
            raise HospitalBindingMismatchError(
                f"Ketidakcocokan identitas rumah sakit: database lokal terikat ke RS ID {bound_rs.id} "
                f"('{bound_rs.nama}'), tetapi konfigurasi instalasi aktif adalah RS ID {expected_id_int}. "
                "Akses ditolak untuk mencegah kontaminasi data antar-rumah sakit."
            )

    return bound_rs


def provision_hospital_binding(
    db: Session,
    hospital_id: int,
    nama_rs: str,
    kode_rs: str,
    alamat: Optional[str] = None,
) -> RumahSakit:
    """Ikat database lokal ke satu rumah sakit secara eksplisit (Provisioning).

    Aturan:
    - ID rumah sakit lokal HARUS menggunakan ID rumah sakit resmi dari cloud (BIGINT).
    - Tidak boleh dilakukan otomatis dari login akun pertama.
    - Menolak jika database lokal sudah terikat ke rumah sakit lain.
    """
    if hospital_id <= 0:
        raise HospitalBindingError("hospital_id harus berupa bilangan bulat positif (> 0).")

    clean_nama = (nama_rs or "").strip()
    clean_kode = (kode_rs or "").strip()
    if not clean_nama:
        raise HospitalBindingError("Nama rumah sakit tidak boleh kosong.")
    if not clean_kode:
        raise HospitalBindingError("Kode rumah sakit tidak boleh kosong.")

    existing = get_bound_hospital(db)
    if existing is not None:
        if int(existing.id) == int(hospital_id):
            # Sudah terikat ke RS yang sama, update nama/kode jika berubah
            existing.nama = clean_nama
            existing.kode = clean_kode
            if alamat is not None:
                existing.alamat = alamat.strip()
            db.commit()
            db.refresh(existing)
            logger.info("Informasi rumah sakit ID %d diperbarui: %s (%s)", existing.id, clean_nama, clean_kode)
            return existing
        raise HospitalBindingError(
            f"Database lokal sudah terikat ke RS ID {existing.id} ('{existing.nama}'). "
            f"Pengikatan ulang ke RS ID {hospital_id} ('{clean_nama}') ditolak."
        )

    new_rs = RumahSakit(
        id=int(hospital_id),
        nama=clean_nama,
        kode=clean_kode,
        alamat=alamat.strip() if alamat else None,
    )
    db.add(new_rs)
    db.commit()
    db.refresh(new_rs)
    logger.info("Database lokal berhasil diikat secara eksplisit ke RS ID %d: %s (%s)", new_rs.id, clean_nama, clean_kode)
    return new_rs
