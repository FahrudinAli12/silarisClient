from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_serializer


class TransaksiCreate(BaseModel):
    jenis_transaksi: str = Field(..., min_length=1, max_length=30)
    kode_transaksi: str = Field(..., min_length=1, max_length=50)
    petugas: Optional[str] = Field(default=None, max_length=100)
    ruangan: Optional[str] = Field(default=None, max_length=100)
    total_linen: Optional[int] = Field(default=0)
    status: Optional[str] = Field(default="selesai", max_length=30)
    catatan: Optional[str] = Field(default=None, max_length=255)


class BatchTransaksiCreate(BaseModel):
    jenis_transaksi: str = Field(..., min_length=1, max_length=30)  # "masuk", "keluar", "cuci"
    kode_transaksi: Optional[str] = Field(default=None)
    petugas: Optional[str] = Field(default=None, max_length=100)
    ruangan: Optional[str] = Field(default=None, max_length=100)
    catatan: Optional[str] = Field(default=None, max_length=255)
    epc_list: list[str] = Field(..., min_items=1)


class TransaksiResponse(TransaksiCreate):
    id: int
    created_at: Optional[datetime] = None

    @field_serializer("created_at")
    def serialize_created_at(self, value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return value.strftime("%d/%m/%Y %H:%M")

    class Config:
        from_attributes = True
