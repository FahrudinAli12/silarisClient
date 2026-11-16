# TINJAUAN ARSITEKTUR & KESELAMATAN ALUR KERJA CI GLADI BERSIH POSTGRESQL 15
## REVIEW FILE WORKFLOW: .github/workflows/rev4_to_10_postgres15_rehearsal.yml
### JALUR DEPLOYMENT: REV4 → MIGRATION 10 | BASELINE: R2.10.4

**Tanggal Review**: 16 November 2026  
**Otoritas Tata Kelola**: Single Owner-Operator Governance (System Owner & Acting DBA)  
**Status Gate B**: **LOCKED**  
**Status Eksekusi Remote CI**: **NOT TRIGGERED (MENUNGGU PERSETUJUAN EKSPLISIT PEMILIK)**  

---

## 1. Ringkasan Eksekutif & Tujuan Alur Kerja

Berdasarkan temuan forensik bahwa alur kerja CI sebelumnya ([.github/workflows/postgresql_runtime_validation.yml](file:///D:/ClientS/.github/workflows/postgresql_runtime_validation.yml)) hanya menguji skrip inkremental `07_package_communication_rpc_consolidated_r2_10_4.sql` dan **tidak pernah mengeksekusi** `00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql` maupun `10_unified_dashboard_hospital_monitoring_rls.sql`, maka disiapkan alur kerja GitHub Actions baru yang terisolasi secara mutlak:

**File Baru**: [rev4_to_10_postgres15_rehearsal.yml](file:///D:/ClientS/.github/workflows/rev4_to_10_postgres15_rehearsal.yml)  
**Tujuan**: Melakukan gladi bersih (*dry-run / runtime rehearsal*) sekuensial penuh terhadap:
1. `00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql` (SHA-256: `1d92a21c5f8f968ba7e05f7e56baaf26707578bb393c02a5992e2f596a56f12b`)
2. `10_unified_dashboard_hospital_monitoring_rls.sql` (SHA-256: `300ab9a7168fbf003778a7283f12ba269c44f210dd9bd6e49c8bcbbec635bf9c`)

pada mesin PostgreSQL 15 murni yang bersifat *disposable* (sekali pakai lalu hancur otomatis).

---

## 2. Prinsip Keselamatan & Batasan Pengoperasian

Alur kerja dirancang dengan mematuhi prinsip keselamatan ketat sesuai arahan System Owner:

| Parameter Keselamatan | Implementasi pada Workflow | Status Kepatuhan |
| :--- | :--- | :---: |
| **Pemicu Alur Kerja (Trigger)** | Dikonfigurasi HANYA dengan `workflow_dispatch:`. Tidak ada pemicu `push` maupun `pull_request`. | **TERTUTUP (MANUAL ONLY)** |
| **Akses Jaringan Remote** | DILARANG menyambung ke Supabase Staging (`bgtxpdydljfeltdimmxr`) maupun Production. Seluruh pengujian berjalan pada `127.0.0.1`. | **TERISOLASI 100%** |
| **Integritas Database Produksi**| [Client_RumahSakit_Production.db](file:///D:/ClientS/Client_RumahSakit_Production.db) tidak pernah diakses atau dimodifikasi. | **TERJAGA (READ-ONLY)** |
| **Workflow yang Sudah Ada** | [.github/workflows/postgresql_runtime_validation.yml](file:///D:/ClientS/.github/workflows/postgresql_runtime_validation.yml) dibiarkan utuh tanpa perubahan 1 byte pun. | **TIDAK DIMODIFIKASI** |
| **Source Code Aplikasi** | Baseline ClientS tetap R2.10.4. Tidak ada bump versi ke R2.10.5 atau R2.11. | **DIBEKUKAN** |
| **Siklus Hidup Wadah (Container)**| Service container `postgres:15-alpine` dimusnahkan otomatis begitu alur kerja selesai. | **DISPOSABLE MUTLAK** |

---

## 3. Spesifikasi Teknis Konfigurasi Alur Kerja

Berikut adalah struktur lengkap berkas [.github/workflows/rev4_to_10_postgres15_rehearsal.yml](file:///D:/ClientS/.github/workflows/rev4_to_10_postgres15_rehearsal.yml):

```yaml
name: PostgreSQL 15 Disposable Rehearsal (REV4 to Migration 10)

on:
  workflow_dispatch:
    inputs:
      rehearsal_tag:
        description: 'Tag atau catatan pengujian gladi bersih'
        required: false
        default: 'REV4_TO_10_REHEARSAL_MANUAL'

jobs:
  postgres15-disposable-rehearsal:
    name: Isolated Disposable PostgreSQL 15 Rehearsal
    runs-on: ubuntu-latest
    timeout-minutes: 20

    services:
      postgres:
        image: postgres:15-alpine
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres_rehearsal_pwd
          POSTGRES_DB: clients_rehearsal
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10

    steps:
      - name: 1. Checkout Repository
        uses: actions/checkout@v4

      - name: 2. Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: 3. Install Rehearsal Dependencies
        run: |
          python -m pip install --upgrade pip
          pip install psycopg2-binary cryptography pytest

      - name: 4. Execute Disposable Rehearsal Runner (REV4 -> Migration 10)
        env:
          PGHOST: 127.0.0.1
          PGPORT: 5432
          PGUSER: postgres
          PGPASSWORD: postgres_rehearsal_pwd
          PGDATABASE: clients_rehearsal
        run: |
          python database_migrations/cloud/run_rev4_to_10_rehearsal.py
```

---

## 4. Analisis Komponen Runner `run_rev4_to_10_rehearsal.py`

Skrip eksekusi [run_rev4_to_10_rehearsal.py](file:///D:/ClientS/database_migrations/cloud/run_rev4_to_10_rehearsal.py) dibagi menjadi 10 tahap logis terstruktur:

1. **Stage 0: Pre-Flight Integrity Check**:
   - Memvalidasi digest SHA-256 berkas REV4 (`1d92a21c5f8f968ba7e05f7e56baaf26707578bb393c02a5992e2f596a56f12b`) dan Migration 10 (`300ab9a7168fbf003778a7283f12ba269c44f210dd9bd6e49c8bcbbec635bf9c`). Jika berbeda 1 bit, alur kerja langsung berhenti (*fail-fast*).
   - Memeriksa konektivitas PostgreSQL. Jika dijalankan di lingkungan lokal tanpa database, skrip memberikan informasi yang jelas dan tidak melakukan klaim palsu.
2. **Stage 1: Supabase Auth Minimal Fixture**:
   - Membentuk skema `auth`, tabel `auth.users`, fungsi `auth.uid()`, serta peran `anon`, `authenticated`, dan `service_role` (dengan opsi `BYPASSRLS`).
   - Menyemai 4 akun pengujian untuk memverifikasi isolasi tenant rumah sakit dan hierarki RBAC.
3. **Stage 2: Eksekusi DDL REV4**:
   - Mengeksekusi seluruh 1.738 baris DDL `00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql`.
4. **Stage 3: Validasi Objek Pasca-REV4**:
   - Memastikan ke-12 tabel dan 13 fungsi RPC telah terbentuk sempurna di skema `public`.
5. **Stage 4: Eksekusi Migration 10**:
   - Mengeksekusi DDL inkremental `10_unified_dashboard_hospital_monitoring_rls.sql`.
6. **Stage 5: Validasi RLS & Penegakan Hak Akses Minimum**:
   - Memeriksa ke-9 kebijakan RLS aktif.
   - Menguji bahwa peran `authenticated` hanya berhak SELECT pada data rumah sakitnya sendiri dan DITOLAK saat melakukan INSERT/UPDATE/DELETE.
   - Menguji bahwa peran `anon` DITOLAK saat melakukan SELECT.
7. **Stage 6: Integrasi Ingestion Supplier**:
   - Menguji pembuatan nonce anti-replay, perhitungan canonical payload digest SHA-256, dan pemanggilan RPC `ingest_supplier_package` oleh `service_role`.
   - Menguji penerbitan Verification Code (`VC-XXXXXXXX`), Manifest Hash kanonikal, dan pembentukan baris detail.
   - Menguji *idempotency replay*: pengiriman ulang paket yang sama mengembalikan HTTP 200 `ALREADY_INGESTED`.
8. **Stage 7: Integrasi ClientS Lifecycle**:
   - Menguji `get_package_info`, `claim_package`, `get_package_claim_status`, dan `confirm_package` oleh operator RS berwenang.
   - Menguji isolasi multi-tenant: Operator RS 2 tidak dapat melihat atau mengklaim paket milik RS 1.
9. **Stage 8: Skenario Kegagalan (Negative Tests)**:
   - Replay Nonce ditolak oleh unique constraint `uq_device_nonce`.
   - Payload digest mismatch ditolak dengan status HTTP 409 `IDEMPOTENCY_PAYLOAD_MISMATCH`.
   - Pengiriman tanpa kontrak aktif ditolak dengan HTTP 403 `CONTRACT_INACTIVE`.
   - Klaim paket oleh peran Auditor ditolak dengan `INSUFFICIENT_PRIVILEGES`.
   - Percobaan manipulasi baris `detail_pengiriman_linen` diblokir oleh trigger immutability `fn_detail_pengiriman_linen_immutability`.
10. **Stage 9: Idempotensi & Re-eksekusi**:
    - Menjalankan ulang REV4 dan Migration 10 pada database yang telah terisi data pengujian.
    - Membuktikan bahwa tidak terjadi error sintaks, tidak terjadi duplikasi objek, dan seluruh baris data tetap utuh (*zero data loss*).

---

## 5. Status Manajemen Perubahan (Git Status & Batasan)

Sesuai instruksi System Owner:
- File alur kerja [.github/workflows/rev4_to_10_postgres15_rehearsal.yml](file:///D:/ClientS/.github/workflows/rev4_to_10_postgres15_rehearsal.yml) dan skrip runner [database_migrations/cloud/run_rev4_to_10_rehearsal.py](file:///D:/ClientS/database_migrations/cloud/run_rev4_to_10_rehearsal.py) berstatus **UNTRACKED / UNCOMMITTED**.
- **TIDAK ADA** instruksi `git commit` yang dieksekusi.
- **TIDAK ADA** instruksi `git push` yang dieksekusi.
- **TIDAK ADA** pemicuan eksekusi remote CI yang dijalankan.
- Status eksekusi runtime PostgreSQL 15 tetap secara transparan dinyatakan sebagai:  
  **`NOT VERIFIED (YET)`** hingga alur kerja di-trigger secara sadar oleh System Owner.

