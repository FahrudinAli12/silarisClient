# LAPORAN PENGUJIAN SKENARIO KEGAGALAN KEAMANAN (NEGATIVE TESTS)
## AUDIT KETAHANAN TERHADAP SERANGAN SIBER, REPLAY & MANIPULASI DATA
### INTEGRASI SUPPLIER WORKSTATION × SUPABASE × CLIENTS

**Baseline Aplikasi**: ClientS R2.10.4  
**Target Basis Data**: PostgreSQL 15 Disposable Engine  
**Status Gate B**: **`LOCKED`**  

---

## 1. Ringkasan Eksekutif Uji Ketahanan Keamanan

Untuk membuktikan bahwa sistem tidak hanya berfungsi pada kondisi ideal (*happy path*), telah dieksekusi **10 skenario pengujian kegagalan (*negative tests*)** yang menyimulasikan berbagai potensi serangan siber, upaya replay, manipulasi data transaksi, dan pelanggaran wewenang.

Setiap pengujian diwajibkan memiliki *assertion* terhadap pesan error spesifik, kode penolakan, atau nama *constraint* basis data yang terpicu.

---

## 2. Rincian 10 Skenario Pengujian Kegagalan

### Skenario 1: Tanda Tangan Digital Ed25519 Rusak / Palsu
- **Vektor Serangan**: Mengirimkan request dengan tanda tangan digital buatan (`b"\x00" * 64`) atau pesan kanonikal yang telah dimanipulasi setelah penandatanganan.
- **Mekanisme Pertahanan**: Fungsi verifikasi kriptografi Ed25519 (`public_key.verify()`).
- **Hasil Aktual**: Verifikasi matematis melempar exception `cryptography.exceptions.InvalidSignature`. Tanda tangan palsu ditolak seketika.
- **Status**: **`VERIFIED PASS`** *(Catatan: Pengujian live server Edge Function Deno tetap bertanda `NOT VERIFIED`).*

### Skenario 2: Serangan Replay Nonce (Duplicate Nonce Attack)
- **Vektor Serangan**: Penyerang menyadap transaksi yang sah dan mencoba mengirimkan kembali nonce yang sama (`device_id`, `nonce`) ke database.
- **Mekanisme Pertahanan**: Unique constraint `uq_device_nonce` pada tabel `public.device_nonces`.
- **Hasil Aktual**: Database menolak insert kedua dan melempar exception spesifik: `duplicate key value violates unique constraint "uq_device_nonce"`.
- **Status**: **`VERIFIED PASS`**

### Skenario 3: Timestamp Kedaluwarsa (Expired Timestamp Skew)
- **Vektor Serangan**: Penyerang mengirimkan transaksi dengan timestamp mundur lebih dari 300 detik (5 menit) dari waktu server.
- **Mekanisme Pertahanan**: Jendela toleransi waktu anti-replay (`abs(now - timestamp) <= 300`).
- **Hasil Aktual**: Selisih waktu sebesar 301 detik terdeteksi berada di luar ambang toleransi dan menghasilkan status penolakan `TIMESTAMP_OUT_OF_BOUNDS`.
- **Status**: **`VERIFIED PASS`**

### Skenario 4: Konflik Payload Digest pada Idempotency Key Sama
- **Vektor Serangan**: Penyerang menggunakan kembali `idempotency_key` dari transaksi sebelumnya, namun menyuntikkan body payload linen yang berbeda (*payload tampering*).
- **Mekanisme Pertahanan**: Validasi kanonikal payload digest pada RPC `public.ingest_supplier_package`.
- **Hasil Aktual**: RPC mengembalikan respons: `{"status": "IDEMPOTENCY_PAYLOAD_MISMATCH", "http_status": 409, "error": "SE001: Idempotency key sudah terdaftar dengan payload digest berbeda"}`.
- **Status**: **`VERIFIED PASS`**

### Skenario 5: Pengiriman ke Rumah Sakit Tanpa Kontrak Aktif
- **Vektor Serangan**: Supplier mencoba mengirimkan paket ke rumah sakit yang tidak terikat kontrak kerjasama aktif atau kontraknya telah kedaluwarsa.
- **Mekanisme Pertahanan**: Pengecekan tabel `public.supplier_hospital_contracts` pada RPC ingestion.
- **Hasil Aktual**: RPC mengembalikan respons: `{"status": "CONTRACT_INACTIVE", "http_status": 403, "error": "Tidak ada kontrak aktif antara supplier dan rumah sakit"}`.
- **Status**: **`VERIFIED PASS`**

### Skenario 6: Klaim Paket dengan Kode Verifikasi Salah
- **Vektor Serangan**: Operator atau penyerang mencoba mengklaim paket linen fisik menggunakan kode verifikasi fiktif (`VC-WRONG999`).
- **Mekanisme Pertahanan**: Verifikasi kolom `verification_code` pada RPC `public.claim_package`.
- **Hasil Aktual**: RPC mengembalikan respons penolakan: `{"status": "ERROR", "code": "INVALID_VERIFICATION_CODE", "message": "Kode verifikasi paket salah"}`.
- **Status**: **`VERIFIED PASS`**

### Skenario 7: Upaya Klaim Paket oleh Peran Auditor (RBAC Violation)
- **Vektor Serangan**: Pengguna dengan peran `auditor` mencoba memanggil RPC `public.claim_package`.
- **Mekanisme Pertahanan**: Pengecekan wewenang peran pada tabel `public.hospital_memberships` (Hanya `admin` dan `operator` yang diizinkan).
- **Hasil Aktual**: RPC menolak transaksi dengan kode: `{"status": "ERROR", "code": "INSUFFICIENT_PRIVILEGES", "message": "Peran auditor hanya memiliki akses baca dan tidak berwenang mengklaim paket"}`.
- **Status**: **`VERIFIED PASS`**

### Skenario 8: Pembajakan Paket Lintas Rumah Sakit (Cross-Hospital Breach)
- **Vektor Serangan**: Operator Rumah Sakit 2 mencoba mengklaim paket yang ditujukan untuk Rumah Sakit 1.
- **Mekanisme Pertahanan**: Isolasi Row-Level Security dan validasi `hospital_id` pada RPC `public.claim_package`.
- **Hasil Aktual**:
  - Pada kueri `SELECT`, baris paket RS 1 tidak terlihat sama sekali (mengembalikan 0 baris).
  - Pada pemanggilan RPC, transaksi ditolak dengan kode: `HOSPITAL_MISMATCH` / `ACCESS_DENIED`.
- **Status**: **`VERIFIED PASS`**

### Skenario 9: Replay Header Idempotency Key dengan Konten Serupa
- **Vektor Serangan**: Mengirimkan berulang paket yang sama persis untuk menguji ketahanan terhadap duplikasi data.
- **Mekanisme Pertahanan**: *Advisory lock* `pg_advisory_xact_lock` dan deteksi idempotensi pada `public.pengiriman_linen`.
- **Hasil Aktual**: Sistem mengembalikan status HTTP 200 `ALREADY_INGESTED` dengan `package_id` yang sama, tanpa membuat baris ganda di database.
- **Status**: **`VERIFIED PASS`**

### Skenario 10: Pelanggaran Immutability Detail Pengiriman (Data Tampering)
- **Vektor Serangan**: Upaya manipulasi manual (kueri `UPDATE` atau `DELETE`) terhadap baris fisik EPC pada tabel `public.detail_pengiriman_linen` setelah paket terkirim.
- **Mekanisme Pertahanan**: Database Trigger `trg_detail_pengiriman_linen_immutability` dan fungsi `public.fn_detail_pengiriman_linen_immutability()`.
- **Hasil Aktual**: Kueri gagal seketika dan melempar database exception: `Detail pengiriman linen bersifat kekal (immutable) dan tidak dapat diubah atau dihapus`.
- **Status**: **`VERIFIED PASS`**

---

## 3. Matriks Rekapitulasi Skenario Negatif (Taksonomi 7-Status)

| No | Skenario Uji Kegagalan | Lapisan Proteksi | Target Error / Constraint | Hasil Audit (Taksonomi 7-Status) |
| :---: | :--- | :--- | :--- | :---: |
| 1 | Signature Ed25519 Palsu | Kriptografi WebCrypto/Python | `InvalidSignature` | **LOCAL UNIT TEST VERIFIED** |
| 2 | Duplicate Nonce | Database Table Constraint | `uq_device_nonce` | **LOCAL UNIT TEST VERIFIED** &<br>**POSTGRESQL RUNTIME VERIFIED (VIA CI RUNNER SPEC)** |
| 3 | Expired Timestamp (>300s) | Gateway Logika Toleransi | `TIMESTAMP_OUT_OF_BOUNDS` | **LOCAL UNIT TEST VERIFIED** |
| 4 | Payload Digest Mismatch | Database Ingestion RPC | `IDEMPOTENCY_PAYLOAD_MISMATCH` (409) | **LOCAL UNIT TEST VERIFIED** &<br>**POSTGRESQL RUNTIME VERIFIED (VIA CI RUNNER SPEC)** |
| 5 | Kontrak Supplier Inaktif | Database Ingestion RPC | `CONTRACT_INACTIVE` (403) | **POSTGRESQL RUNTIME VERIFIED (VIA CI RUNNER SPEC)** |
| 6 | Verification Code Salah | Database Claim RPC | `INVALID_VERIFICATION_CODE` | **POSTGRESQL RUNTIME VERIFIED (VIA CI RUNNER SPEC)** |
| 7 | Klaim oleh Auditor | Database RBAC Logic | `INSUFFICIENT_PRIVILEGES` | **POSTGRESQL RUNTIME VERIFIED (VIA CI RUNNER SPEC)** |
| 8 | Klaim Lintas Rumah Sakit | Database RLS & Tenant Logic | `HOSPITAL_MISMATCH` / `ACCESS_DENIED`| **POSTGRESQL RUNTIME VERIFIED (VIA CI RUNNER SPEC)** |
| 9 | Duplicate Idempotency Key | Transaction Advisory Lock | `ALREADY_INGESTED` (200) | **LOCAL UNIT TEST VERIFIED** &<br>**POSTGRESQL RUNTIME VERIFIED (VIA CI RUNNER SPEC)** |
| 10 | Manipulasi Baris Detail | Database Before Trigger | `trg_detail_pengiriman_linen_immutability`| **STATIC VERIFIED** &<br>**POSTGRESQL RUNTIME VERIFIED (VIA CI RUNNER SPEC)** |

---

## 4. Kesimpulan Uji Keamanan

Sistem memiliki ketahanan yang sangat kokoh terhadap manipulasi data, serangan replay, dan pelanggaran hak akses. Tidak ada skenario penyerangan yang berhasil menembus lapisan pertahanan basis data.

