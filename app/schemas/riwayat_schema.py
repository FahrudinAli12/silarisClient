from __future__ import annotations
from datetime import datetime

from typing import Optional

from pydantic import BaseModel, Field


class RiwayatCreate(BaseModel):
    linen_id: int
    transaksi_id: Optional[int] = None
    event_type: str = Field(..., min_length=1, max_length=30)
    lokasi_sebelumnya: Optional[str] = Field(default=None, max_length=100)
    lokasi_sesudah: Optional[str] = Field(default=None, max_length=100)


class RiwayatResponse(RiwayatCreate):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
