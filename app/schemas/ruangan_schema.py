from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RuanganCreate(BaseModel):
    kode_ruangan: str = Field(..., min_length=1, max_length=20)
    nama_ruangan: str = Field(..., min_length=1, max_length=100)
    keterangan: Optional[str] = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)


class RuanganUpdate(BaseModel):
    kode_ruangan: Optional[str] = Field(default=None, min_length=1, max_length=20)
    nama_ruangan: Optional[str] = Field(default=None, min_length=1, max_length=100)
    keterangan: Optional[str] = Field(default=None, max_length=255)
    is_active: Optional[bool] = Field(default=None)



class RuanganResponse(RuanganCreate):
    id: int

    class Config:
        from_attributes = True
