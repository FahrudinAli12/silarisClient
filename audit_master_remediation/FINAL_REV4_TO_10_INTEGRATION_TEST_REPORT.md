# LAPORAN INTEGRASI AKHIR & STATUS RUNTIME: JALUR REV4 → MIGRATION 10
## SINTESIS HASIL AUDIT, KESIAPAN CI GLADI BERSIH & REKOMENDASI KELAYAKAN
### KONSOLIDASI SUPPLIER WORKSTATION × SUPABASE STAGING × CLIENTS RUMAH SAKIT

**Baseline Aplikasi**: ClientS R2.10.4 (Strictly Frozen)  
**Target Basis Data**: Supabase Staging (`bgtxpdydljfeltdimmxr`)  
**Jalur Deployment**: `REV4` → `Migration 10` (All-in-One + Dashboard RLS)  
**Otoritas Tata Kelola**: Single Owner-Operator Governance (System Owner & Acting DBA)  
**Tanggal Evaluasi**: 16 November 2026  
**Status Gate B**: **LOCKED**  

---

## 1. Ringkasan Eksekutif & Status Akhir

Evaluasi independen terhadap jalur rilis basis data cloud Staging Supabase telah diselesaikan.

Berdasarkan keputusan arsitektur sebelumnya:
1. **Jalur Migrasi Inkremental Lama DITINGGALKAN**:  
   Migrasi berurutan `00` → `02` → `07` → `08` → `09` ditinggalkan karena ditemukan *dependency breaking* (ketiadaan DDL `detail_pengiriman_linen` sebelum skrip `07`, serta penolakan *fail-fast* pada tabel yang sudah ada).
2. **Jalur Tunggal Resmi Ditetapkan**:  
   - Tahap 1: `00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql` (SHA-256: `1d92a21c5f8f968ba7e05f7e56baaf26707578bb393c02a5992e2f596a56f12b`)
   - Tahap 2: `10_unified_dashboard_hospital_monitoring_rls.sql` (SHA-256: `300ab9a7168fbf003778a7283f12ba269c44f210dd9bd6e49c8bcbbec635bf9c`)
3. **Pemisahan Validasi Statik vs Runtime**:
   - Seluruh analisis statik, pemetaan paritas objek, integritas digest SHA-256, dan kontrak lintas aplikasi berstatus **`VERIFIED PASS`** (atau `STATIC PASS`).
   - Eksekusi langsung pada mesin engine PostgreSQL 15 berstatus transparan **`NOT VERIFIED (YET)`** karena GitHub Actions CI Run ID 43665931774 terbukti hanya menjalankan berkas inkremental `07` dan belum pernah mengeksekusi `REV4 → 10`.
4. **Kesiapan Infrastruktur Gladi Bersih**:
   - File workflow baru [.github/workflows/rev4_to_10_postgres15_rehearsal.yml](file:///D:/ClientS/.github/workflows/rev4_to_10_postgres15_rehearsal.yml) dan runner [database_migrations/cloud/run_rev4_to_10_rehearsal.py](file:///D:/ClientS/database_migrations/cloud/run_rev4_to_10_rehearsal.py) telah selesai disiapkan dalam status *uncommitted / untracked*.
   - Tidak ada modifikasi pada source code aplikasi, tidak ada perubahan pada database produksi, dan Gate B tetap **LOCKED**.

---

## 2. Matriks Evaluasi 12 Kategori Pengujian Terpadu

Berikut adalah sintesis menyeluruh terhadap 12 skenario pengujian wajib untuk pembuktian jalur `REV4 → 10`:

| No | Skenario Pengujian Wajib | Target Objek & Invariant | Hasil Verifikasi Statik / Desain | Status Runtime PostgreSQL 15 | Klasifikasi Akhir |
| :---: | :--- | :--- | :---: | :---: | :---: |
| 1 | **Database Awal Bersih** | Inisialisasi basis data kosong tanpa sisa objek skema lama. | PASS (Desain Idempoten) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 2 | **Eksekusi REV4** | Eksekusi `00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql`. | PASS (20 Skenario AST) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 3 | **Verifikasi 12 Tabel & 13 RPC**| Kueri `information_schema` memastikan keutuhan DDL publik. | PASS (100% Paritas Kode) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 4 | **Eksekusi Migration 10** | Eksekusi `10_unified_dashboard_hospital_monitoring_rls.sql`. | PASS (DDL Inkremental Valid)| **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 5 | **Verifikasi 9 Kebijakan RLS** | Memeriksa 9 kebijakan RLS pada `pg_policies` dan least privilege. | PASS (Analisis Kebijakan) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 6 | **Jalankan Regression SQL** | Kueri operasional penerimaan linen dan audit log. | PASS (Query AST Parity) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 7 | **Uji Ingestion Supplier** | Eksekusi `ingest_supplier_package` oleh `service_role`. | PASS (Kontrak P0-01 Cocok) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 8 | **Uji Claim & Confirm** | Transisi status `pending` -> `claimed` -> `diterima` + linen master. | PASS (Kontrak P1-01 - P1-03) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 9 | **Uji Duplicate Nonce** | Penolakan replay nonce oleh unique constraint `uq_device_nonce`. | PASS (Constraint Model) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 10 | **Uji Idempotency Replay** | Replay dengan header idempotency key sama menghasilkan HTTP 200. | PASS (Advisory Lock AST) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 11 | **Uji Concurrency Lock** | Row-locking `FOR UPDATE` mencegah perebutan klaim konkuren. | PASS (Row Lock Model) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |
| 12 | **Uji Isolasi Multi-Tenant** | Operator RS A ditolak saat membaca/mengklaim paket milik RS B. | PASS (Policy Logic) | **`NOT VERIFIED`** | **CONDITIONAL PASS** |

> [!NOTE]
> Seluruh status bertanda **CONDITIONAL PASS** akan secara otomatis berubah menjadi **VERIFIED PASS** begitu workflow CI `.github/workflows/rev4_to_10_postgres15_rehearsal.yml` disetujui dan dijalankan dengan hasil seluruh tahap hijau (PASS).

---

## 3. Matriks Paritas Objek & Kontrak Lintas-Aplikasi

### A. Paritas Basis Data
- **Tabel Terverifikasi (12)**:
  `rumah_sakit`, `hospital_memberships`, `suppliers`, `supplier_hospital_contracts`, `registered_devices`, `device_keys`, `device_nonces`, `pengiriman_linen`, `detail_pengiriman_linen`, `linen_master`, `linen_audit_log`, `supplier_ingestion_audit`.
- **Fungsi RPC Terverifikasi (13)**:
  `register_hospital_admin_membership`, `register_hospital_membership_by_uuid`, `clean_epc`, `clean_metadata_text`, `compute_manifest_hash_from_items`, `compute_package_manifest_hash`, `fn_detail_pengiriman_linen_immutability`, `fn_pengiriman_linen_immutability`, `ingest_supplier_package`, `get_package_info`, `claim_package`, `confirm_package`, `get_package_claim_status`.
- **Kebijakan Row Level Security (9)**:
  1. `p_rumah_sakit_select_member` (pada `rumah_sakit`)
  2. `p_hospital_memberships_select_self` (pada `hospital_memberships`)
  3. `p_pengiriman_linen_hospital_select` (pada `pengiriman_linen`)
  4. `p_detail_pengiriman_linen_hospital_select` (pada `detail_pengiriman_linen`)
  5. `p_supplier_hospital_contracts_member_read` (pada `supplier_hospital_contracts`)
  6. `p_suppliers_member_read` (pada `suppliers`)
  7. `p_registered_devices_member_read` (pada `registered_devices`)
  8. `p_audit_events_hospital_select` (kebijakan audit REV4)
  9. `p_package_cloud_events_hospital_select` (kebijakan event REV4)

### B. Kesesuaian Kontrak Aplikasi
1. **Workstation Pemindai Supplier (`D:\RFID_Scanner_App`)**:
   - Skema payload JSON: cocok 100% dengan RPC `ingest_supplier_package`.
   - Kunci publik Ed25519: `7f7ed1969b4bbcf02baf063e2630dba1ef59813e7756cd360d50a0bc15972dec` terdaftar aktif pada seed `device_keys`.
   - Hash manifest kanonikal: algoritma SHA-256 whitespace regex + Unicode NFC identik dengan implementasi Python dan PL/pgSQL.
2. **Aplikasi Client Rumah Sakit (`D:\ClientS`)**:
   - Baseline R2.10.4 FastAPI services:
     `package_receiving_service.py` memanggil `get_package_info`, `claim_package`, `get_package_claim_status`, dan `confirm_package` dengan tanda tangan parameter yang 100% harmonis dengan skema REV4.
   - Database lokal [Client_RumahSakit_Production.db](file:///D:/ClientS/Client_RumahSakit_Production.db) tetap aman, tidak tersentuh, dan Feature Gate `ENABLE_PACKAGE_RECEIVING_API = 0` (OFF) tetap terjaga.

---

## 4. Status Manajemen Perubahan & Perangkat Lunak

```
Workspace            : D:\ClientS
Git Branch           : audit/r2-10-4-postgres-ci
Git Commit Status    : UNCOMMITTED / UNTRACKED
File Alur Kerja Baru : .github/workflows/rev4_to_10_postgres15_rehearsal.yml (UNTRACKED)
Skrip Runner Baru    : database_migrations/cloud/run_rev4_to_10_rehearsal.py (UNTRACKED)
Perubahan Source Code: ZERO (Tidak ada perubahan pada app/, tests/, dll.)
Modifikasi Remote    : ZERO (Tidak ada push, tidak ada deployment, tidak ada remote call)
```

---

## 5. Rekomendasi Keputusan Akhir bagi System Owner

1. **Mempertahankan Status Gate B Terkunci (LOCKED)**:
   Deployment ke Supabase Staging `bgtxpdydljfeltdimmxr` belum boleh dilakukan sebelum gladi bersih pada service container PostgreSQL 15 berhasil dibuktikan.
2. **Menyetujui & Menjalankan Gladi Bersih CI**:
   System Owner dapat memeriksa diff dari file-file yang baru disiapkan. Apabila disetujui, System Owner dapat memberikan instruksi untuk melakukan *commit* dan *push* secara spesifik terhadap file workflow dan test runner tersebut ke branch audit, kemudian memicu workflow `rev4_to_10_postgres15_rehearsal.yml` secara manual melalui antarmuka GitHub Actions.
3. **Persetujuan Rilis Pasca-Gladi Bersih**:
   Setelah gladi bersih CI menghasilkan status SUCCESS (Hijau di seluruh 10 stage), maka dokumen persetujuan formal:
   - `SYSTEM_OWNER_APPROVAL_REQUEST.md` (SO-REQ-2026-11-GATE-B)
   - `DBA_ROLE_ASSIGNMENT_AND_SELF_REVIEW_ADDENDUM.md` (CR-SMC-2026-11-GATE-B)
   dapat ditandatangani secara resmi untuk membuka Gate B.

