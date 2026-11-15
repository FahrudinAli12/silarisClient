from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.linen import Linen
from app.models.ruangan import Ruangan
from app.models.reader_device import ReaderDevice
from app.models.transaksi_linen import TransaksiLinen
from app.schemas.linen_schema import LinenCreate, LinenUpdate, LinenResponse
from app.schemas.ruangan_schema import RuanganCreate, RuanganUpdate, RuanganResponse
from app.services.auth_dependencies import require_active_session, require_master_data_editor
from app.services.session_store import SessionData

router = APIRouter(
    prefix="/master",
    tags=["master"],
    dependencies=[Depends(require_active_session)],
)


@router.get("/linen", response_model=list[LinenResponse])
def list_linen(q: str | None = None, status: str | None = None, db: Session = Depends(get_db)) -> list[Linen]:
    """Daftar master linen dengan pencarian dan filter status (tersedia/dicuci/dipakai)."""
    query = db.query(Linen)
    if status:
        query = query.filter(Linen.status == status)
    if q:
        search = f"%{q.lower()}%"
        query = query.filter(
            (Linen.epc.ilike(search))
            | (Linen.nama_linen.ilike(search))
            | (Linen.kategori.ilike(search))
            | (Linen.lokasi.ilike(search))
        )
    return query.order_by(Linen.created_at.desc()).all()


@router.post("/linen", response_model=LinenResponse, status_code=status.HTTP_201_CREATED)
def create_linen(payload: LinenCreate, db: Session = Depends(get_db)) -> Linen:
    """Buat data linen baru."""
    existing = db.query(Linen).filter(Linen.epc == payload.epc).first()
    if existing:
        raise HTTPException(status_code=400, detail="EPC sudah terdaftar")
    linen = Linen(**payload.model_dump())
    db.add(linen)
    db.commit()
    db.refresh(linen)
    return linen


@router.get("/linen/{linen_id}", response_model=LinenResponse)
def get_linen(linen_id: int, db: Session = Depends(get_db)) -> Linen:
    linen = db.query(Linen).filter(Linen.id == linen_id).first()
    if linen is None:
        raise HTTPException(status_code=404, detail="Linen tidak ditemukan")
    return linen


@router.put("/linen/{linen_id}", response_model=LinenResponse)
def update_linen(linen_id: int, payload: LinenUpdate, epc: str | None = None, db: Session = Depends(get_db)) -> Linen:
    linen = db.query(Linen).filter(Linen.id == linen_id).first()
    if not linen and epc:
        linen = db.query(Linen).filter(Linen.epc == epc).first()
    if linen is None:
        raise HTTPException(status_code=404, detail="Linen tidak ditemukan")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "lokasi":
            setattr(linen, key, value)
        elif value is not None and value != "":
            setattr(linen, key, value)
    db.commit()
    db.refresh(linen)
    return linen


@router.delete("/linen/{linen_id}", response_model=dict[str, str])
def delete_linen(linen_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    linen = db.query(Linen).filter(Linen.id == linen_id).first()
    if linen is None:
        raise HTTPException(status_code=404, detail="Linen tidak ditemukan")
    db.delete(linen)
    db.commit()
    return {"status": "deleted"}


@router.get("/ruangan", response_model=list[RuanganResponse])
def list_ruangan(
    q: str | None = None,
    is_active: bool | None = None,
    session: SessionData = Depends(require_active_session),
    db: Session = Depends(get_db),
) -> list[Ruangan]:
    """Daftar master ruangan dengan pencarian sederhana dan filter status aktif.

    Akses:
    - Seluruh pengguna terautentikasi (Admin, Supervisor, Operator) dapat membaca.
    - Parameter is_active:
      - None: Kembalikan semua ruangan (aktif & nonaktif) untuk kebutuhan manajemen.
      - True: Hanya kembalikan ruangan aktif (untuk dropdown transaksi).
      - False: Hanya kembalikan ruangan nonaktif.
    """
    query = db.query(Ruangan)
    if is_active is not None:
        query = query.filter(Ruangan.is_active == is_active)
    if q:
        search = f"%{q.lower()}%"
        query = query.filter(
            (Ruangan.kode_ruangan.ilike(search))
            | (Ruangan.nama_ruangan.ilike(search))
            | (Ruangan.keterangan.ilike(search))
        )
    return query.order_by(Ruangan.id.desc()).all()


@router.post("/ruangan", response_model=RuanganResponse, status_code=status.HTTP_201_CREATED)
def create_ruangan(
    payload: RuanganCreate,
    session: SessionData = Depends(require_master_data_editor),
    db: Session = Depends(get_db),
) -> Ruangan:
    """Buat data ruangan baru. Wajib role Admin atau Supervisor."""
    existing = db.query(Ruangan).filter(Ruangan.kode_ruangan == payload.kode_ruangan).first()
    if existing:
        raise HTTPException(status_code=400, detail="Kode ruangan sudah ada")
    ruangan = Ruangan(**payload.model_dump())
    db.add(ruangan)
    db.commit()
    db.refresh(ruangan)
    return ruangan


@router.put("/ruangan/{ruangan_id}", response_model=RuanganResponse)
def update_ruangan(
    ruangan_id: int,
    payload: RuanganUpdate,
    session: SessionData = Depends(require_master_data_editor),
    db: Session = Depends(get_db),
) -> Ruangan:
    """Perbarui data ruangan (kode, nama, keterangan, is_active). Wajib role Admin atau Supervisor."""
    ruangan = db.query(Ruangan).filter(Ruangan.id == ruangan_id).first()
    if ruangan is None:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan")

    update_dict = payload.model_dump(exclude_unset=True)

    if "kode_ruangan" in update_dict and update_dict["kode_ruangan"]:
        new_kode = update_dict["kode_ruangan"].strip()
        if new_kode != ruangan.kode_ruangan:
            existing = db.query(Ruangan).filter(
                Ruangan.kode_ruangan == new_kode,
                Ruangan.id != ruangan_id,
            ).first()
            if existing:
                raise HTTPException(status_code=400, detail="Kode ruangan sudah digunakan oleh ruangan lain")
            ruangan.kode_ruangan = new_kode

    if "nama_ruangan" in update_dict and update_dict["nama_ruangan"]:
        ruangan.nama_ruangan = update_dict["nama_ruangan"].strip()

    if "keterangan" in update_dict:
        ruangan.keterangan = update_dict["keterangan"]

    if "is_active" in update_dict and update_dict["is_active"] is not None:
        ruangan.is_active = bool(update_dict["is_active"])

    db.commit()
    db.refresh(ruangan)
    return ruangan


@router.delete("/ruangan/{ruangan_id}", response_model=dict[str, Any])
def delete_ruangan(
    ruangan_id: int,
    session: SessionData = Depends(require_master_data_editor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Penonaktifan ruangan (Soft-Delete Enforcement). Wajib role Admin atau Supervisor.

    Kebijakan Integritas:
    - Tidak melakukan SQL DELETE fisik pada tabel ruangan dalam kondisi apapun.
    - Mengubah kolom is_active menjadi False.
    - Record master ruangan tetap abadi di SQLite demi menjaga integritas data riwayat
      TransaksiLinen dan Linen.lokasi seumur hidup sistem (bahkan jika ruangan pernah di-rename).
    - Mencegah orphaning jejak audit historis.
    """
    ruangan = db.query(Ruangan).filter(Ruangan.id == ruangan_id).first()
    if ruangan is None:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan")

    # Soft-delete enforcement: ubah is_active menjadi False tanpa SQL DELETE fisik
    ruangan.is_active = False
    db.commit()
    db.refresh(ruangan)
    return {
        "status": "deleted",
        "action": "deactivated",
        "message": f"Ruangan '{ruangan.nama_ruangan}' berhasil dinonaktifkan (soft-delete). Record master tetap tersimpan untuk integritas riwayat transaksi.",
        "id": ruangan.id,
        "is_active": ruangan.is_active,
    }


@router.get("/reader", response_model=list[dict])
def list_reader(db: Session = Depends(get_db)) -> list[dict]:
    """Daftar reader device yang terdaftar."""
    readers = db.query(ReaderDevice).all()
    return [{"id": item.id, "nama_reader": item.nama_reader, "port": item.port, "status": item.status} for item in readers]

