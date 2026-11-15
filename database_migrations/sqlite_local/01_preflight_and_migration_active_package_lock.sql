-- =============================================================================
-- RANCANGAN MIGRASI DAN PREFLIGHT CHECK: PARTIAL UNIQUE INDEX PAKET AKTIF
-- Tujuan: Mencegah operasi aktif ganda pada paket yang sama di tingkat database SQLite.
-- CATATAN: BERKAS INI HANYA RANCANGAN DAN TIDAK BOLEH DIEKSEKUSI PADA DATABASE OPERASIONAL.
-- =============================================================================

-- TAHAP 1: PREFLIGHT CHECK
-- Periksa apakah terdapat baris aktif duplikat yang melanggar aturan sebelum membuat index.
-- Jika query ini mengembalikan baris, migrasi HARUS DIBATALKAN untuk rekonsiliasi manual.
SELECT 
    paket_id, 
    COUNT(*) AS active_operation_count, 
    GROUP_CONCAT(operation_id, ', ') AS conflicting_operation_ids,
    GROUP_CONCAT(lifecycle_phase, ', ') AS phases
FROM paket_receiving_operation
WHERE lifecycle_phase IN (
    'claiming', 
    'claim_verified', 
    'local_committed', 
    'claim_uncertain', 
    'claim_held_reconciliation', 
    'awaiting_reauth'
)
GROUP BY paket_id
HAVING COUNT(*) > 1;

-- TAHAP 2: EKSEKUSI PEMBUATAN PARTIAL UNIQUE INDEX
-- Hanya dieksekusi jika TAHAP 1 mengembalikan 0 baris pelanggaran.
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_paket_operation 
ON paket_receiving_operation(paket_id) 
WHERE lifecycle_phase IN (
    'claiming', 
    'claim_verified', 
    'local_committed', 
    'claim_uncertain', 
    'claim_held_reconciliation', 
    'awaiting_reauth'
);

-- TAHAP 3: POST-MIGRATION VERIFICATION
-- Verifikasi bahwa index telah terbentuk pada skema SQLite
SELECT name, tbl_name, sql 
FROM sqlite_master 
WHERE type = 'index' AND name = 'uq_active_paket_operation';

