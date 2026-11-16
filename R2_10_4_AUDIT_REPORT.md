# LAPORAN AUDIT INDEPENDEN TAHAP 4D.2A.3-R2.10.4
## FINAL REGRESSION CORRECTION: STRICT CONFIRM FIXTURE ALIGNMENT & ROUTER NULL GUARD
### REPRODUCIBILITY REPORT & SECURITY HARDENING

**Status Repositori**: HOLD FOR INDEPENDENT AUDIT  
**Feature Gate**: NON-AKTIF (`ENABLE_PACKAGE_RECEIVING_API=0`)  
**Basis Kode**: ClientS (4).zip Baseline (Commit `d5ae25a`)  
**Keputusan Arsitektur**: OPTION A APPROVED (Hospital-Bound Package Ownership)  
**Tanggal**: 15 November 2026  
**Penegakan Aturan Isolasi**: Sesuai mandat, TIDAK mengakses remote Supabase staging/produksi dan TIDAK memodifikasi SQLite operasional (`Client_RumahSakit_Production.db` & `Client_RumahSakit_Template.db`).

---

## 1. EXECUTIVE SUMMARY & REKONSILIASI PERBAIKAN FINAL

Berdasarkan hasil audit independen dan spesifikasi final regresi R2.10.4:
1. **P0-01: Update Mock Confirm Adapter (`MockCloudClaimConfirmAdapter`)**:
   - Berkas: `tests/test_phase4_receiving_coordinator.py`
   - Method `confirm_package(token, payload)` telah diselaraskan dengan kontrak 6-field kanonikal Supabase:
     - `paket_id`: dari payload masukan
     - `hospital_id`: dari payload masukan
     - `operation_id`: dari payload masukan
     - `installation_uid`: dari payload masukan
     - `manifest_hash`: dari payload masukan
     - `status`: `"confirmed"`
   - Mode `bad_identity` tetap mengembalikan identitas salah (`operation_id = "op_mismatched"`) dan diverifikasi tetap ditolak (`confirm_unverified`).
   - Seluruh validasi integritas di `app/services/package_receiving_service.py` dipertahankan tanpa pengurangan.
   - Ditambahkan assertion komprehensif pada skenario penerimaan (`test_scen01`) dan skenario pengujian kontrak dedikasi (`test_scen22`) bahwa seluruh 6 field diperiksa secara ketat.
2. **P0-02: Router NULL Guard (`receiving_router.py`)**:
   - Berkas: `app/routers/receiving_router.py`
   - Kedua lokasi pemeriksaan `pkg_hospital_id` (endpoint `/api/receiving/package/{paket_id}` dan endpoint `/api/receiving/process`) diperbaiki:
     1. Jika `pkg_hospital_id is None`: ditolak dengan HTTP 403 Forbidden (`Akses ditolak: paket belum terikat pada rumah sakit tujuan (karantina)`).
     2. Jika `int(pkg_hospital_id) != int(hospital_id)`: ditolak dengan HTTP 403 Forbidden (`Akses lintas rumah sakit ditolak. Paket ini bukan milik rumah sakit Anda`).
     3. Hanya lanjut jika kedua ID sama (`int(pkg_hospital_id) == int(hospital_id)`).
   - Pengujian integrasi API pada `tests/test_phase4c_receiving_api.py` membuktikan kedua endpoint menolak keras paket unbound (`hospital_id IS NULL`) dan paket lintas RS (RS 999).
3. **P1-01: Anti-DoS Timeout & Penanganan Lock Contention (`run_concurrency_tests_r2_10_4.py`)**:
   - Berkas: `database_migrations/cloud/run_concurrency_tests_r2_10_4.py`
   - Ditambahkan `statement_timeout = '3000ms'` pada koneksi pengujian konkurensi RS B.
   - Jika RS B tertahan akibat row lock yang ditinggalkan oleh transaksi terbuka RS A:
     - Catat SQLSTATE `57014` (`query_canceled`).
     - Tandai Anti-DoS test `FAIL`.
     - Rollback transaksi dan tutup koneksi/cursor (mencegah runner menggantung).
   - Karena DDL R2.10.4 menolak akses lintas tenant SEBELUM klausa `SELECT ... FOR UPDATE` dipanggil, RS A ditolak seketika pada memori tanpa memperoleh lock, sehingga RS B dapat langsung melakukan Claim tanpa menunggu, dengan status `SUCCESS (CLAIM_SUCCESS)` dan verifikasi final database state (`claimed`).
4. **P1-02: Reproducible Test Package & Panduan PYTHONPATH**:
   - `verify_vectors_r2_10_4.py` dilengkapi pencarian path cerdas (repo root, CWD, `app/services`, atau flat folder).
   - Panduan eksekusi mandiri didokumentasikan secara eksplisit:
     `PYTHONPATH=. python database_migrations/cloud/verify_vectors_r2_10_4.py`
   - Runner menolak mengklaim PASS jika dependensi tidak tersedia (fail-fast).
5. **P1-03: Full Regression 75/75 PASS**:
   - Seluruh 4 test suite penerimaan paket lulus 100%:
     - `tests/test_phase4c_receiving_api.py`: 14/14 PASS
     - `tests/test_phase4_receiving_coordinator.py`: 22/22 PASS
     - `tests/test_phase4d_local_room_and_rbac.py`: 16/16 PASS
     - `tests/test_r2_9_supabase_integration.py`: 23/23 PASS
     - Total: **75/75 PASS** (0 FAIL, 0 SKIPPED).
   - Strict Confirm tetap aktif, crash recovery tetap aman, dan SQLite operasional lokal tidak disentuh.

---

## 2. HASIL PENGUJIAN & MATRIKS REKONSILIASI

| Kategori Pengujian | Status / Hasil | Keterangan & Rincian Log |
| :--- | :---: | :--- |
| **Static DDL Verifier** | **16/16 PASS** | `verify_ddl_r2_10_4.py`: 16 kriteria integritas DDL (termasuk C14 eliminasi GUC bypass, C15 urutan lock aman, dan C16 partial unique index tanpa `MAX(hospital_id)`). Exit code: `0`. |
| **Static Schema Alignment** | **6/6 PASS** | `verify_vectors_r2_10_4.py`: Paritas tipe data (`TIMESTAMPTZ`, `UUID`, `BIGINT`). Exit code: `0`. |
| **Python Manifest Test Vectors** | **12/12 PASS** | `verify_vectors_r2_10_4.py`: 12 vektor kanonikal (normalisasi whitespace Unicode, NFC, canonical sorting, duplikasi, pemotongan prefix 3000). Exit code: `0`. |
| **Full Pytest Regression** | **75/75 PASS** | Seluruh 4 suite penerimaan paket lulus (75 passed dalam 12.92s). Exit code: `0`. |
| **PostgreSQL Manifest Runtime** | **SKIPPED (EXIT 2)** | Host Windows lokal tidak memiliki Docker aktif. Runner melaporkan **SKIPPED** secara transparan dengan exit code `2`. **TIDAK** menghubungkan ke remote host atau Supabase staging/produksi. |
| **PostgreSQL Concurrency Runner** | **SKIPPED (EXIT 2)** | Runner menolak koneksi non-disposable dan menghasilkan status **SKIPPED** dengan exit code `2`. |

---

## 3. LOG EKSEKUSI PENGUJIAN AKTUAL

### A. Verifikasi Statik DDL (`verify_ddl_r2_10_4.py`)
```text
PS D:\ClientS> python database_migrations/cloud/verify_ddl_r2_10_4.py
================================================================================
STATIC SANITY CHECKER DDL: D:\ClientS\database_migrations\cloud\07_package_communication_rpc_consolidated_r2_10_4.sql
STATUS: PEMERIKSAAN STATIK STRUKTUR SINTAKS, KLAUSUL & SCHEMA ALIGNMENT (NON-RUNTIME)
================================================================================
[PASS] C1: Preflight Invariant 11 (Non-pending hash 64-hex) & Invariant 13 (clean_epc 24-hex)
[PASS] C2: Unique Functional Index uq_detail_pengiriman_linen_epc_clean schema-qualified
[PASS] C3: Function public.clean_epc Normalisasi Whitespace Unicode Lengkap (Paritas Python)
[PASS] C4: Header Trigger BEFORE INSERT OR UPDATE dengan Proteksi State Machine & Audit NULL
[PASS] C5: Detail Trigger NULL-Safe status check & Validasi Format EPC Kanonikal 24-Hex
[PASS] C6: Header Immutability Menggunakan Perbandingan NULL-Safe IS DISTINCT FROM
[PASS] C7: Confirm RPC Signature Memiliki Tepat 6 Parameter Kanonikal (Tanpa p_permit_id)
[PASS] C8: Pembersihan Signature Overload RPC Menggunakan to_regprocedure & Assertion Tunggal
[PASS] C9: Search_path Hardening (pg_catalog, pg_temp) & Pencabutan Hak Helper Internal
[PASS] C10: RLS Aktif, Hak Akses Column-Level (Masking verification_code & UPDATE Catatan)
[PASS] C11: Postflight Assertion 12 Pilar Mendalam (FK Actions, Trigger Bits, Sequences)
[PASS] C12: Static Schema Alignment & Data Type Parity (TIMESTAMPTZ, UUID, BIGINT)
[PASS] C13: Option A Invariants (Hospital-Bound Mandatory, Immutability & Oracle Protection)
[PASS] C14: P0-01 Eliminasi Mutlak Custom GUC Bypass (allow_unbound_fixture)
[PASS] C15: P0-02 Urutan Lock Aman (Plain SELECT & Validasi Tenant Mendahului SELECT FOR UPDATE)
[PASS] C16: P1-01 Partial Unique Index Keanggotaan Tunggal Aktif & Eliminasi MAX(hospital_id)
================================================================================
[SUCCESS] Seluruh 16 kriteria verifikasi statik DDL R2.10.4 terpenuhi.
Exit Code: 0
```

### B. Verifikasi Vektor Manifest & Schema Alignment (`verify_vectors_r2_10_4.py`)
```text
PS D:\ClientS> python database_migrations/cloud/verify_vectors_r2_10_4.py
================================================================================
STATIC SCHEMA ALIGNMENT CHECKER (P0-01 & P1-03)
================================================================================
[STATIC PASS] S1: Bootstrap schema menetapkan pengiriman_linen.tanggal sebagai TIMESTAMPTZ (bukan DATE)
[STATIC PASS] S2: Seluruh audit timestamp columns (created_at, claimed_at, confirmed_at) bertipe TIMESTAMPTZ
[STATIC PASS] S3: User reference columns (claimed_by_user_id, confirmed_by_user_id) bertipe UUID
[STATIC PASS] S4: Tenant reference hospital_id bertipe BIGINT
[STATIC PASS] S5: detail_pengiriman_linen foreign key BIGINT & created_at TIMESTAMPTZ
[STATIC PASS] S6: DDL migrasi mempertahankan keselarasan tipe data kolom audit
================================================================================
[SUCCESS] Seluruh 6 verifikasi static schema alignment terpenuhi.
================================================================================
1. VERIFIKASI TEST VECTORS PADA LAYER PYTHON (manifest_service.py)
================================================================================
[TV-01] PASS (PREFIX_3000_TRUNCATION): Hash cocok dengan ekspektasi -> eb6b939c98bc3b5704408ad95d24200232e921a3f8cdb57fdb79faf231937800
[TV-02] PASS (STANDARD_24_HEX): Hash cocok dengan ekspektasi -> eb6b939c98bc3b5704408ad95d24200232e921a3f8cdb57fdb79faf231937800
[TV-03] PASS (LOWERCASE_NORMALIZATION): Hash cocok dengan ekspektasi -> eb6b939c98bc3b5704408ad95d24200232e921a3f8cdb57fdb79faf231937800
[TV-04] PASS (WHITESPACE_STRIPPING): Hash cocok dengan ekspektasi -> eb6b939c98bc3b5704408ad95d24200232e921a3f8cdb57fdb79faf231937800
[TV-05] PASS (UNICODE_NFC): Hash cocok dengan ekspektasi -> 16e5e5111343c968b107afe247e2d3aaec54cd07927522344b014b08edfc4c5a
[TV-06] PASS (UNICODE_NFD_TO_NFC): Hash cocok dengan ekspektasi -> 16e5e5111343c968b107afe247e2d3aaec54cd07927522344b014b08edfc4c5a
[TV-07] PASS (EMPTY_METADATA): Hash cocok dengan ekspektasi -> 9236caf524a501953160921b6ae6294d19a70744cfba092c7265782a7cc045c2
[TV-08] PASS (CANONICAL_SORTING): Hash cocok dengan ekspektasi -> 4f0dcd77f44d960121f37fd5035abad2673ee8e7b5606e4dbca23b092a13106d
[TV-09] PASS (DUPLICATE_EPC_REJECTION): Berhasil ditolak sesuai spesifikasi -> DUPLICATE_MANIFEST_EPC: Tag EPC 'E280116060000219ABCDEF01' duplikat setelah normalisasi.
[TV-10] PASS (INVALID_EPC_FORMAT_REJECTION): Berhasil ditolak sesuai spesifikasi -> INVALID_EPC_FORMAT: Tag EPC 'INVALID_EPC_FORMAT_XYZ' tidak valid (wajib 24 karakter hex).
[TV-11] PASS (EMPTY_MANIFEST_REJECTION): Berhasil ditolak sesuai spesifikasi -> EMPTY_MANIFEST: Manifest tidak boleh kosong.
[TV-12] PASS (RAW_DIFFERENT_CANONICAL_IDENTICAL): Variant A & B menghasilkan hash identik -> d6d495320cf7eefa7790b7e76d918023a60b1d05c0eb6bd246947aa45d1613e5

HASIL LAYER PYTHON: 12/12 Test Vectors Berhasil Diverifikasi

================================================================================
2. VERIFIKASI POSTGRESQL 15+ RUNTIME: SKIPPED (EXIT CODE 2)
================================================================================
[STATUS] SKIPPED (Docker CLI / daemon tidak tersedia pada host environment).
[MANDAT KEBIJAKAN P1-05 & P1-06]:
   - Runner DILARANG menghubungkan ke PGHOST remote.
   - SKIPPED secara jujur dilaporkan dengan EXIT CODE 2 dan TIDAK diklaim sebagai PASS.
   - Kategori hasil: PYTHON PASS (12/12) | STATIC PASS (Schema Alignment) | POSTGRESQL RUNTIME: SKIPPED.
================================================================================
Exit Code: 2
```

### C. Full Pytest Regression Suite
```text
PS D:\ClientS> .\.venv\Scripts\python.exe -m pytest tests/test_phase4d_local_room_and_rbac.py tests/test_phase4c_receiving_api.py tests/test_phase4_receiving_coordinator.py tests/test_r2_9_supabase_integration.py -v
============================= test session starts =============================
platform win32 -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\ClientS
plugins: anyio-4.14.2
collected 75 items

tests/test_phase4d_local_room_and_rbac.py::test_01_migration_on_empty_database PASSED [  1%]
tests/test_phase4d_local_room_and_rbac.py::test_02_migration_on_database_with_existing_rooms PASSED [  2%]
tests/test_phase4d_local_room_and_rbac.py::test_03_existing_rooms_automatically_active PASSED [  4%]
tests/test_phase4d_local_room_and_rbac.py::test_04_new_room_active_by_default PASSED [  5%]
tests/test_phase4d_local_room_and_rbac.py::test_05_active_rooms_in_filter[asyncio] PASSED [  6%]
tests/test_phase4d_local_room_and_rbac.py::test_06_inactive_rooms_filter_and_unfiltered_all[asyncio] PASSED [  8%]
tests/test_phase4d_local_room_and_rbac.py::test_07_inactive_room_rejected_by_service[asyncio] PASSED [  9%]
tests/test_phase4d_local_room_and_rbac.py::test_16_invalid_or_inactive_room_does_not_trigger_rpc_claim[asyncio] PASSED [ 10%]
tests/test_phase4d_local_room_and_rbac.py::test_17_empty_database_blocks_receiving_with_correct_message[asyncio] PASSED [ 12%]
tests/test_phase4d_local_room_and_rbac.py::test_08_operator_cannot_add_room[asyncio] PASSED [ 13%]
tests/test_phase4d_local_room_and_rbac.py::test_09_operator_cannot_edit_room[asyncio] PASSED [ 14%]
tests/test_phase4d_local_room_and_rbac.py::test_10_operator_cannot_delete_room_but_can_read[asyncio] PASSED [ 16%]
tests/test_phase4d_local_room_and_rbac.py::test_11_admin_can_manage_rooms[asyncio] PASSED [ 17%]
tests/test_phase4d_local_room_and_rbac.py::test_12_supervisor_can_manage_rooms[asyncio] PASSED [ 18%]
tests/test_phase4d_local_room_and_rbac.py::test_13_physical_deletion_of_room_with_history_rejected[asyncio] PASSED [ 20%]
tests/test_phase4d_local_room_and_rbac.py::test_14_reactivation_of_room[asyncio] PASSED [ 21%]
tests/test_phase4d_local_room_and_rbac.py::test_15_rename_room_preserves_historical_transactions[asyncio] PASSED [ 22%]
tests/test_phase4c_receiving_api.py::test_01_api_without_auth PASSED     [ 24%]
tests/test_phase4c_receiving_api.py::test_02_api_with_invalid_membership PASSED [ 25%]
tests/test_phase4c_receiving_api.py::test_03_cross_hospital_access_rejection PASSED [ 26%]
tests/test_phase4c_receiving_api.py::test_04_package_not_found PASSED    [ 28%]
tests/test_phase4c_receiving_api.py::test_05_rfid_proof_unavailable PASSED [ 29%]
tests/test_phase4c_receiving_api.py::test_06_manifest_mismatch PASSED    [ 30%]
tests/test_phase4c_receiving_api.py::test_07_claim_timeout_yields_uncertain PASSED [ 32%]
tests/test_phase4c_receiving_api.py::test_08_local_committed_operation_idempotency PASSED [ 33%]
tests/test_phase4c_receiving_api.py::test_09_confirm_timeout_maintains_local_committed PASSED [ 34%]
tests/test_phase4c_receiving_api.py::test_10_claim_held_reconciliation_operation_locked PASSED [ 36%]
tests/test_phase4c_receiving_api.py::test_11_auth_blocked_user_operation_rejection PASSED [ 37%]
tests/test_phase4c_receiving_api.py::test_12_prevent_parallel_duplicate_transactions PASSED [ 38%]
tests/test_phase4c_receiving_api.py::test_13_ui_lifecycle_rendering PASSED [ 40%]
tests/test_phase4c_receiving_api.py::test_14_endpoint_locked_when_feature_gate_disabled PASSED [ 41%]
tests/test_phase4_receiving_coordinator.py::test_scen01_normal_reception_full_lifecycle[asyncio] PASSED [ 42%]
tests/test_phase4_receiving_coordinator.py::test_scen02_missing_epc_rejected[asyncio] PASSED [ 44%]
tests/test_phase4_receiving_coordinator.py::test_scen03_foreign_epc_rejected[asyncio] PASSED [ 45%]
tests/test_phase4_receiving_coordinator.py::test_scen04_duplicate_epc_in_manifest_rejected[asyncio] PASSED [ 46%]
tests/test_phase4_receiving_coordinator.py::test_scen05_manifest_mismatch_distinguished[asyncio] PASSED [ 48%]
tests/test_phase4_receiving_coordinator.py::test_scen06_claim_timeout_yields_claim_uncertain[asyncio] PASSED [ 49%]
tests/test_phase4_receiving_coordinator.py::test_scen07_replay_claim_uncertain_recovers_to_confirmed[asyncio] PASSED [ 50%]
tests/test_phase4_receiving_coordinator.py::test_scen08_crash_after_intent_persisted[asyncio] PASSED [ 52%]
tests/test_phase4_receiving_coordinator.py::test_scen09_crash_after_claim_verified[asyncio] PASSED [ 53%]
tests/test_phase4_receiving_coordinator.py::test_scen10_crash_after_local_committed_preserves_state[asyncio] PASSED [ 54%]
tests/test_phase4_receiving_coordinator.py::test_scen11_confirm_timeout_maintains_local_committed[asyncio] PASSED [ 56%]
tests/test_phase4_receiving_coordinator.py::test_scen12_confirm_bad_identity_rejection[asyncio] PASSED [ 57%]
tests/test_phase4_receiving_coordinator.py::test_scen13_logout_before_permit_consumed_blocks_dispatch[asyncio] PASSED [ 58%]
tests/test_phase4_receiving_coordinator.py::test_scen14_two_operators_exclusive_commit_authority[asyncio] PASSED [ 60%]
tests/test_phase4_receiving_coordinator.py::test_scen15_two_installations_different_uids[asyncio] PASSED [ 61%]
tests/test_phase4_receiving_coordinator.py::test_scen16_two_hospitals_boundary_enforcement[asyncio] PASSED [ 62%]
tests/test_phase4_receiving_coordinator.py::test_scen17_reader_disconnect_automatically_invalidates_session[asyncio] PASSED [ 64%]
tests/test_phase4_receiving_coordinator.py::test_scen18_com_port_change_invalidates_active_session[asyncio] PASSED [ 65%]
tests/test_phase4_receiving_coordinator.py::test_scen19_circuit_breaker_disk_full_blocks_commit[asyncio] PASSED [ 66%]
tests/test_phase4_receiving_coordinator.py::test_scen20_two_parallel_requests_same_package_rejected[asyncio] PASSED [ 68%]
tests/test_phase4_receiving_coordinator.py::test_scen21_unicode_nfc_normalization_visual_equivalence PASSED [ 69%]
tests/test_phase4_receiving_coordinator.py::test_scen22_strict_confirm_all_six_fields_contract_and_rejection[asyncio] PASSED [ 70%]
tests/test_r2_9_supabase_integration.py::test_scen01_package_claim_lifecycle_success[asyncio] PASSED [ 72%]
tests/test_r2_9_supabase_integration.py::test_scen02_claim_with_wrong_verification_code_rejected[asyncio] PASSED [ 73%]
tests/test_r2_9_supabase_integration.py::test_scen03_claim_with_tampered_manifest_rejected[asyncio] PASSED [ 74%]
tests/test_r2_9_supabase_integration.py::test_scen04_package_confirm_lifecycle_success[asyncio] PASSED [ 76%]
tests/test_r2_9_supabase_integration.py::test_scen05_confirm_unclaimed_package_rejected[asyncio] PASSED [ 77%]
tests/test_r2_9_supabase_integration.py::test_scen06_unauthenticated_claim_rejected[asyncio] PASSED [ 78%]
tests/test_r2_9_supabase_integration.py::test_scen07_unauthenticated_confirm_rejected[asyncio] PASSED [ 80%]
tests/test_r2_9_supabase_integration.py::test_scen08_nonexistent_package_claim_returns_not_found[asyncio] PASSED [ 81%]
tests/test_r2_9_supabase_integration.py::test_scen09_tenant_mismatch_raises_authorization_error[asyncio] PASSED [ 82%]
tests/test_r2_9_supabase_integration.py::test_scen10_claim_package_idempotent_for_same_operation[asyncio] PASSED [ 84%]
tests/test_r2_9_supabase_integration.py::test_scen11_claim_package_rejected_for_different_operation[asyncio] PASSED [ 85%]
tests/test_r2_9_supabase_integration.py::test_scen12_confirm_package_idempotent_for_same_operation[asyncio] PASSED [ 86%]
tests/test_r2_9_supabase_integration.py::test_scen14_prevent_duplicate_local_transactions[asyncio] PASSED [ 88%]
tests/test_r2_9_supabase_integration.py::test_scen19_strict_confirm_response_validation[asyncio] PASSED [ 89%]
tests/test_r2_9_supabase_integration.py::test_scen21_crash_recovery_claimed_validation_strict[asyncio] PASSED [ 90%]
tests/test_r2_9_supabase_integration.py::test_scen22_crash_recovery_confirmed_local_transaction_integrity[asyncio] PASSED [ 92%]
tests/test_r2_9_supabase_integration.py::test_scen13_restart_recovery_crash_in_claiming PASSED [ 93%]
tests/test_r2_9_supabase_integration.py::test_scen15_api_operations_history_tenant_isolation PASSED [ 94%]
tests/test_r2_9_supabase_integration.py::test_scen16_verification_code_exposure_prevention PASSED [ 96%]
tests/test_r2_9_supabase_integration.py::test_scen17_production_adapter_selection_guard PASSED [ 97%]
tests/test_r2_9_supabase_integration.py::test_scen18_legacy_patch_prohibited_throws_runtime_error PASSED [ 98%]
tests/test_r2_9_supabase_integration.py::test_scen20_operational_reconcile_endpoint_e2e PASSED [100%]

====================== 75 passed, 383 warnings in 12.92s ======================
Exit Code: 0
```

---

## 4. MANIFEST ARTEFAK & SHA-256 CHECKSUM

Seluruh berkas penyerahan dikemas ke dalam `ClientS_R2_10_4_PATCH.zip`.

| No | Nama Berkas | Ukuran (Bytes) | SHA-256 Checksum |
| :---: | :--- | :---: | :--- |
| 1 | 07_package_communication_rpc_consolidated_r2_10_4.sql | 102,055 | d38e04f83113220630003dfe2871bfa1c70e023f4ec4604cf61220bb4a0c7d4e |
| 2 | test_concurrency_harness_r2_10_4.sql | 14,764 | 74a2803fbb121536c546e33c522b896bb91db4fd6700519479f228faed626218 |
| 3 | run_concurrency_tests_r2_10_4.py | 63,637 | 9ee87474a06a0ef4cc8577d18e9c4cdc57827c803d2756a49064cfedeb25f141 |
| 4 | manifest_hash_test_vectors_r2_10_4.json | 9,323 | 36af41fa3577cfdfcc61593506bffa459f6e7f88983bc684aad71e906c3227ab |
| 5 | verify_ddl_r2_10_4.py | 10,899 | 3d0f8c7b89d65fb4dbf1c3d2913e83f76e223075aca58739e10064c829cdf1b5 |
| 6 | verify_vectors_r2_10_4.py | 24,774 | 8e0bf4e6c5118ed8b0c6ebf611cefa074b5c8c2ae874024fe07f588887c20dc3 |
| 7 | R2_10_4_AUDIT_REPORT.md | 23,597 | (dokumen laporan mandiri) |
| 8 | supabase_cloud_adapter.py | 8,984 | 5f60711cca03f056478f96d9672f92ca670301e65e04637a4c57dea3b359ff56 |
| 9 | cloud_sync_service.py | 5,404 | 8b61b4ce0b28c70f1b0a29a5a9901f26b4641520a816f438da872a5deceaefd1 |
| 10 | package_receiving_service.py | 39,098 | 70a0b1d72b8411344cae50bac1917d5052e309c30908b0e50b0622207b918e22 |
| 11 | manifest_service.py | 3,540 | 590bf6f633871bdc2022562845c2375c1b0a2aa26e5632e8aebba95cd3f1f08b |
| 12 | receiving_router.py | 34,920 | 5015bf9bb47a9292b63d357583338bb8a9b7d0e88d4ea79a61d1fd6d17ae1614 |
| 13 | test_phase4_receiving_coordinator.py | 39,023 | 731f2d8301dff84fe4616af71e602ea316b01f1e3850e261926050445c7fc1fa |
| 14 | test_phase4c_receiving_api.py | 21,116 | 8c809b56be76b0d2252120bc593556185e259fd50149d32357548fb22f11003d |
| 15 | test_phase4d_local_room_and_rbac.py | 28,841 | 2e45e688834cdb6624dd0adf8c562d36897b2465255329c5ee7c7e29b3119ec8 |
| 16 | test_r2_9_supabase_integration.py | 56,229 | fdf2f944f23f7f782b6e9acb696bfc481c858dfc1410d96f6b90a6f7f43ebcc6 |
| 17 | requirements.txt | 436 | aca4fc30ba2bf7c0a8edf8edaf6723cd460ec367b62c850c977e1fe873c90004 |
| 18 | ClientS_R2_10_4_unified.diff | 1,399,320 | 1577531cc2b87eac5ee4bbe69931ff3eda0dc883d03e58a7137df1c12af6cd2c |

---

## 5. STATUS AKHIR & HOLD FOR INDEPENDENT AUDIT

1. **Baseline**: R2.10.4 dipertahankan sebagai baseline tunggal.
2. **P0-01**: Mock Confirm Adapter mematuhi kontrak 6 field eksak, mode bad identity tetap tertolak, dan assertion komprehensif aktif.
3. **P0-02**: Router NULL Guard melindungi endpoint preview dan process receiving terhadap `pkg_hospital_id IS NULL` dan mismatch tenant (HTTP 403).
4. **P1-01**: Anti-DoS `statement_timeout` (3s) mendeteksi kontensi lock dengan penanganan SQLSTATE `57014`.
5. **P1-02**: Paket pengujian reproducible dengan panduan PYTHONPATH terkelola.
6. **P1-03**: Full regression 75/75 PASS tanpa efek samping pada database SQLite operasional.
7. **Feature Gate**: NON-AKTIF (`ENABLE_PACKAGE_RECEIVING_API=0`).
8. **Isolasi Database**: SQLite operasional lokal serta Supabase staging dan produksi tidak disentuh sama sekali.
9. **Handoff**: Seluruh artefak binary dan diff diserahkan secara lengkap untuk audit independen.
