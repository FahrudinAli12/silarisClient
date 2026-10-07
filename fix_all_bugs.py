import re

# 1. Update ruangan_schema.py
with open('d:/ClientS/app/schemas/ruangan_schema.py', 'rb') as f:
    ruangan = f.read().decode('utf-8')

ruangan_update = """class RuanganCreate(BaseModel):
    kode_ruangan: str = Field(..., min_length=1, max_length=20)
    nama_ruangan: str = Field(..., min_length=1, max_length=100)
    keterangan: Optional[str] = Field(default=None, max_length=255)

class RuanganUpdate(BaseModel):
    kode_ruangan: Optional[str] = Field(default=None, min_length=1, max_length=20)
    nama_ruangan: Optional[str] = Field(default=None, min_length=1, max_length=100)
    keterangan: Optional[str] = Field(default=None, max_length=255)
"""
ruangan = re.sub(r'class RuanganCreate.*?max_length=255\)', ruangan_update, ruangan, flags=re.DOTALL)

with open('d:/ClientS/app/schemas/ruangan_schema.py', 'wb') as f:
    f.write(ruangan.encode('utf-8'))


# 2. Update linen_schema.py
with open('d:/ClientS/app/schemas/linen_schema.py', 'rb') as f:
    linen = f.read().decode('utf-8')

linen_update = """class LinenCreate(BaseModel):
    epc: str = Field(..., min_length=1)
    kategori: str = Field(..., min_length=1)
    nama_linen: str = Field(..., min_length=1)
    lokasi: str = Field(..., min_length=1)

class LinenUpdate(BaseModel):
    epc: Optional[str] = Field(default=None, min_length=1)
    kategori: Optional[str] = Field(default=None, min_length=1)
    nama_linen: Optional[str] = Field(default=None, min_length=1)
    lokasi: Optional[str] = Field(default=None, min_length=1)
"""
linen = re.sub(r'class LinenCreate.*?min_length=1\)', linen_update, linen, flags=re.DOTALL)

with open('d:/ClientS/app/schemas/linen_schema.py', 'wb') as f:
    f.write(linen.encode('utf-8'))


# 3. Update master_router.py
with open('d:/ClientS/app/routers/master_router.py', 'rb') as f:
    router = f.read().decode('utf-8')

# Import schemas
router = router.replace('from app.schemas.ruangan_schema import RuanganCreate, RuanganResponse', 'from app.schemas.ruangan_schema import RuanganCreate, RuanganUpdate, RuanganResponse')
router = router.replace('from app.schemas.linen_schema import LinenCreate, LinenResponse', 'from app.schemas.linen_schema import LinenCreate, LinenUpdate, LinenResponse')

# Update logic for Ruangan
ruangan_route = """def update_ruangan(ruangan_id: int, payload: RuanganUpdate, db: Session = Depends(get_db)) -> Ruangan:
    ruangan = db.query(Ruangan).filter(Ruangan.id == ruangan_id).first()
    if ruangan is None:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "keterangan":
            setattr(ruangan, key, value)
        elif value is not None and value != "":
            setattr(ruangan, key, value)
    db.commit()"""
router = re.sub(r'def update_ruangan.*?db\.commit\(\)', ruangan_route, router, flags=re.DOTALL)

# Update logic for Linen
linen_route = """def update_linen(epc: str, payload: LinenUpdate, db: Session = Depends(get_db)) -> Linen:
    linen = db.query(Linen).filter(Linen.epc == epc).first()
    if linen is None:
        raise HTTPException(status_code=404, detail="Linen tidak ditemukan")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "lokasi":
            setattr(linen, key, value)
        elif value is not None and value != "":
            setattr(linen, key, value)
    db.commit()"""
router = re.sub(r'def update_linen.*?db\.commit\(\)', linen_route, router, flags=re.DOTALL)

with open('d:/ClientS/app/routers/master_router.py', 'wb') as f:
    f.write(router.encode('utf-8'))


# 4. Update app_core.js
with open('d:/ClientS/app/static/app_core.js', 'rb') as f:
    js = f.read().decode('utf-8')

# Fix SVG click issue
js = js.replace('if (!(target instanceof HTMLElement)) return;', 'if (!(target instanceof Element)) return;')

# Fix payload empty strings for ruangan
ruangan_js = """    if (!editId) {
      if (!payload.kode_ruangan || !payload.nama_ruangan) {
        notify('Kode Ruangan dan Nama Ruangan wajib diisi untuk menambah ruangan baru.', 'error');
        return;
      }
    } else {
      if (!payload.kode_ruangan) delete payload.kode_ruangan;
      if (!payload.nama_ruangan) delete payload.nama_ruangan;
    }"""
js = re.sub(r'    if \(!editId\) \{\s*if \(!payload\.kode_ruangan \|\| !payload\.nama_ruangan\) \{\s*notify\(\'Kode Ruangan dan Nama Ruangan wajib diisi untuk menambah ruangan baru\.\', \'error\'\);\s*return;\s*\}\s*\}', ruangan_js, js)

# Fix payload empty strings for linen
linen_js = """    if (!editEpc) {
      if (!payload.epc || !payload.kategori || !payload.nama_linen) {
        notify('EPC, Kategori, dan Nama Linen wajib diisi untuk menambah linen baru.', 'error');
        return;
      }
    } else {
      if (!payload.epc) delete payload.epc;
      if (!payload.kategori) delete payload.kategori;
      if (!payload.nama_linen) delete payload.nama_linen;
    }"""
js = re.sub(r'    if \(!editEpc\) \{\s*if \(!payload\.epc \|\| !payload\.kategori \|\| !payload\.nama_linen\) \{\s*notify\(\'EPC, Kategori, dan Nama Linen wajib diisi untuk menambah linen baru\.\', \'error\'\);\s*return;\s*\}\s*\}', linen_js, js)

with open('d:/ClientS/app/static/app_core.js', 'wb') as f:
    f.write(js.encode('utf-8'))
