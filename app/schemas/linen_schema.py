from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LinenCreate(BaseModel):
    epc: str = Field(..., min_length=4, max_length=64)
    kategori: str = Field(..., min_length=1, max_length=50)
    nama_linen: str = Field(..., min_length=1, max_length=100)
    lokasi: Optional[str] = Field(default="Storage", max_length=100)
    total_cuci: int = Field(default=0, ge=0)
    status: Optional[str] = Field(default="tersedia", max_length=30)

class LinenUpdate(BaseModel):
    epc: Optional[str] = Field(default=None, min_length=4, max_length=64)
    kategori: Optional[str] = Field(default=None, min_length=1, max_length=50)
    nama_linen: Optional[str] = Field(default=None, min_length=1, max_length=100)
    lokasi: Optional[str] = Field(default=None, max_length=100)
    total_cuci: Optional[int] = Field(default=None, ge=0)
    status: Optional[str] = Field(default=None, max_length=30)


class LinenResponse(LinenCreate):
    id: int

    class Config:
        from_attributes = True
