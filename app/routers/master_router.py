from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.linen import Linen
from app.models.ruangan import Ruangan
from app.models.reader_device import ReaderDevice
from app.models.transaksi_linen import TransaksiLinen
from app.schemas.linen_schema import LinenCreate, LinenUpdate, LinenResponse
from app.schemas.ruangan_schema import RuanganCreate, RuanganUpdate, RuanganResponse

router = APIRouter(prefix="/master", tags=["master"])


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
def list_ruangan(q: str | None = None, db: Session = Depends(get_db)) -> list[Ruangan]:
    """Daftar master ruangan dengan pencarian sederhana."""
    query = db.query(Ruangan)
    if q:
        search = f"%{q.lower()}%"
        query = query.filter(
            (Ruangan.kode_ruangan.ilike(search))
            | (Ruangan.nama_ruangan.ilike(search))
            | (Ruangan.keterangan.ilike(search))
        )
    return query.order_by(Ruangan.id.desc()).all()


@router.post("/ruangan", response_model=RuanganResponse, status_code=status.HTTP_201_CREATED)
def create_ruangan(payload: RuanganCreate, db: Session = Depends(get_db)) -> Ruangan:
    """Buat data ruangan baru."""
    existing = db.query(Ruangan).filter(Ruangan.kode_ruangan == payload.kode_ruangan).first()
    if existing:
        raise HTTPException(status_code=400, detail="Kode ruangan sudah ada")
    ruangan = Ruangan(**payload.model_dump())
    db.add(ruangan)
    db.commit()
    db.refresh(ruangan)
    return ruangan


@router.put("/ruangan/{ruangan_id}", response_model=RuanganResponse)
def update_ruangan(ruangan_id: int, payload: RuanganUpdate, db: Session = Depends(get_db)) -> Ruangan:
    ruangan = db.query(Ruangan).filter(Ruangan.id == ruangan_id).first()
    if ruangan is None:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "keterangan":
            setattr(ruangan, key, value)
        elif value is not None and value != "":
            setattr(ruangan, key, value)
    db.commit()
    db.refresh(ruangan)
    return ruangan


@router.delete("/ruangan/{ruangan_id}", response_model=dict[str, str])
def delete_ruangan(ruangan_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    ruangan = db.query(Ruangan).filter(Ruangan.id == ruangan_id).first()
    if ruangan is None:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan")
        
    # Validasi integritas referensial
    linen_terkait = db.query(Linen).filter(Linen.lokasi == ruangan.nama_ruangan).first()
    if linen_terkait:
        raise HTTPException(status_code=400, detail=f"Ruangan ini sedang dipakai sebagai lokasi linen ({linen_terkait.epc}). Hapus atau pindahkan linen tersebut terlebih dahulu.")
        
    transaksi_terkait = db.query(TransaksiLinen).filter(TransaksiLinen.ruangan == ruangan.nama_ruangan).first()
    if transaksi_terkait:
        raise HTTPException(status_code=400, detail=f"Ruangan ini terdapat pada riwayat transaksi ({transaksi_terkait.kode_transaksi}). Penghapusan ditolak untuk menjaga integritas history.")

    db.delete(ruangan)
    db.commit()
    return {"status": "deleted"}


@router.get("/reader", response_model=list[dict])
def list_reader(db: Session = Depends(get_db)) -> list[dict]:
    """Daftar reader device yang terdaftar."""
    readers = db.query(ReaderDevice).all()
    return [{"id": item.id, "nama_reader": item.nama_reader, "port": item.port, "status": item.status} for item in readers]

