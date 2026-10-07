import time
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.detail_transaksi_linen import DetailTransaksiLinen
from app.models.linen import Linen
from app.models.riwayat_linen import RiwayatLinen
from app.models.transaksi_linen import TransaksiLinen
from app.schemas.detail_transaksi_schema import DetailTransaksiCreate, DetailTransaksiResponse
from app.schemas.riwayat_schema import RiwayatCreate, RiwayatResponse
from app.schemas.transaksi_schema import BatchTransaksiCreate, TransaksiCreate, TransaksiResponse
from app.services.sync_service import SyncService

router = APIRouter(prefix="/transaksi", tags=["transaksi"])


@router.get("", response_model=list[TransaksiResponse])
def list_transaksi(db: Session = Depends(get_db)) -> list[TransaksiLinen]:
    """Daftar seluruh transaksi."""
    return db.query(TransaksiLinen).order_by(TransaksiLinen.id.desc()).all()


@router.post("", response_model=TransaksiResponse, status_code=status.HTTP_201_CREATED)
def create_transaksi(payload: TransaksiCreate, db: Session = Depends(get_db)) -> TransaksiLinen:
    """Buat header transaksi baru."""
    transaksi = TransaksiLinen(**payload.model_dump())
    db.add(transaksi)
    db.commit()
    db.refresh(transaksi)
    return transaksi


@router.post("/batch", response_model=TransaksiResponse, status_code=status.HTTP_201_CREATED)
def create_batch_transaksi(payload: BatchTransaksiCreate, db: Session = Depends(get_db)) -> TransaksiLinen:
    """Proses transaksi batch multiple EPC (Linen Masuk / Keluar / Cuci)."""
    if not payload.epc_list:
        raise HTTPException(status_code=400, detail="Daftar EPC tidak boleh kosong")

    kode_trx = payload.kode_transaksi or f"TRX-{payload.jenis_transaksi.upper()}-{int(time.time())}"

    # 1. Header Transaksi
    transaksi = TransaksiLinen(
        jenis_transaksi=payload.jenis_transaksi,
        kode_transaksi=kode_trx,
        petugas=payload.petugas or "Petugas UI",
        ruangan=payload.ruangan or ("R. Laundry" if payload.jenis_transaksi == "masuk" else "Ruangan Pasien"),
        total_linen=len(payload.epc_list),
        status="selesai",
        catatan=payload.catatan,
    )
    db.add(transaksi)
    db.commit()
    db.refresh(transaksi)

    unique_epcs = list(dict.fromkeys(payload.epc_list))

    for epc in unique_epcs:
        linen = db.query(Linen).filter(Linen.epc == epc).first()
        if not linen:
            linen = Linen(
                epc=epc,
                kategori="Linen General",
                nama_linen=f"Linen {epc[-6:]}",
                lokasi="Storage",
                status="tersedia",
                total_cuci=0,
            )
            db.add(linen)
            db.commit()
            db.refresh(linen)

        status_sebelum = linen.status
        lokasi_sebelum = linen.lokasi

        if payload.jenis_transaksi == "keluar":
            status_setelah = "dipakai"
            lokasi_setelah = payload.ruangan or "Ruangan Pasien"
        elif payload.jenis_transaksi == "masuk":
            status_setelah = "dicuci"
            lokasi_setelah = "R. Laundry"
            linen.total_cuci += 1
        elif payload.jenis_transaksi == "cuci":
            status_setelah = "tersedia"
            lokasi_setelah = "Gudang / Storage"
        else:
            status_setelah = linen.status
            lokasi_setelah = linen.lokasi

        linen.status = status_setelah
        linen.lokasi = lokasi_setelah
        db.add(linen)

        detail = DetailTransaksiLinen(
            transaksi_id=transaksi.id,
            linen_id=linen.id,
            epc=epc,
            status_sebelum=status_sebelum,
            status_setelah=status_setelah,
            keterangan=payload.catatan or f"Batch {payload.jenis_transaksi}",
        )
        db.add(detail)

        riwayat = RiwayatLinen(
            linen_id=linen.id,
            transaksi_id=transaksi.id,
            event_type=payload.jenis_transaksi,
            lokasi_sebelumnya=lokasi_sebelum,
            lokasi_sesudah=lokasi_setelah,
        )
        db.add(riwayat)

    db.commit()

    try:
        SyncService().enqueue_for_sync(
            {
                "transaksi_id": transaksi.id,
                "kode_transaksi": transaksi.kode_transaksi,
                "jenis_transaksi": transaksi.jenis_transaksi,
                "petugas": transaksi.petugas,
                "ruangan": transaksi.ruangan,
                "epcs": unique_epcs,
                "timestamp": time.time(),
            }
        )
    except Exception:
        pass

    return transaksi


@router.post("/detail", response_model=DetailTransaksiResponse, status_code=status.HTTP_201_CREATED)
def create_detail_transaksi(payload: DetailTransaksiCreate, db: Session = Depends(get_db)) -> DetailTransaksiLinen:
    """Tambah detail item ke transaksi."""
    existing = (
        db.query(DetailTransaksiLinen)
        .filter(DetailTransaksiLinen.transaksi_id == payload.transaksi_id, DetailTransaksiLinen.epc == payload.epc)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="EPC sudah ada dalam transaksi ini")

    detail = DetailTransaksiLinen(**payload.model_dump())
    db.add(detail)
    db.commit()
    db.refresh(detail)

    linen = db.query(Linen).filter(Linen.epc == payload.epc).first()
    if linen is not None:
        linen.status = "dipakai" if payload.transaksi_id else linen.status
        linen.lokasi = "Ruangan" if payload.transaksi_id else linen.lokasi
        db.add(linen)
        db.commit()

    return detail


@router.post("/riwayat", response_model=RiwayatResponse, status_code=status.HTTP_201_CREATED)
def create_riwayat(payload: RiwayatCreate, db: Session = Depends(get_db)) -> RiwayatLinen:
    """Catat riwayat pergerakan linen."""
    riwayat = RiwayatLinen(**payload.model_dump())
    db.add(riwayat)

    linen = db.query(Linen).filter(Linen.id == payload.linen_id).first()
    if linen is not None:
        if payload.event_type == "keluar":
            linen.status = "dipakai"
            linen.lokasi = payload.lokasi_sesudah or linen.lokasi
        elif payload.event_type == "masuk":
            linen.status = "tersedia"
            linen.lokasi = payload.lokasi_sesudah or linen.lokasi
        elif payload.event_type == "cuci":
            linen.status = "dicuci"
            linen.lokasi = payload.lokasi_sesudah or linen.lokasi
        db.add(linen)

    db.commit()
    db.refresh(riwayat)
    return riwayat


@router.get("/riwayat", response_model=list[RiwayatResponse])
def list_riwayat(db: Session = Depends(get_db)) -> list[RiwayatLinen]:
    """Ambil seluruh riwayat linen."""
    return db.query(RiwayatLinen).order_by(RiwayatLinen.id.desc()).all()


@router.get("/{transaksi_id}/detail")
def get_transaksi_detail(transaksi_id: int, db: Session = Depends(get_db)) -> list[dict]:
    """Ambil detail per transaksi dengan data master linen."""
    details = (
        db.query(DetailTransaksiLinen, Linen)
        .join(Linen, DetailTransaksiLinen.linen_id == Linen.id)
        .filter(DetailTransaksiLinen.transaksi_id == transaksi_id)
        .all()
    )
    result = []
    for d, l in details:
        result.append({
            "id": d.id,
            "epc": d.epc,
            "kategori": l.kategori,
            "nama_linen": l.nama_linen,
            "total_cuci": l.total_cuci,
            "keterangan": d.keterangan
        })
    return result


from pydantic import BaseModel, Field
from typing import Optional

class AutoScanRequest(BaseModel):
    epc: str = Field(..., min_length=1)
    transaction_type: str = Field(..., description="LINEN_MASUK or LINEN_KELUAR or masuk or keluar")
    reader_name: Optional[str] = None
    port: Optional[str] = None
    ruangan: Optional[str] = None
    petugas: Optional[str] = None


@router.post("/auto-scan", status_code=status.HTTP_201_CREATED)
def auto_scan_transaction(payload: AutoScanRequest, db: Session = Depends(get_db)):
    """Proses otomatis 1 RFID Scan dari COM3 (Linen Masuk) atau COM4 (Linen Keluar)."""
    epc = payload.epc.strip().upper()
    if len(epc) < 4:
        raise HTTPException(status_code=400, detail="Format EPC tidak valid")

    r_name = (payload.reader_name or "").upper()
    t_type = (payload.transaction_type or "").upper()
    port = (payload.port or "").upper()

    # Rule checks for COM3 (Linen Masuk) and COM4 (Linen Keluar)
    if "KELUAR" in t_type:
        if "MASUK" in r_name or port == "COM3":
            raise HTTPException(
                status_code=400,
                detail="Koneksi COM3 (RFID Linen Masuk) tidak diizinkan membuat transaksi Linen Keluar"
            )
        jenis_transaksi = "keluar"
        reader_source = payload.reader_name or "RFID_LINEN_KELUAR"
        port_source = payload.port or "COM4"
        status_setelah = "dipakai"
        lokasi_setelah = payload.ruangan or "Ruangan Pasien"
    else:
        if "KELUAR" in r_name or port == "COM4":
            raise HTTPException(
                status_code=400,
                detail="Koneksi COM4 (RFID Linen Keluar) tidak diizinkan membuat transaksi Linen Masuk"
            )
        jenis_transaksi = "masuk"
        reader_source = payload.reader_name or "RFID_LINEN_MASUK"
        port_source = payload.port or "COM3"
        status_setelah = "dicuci"
        lokasi_setelah = "R. Laundry"

    # Find or create linen
    linen = db.query(Linen).filter(Linen.epc == epc).first()
    status_sebelum = "baru"
    lokasi_sebelum = "Storage"

    if not linen:
        linen = Linen(
            epc=epc,
            kategori="Linen General",
            nama_linen=f"Linen {epc[-6:]}",
            lokasi=lokasi_setelah,
            status=status_setelah,
            total_cuci=1 if jenis_transaksi == "masuk" else 0,
        )
        db.add(linen)
        db.commit()
        db.refresh(linen)
    else:
        status_sebelum = linen.status
        lokasi_sebelum = linen.lokasi
        if jenis_transaksi == "masuk":
            linen.total_cuci += 1
        linen.status = status_setelah
        linen.lokasi = lokasi_setelah
        db.add(linen)

    kode_trx = f"AUTO-{jenis_transaksi.upper()}-{int(time.time()*1000)}"
    transaksi = TransaksiLinen(
        jenis_transaksi=jenis_transaksi,
        kode_transaksi=kode_trx,
        petugas=payload.petugas or f"RFID Scanner ({reader_source})",
        ruangan=lokasi_setelah,
        total_linen=1,
        status="selesai",
        catatan=f"Auto-scan dari {reader_source} ({port_source})",
        reader_source=reader_source,
        port_source=port_source,
    )
    db.add(transaksi)
    db.commit()
    db.refresh(transaksi)

    detail = DetailTransaksiLinen(
        transaksi_id=transaksi.id,
        linen_id=linen.id,
        epc=epc,
        status_sebelum=status_sebelum,
        status_setelah=status_setelah,
        keterangan=f"Auto scan via {reader_source}",
    )
    db.add(detail)

    riwayat = RiwayatLinen(
        linen_id=linen.id,
        transaksi_id=transaksi.id,
        event_type=jenis_transaksi,
        lokasi_sebelumnya=lokasi_sebelum,
        lokasi_sesudah=lokasi_setelah,
    )
    db.add(riwayat)
    db.commit()

    return {
        "status": "ok",
        "transaksi_id": transaksi.id,
        "kode_transaksi": transaksi.kode_transaksi,
        "jenis_transaksi": jenis_transaksi,
        "reader_source": reader_source,
        "port_source": port_source,
        "linen": {
            "id": linen.id,
            "epc": linen.epc,
            "nama_linen": linen.nama_linen,
            "kategori": linen.kategori,
            "status": linen.status,
            "lokasi": linen.lokasi,
            "total_cuci": linen.total_cuci,
        }
    }

