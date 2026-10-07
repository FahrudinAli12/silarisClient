import re

# 1. Update data_ruangan.html
with open('d:/ClientS/app/templates/data_ruangan.html', 'rb') as f:
    html = f.read().decode('utf-8')
html = html.replace('<input id="kode_ruangan" name="kode_ruangan" required', '<input id="kode_ruangan" name="kode_ruangan"')
html = html.replace('<input id="nama_ruangan" name="nama_ruangan" required', '<input id="nama_ruangan" name="nama_ruangan"')
with open('d:/ClientS/app/templates/data_ruangan.html', 'wb') as f:
    f.write(html.encode('utf-8'))

# 2. Update data_linen.html
with open('d:/ClientS/app/templates/data_linen.html', 'rb') as f:
    html = f.read().decode('utf-8')
html = html.replace('name="epc" required', 'name="epc"')
html = html.replace('name="kategori" required', 'name="kategori"')
html = html.replace('name="nama_linen" required', 'name="nama_linen"')
with open('d:/ClientS/app/templates/data_linen.html', 'wb') as f:
    f.write(html.encode('utf-8'))

# 3. Update app_core.js
with open('d:/ClientS/app/static/app_core.js', 'rb') as f:
    js = f.read().decode('utf-8')

ruangan_submit = """  document.getElementById('ruangan-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form));
    const editId = payload.id;
    delete payload.id;
    
    if (!editId) {
      if (!payload.kode_ruangan || !payload.nama_ruangan) {
        notify('Kode Ruangan dan Nama Ruangan wajib diisi untuk menambah ruangan baru.', 'error');
        return;
      }
    }
    
    try {"""
js = re.sub(
    r"  document\.getElementById\('ruangan-form'\)\?\.addEventListener\('submit', async \(event\) => \{\s*event\.preventDefault\(\);\s*const form = event\.currentTarget;\s*const payload = Object\.fromEntries\(new FormData\(form\)\);\s*const editId = payload\.id;\s*delete payload\.id;\s*try \{",
    ruangan_submit,
    js
)

linen_submit = """  document.getElementById('linen-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form));
    const editEpc = payload.edit_epc;
    delete payload.edit_epc;
    
    if (!editEpc) {
      if (!payload.epc || !payload.kategori || !payload.nama_linen) {
        notify('EPC, Kategori, dan Nama Linen wajib diisi untuk menambah linen baru.', 'error');
        return;
      }
    }
    
    try {"""
js = re.sub(
    r"  document\.getElementById\('linen-form'\)\?\.addEventListener\('submit', async \(event\) => \{\s*event\.preventDefault\(\);\s*const form = event\.currentTarget;\s*const payload = Object\.fromEntries\(new FormData\(form\)\);\s*const editEpc = payload\.edit_epc;\s*delete payload\.edit_epc;\s*try \{",
    linen_submit,
    js
)

with open('d:/ClientS/app/static/app_core.js', 'wb') as f:
    f.write(js.encode('utf-8'))

# 4. Update master_router.py
with open('d:/ClientS/app/routers/master_router.py', 'rb') as f:
    py = f.read().decode('utf-8')

ruangan_update = """def update_ruangan(ruangan_id: int, payload: RuanganCreate, db: Session = Depends(get_db)) -> Ruangan:
    ruangan = db.query(Ruangan).filter(Ruangan.id == ruangan_id).first()
    if ruangan is None:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan")
    for key, value in payload.model_dump().items():
        if key == "keterangan":
            setattr(ruangan, key, value)
        elif value is not None and value != "":
            setattr(ruangan, key, value)
    db.commit()"""
py = re.sub(
    r'def update_ruangan\(ruangan_id: int, payload: RuanganCreate, db: Session = Depends\(get_db\)\) -> Ruangan:\s*ruangan = db\.query\(Ruangan\)\.filter\(Ruangan\.id == ruangan_id\)\.first\(\)\s*if ruangan is None:\s*raise HTTPException\(status_code=404, detail="Ruangan tidak ditemukan"\)\s*for key, value in payload\.model_dump\(\)\.items\(\):\s*setattr\(ruangan, key, value\)\s*db\.commit\(\)',
    ruangan_update,
    py
)

linen_update = """def update_linen(epc: str, payload: LinenCreate, db: Session = Depends(get_db)) -> Linen:
    linen = db.query(Linen).filter(Linen.epc == epc).first()
    if linen is None:
        raise HTTPException(status_code=404, detail="Linen tidak ditemukan")
    for key, value in payload.model_dump().items():
        if key == "lokasi":
            setattr(linen, key, value)
        elif value is not None and value != "":
            setattr(linen, key, value)
    db.commit()"""
py = re.sub(
    r'def update_linen\(epc: str, payload: LinenCreate, db: Session = Depends\(get_db\)\) -> Linen:\s*linen = db\.query\(Linen\)\.filter\(Linen\.epc == epc\)\.first\(\)\s*if linen is None:\s*raise HTTPException\(status_code=404, detail="Linen tidak ditemukan"\)\s*for key, value in payload\.model_dump\(\)\.items\(\):\s*setattr\(linen, key, value\)\s*db\.commit\(\)',
    linen_update,
    py
)

with open('d:/ClientS/app/routers/master_router.py', 'wb') as f:
    f.write(py.encode('utf-8'))
