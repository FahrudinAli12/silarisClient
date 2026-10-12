## **4. Modul & Fitur Utama**

Sistem terbagi ke dalam empat modul utama pada antarmuka web, didukung oleh servis background otomatis dan API backend yang tangguh.

---

### **4.1 Modul Dashboard (`/dashboard`)**

Halaman ringkasan eksekutif untuk memantau performa dan pergerakan linen secara real-time.

1. **Hero Banner & Identitas Sistem:**  
   * Menampilkan headline status operasional rumah sakit dengan badge *Live • Secure • Hospital Grade*.
2. **Statistik Utama (*Counter Cards*):**  
   * **Total Linen:** Jumlah seluruh inventaris linen yang terdaftar di database lokal.
   * **Linen Masuk:** Jumlah linen yang berada dalam status dicuci / di unit laundry.
   * **Linen Keluar:** Jumlah linen yang sedang didistribusikan dan dipakai di ruangan perawatan.
   * Indikator tren persentase operasional dibandingkan periode sebelumnya.
3. **Live Scan RFID Card:**  
   * Tabel streaming data pembacaan antena RFID secara live (*Waktu*, *EPC*, *Lokasi*, *Status Masuk/Keluar*).
   * Efek visual *highlight animation* saat tag baru terdeteksi.
4. **Ringkasan Status Linen (*Interactive Donut Chart*):**  
   * Grafik donat visual yang membandingkan persentase proporsi Linen Masuk vs Linen Keluar.
   * Total linen di tengah grafik lingkaran serta legend persentase interaktif.
5. **Riwayat Aktivitas Terakhir (*Recent Activity*):**  
   * Menampilkan 5 transaksi pergerakan linen terakhir lengkap dengan ikon status, tipe transaksi (*masuk*, *keluar*, *penerimaan*), lokasi tujuan, dan timestamp.
6. **Reader Status Widget (Top Header & Sidebar):**  
   * Menampilkan mode aktif (*Single Reader Mode* / *Dual Reader Mode*), *Active Scanner* (misal: `COM3 - Linen Masuk`), port serial yang sedang dipakai, waktu sinkronisasi terakhir, badge LED status (*CONNECTED / DISCONNECTED*), serta tombol **Test Connection**.

---

### **4.2 Modul Master Data (`/master-data`)**

Pengelolaan inventaris master linen dan master ruangan rumah sakit.

#### **A. Data Linen (`/master-data/linen`)**
* **3 Tab Tampilan Data:**
  1. **Semua Linen:** Menampilkan seluruh koleksi linen dengan kolom *No*, *EPC* (diberi porsi lebar 32% untuk keterbacaan), *Kategori* (badge berwarna: Sprei, Sarung Bantal, Selimut, Baju Pasien, dll.), *Nama Linen*, *Lokasi*, dan atribut khusus **Total Cuci**.
  2. **Linen di Cuci:** Khusus menampilkan linen yang sedang berstatus `dicuci` atau berada di `R. Laundry`.
  3. **Linen di Pakai:** Khusus menampilkan linen yang berstatus `dipakai` di ruangan-ruangan pasien.
* **Fitur Pencarian & Filter Cepat:** Kotak search instan per kata kunci EPC, Nama Linen, Kategori, atau Lokasi.
* **Pagination:** Navigasi halaman yang rapi diletakkan di pojok kanan bawah tabel.

#### **B. Data Ruangan (`/master-data/ruangan`)**
* **Tabel Ruangan:** Kolom *No*, *Kode Ruangan*, *Nama Ruangan*, *Keterangan*, *Status (Aktif)*, dan *Aksi*.
* **Form & Modal Tambah/Edit Ruangan:** Penambahan ruangan baru atau pembaruan keterangan nama ruangan.
* **Proteksi Integritas Referensial (*Safe Delete*):**
  * Penghapusan ruangan ditolak jika ruangan tersebut sedang digunakan sebagai lokasi aktif linen.
  * Penghapusan ruangan ditolak jika ruangan tersebut sudah tercatat di riwayat header transaksi sebelumnya guna menjamin validitas histori audit.

---

### **4.3 Modul Laundry (`/laundry`)**

Pusat kendali proses pencucian, distribusi, dan riwayat transaksi linen harian.

#### **A. Linen Masuk (`/laundry?tab=masuk`)**
* **Area Scan Linen:**
  * Tombol dinamis **Start Scan / Stop Scan** dengan animasi ikon *play/stop*.
  * Tombol **Reset Tabel** untuk mengosongkan antrean scan sementara.
  * Tabel ringkas **Cek Linen Scanned** (*No*, *EPC*) yang otomatis bertambah saat reader mendeteksi tag.
  * **Aturan Scan:** Pemindaian hanya menampung tag ke dalam tabel antrean sementara di UI untuk verifikasi visual operator. Scan RFID **tidak membuat transaksi prematur** dan **tidak menambah jumlah cuci**.
* **Form Pengambilan Linen:**
  * Input Ruangan (default: `R. Laundry`), Nama Petugas, dan Timestamp real-time.
  * Tombol **Proses**: Memproses transaksi batch resmi (`POST /api/transaksi/batch`), mengubah status linen menjadi `dicuci`, memperbarui lokasi ke `R. Laundry`, mencatat transaksi resmi dengan nama petugas form, dan menambah nilai `total_cuci` (+1).
* **Daftar Linen Masuk:** Tabel utama dengan kolom lengkap (*No, ID, EPC, Kategori, Nama Linen, Jumlah Cuci, Lokasi, Status, Keterangan*) serta search box filter.

#### **B. Linen Keluar (`/laundry?tab=keluar`)**
* **Area Scan Linen:** Fitur scanning linen bersih yang siap didistribusikan ke ruangan perawatan.
* **Form Pengiriman Linen:**
  * Komponen **Custom Dropdown** untuk memilih ruangan tujuan yang terdaftar pada Master Ruangan.
  * Input Nama Petugas dan Timestamp.
  * Tombol **Proses**: Mengubah status linen menjadi `dipakai`, memperbarui lokasi fisik linen ke ruangan tujuan, dan membukukan transaksi pengiriman resmi (tanpa menambah `total_cuci`).
* **Daftar Linen Keluar:** Tabel utama yang menampilkan daftar linen keluar yang berhasil didistribusikan.

#### **C. History Linen (`/laundry?tab=history`)**
* **Ringkasan Metrik:** Counter *Total Linen Masuk*, *Total Linen Keluar*, dan grafik donat ringkasan aktivitas 7 hari terakhir.
* **Dual Log Table:**
  * **Log Linen Masuk:** Menampilkan seluruh transaksi masuk yang sah dari pengisian formulir oleh petugas (*No, Tanggal, Petugas, Ruangan, Total Linen, Action Arrow*). Log bersih dari entri dummy/prematur scanner.
  * **Log Linen Keluar:** Daftar transaksi keluar ke ruangan pasien dari formulir distribusi.
* **Detail Log Interaktif (Panel Kanan):**
  * Saat salah satu baris transaksi diklik, tabel **Detail Log** di sebelah kanan otomatis menampilkan seluruh item EPC yang terlibat beserta kategori, nama linen, riwayat cuci, dan catatan transaksi.

---

### **4.4 Modul Settings & Verifikasi Sync (`/settings`)**

Modul integrasi eksternal untuk verifikasi pengiriman linen baru dan audit sinkronisasi cloud.

#### **A. Sync Data & Verifikasi Linen (`/settings?tab=sync`)**
1. **Stat Cards:** Memantau jumlah *Total Linen Terdaftar* vs *Total Linen Tidak Terdaftar*.
2. **Form Verifikasi Linen Baru (Cloud Ingest):**
   * Operator memasukkan **Kode Verifikasi** yang diterbitkan supplier/pusat logistik dan Nama Petugas.
   * Tombol **Get Data**: Mengambil manifest paket pengiriman dari Supabase (`GET /api/setting/get-data`).
3. **Fitur Cek Linen (*Live Scan Matching*):**
   * Tombol Start Scan Cek Linen membaca tag RFID fisik pada paket pengiriman.
   * Sistem otomatis memetakan EPC hasil scan ke dalam:
     * **Tabel Linen Terdaftar:** Item yang cocok dengan manifest cloud supplier.
     * **Tabel Linen Tidak Terdaftar:** Item yang terbaca namun tidak ada pada manifest pengiriman.
4. **Simpan Data Atomik (`POST /api/setting/simpan-data`):**
   * **Hanya tag yang cocok dengan manifest cloud yang didaftarkan ke master database.** Tag tidak terdaftar diabaikan dan tidak dimasukkan ke tabel `linen`.
   * Seluruh linen baru yang disimpan otomatis diinisialisasi dengan **lokasi: Storage**, **status: tersedia**, dan **total_cuci: 0**.
   * Membuat header transaksi penerimaan dan riwayat pergerakan item.
   * Menandai status paket di Supabase sebagai `received` secara otomatis.
   * Mencegah duplikasi data melalui proteksi idempotensi (HTTP 409 jika kode sudah pernah diproses).

#### **B. Log Penerimaan Linen (`/settings?tab=log`)**
* **Tabel Log Penerimaan:** Riwayat seluruh paket yang pernah diterima (*No, Tanggal, Petugas, Jumlah Linen, Keterangan*).
* **Tabel Detail Linen:** Menampilkan rincian daftar EPC, kategori, dan nama linen saat salah satu log penerimaan diklik.

#### **C. Konfigurasi Port Reader:**
* Custom Dropdown modern pada header atas untuk mengganti alokasi COM Port reader secara dinamis tanpa perlu restart aplikasi.

---

### **4.5 Auto-Scan Individual Transaction (`/api/transaksi/auto-scan`)**

Selain pemrosesan form batch, sistem menyediakan endpoint auto-scan untuk integrasi gate/lorong RFID mandiri tanpa UI:
* **Validasi Pendaftaran Tag:** Endpoint secara ketat menolak (HTTP 400 Bad Request) tag yang belum terdaftar di database master rumah sakit. Tag baru wajib diverifikasi terlebih dahulu melalui modul Settings.
* Jika tag sah dan terdaftar:
  * Tag terbaca pada reader **COM3** diproses sebagai transaksi Linen Masuk (`status=dicuci`, `lokasi=R. Laundry`, `total_cuci + 1`).
  * Tag terbaca pada reader **COM4** diproses sebagai transaksi Linen Keluar (`status=dipakai`, `lokasi=Ruangan Pasien`).
* Antarmuka web klien **TIDAK memanggil endpoint ini secara otomatis** saat proses scan interaktif di halaman Laundry/Settings, sehingga alur UI tetap terkontrol melalui pengisian form.

---

### **4.6 Penanganan Error & Keandalan (*Fault Tolerance*)**

1. **System Status Overlay (*Auto Reconnect Panel*):**  
   * Jika server backend mati/restart, halaman web secara otomatis menampilkan modal overlay *dark blurred glassmorphism* dengan spinner animasi, status LED berdenyut merah, dan waktu hitung mundur retry per detik.
   * Saat server kembali aktif (terdeteksi via `GET /api/health`), overlay tertutup otomatis dan koneksi WebSocket pulih tanpa reload halaman manual.
2. **Offline Sync Buffer:**  
   * Jika koneksi internet ke Supabase terputus, data tersimpan aman di tabel lokal `pengiriman_temp` dan dikirimkan ulang secara otomatis oleh background service saat internet kembali online.
3. **Indikator Hardware Disconnected:**  
   * Jika kabel USB RFID reader tercabut, badge status di sidebar dan header seketika berubah menjadi `DISCONNECTED` (LED merah).
