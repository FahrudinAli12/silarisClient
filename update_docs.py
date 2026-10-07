import os

docs = {
    "1_Tujuan-sistem.md": """## **1. Tujuan Sistem**

* Melacak linen, status, dan lokasi secara real-time di lingkungan Rumah Sakit.
* Mengelola proses operasional *laundry*, distribusi ke ruangan, dan pengembalian kotor.
* Mendukung operasional offline dengan database lokal SQLite dan sinkronisasi berkala (REST API) ke server Cloud (Supabase).
* Memberikan laporan visual interaktif berupa *Dashboard*, inventaris, distribusi, riwayat transaksi, dan verifikasi linen baru.
""",
    
    "2_Arsitektur-and-Aturan-dasar.md": """## **2. Arsitektur & Aturan Dasar**

* **Frontend Web:** HTML5, CSS3 (Vanilla), Vanilla JS. Memiliki UI/UX bertema modern *dark-mode* (Deep Blue/Teal) dengan efek *smooth transition* dan desain responsif.
* **Backend Service:** Python FastAPI, melayani REST API dan koneksi WebSocket. Dilengkapi *background service* yang berjalan *non-stop* untuk mendengarkan port Serial RFID.
* **Database Lokal:** SQLite, menyimpan seluruh data operasional klien (master linen, ruangan, riwayat transaksi, dan log) untuk memastikan aplikasi tetap berjalan tanpa internet.
* **Cloud Database:** API ke Supabase (atau server eksternal lain) untuk mengambil data rumah sakit, paket pengiriman linen baru, dan mengirim data riwayat lokal.
* **RFID Reader:** Perangkat reader terhubung via Serial Port (contoh: COM3, baud 115200). Koneksi dijaga secara real-time.
* **Aturan Dasar:**
  * Backend memonitor status RFID secara berkelanjutan.
  * Frontend menerima data *live scan* RFID melalui WebSocket, dengan tampilan UI yang reaktif.
  * Klien memprioritaskan database lokal (SQLite).
  * Sinkronisasi ke Cloud berjalan secara independen (Verifikasi Data & Sync Data).
""",

    "3_Protokol-Komunikasi-RFID.md": """## **3. Protokol Komunikasi RFID**

### **3.1 Interaksi Backend ke Hardware**
* Backend (FastAPI) membuka port Serial (contoh: COM3) dan berjalan di *background thread*.
* Mengirimkan *command* untuk *Multiple Inventory* (Mulai Scan) atau *Stop* saat pengguna menekan tombol *Play/Stop* dari antarmuka Web.

### **3.2 Integrasi Frontend ke Backend**
* Frontend Web terhubung menggunakan **WebSocket** (`ws://localhost:8000/ws`).
* Pesan yang diterima dari WebSocket (format JSON) berisi:
  * `type: "rfid_scan"` -> Membawa data daftar EPC yang terdeteksi secara real-time.
  * `type: "status"` -> Membawa informasi status pembacaan (apakah reader terputus atau tersambung).
* REST API `/api/rfid/status` digunakan untuk mengecek status port, ketersediaan *port* lain, dan mode koneksi awal.
""",

    "4_Modul-and-Fitur-utama.md": """## **4. Modul & Fitur Utama**

### **4.1 Dashboard**
* Ringkasan informasi operasional secara langsung: Total Linen, Linen di Cuci, Linen di Pakai, dan Total Ruangan.
* Terdapat *Widget* status konektivitas reader di header dan sidebar.

### **4.2 Master Data**
* **Data Linen:** Menampilkan daftar semua linen dengan tab khusus (*Semua Linen*, *Linen di Cuci*, *Linen di Pakai*). Kolom "Total Cuci" hanya ada di tab Semua Linen. Kolom EPC diberikan ruang lebih lebar agar mudah dibaca.
* **Data Ruangan:** Manajemen nama ruangan dan kode ruangan dengan fasilitas edit/hapus *inline*.
* Pagination di bagian kanan bawah tabel.

### **4.3 Laundry**
* **Linen Masuk:** Scanning massal linen kotor masuk ke laundry menggunakan tombol *Play/Stop* dinamis.
* **Linen Keluar:** Distribusi linen bersih dari laundry ke ruangan tertentu (pilihan dropdown ruangan).
* **History Linen:** Log aktivitas harian seluruh transaksi keluar dan masuk.

### **4.4 Settings & Verifikasi (Sync)**
* **Verifikasi Linen (Penerimaan):** Pengguna menginput kode verifikasi dari supplier/pusat, memanggil API Cloud, dan mencocokkan *live scan* RFID untuk mendaftarkan linen baru ke database lokal.
* **Sync Data:** Sinkronisasi manual atau monitoring data yang dikirimkan ke Cloud.
* Konfigurasi *Port* RFID menggunakan antarmuka *Custom Dropdown* modern.
""",

    "5_Database-Lokal-Client-(SQLite).md": """## **5. Database Lokal Client (SQLite)**

### **5.1 Tabel Master**
* `linen`: ID, epc (unik), kategori, nama_linen, status (Tercatat, dicuci, dipakai), lokasi, total_cuci.
* `ruangan`: ID, kode_ruangan, nama_ruangan, keterangan.

### **5.2 Tabel Transaksi & Log**
* `transaksi`: Mencatat kejadian umum dengan kolom `jenis_transaksi` (masuk/keluar), `kode_transaksi`, `petugas`, dan waktu.
* `detail_transaksi`: Merincikan setiap EPC yang terlibat dalam satu transaksi beserta statusnya.
* `pengiriman_cloud`: Tabel temporer atau *log* yang merekam detail sinkronisasi linen baru dari supplier/pusat.

Data disimpan ke dalam `app.db` secara lokal di lingkungan instalasi klien.
""",

    "6_UI-UX Guidelines.md": """## **6. UI/UX Guidelines**

1. **Tema Visual (Dark Mode Premium):**
   * Menggunakan skema warna *Deep Blue/Teal* (Warna latar `#051324`, gradasi biru `#0c2b42`).
   * Tombol aksen dan elemen interaktif menggunakan warna Cyan/Teal terang (`#00D4FF`).
   * Desain *Glassmorphism* dan *glow effect* halus pada *card* dan *tab*.

2. **Layout Utama:**
   * **Sidebar Kiri (Collapsible):** Memuat navigasi menu (*Master Data*, *Laundry*, *Settings*) dan widget status Reader RFID di bawah. Transisi *collapse/expand* diatur halus tanpa patah.
   * **Top Header:** Judul halaman, sub-judul, konfigurasi Port (Custom Dropdown), indikator notifikasi, dan avatar.
   * **Area Konten (Main):** Tempat form, *Hero Banner*, dan tabel-tabel data diletakkan.

3. **Komponen Antarmuka:**
   * **Custom Dropdown:** Elemen `<select>` konvensional diganti dengan DIV interaktif yang dapat di-*toggle* untuk estetika modern (termasuk pada pengaturan port).
   * **Tabel & Pagination:** Tabel bersih tanpa *border* vertikal tebal, dengan efek *hover* halus. Kontrol *pagination* selalu diletakkan rapi di pojok kanan bawah dari wadah tabel.
   * **Status Badge:** Indikator koneksi berbentuk kapsul (*rounded*) kecil beranimasi LED kedip, memberikan umpan balik status secara konstan (*CONNECTED/DISCONNECTED*).
""",

    "7_Alur-Kerja-Client.md": """## **7. Alur Kerja Client**

1. **Setup & Konektivitas**
   * Buka aplikasi web. Aplikasi secara otomatis menyambungkan *backend* ke port RFID. Jika perlu, ubah port via header *dropdown*.
   
2. **Penerimaan Linen Baru (Verifikasi)**
   * Masuk ke **Settings > Verifikasi Linen**.
   * Masukkan kode pengiriman dari pusat (API Cloud), lalu lakukan *Scan* barang fisik menggunakan RFID.
   * Simpan data, maka linen resmi terdaftar di database lokal (Master Data).

3. **Siklus Laundry (Kotor ke Bersih)**
   * **Linen Masuk:** Kumpulkan linen kotor dari ruangan, *Scan* di menu **Laundry > Linen Masuk**, lalu simpan (status menjadi *dicuci*, lokasi otomatis masuk ke *R. Laundry*).
   * **Linen Keluar:** Setelah bersih, scan linen untuk didistribusikan ke ruangan yang dituju, pilih tujuan ruangan, dan simpan (status menjadi *dipakai*, angka *Total Cuci* bertambah +1).

4. **Monitoring**
   * Pantau jumlah stok, lokasi per item, dan total pencucian melalui *Dashboard* dan *Master Data*.
   * Cek *History Linen* untuk penelusuran jika ada laporan linen hilang.
"""
}

for filename, content in docs.items():
    with open(os.path.join('d:/ClientS', filename), 'w', encoding='utf-8') as f:
        f.write(content)

print("Semua file .md berhasil diupdate.")
