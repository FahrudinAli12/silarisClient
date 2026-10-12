## **1. Tujuan Sistem**

Sistem Informasi Client Rumah Sakit (*Client Checking & Laundry Linen RS*) dirancang sebagai solusi operasional terpadu berbasis **RFID (Radio Frequency Identification)** untuk mengelola, melacak, dan memvalidasi sirkulasi linen di lingkungan rumah sakit secara real-time, akurat, dan terstandarisasi.

---

### **1.1 Tujuan Utama**

1. **Pelacakan Linen Real-Time (*Real-Time RFID Tracking*)**  
   * Melacak identitas unik (EPC Gen2 96-bit), posisi/lokasi fisik terkini (*Storage*, *R. Laundry*, atau Ruangan Pasien), serta status siklus operasional linen (*tersedia*, *dicuci*, *dipakai*).
   * Menghilangkan proses pencatatan manual linen kotor dan linen bersih yang memakan waktu dan rawan selisih hitung (*human error*).

2. **Manajemen Siklus Operasional Laundry & Distribusi**  
   * **Penerimaan & Verifikasi Baru:** Memvalidasi linen baru dari supplier/pusat logistik melalui integrasi kode verifikasi cloud sebelum didaftarkan ke inventaris lokal.
   * **Linen Masuk (Kotor):** Mencatat penerimaan linen kotor ke unit laundry untuk proses pencucian.
   * **Linen Keluar (Bersih):** Mencatat distribusi linen bersih dari unit laundry ke ruangan perawatan/poliklinik tujuan.
   * **Perhitungan Siklus Cuci (*Life Cycle Tracking*):** Menghitung akumulasi jumlah cuci (*total_cuci*) setiap lembar linen secara otomatis untuk evaluasi masa pakai kelayakan bahan linen rumah sakit.

3. **Operasional *Offline-First* dengan Sinkronisasi Cloud**  
   * Memastikan seluruh kegiatan scanning dan transaksi tetap berjalan normal 100% tanpa hambatan meskipun koneksi internet terputus, menggunakan database lokal SQLite (`Client_RumahSakit_Template.db`).
   * Menyediakan mekanisme buffer offline (`pengiriman_temp`) yang secara otomatis mengirimkan data transaksi ke cloud database (Supabase) saat jaringan internet kembali stabil.

4. **Monitoring Visual & Laporan Operasional Lengkap**  
   * Menyajikan **Dashboard** informatif dengan metrik ringkasan, visualisasi donut chart perbandingan linen cuci vs pakai, streaming live scan RFID, dan log aktivitas terkini.
   * Menyediakan modul **Master Data** (Data Linen & Data Ruangan) dan **History Linen** dengan visualisasi audit trail detail item per transaksi.
   * Menyediakan modul **Settings** untuk verifikasi paket linen baru dan audit log penerimaan supplier.

5. **Otomatisasi Hardware & Keandalan Operasional (*Active Scan Control*)**  
   * Mendukung skenario Single Reader maupun Dual Reader (COM3 untuk Linen Masuk dan COM4 untuk Linen Keluar) dengan proteksi buffer standby guna mencegah kontaminasi data antar unit scanner.
