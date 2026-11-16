-- =============================================================================
-- RANCANGAN MIGRASI DAN PREFLIGHT CHECK: PENAMBAHAN KOLOM IS_ACTIVE PADA RUANGAN
-- Tujuan: Menambahkan kolom is_active untuk soft-disable ruangan tanpa merusak histori transaksi.
-- Database: SQLite Lokal (ClientS)
-- CATATAN: BERKAS INI DAPAT DIJALANKAN SECARA TERISOLASI ATAU MELALUI MIGRASI RESMI.
-- =============================================================================

-- TAHAP 1: PREFLIGHT CHECK
-- 1.1 Pastikan tabel ruangan tersedia di database fisik
SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ruangan';

-- 1.2 Periksa apakah kolom is_active sudah ada pada tabel ruangan
-- Jika query ini mengembalikan baris 'is_active', migrasi penambahan kolom TIDAK perlu dijalankan ulang.
SELECT cid, name, type, "notnull", dflt_value, pk 
FROM pragma_table_info('ruangan') 
WHERE name = 'is_active';

-- TAHAP 2: EKSEKUSI ALTER TABLE DAN PEMBUATAN INDEKS
-- 2.1 Menambahkan kolom is_active bertipe BOOLEAN, NOT NULL, dengan DEFAULT 1 (TRUE).
-- Pada SQLite, ALTER TABLE ADD COLUMN dengan DEFAULT otomatis mengisi baris lama dengan nilai default tersebut.
ALTER TABLE ruangan ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1;

-- 2.2 Pembuatan Indeks Lookup Keaktifan Ruangan
-- CATATAN INTEGRITAS SKEMA SINGLE-TENANT LOKAL:
-- Tabel ruangan pada SQLite lokal ClientS TIDAK memiliki kolom 'id_rumah_sakit' atau 'hospital_id'
-- karena database lokal didedikasikan secara tunggal untuk instalasi rumah sakit bersangkutan.
-- Oleh karena itu, indeks difokuskan secara akurat pada kolom keaktifan ruangan: (is_active).
CREATE INDEX IF NOT EXISTS ix_ruangan_is_active ON ruangan (is_active);

-- TAHAP 3: POST-MIGRATION VERIFICATION
-- 3.1 Verifikasi bahwa kolom is_active sudah terdaftar pada skema tabel ruangan
SELECT cid, name, type, "notnull", dflt_value, pk 
FROM pragma_table_info('ruangan') 
WHERE name = 'is_active';

-- 3.2 Verifikasi bahwa indeks ix_ruangan_is_active telah terbentuk
SELECT name, tbl_name, sql 
FROM sqlite_master 
WHERE type = 'index' AND name = 'ix_ruangan_is_active';

-- 3.3 Verifikasi bahwa seluruh baris ruangan lama memiliki is_active = 1
SELECT COUNT(*) AS total_ruangan, 
       SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS total_aktif,
       SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END) AS total_nonaktif,
       SUM(CASE WHEN is_active IS NULL THEN 1 ELSE 0 END) AS total_null
FROM ruangan;

