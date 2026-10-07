from __future__ import annotations

import time
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.models.detail_transaksi_linen import DetailTransaksiLinen
from app.models.linen import Linen
from app.models.riwayat_linen import RiwayatLinen
from app.models.transaksi_linen import TransaksiLinen
from app.services.cloud_sync_service import CloudSyncService, clean_epc

router = APIRouter(prefix="/setting", tags=["setting"])
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# GET DATA DARI CLOUD
# ──────────────────────────────────────────────────────────

@router.get("/get-data")
def get_data_cloud(kode: str, db: Session = Depends(get_db)) -> dict:
    """Ambil data paket linen dari cloud berdasarkan kode verifikasi."""
    if not kode or not kode.strip():
        raise HTTPException(status_code=400, detail="Kode verifikasi tidak boleh kosong.")

    kode = kode.strip()

    try:
        service = CloudSyncService()
        result = service.get_paket_by_kode(kode)
        return {
            "status": "ok",
            "paket": result["paket"],
            "linen_items": result["linen_items"],
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Koneksi ke cloud timeout. Periksa koneksi internet Anda.")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Cloud mengembalikan error {exc.response.status_code}. Silakan coba lagi."
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Gagal terhubung ke cloud: {exc}")


# ──────────────────────────────────────────────────────────
# SIMPAN DATA KE LOKAL
# ──────────────────────────────────────────────────────────

class SimpanDataPayload(BaseModel):
    verification_code: str
    petugas: str
    paket_id: str | int
    supplier: Optional[str] = None
    catatan: Optional[str] = None
    linen_terdaftar: list[dict]  # [{id, epc, kategori, nama_linen}]


@router.post("/simpan-data")
def simpan_data(payload: SimpanDataPayload, db: Session = Depends(get_db)) -> dict:
    """Simpan data linen yang diterima dari cloud ke database lokal."""
    kode = payload.verification_code.strip()
    petugas = payload.petugas.strip()
    if not kode:
        raise HTTPException(status_code=400, detail="Kode verifikasi tidak boleh kosong.")
    if not petugas:
        raise HTTPException(status_code=400, detail="Nama petugas tidak boleh kosong.")

    # 1. Cek idempotency — sudah pernah diproses?
    existing = (
        db.query(TransaksiLinen)
        .filter(
            TransaksiLinen.jenis_transaksi == "penerimaan",
            TransaksiLinen.catatan.contains(kode),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Kode verifikasi '{kode}' sudah pernah diproses pada transaksi {existing.kode_transaksi}. Penghapusan data baru dicegah."
        )

    # Jangan percaya atribut linen dari browser. Ambil ulang paket berdasarkan
    # kode agar penyimpanan tetap dibatasi oleh data cloud yang terotorisasi.
    try:
        cloud_result = CloudSyncService().get_paket_by_kode(kode)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Koneksi ke cloud timeout. Periksa koneksi internet Anda.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Cloud tidak dapat memvalidasi paket penerimaan.") from exc

    if str(cloud_result["paket"].get("id")) != str(payload.paket_id):
        raise HTTPException(status_code=400, detail="Paket cloud tidak sesuai dengan kode verifikasi.")
    cloud_items = {clean_epc(item["epc"]): item for item in cloud_result["linen_items"]}

    unique_items = []
    seen_epcs = set()
    for item in payload.linen_terdaftar:
        epc = clean_epc(item.get("epc", ""))
        if epc and epc not in cloud_items:
            raise HTTPException(status_code=400, detail=f"EPC {epc} bukan bagian dari paket cloud ini.")
        if epc and epc not in seen_epcs:
            seen_epcs.add(epc)
            unique_items.append(cloud_items[epc])

    if not unique_items:
        raise HTTPException(status_code=400, detail="Tidak ada data linen terdaftar untuk disimpan.")

    # 2. Buat header transaksi penerimaan
    kode_trx = f"TRX-PENERIMAAN-{int(time.time())}"
    supplier_info = payload.supplier or "Supplier"
    catatan_text = f"Penerimaan dari {supplier_info} | Kode: {kode}"

    try:
        transaksi = TransaksiLinen(
            jenis_transaksi="penerimaan",
            kode_transaksi=kode_trx,
            petugas=petugas,
            ruangan="Storage",
            total_linen=len(unique_items),
            status="selesai",
            catatan=catatan_text,
        )
        db.add(transaksi)
        db.flush()

        saved_count = 0
        for item in unique_items:
            epc = item["epc"]
            linen = db.query(Linen).filter(Linen.epc == epc).first()
            if not linen:
                linen = Linen(
                    epc=epc,
                    kategori=str(item.get("kategori")),
                    nama_linen=str(item.get("nama_linen")),
                    lokasi="Storage",
                    status="tersedia",
                    total_cuci=0,
                )
                db.add(linen)
                db.flush()

            detail = DetailTransaksiLinen(
                transaksi_id=transaksi.id,
                linen_id=linen.id,
                epc=epc,
                status_sebelum="baru",
                status_setelah="tersedia",
                keterangan=f"Diterima dari cloud | Kode: {kode}",
            )
            db.add(detail)

            riwayat = RiwayatLinen(
                linen_id=linen.id,
                transaksi_id=transaksi.id,
                event_type="penerimaan",
                lokasi_sebelumnya="Cloud Supplier",
                lokasi_sesudah="Storage",
            )
            db.add(riwayat)
            saved_count += 1

        db.commit()
        db.refresh(transaksi)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Gagal menyimpan penerimaan linen secara atomik")
        raise HTTPException(status_code=500, detail="Data penerimaan gagal disimpan ke database lokal.") from exc

    # 4. Tandai paket sebagai received di cloud (best-effort)
    try:
        cloud = CloudSyncService()
        cloud.mark_paket_received(payload.paket_id)
    except Exception as exc:
        logger.warning("Gagal menandai paket cloud sebagai received: %s", exc)

    return {
        "status": "ok",
        "kode_transaksi": kode_trx,
        "jumlah_disimpan": saved_count,
        "message": f"Berhasil menyimpan {saved_count} linen dari paket '{kode}'.",
    }


# ──────────────────────────────────────────────────────────
# LOG PENGIRIMAN DATA
# ──────────────────────────────────────────────────────────

@router.get("/log")
def get_log_penerimaan(db: Session = Depends(get_db)) -> list[dict]:
    """Ambil semua log transaksi penerimaan linen dari cloud."""
    rows = (
        db.query(TransaksiLinen)
        .filter(TransaksiLinen.jenis_transaksi == "penerimaan")
        .order_by(TransaksiLinen.id.desc())
        .all()
    )
    result = []
    for i, row in enumerate(rows, 1):
        result.append({
            "no": i,
            "id": row.id,
            "tanggal": row.created_at.strftime("%d/%m/%Y %H:%M") if row.created_at else "-",
            "petugas": row.petugas or "-",
            "jumlah_linen": row.total_linen,
            "keterangan": row.catatan or "-",
            "kode_transaksi": row.kode_transaksi,
        })
    return result


@router.get("/log/{transaksi_id}/detail")
def get_log_detail(transaksi_id: int, db: Session = Depends(get_db)) -> list[dict]:
    """Ambil detail linen dari transaksi penerimaan tertentu."""
    details = (
        db.query(DetailTransaksiLinen, Linen)
        .join(Linen, DetailTransaksiLinen.linen_id == Linen.id)
        .filter(DetailTransaksiLinen.transaksi_id == transaksi_id)
        .all()
    )
    result = []
    for i, (d, l) in enumerate(details, 1):
        result.append({
            "no": i,
            "epc": d.epc,
            "kategori": l.kategori,
            "nama_linen": l.nama_linen,
        })
    return result
