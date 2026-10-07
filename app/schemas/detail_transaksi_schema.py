from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DetailTransaksiCreate(BaseModel):
    transaksi_id: int
    linen_id: int
    epc: str = Field(..., min_length=4, max_length=64)
    status_sebelum: Optional[str] = Field(default=None, max_length=50)
    status_setelah: Optional[str] = Field(default=None, max_length=50)
    keterangan: Optional[str] = Field(default=None, max_length=255)


class DetailTransaksiResponse(DetailTransaksiCreate):
    id: int

    class Config:
        from_attributes = True

