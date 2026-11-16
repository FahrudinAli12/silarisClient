# LAPORAN EVALUASI & PERSIAPAN RUNTIME POSTGRESQL 15
## STATUS PENGUJIAN RUNTIME JALUR REV4 → 10
### AUDIT FORENSIK CI RUN 43665931774 & PROTOKOL GLADI BERSIH DISPOSABLE

**Baseline Aplikasi**: ClientS R2.10.4  
**Target Basis Data**: PostgreSQL 15+ (Engine Supabase Staging & Docker Container)  
**Otoritas Evaluasi**: Single Owner-Operator Governance (System Owner & Acting DBA)  
**Status Gate B**: **LOCKED**  
**Status Runtime Aktual**: **`NOT VERIFIED (YET)`**  

---

## 1. Fakta Forensik CI Run 43665931774

Sebelum melangkah ke tahap deployment apa pun, System Owner menginstruksikan investigasi menyeluruh: *"Periksa apakah GitHub Actions Run ID 43665931774 benar-benar menjalankan REV4 → 10. Jika belum, jangan mengklaim kedua file sudah lolos runtime."*

Hasil audit forensik terhadap log eksekusi aktual Run ID 43665931774 membuktikan fakta-fakta berikut:

```
Commit Hash CI  : 1c028ba (Branch: audit/r2-10-4-postgres-ci)
Workflow File   : .github/workflows/postgresql_runtime_validation.yml
Langkah 1       : python database_migrations/cloud/verify_ddl_r2_10_4.py
                  -> Hanya memeriksa AST DDL file 07_package_communication_rpc_consolidated_r2_10_4.sql
Langkah 2       : python database_migrations/cloud/verify_vectors_r2_10_4.py
                  -> Hanya memeriksa 12 test vector manifest hash Python vs PL/pgSQL
Langkah 3       : python database_migrations/cloud/run_concurrency_tests_r2_10_4.py
                  -> Menjalankan container disposable postgres:15-alpine
                  -> Skrip Python secara artifisial membuat tabel ad-hoc (hardcoded DDL)
                  -> Menjalankan file 07_package_communication_rpc_consolidated_r2_10_4.sql
                  -> TIDAK PERNAH membaca berkas 00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql
                  -> TIDAK PERNAH membaca berkas 10_unified_dashboard_hospital_monitoring_rls.sql
Langkah 4       : pytest tests/ (75 tests)
                  -> Hanya menguji FastAPI ClientS dengan SQLite mock / memory database
```

> [!WARNING]
> **TEMUAN EMPIRIS MUTLAK**:  
> Berkas `00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql` dan `10_unified_dashboard_hospital_monitoring_rls.sql` **BELUM PERNAH SEKALI PUN DIEKSEKUSI PADA MESIN POSTGRESQL NYATA DI CI RUN 43665931774**.  
> Oleh karena itu, klaim kelulusan runtime untuk jalur `REV4 → 10` pada sesi audit sebelumnya dikoreksi secara tegas menjadi **`NOT VERIFIED`**.

---

## 2. Pemisahan Bukti: Analisis Statik (PASS) vs Runtime Aktual (NOT VERIFIED)

Untuk menjaga kejujuran ilmiah dan integritas tata kelola, evaluasi dibagi menjadi dua domain yang terpisah secara tegas:

| Parameter Evaluasi | Metode Verifikasi | Bukti Empiris | Status Audit |
| :--- | :--- | :--- | :---: |
| **Integritas SHA-256 REV4** | Hash Digest 64-hex | `1d92a21c5f8f968ba7e05f7e56baaf26707578bb393c02a5992e2f596a56f12b` | **VERIFIED PASS** |
| **Integritas SHA-256 Mig 10**| Hash Digest 64-hex | `300ab9a7168fbf003778a7283f12ba269c44f210dd9bd6e49c8bcbbec635bf9c` | **VERIFIED PASS** |
| **Sintaks DDL REV4** | Static SQL Parsing & AST | 12 tabel, 13 fungsi, 2 trigger, search_path eksplisit | **STATIC PASS** |
| **Sintaks DDL Migration 10** | Static SQL Parsing & AST | 3 tabel diaktifkan RLS, 3 kebijakan RLS, REVOKE/GRANT | **STATIC PASS** |
| **Paritas Objek Database** | Cross-Check Kode DDL | Mencakup 100% objek dari migrasi inkremental 00 s.d 09 | **STATIC PASS** |
| **Kesesuaian Kontrak Aplikasi**| Perbandingan Skema vs API | Kontrak Supplier (P0-01) & ClientS FastAPI (P1-01 s.d P1-03) cocok | **STATIC PASS** |
| **Eksekusi Mesin PostgreSQL 15**| Eksekusi SQL pada Engine | Memerlukan koneksi engine PostgreSQL 15 aktif | **NOT VERIFIED (YET)** |
| **Verifikasi Runtime RLS** | Pengujian Sesi Authenticated | Memerlukan isolasi transaksi pada engine aktif | **NOT VERIFIED (YET)** |
| **Verifikasi Ingestion & Nonce**| RPC Ingestion & Anti-Replay | Memerlukan pemanggilan RPC langsung di basis data | **NOT VERIFIED (YET)** |
| **Verifikasi Idempotensi Runtime**| Re-eksekusi DDL pada Data | Memerlukan basis data terisi data operasional | **NOT VERIFIED (YET)** |

---

## 3. Protokol Pengujian Gladi Bersih 10 Tahap

Untuk memverifikasi secara nyata tanpa menyentuh Supabase Staging (`bgtxpdydljfeltdimmxr`) maupun database produksi [Client_RumahSakit_Production.db](file:///D:/ClientS/Client_RumahSakit_Production.db), telah disiapkan runner pengujian otomatis:

**Skrip Runner**: [database_migrations/cloud/run_rev4_to_10_rehearsal.py](file:///D:/ClientS/database_migrations/cloud/run_rev4_to_10_rehearsal.py)  
**File Alur Kerja CI**: [.github/workflows/rev4_to_10_postgres15_rehearsal.yml](file:///D:/ClientS/.github/workflows/rev4_to_10_postgres15_rehearsal.yml)

### Matriks Protokol Pengujian Gladi Bersih:

| Tahap | Skenario Pengujian | Aksi & Invariant yang Diuji | Kriteria Kelulusan |
| :---: | :--- | :--- | :--- |
| **Stage 0** | **Pre-flight & SHA-256** | Verifikasi hash SHA-256 kedua file SQL sebelum eksekusi. | Hash 100% identik dengan standar yang dibekukan. |
| **Stage 1** | **Supabase Auth Fixture** | Bootstrap skema `auth`, tabel `auth.users`, fungsi `auth.uid()`, dan 3 peran (`anon`, `authenticated`, `service_role`). | Skema auth terbentuk, 4 akun uji terdaftar. |
| **Stage 2** | **Eksekusi DDL REV4** | Eksekusi seluruh isi berkas `00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql`. | Transaksi COMMIT tanpa syntax error atau missing object. |
| **Stage 3** | **Verifikasi Objek REV4** | Kueri `information_schema` untuk 12 tabel dan 13 RPC publik. | 12 tabel dan 13 RPC terverifikasi secara fisik. |
| **Stage 4** | **Eksekusi Migration 10** | Eksekusi `10_unified_dashboard_hospital_monitoring_rls.sql`. | Transaksi COMMIT tanpa syntax error. |
| **Stage 5** | **Verifikasi RLS & Least Privilege** | Evaluasi 9 kebijakan RLS pada `pg_policies` dan pengujian hak akses peran `authenticated` vs `anon`. | Operator RS 1 hanya melihat data RS 1; INSERT/UPDATE/DELETE ditolak; Anon ditolak. |
| **Stage 6** | **Integrasi Ingestion Supplier** | Eksekusi RPC `ingest_supplier_package` oleh `service_role` dengan SHA-256 payload digest dan nonce. | Return HTTP 200, Verification Code terbit, status `terkirim`, Replay Idempoten menghasilkan `ALREADY_INGESTED`. |
| **Stage 7** | **Integrasi ClientS Lifecycle** | Eksekusi alur `get_package_info` -> `claim_package` -> `get_package_claim_status` -> `confirm_package`. | Status transisi menjadi `diterima`, linen masuk ke `linen_master`, audit log tercatat. Operator RS 2 terisolasi. |
| **Stage 8** | **Skenario Kegagalan (Negative Tests)**| Uji duplicate nonce, mismatch payload digest (HTTP 409), kontrak tidak aktif (HTTP 403), klaim auditor (ditolak), dan pelanggaran immutability. | Seluruh pelanggaran keamanan berhasil digagalkan sesuai spesifikasi. |
| **Stage 9** | **Idempotensi & Re-eksekusi** | Menjalankan ulang REV4 dan Migration 10 pada basis data yang telah memuat transaksi aktif. | Eksekusi ulang SUKSES tanpa error; zero duplicate objects; zero data loss. |

---

## 4. Evaluasi Lokal Mesin Pengembang Windows Saat Ini

Ketika [run_rev4_to_10_rehearsal.py](file:///D:/ClientS/database_migrations/cloud/run_rev4_to_10_rehearsal.py) dijalankan secara lokal pada workstation pengembang:
- Skrip berhasil memvalidasi SHA-256 kedua file SQL (Stage 0: PASS).
- Skrip mendeteksi bahwa layanan PostgreSQL 15 tidak berjalan pada host lokal (hal ini wajar pada workstation pengembangan tanpa instalasi daemon PostgreSQL).
- Skrip tidak memaksakan klaim palsu dan secara jujur mengeluarkan log peringatan:
  `HASIL PEMERIKSAAN STATIK: PASS (File DDL, Hash, & Kontrak Siap)`  
  `HASIL RUNTIME ENGINE POSTGRESQL 15: NOT VERIFIED (YET)`

---

## 5. Rekomendasi Langkah Selanjutnya untuk System Owner

1. **Tetap Kunci Gate B**:
   Status deployment remote Staging `bgtxpdydljfeltdimmxr` tetap **LOCKED**.
2. **Review Alur Kerja CI**:
   System Owner dapat meninjau diff alur kerja baru [.github/workflows/rev4_to_10_postgres15_rehearsal.yml](file:///D:/ClientS/.github/workflows/rev4_to_10_postgres15_rehearsal.yml) dan skrip runner [database_migrations/cloud/run_rev4_to_10_rehearsal.py](file:///D:/ClientS/database_migrations/cloud/run_rev4_to_10_rehearsal.py).
3. **Eksekusi Gladi Bersih (Opsional atas Izin Pemilik)**:
   Jika System Owner menyetujui, alur kerja baru tersebut dapat di-commit, di-push ke branch audit, dan dipicu secara manual via GitHub Actions UI (`workflow_dispatch`).
4. **Validasi Pasca-Gladi Bersih**:
   Setelah alur kerja CI disposable selesai dengan status HIJAU (PASS), maka status runtime engine PostgreSQL 15 untuk jalur `REV4 → 10` baru dapat secara resmi ditingkatkan menjadi **`VERIFIED PASS`**.

