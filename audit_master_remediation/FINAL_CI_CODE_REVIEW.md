# LAPORAN PEMERIKSAAN AKHIR KODE & OTORISASI RILIS CI
## FINAL CODE REVIEW: ALUR KERJA & RUNNER GLADI BERSIH POSTGRESQL 15
### JALUR DEPLOYMENT: REV4 → MIGRATION 10 | BASELINE: R2.10.4

**Tanggal Audit**: 16 November 2026  
**Otoritas Tata Kelola**: Single Owner-Operator Governance (System Owner merangkap Acting DBA)  
**Status Gate B**: **LOCKED**  
**Status Rekomendasi Kode**: **`CONDITIONAL APPROVAL` (MENUNGGU PERSETUJUAN EKSPLISIT PEMILIK UNTUK COMMIT)**  

---

## 1. Ringkasan Eksekutif Hasil Pemeriksaan

Sesuai instruksi System Owner, telah dilakukan pemeriksaan kode mendalam (*deep code review*) terhadap 6 berkas baru:
1. [.github/workflows/rev4_to_10_postgres15_rehearsal.yml](file:///D:/ClientS/.github/workflows/rev4_to_10_postgres15_rehearsal.yml)
2. [database_migrations/cloud/run_rev4_to_10_rehearsal.py](file:///D:/ClientS/database_migrations/cloud/run_rev4_to_10_rehearsal.py)
3. [audit_master_remediation/REV4_TO_10_CI_WORKFLOW_REVIEW.md](file:///D:/ClientS/audit_master_remediation/REV4_TO_10_CI_WORKFLOW_REVIEW.md)
4. [audit_master_remediation/POSTGRESQL_15_RUNTIME_EXECUTION_REPORT.md](file:///D:/ClientS/audit_master_remediation/POSTGRESQL_15_RUNTIME_EXECUTION_REPORT.md)
5. [audit_master_remediation/SUPABASE_AUTH_FIXTURE_VALIDATION.md](file:///D:/ClientS/audit_master_remediation/SUPABASE_AUTH_FIXTURE_VALIDATION.md)
6. [audit_master_remediation/FINAL_REV4_TO_10_INTEGRATION_TEST_REPORT.md](file:///D:/ClientS/audit_master_remediation/FINAL_REV4_TO_10_INTEGRATION_TEST_REPORT.md)

Pemeriksaan ini dilakukan dalam mode **STRICT READ-ONLY** terhadap lingkungan operasional:
- **TIDAK ADA** instruksi `git commit` yang dieksekusi.
- **TIDAK ADA** instruksi `git push` yang dieksekusi.
- **TIDAK ADA** instruksi `git merge` yang dieksekusi.
- **TIDAK ADA** pemicuan eksekusi remote CI yang dijalankan.
- **TIDAK ADA** akses ke Supabase Staging (`bgtxpdydljfeltdimmxr`).
- Database produksi [Client_RumahSakit_Production.db](file:///D:/ClientS/Client_RumahSakit_Production.db) tetap aman, tidak tersentuh, dan Feature Gate tetap `OFF`.

---

## 2. SEKSI A: AUDIT KEAMANAN WORKFLOW

Tinjauan kepatuhan keamanan pada [.github/workflows/rev4_to_10_postgres15_rehearsal.yml](file:///D:/ClientS/.github/workflows/rev4_to_10_postgres15_rehearsal.yml):

| Kriteria Keamanan | Status Audit | Bukti Implementasi pada Workflow |
| :--- | :---: | :--- |
| **Pemicu Alur Kerja** | **LULUS (PASS)** | Dikonfigurasi HANYA dengan `workflow_dispatch:`. Tidak ada pemicu `push` maupun `pull_request` otomatis. |
| **Hak Akses `GITHUB_TOKEN`** | **LULUS (PASS)** | Dibatasi minimum mutlak pada level teratas dengan `permissions: contents: read`. |
| **Pinning Action Pihak Ketiga** | **LULUS (PASS)** | Menggunakan commit SHA penuh: <br>- `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683` (v4.2.2)<br>- `actions/setup-python@0a5c61591373683505ea898e09a3ea4f39ef2b9c` (v5.0.0) |
| **Reproducibility Image DB** | **LULUS (PASS)** | Service container dipin ke versi patch resmi `postgres:15.8-alpine` (bukan tag mengambang `latest`). |
| **Ketiadaan Secret Staging** | **LULUS (PASS)** | Nol secret remote. Tidak ada variabel `SUPABASE_SERVICE_ROLE_KEY` atau URL Supabase Staging. |
| **Ketiadaan Kredensial Produksi**| **LULUS (PASS)** | Password database container di-generate lokal `postgres_rehearsal_pwd` pada `127.0.0.1`. |
| **Isolasi Database Eksternal** | **LULUS (PASS)** | Service container berjalan pada jaringan runner GitHub Actions internal, hancur otomatis setelah selesai. |

---

## 3. SEKSI B: AUDIT PYTHON RUNNER (`run_rev4_to_10_rehearsal.py`)

Pemeriksaan baris demi baris pada [database_migrations/cloud/run_rev4_to_10_rehearsal.py](file:///D:/ClientS/database_migrations/cloud/run_rev4_to_10_rehearsal.py):

| Kriteria Pengujian Runner | Status Audit | Bukti Pemeriksaan Kode |
| :--- | :---: | :--- |
| **Validasi Target Host** | **LULUS (PASS)** | Fungsi `get_db_connection()` memeriksa `host in ('127.0.0.1', 'localhost')`. Jika mengarah ke IP/domain lain, langsung `sys.exit(1)` dengan log fatal. |
| **Validasi Nama Basis Data** | **LULUS (PASS)** | Memblokir koneksi jika `dbname` mengandung kata `production`, `prod`, atau `bgtxpdydljfeltdimmxr`. |
| **Verifikasi Checksum SHA-256** | **LULUS (PASS)** | Stage 0 memverifikasi hash SHA-256 REV4 (`1d92a21...`) dan Migration 10 (`300ab9a...`). Jika tidak cocok, eksekusi langsung berhenti. |
| **Urutan Eksekusi Sekuensial**| **LULUS (PASS)** | Alur eksekusi strictly sekuensial: Stage 0 (Hash) → Stage 1 (Auth Fixture) → Stage 2 (REV4) → Stage 3 (Cek Objek) → Stage 4 (Mig 10) → Stage 5 (RLS) → Stage 6 (Supplier) → Stage 7 (ClientS) → Stage 8 (Concurrency) → Stage 9 (Negative Tests) → Stage 10 (Idempotensi). |
| **Fail-Fast pada Error SQL** | **LULUS (PASS)** | Setiap blok `cur.execute()` ditangkap dalam `try...except` dengan log fatal dan `sys.exit(1)`. Tidak ada silent error. |
| **Manajemen Transaksi & Rollback**| **LULUS (PASS)** | Menggunakan transaksi atomik eksplisit (`BEGIN; ... COMMIT;` / `ROLLBACK;`) pada setiap pengujian hak akses dan RPC. |
| **Ketiadaan Data Produksi** | **LULUS (PASS)** | Seluruh data pengujian berupa EPC fiktif (`E280116060...`), rumah sakit mock, dan UUID terisolasi. |
| **Zero Credential Leakage** | **LULUS (PASS)** | Tidak ada pencatatan password dalam log sistem. |
| **Ketegasan Pernyataan (Assertions)**| **LULUS (PASS)** | Seluruh tahapan menggunakan `assert` eksplisit dengan pesan kegagalan yang jelas (misal: format VC, panjang hash, row counts). |

---

## 4. SEKSI C: AUDIT SUPABASE AUTH FIXTURE

Pemeriksaan integritas replikasi ekosistem GoTrue pada Stage 1:

1. **Komponen Lengkap Terbentuk**:
   - Skema terisolasi: `auth`
   - Tabel identitas pengguna: `auth.users` dengan kolom `id UUID`, `email VARCHAR`, `raw_user_meta_data JSONB`.
   - Fungsi konteks: `auth.uid() RETURNS UUID` berbasis klaim JWT `request.jwt.claim.sub`.
   - Peran bawaan: `anon`, `authenticated`, `service_role` (dengan atribut `BYPASSRLS`).
2. **Identitas Multi-Tenant (Dua Rumah Sakit Berbeda)**:
   - **Rumah Sakit 1 (`RS-SMC-001`, ID: 1)**:
     - Admin: `admin_smc@hospital.org` (`11111111-1111-1111-1111-111111111111`)
     - Operator: `operator_smc@hospital.org` (`22222222-2222-2222-2222-222222222222`)
     - Auditor: `auditor_smc@hospital.org` (`33333333-3333-3333-3333-333333333333`)
   - **Rumah Sakit 2 (`RS-OTHER-002`, ID: 2)**:
     - Operator: `operator_other@hospital.org` (`44444444-4444-4444-4444-444444444444`)
3. **Penegakan Role Switching Nyata (Bukan Superuser Bypass)**:
   - Pengujian RLS pada Stage 5 dan Stage 7 **TIDAK PERNAH** dijalankan sebagai user superuser `postgres`.
   - Runner secara aktif mengeksekusi:
     ```sql
     SET LOCAL ROLE authenticated;
     SET LOCAL "request.jwt.claim.sub" = '22222222-2222-2222-2222-222222222222';
     ```
     dan untuk peran publik anonim:
     ```sql
     SET LOCAL ROLE anon;
     ```
   - Hal ini membuktikan bahwa RLS benar-benar diuji mengevaluasi izin akses pengguna, bukan sekadar dilewati oleh hak superuser.

---

## 5. SEKSI D: AUDIT SKENARIO INTEGRASI RUNNER

Pemeriksaan terhadap 11 aspek pembuktian integrasi fungsional:

| No | Aspek Integrasi Wajib | Metode Pembuktian Aktual pada Runner | Status Audit |
| :---: | :--- | :--- | :---: |
| 1 | **REV4 Database Bersih** | Mengeksekusi 1.738 baris DDL REV4 pada container bersih pasca fixture. | **TERUJI (STAGE 2)** |
| 2 | **Migration 10 Pasca REV4** | Mengeksekusi migrasi RLS 10 secara sekuensial setelah REV4. | **TERUJI (STAGE 4)** |
| 3 | **Supplier Ingestion** | Memanggil RPC `ingest_supplier_package` oleh `service_role` dengan payload valid. | **TERUJI (STAGE 6)** |
| 4 | **Verifikasi Kriptografi Ed25519** | Menghasilkan keypair Ed25519, menandatangani pesan kanonikal, dan memverifikasi kriptografi via `cryptography.hazmat.primitives.asymmetric.ed25519`. Menguji penolakan signature palsu pada Stage 9. | **TERUJI (STAGE 6 & 9)** |
| 5 | **Nonce Replay Protection** | Memvalidasi pencatatan nonce pada `device_nonces` dan membuktikan error constraint `uq_device_nonce` saat duplicate nonce dikirimkan. | **TERUJI (STAGE 6 & 9)** |
| 6 | **Idempotensi Replay** | Mengirimkan paket identik dengan `idempotency_key` sama -> HTTP 200 `ALREADY_INGESTED`. Mengirimkan digest berbeda -> HTTP 409 `IDEMPOTENCY_PAYLOAD_MISMATCH`. | **TERUJI (STAGE 6 & 9)** |
| 7 | **Claim Package** | Memanggil RPC `claim_package` oleh operator RS 1 -> status berubah `claimed`, verifikasi kode dan manifest hash cocok. | **TERUJI (STAGE 7)** |
| 8 | **Confirm Package** | Memanggil RPC `confirm_package` -> status berubah `diterima`, linen masuk ke master table `linen_master`. | **TERUJI (STAGE 7)** |
| 9 | **Isolasi Multi-Tenant** | Operator RS 2 mencoba membaca paket RS 1 via `SELECT` -> terbukti 0 baris (RLS memblokir). Percobaan klaim lintas RS ditolak. | **TERUJI (STAGE 7)** |
| 10 | **Konkurensi Row-Locking** | Menjalankan 2 thread paralel (`threading.Thread`) yang menembak bersamaan via `threading.Barrier` untuk memperebutkan satu paket. Membuktikan `FOR UPDATE` mengizinkan satu pemenang (`SUCCESS`) dan menolak thread kedua (`PACKAGE_ALREADY_CLAIMED`). | **TERUJI (STAGE 8)** |
| 11 | **Audit Trail Logging** | Memeriksa penambahan baris secara fisik pada `supplier_ingestion_audit` dan `linen_audit_log`. | **TERUJI (STAGE 6 & 7)** |

> [!IMPORTANT]
> Runner **TIDAK HANYA** memeriksa keberadaan objek SQL, melainkan secara aktif mengeksekusi operasi transaksi DML, verifikasi matematika tanda tangan digital, konkurensi multi-thread, isolasi tenant, dan re-eksekusi idempotensi.

---

## 6. SEKSI E: AUDIT GITHUB WORKFLOW DISPATCH & STRATEGI PR

### Analisis Persyaratan `workflow_dispatch`:
1. Pada GitHub Actions, sebuah alur kerja dengan pemicu `workflow_dispatch` **hanya akan muncul pada tab Actions antarmuka web (UI) jika berkas `.github/workflows/*.yml` tersebut telah tersedia pada default branch** (yaitu `main` atau `master`).
2. Jika berkas alur kerja hanya di-push ke branch audit (`audit/r2-10-4-postgres-ci`), GitHub tidak akan menampilkan tombol manual "Run workflow" di peramban web.
3. Sesuai instruksi System Owner: *"Jangan mengubah default branch. Siapkan rekomendasi Pull Request khusus untuk memasukkan perangkat CI ke default branch tanpa mengubah source code aplikasi."*

### Rekomendasi Strategi Pull Request (CI-Only PR):
- **Judul PR**: `ci: add isolated postgresql 15 disposable rehearsal workflow and test harness`
- **Target Branch**: `main` (Default Branch)
- **Source Branch**: `audit/r2-10-4-postgres-ci`
- **Cakupan Berkas yang Diizinkan (Whitelist File PR)**:
  1. `.github/workflows/rev4_to_10_postgres15_rehearsal.yml`
  2. `database_migrations/cloud/run_rev4_to_10_rehearsal.py`
  3. Dokumen audit pendukung pada folder `audit_master_remediation/`
- **Cakupan yang DILARANG Masuk PR**:
  - DILARANG memasukkan modifikasi source code aplikasi (`app/`, `tests/`).
  - DILARANG menyentuh konfigurasi produksi.
  - Baseline ClientS tetap R2.10.4.
- **Hasil Setelah PR Di-merge**:
  Alur kerja muncul di antarmuka GitHub Actions, dan System Owner dapat memicu eksekusi kapan saja secara manual melalui tombol "Run workflow" dengan memilih branch yang diinginkan.

---

## 7. SEKSI F: PEMERIKSAAN GIT & DIFF SELESAI

Pemeriksaan status repositori saat ini:
- **Baseline Aplikasi**: ClientS R2.10.4 (Terkunci).
- **Aplikasi Asli Supplier**: `D:\silarisV2-main` tetap STRICT READ-ONLY (Tidak tersentuh).
- **Aplikasi Workstation Supplier**: `D:\RFID_Scanner_App` (Tidak disentuh).
- **File Migrasi SQL**: `00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql` dan `10_unified_dashboard_hospital_monitoring_rls.sql` memiliki hash yang 100% identik dengan standar yang dibekukan.
- **Git Working Tree**: Tidak ada commit baru, tidak ada push remote.

---

## 8. KEPUTUSAN AKHIR CODE REVIEW

Berdasarkan hasil audit mendalam terhadap seluruh aspek keamanan, keandalan skrip, isolasi container, dan protokol pengujian integrasi:

```
=============================================================================
KEPUTUSAN CODE REVIEW: CONDITIONAL APPROVAL
(LULUS TINJAUAN KODE - SIAP DI-COMMIT HANYA JIKA DISETUJUI SYSTEM OWNER)
=============================================================================
```

### Rincian Ketetapan:
1. **Keamanan Kode**: LULUS (Aman dari kebocoran kredensial, bebas akses remote, terisolasi penuh).
2. **Kelengkapan Pengujian**: LULUS (Mencakup seluruh 11 poin pengujian integrasi termasuk Ed25519 dan konkurensi multi-thread).
3. **Status Gate B**: Tetap **`LOCKED`**.
4. **Status Komitmen Git**: **BELUM DI-COMMIT & BELUM DI-PUSH**. Menunggu arahan resmi dan tanda tangan System Owner.

