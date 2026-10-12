## **7. Alur Kerja Client**

Dokumen ini menguraikan seluruh siklus operasional standar (*Standard Operating Procedure / SOP*) penggunaan aplikasi klien di rumah sakit, mulai dari inisialisasi awal, penerimaan linen kiriman baru, sirkulasi pencucian laundry, distribusi ke ruangan pasien, hingga sinkronisasi data cloud.

---

```
[ Supplier / Cloud Pusat ]
          │ (Kode Verifikasi Manifest)
          ▼
┌─────────────────────────────────┐
│ 1. Verifikasi Penerimaan Linen  │ ──► Mendaftarkan tag baru ke SQLite lokal
└─────────────────────────────────┘
          │ (Status: "tersedia", Lokasi: "Storage")
          ▼
┌─────────────────────────────────┐
│ 2. Distribusi Linen Keluar      │ ──► Dikirim ke Ruangan Pasien (Status: "dipakai")
└─────────────────────────────────┘
          │ (Linen kotor setelah dipakai)
          ▼
┌─────────────────────────────────┐
│ 3. Laundry Masuk (Linen Kotor)  │ ──► Diterima di R. Laundry (Status: "dicuci", Cuci +1)
└─────────────────────────────────┘
          │ (Proses Cuci, Pengeringan, Setrika)
          ▼
┌─────────────────────────────────┐
│ 4. Laundry Selesai (Bersih)     │ ──► Kembali siap didistribusikan ke Ruangan
└─────────────────────────────────┘
          ▲                               │
          └───────────────────────────────┘ (Siklus berulang)
```

---

### **Langkah 1: Setup & Inisialisasi Konektivitas Reader**

1. Petugas menyalakan server aplikasi klien (`uvicorn app.main:app`).
2. Backend secara otomatis:
   * Menginisialisasi dan memvalidasi integritas database SQLite `Client_RumahSakit_Template.db`.
   * Menjalankan `RFIDServiceManager` yang melakukan deteksi port serial yang tersedia pada sistem (misal: COM3 dan COM4).
   * Menentukan mode reader secara dinamis (*Single Reader Mode* atau *Dual Reader Mode*).
   * Menjalankan background worker `SyncService` untuk memantau sinkronisasi cloud.
3. Operator membuka antarmuka web di browser. Frontend otomatis terhubung ke WebSocket `ws://localhost:8000/ws/epc-live`.
4. Widget status reader di sidebar dan top header menampilkan badge hijau **CONNECTED**.

---

### **Langkah 2: Verifikasi & Penerimaan Linen Baru dari Cloud Supplier**

Tahap ini dilakukan saat rumah sakit menerima pengiriman paket linen baru dari vendor atau pusat pencucian eksternal:

1. Operator membuka menu **Settings > Sync Data** (`/settings?tab=sync`).
2. Masukkan **Kode Verifikasi** yang tertera pada surat jalan supplier dan **Nama Petugas** pada kartu *Verifikasi Linen*.
3. Klik tombol **Get Data**:
   * Sistem menghubungi API Supabase (`GET /api/setting/get-data`) untuk mengambil manifest paket pengiriman (`pengiriman_linen` dan `detail_pengiriman_linen`).
4. Klik tombol **Start Scan** pada kartu *Scan Linen*:
   * Reader RFID membaca seluruh fisik linen baru yang ada di dalam bungkusan/troli.
   * Sistem otomatis membandingkan EPC hasil scan dengan manifest cloud:
     * Tag yang sesuai masuk ke tabel **Linen Terdaftar**.
     * Tag yang tidak terdaftar di manifest masuk ke tabel **Linen Tidak Terdaftar**.
5. Operator meninjau hasil pencocokan, lalu mengklik **Simpan Data**:
   * Seluruh linen baru otomatis tersimpan secara atomik ke tabel lokal `linen` dengan status awal `tersedia` dan lokasi `Storage`.
   * Sistem membuat header transaksi di `transaksi_linen` (`jenis_transaksi="penerimaan"`), detail item di `detail_transaksi_linen`, dan audit log di `riwayat_linen`.
   * Sistem secara otomatis mengirimkan patch ke Supabase untuk mengubah status paket pengiriman menjadi `received`.
   * Sistem memvalidasi idempotensi (mencegah kode yang sama diproses berulang).

---

### **Langkah 3: Siklus Laundry Masuk (Penerimaan Linen Kotor)**

Tahap pencatatan linen kotor yang ditarik dari ruangan-ruangan perawatan ke unit laundry:

1. Petugas membawa troli linen kotor ke ruang laundry.
2. Operator membuka menu **Laundry > Linen Masuk** (`/laundry?tab=masuk`).
3. Klik tombol **Start Scan**:
   * Sistem mengaktifkan reader `COM3` (*Active Scanner: COM3 - Linen Masuk*) dan menonaktifkan reader `COM4` (standby & buffer UART dibersihkan).
   * Setiap EPC linen kotor yang terbaca antena muncul seketika di tabel *Cek Linen Scanned*.
4. Pada kartu *Form Pengambilan Linen*, periksa Ruangan (default: `R. Laundry`), masukkan **Nama Petugas**, lalu klik **Proses**:
   * Transaksi batch diproses (`POST /api/transaksi/batch`).
   * Status linen di master data diubah menjadi `dicuci`.
   * Lokasi fisik linen diubah menjadi `R. Laundry`.
   * Nilai penghitung **Total Cuci** bertambah (+1) untuk setiap lembar linen.
   * Transaksi masuk tercatat di `transaksi_linen`, `detail_transaksi_linen`, dan `riwayat_linen`.
5. *(Opsi Otomatis)*: Jika fasilitas lorong laundry telah dilengkapi gate RFID scanner mandiri, scan fisik langsung diproses secara otomatis per item melalui endpoint `/api/transaksi/auto-scan` dengan proteksi debounce 4 detik.

---

### **Langkah 4: Siklus Laundry Keluar & Distribusi (Linen Bersih ke Ruangan)**

Tahap pengiriman linen yang telah dicuci, dikeringkan, dan disetrika menuju ruangan pasien:

1. Petugas menyiapkan bundel linen bersih pada troli distribusi.
2. Operator membuka menu **Laundry > Linen Keluar** (`/laundry?tab=keluar`).
3. Klik tombol **Start Scan**:
   * Sistem mengaktifkan reader `COM4` (*Active Scanner: COM4 - Linen Keluar*) dan menonaktifkan reader `COM3`.
   * Seluruh tag linen bersih yang berada di troli terbaca secara massal.
4. Pada *Form Pengiriman Linen*:
   * Pilih **Ruangan Tujuan** melalui komponen *Custom Dropdown* (pilihan diambil langsung dari Master Data Ruangan, contoh: *Ruang Mawar 01*).
   * Masukkan **Nama Petugas** pengirim.
   * Klik tombol **Proses**.
5. Sistem memperbarui database:
   * Status linen diubah menjadi `dipakai`.
   * Lokasi fisik linen diubah menjadi nama ruangan tujuan (misal: *Ruang Mawar 01*).
   * Transaksi keluar berhasil dibukukan.

---

### **Langkah 5: Siklus Pengembalian Linen Kotor**

1. Setelah dipakai oleh pasien dalam jangka waktu tertentu, linen kotor diganti oleh perawat dan dikumpulkan kembali ke kantong linen kotor.
2. Linen kotor dibawa kembali menuju unit laundry.
3. Proses operasional kembali berulang ke **Langkah 3 (Laundry Masuk)**.

---

### **Langkah 6: Mekanisme Sinkronisasi Cloud & Operasional Offline**

1. Setiap transaksi yang berhasil dibukukan di klien lokal secara otomatis diantrekan ke modul sinkronisasi (`SyncService`).
2. **Kondisi Online:**
   * Payload transaksi langsung dikirim ke Cloud Supabase (`sync_events`).
3. **Kondisi Offline (Internet Terputus):**
   * Aplikasi klien tetap dapat melakukan scan, simpan transaksi masuk, dan simpan transaksi keluar tanpa gangguan.
   * Payload yang gagal dikirim disimpan ke tabel lokal `pengiriman_temp` dengan status `pending`.
   * Background thread memeriksa jaringan setiap 5 detik. Begitu koneksi internet pulih, seluruh antrean `pending` dikirimkan secara otomatis (*auto-drain buffer*).

---

### **Langkah 7: Monitoring, Audit Trail & Pelaporan**

1. **Dashboard Monitoring (`/dashboard`):**
   * Memantau jumlah total linen, linen dalam pencucian, dan linen yang sedang dipakai di ruangan.
   * Mengamati visualisasi rasio cuci vs pakai pada *Interactive Donut Chart*.
   * Mengamati streaming transaksi *Live Scan* dan ringkasan 5 aktivitas terkini.
2. **Audit History Transaksi (`/laundry?tab=history`):**
   * Meninjau rekaman log masuk dan log keluar harian.
   * Mengklik salah satu baris riwayat untuk melihat **Detail Log** lengkap (seluruh nomor EPC linen yang diproses pada transaksi tersebut beserta akumulasi jumlah pencuciannya).
3. **Audit Log Penerimaan (`/settings?tab=log`):**
   * Memeriksa arsip seluruh pengiriman paket linen baru yang pernah diterima dari supplier eksternal.
