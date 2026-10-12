## **5. Database Lokal Client (SQLite)**

Aplikasi klien menggunakan database lokal berbasis **SQLite** yang disimpan pada file fisik `Client_RumahSakit_Template.db`. Struktur skema didefinisikan menggunakan **SQLAlchemy 2.0 ORM** pada direktori `app/models/` dan diverifikasi saat startup melalui `app/database.py`.

---

### **5.1 Tabel Master & Operasional Lokal**

#### **1. Tabel `linen` (Master Data Linen)**
Menyimpan identitas unik seluruh lembar linen rumah sakit.

| Kolom | Tipe Data | Constraint | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | PK, Autoincrement | ID unik internal |
| `epc` | `VARCHAR(64)`| Unique, Index, Not Null | Kode tag RFID EPC Gen2 (96-bit) |
| `kategori` | `VARCHAR(50)`| Not Null | Kategori linen (Sprei, Sarung Bantal, Selimut, Baju Pasien, dll.) |
| `nama_linen` | `VARCHAR(100)`| Not Null | Nama barang |
| `lokasi` | `VARCHAR(100)`| Default: `"Storage"` | Lokasi fisik terkini (*Storage*, *R. Laundry*, atau Ruangan) |
| `total_cuci` | `INTEGER` | Default: `0` | Akumulator jumlah siklus pencucian linen |
| `status` | `VARCHAR(30)`| Default: `"tersedia"` | Status siklus (`tersedia`, `dicuci`, `dipakai`) |
| `created_at` | `DATETIME` | Default: `utcnow` | Waktu pertama kali didaftarkan |

#### **2. Tabel `ruangan` (Master Data Ruangan)**
Menyimpan daftar ruangan atau poliklinik tujuan distribusi linen di rumah sakit.

| Kolom | Tipe Data | Constraint | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | PK, Autoincrement | ID unik ruangan |
| `kode_ruangan` | `VARCHAR(20)`| Unique, Index, Not Null | Kode unik ruangan (misal: `R001`, `ICU-01`) |
| `nama_ruangan` | `VARCHAR(100)`| Not Null | Nama lengkap ruangan (misal: *Ruang Mawar 01*) |
| `keterangan` | `VARCHAR(255)`| Nullable | Informasi tambahan peruntukan ruangan |
| `created_at` | `DATETIME` | Default: `utcnow` | Waktu pencatatan ruangan |

---

### **5.2 Tabel Transaksional & Audit Trail**

#### **3. Tabel `transaksi_linen` (Header Transaksi)**
Mencatat setiap sesi transaksi batch atau auto-scan pergerakan linen.

| Kolom | Tipe Data | Constraint | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | PK, Autoincrement | ID transaksi |
| `jenis_transaksi` | `VARCHAR(30)`| Not Null | Jenis transaksi (`masuk`, `keluar`, `penerimaan`, `cuci`) |
| `kode_transaksi` | `VARCHAR(50)`| Unique, Not Null | Kode transaksi (misal: `TRX-MASUK-1718000000`, `AUTO-MASUK-...`) |
| `petugas` | `VARCHAR(100)`| Nullable | Nama petugas operator yang memproses |
| `ruangan` | `VARCHAR(100)`| Nullable | Ruangan asal/tujuan distribusi |
| `total_linen` | `INTEGER` | Default: `0` | Jumlah lembar linen yang terlibat dalam transaksi |
| `status` | `VARCHAR(30)`| Default: `"selesai"` | Status transaksi (`selesai`, `pending`) |
| `catatan` | `VARCHAR(255)`| Nullable | Catatan transaksi atau kode verifikasi cloud |
| `reader_source` | `VARCHAR(50)`| Nullable | Nama reader yang membaca (misal: `RFID_LINEN_MASUK`) |
| `port_source` | `VARCHAR(50)`| Nullable | Port sumber pembacaan (misal: `COM3`, `COM4`) |
| `created_at` | `DATETIME` | Default: `utcnow` | Waktu transaksi dibuat |

#### **4. Tabel `detail_transaksi_linen` (Item per Transaksi)**
Merinci setiap EPC linen yang termasuk ke dalam satu transaksi tertentu.

| Kolom | Tipe Data | Constraint | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | PK, Autoincrement | ID detail transaksi |
| `transaksi_id` | `INTEGER` | FK: `transaksi_linen.id` | Relasi ke header transaksi |
| `linen_id` | `INTEGER` | FK: `linen.id` | Relasi ke master linen |
| `epc` | `VARCHAR(64)`| Not Null | Kode EPC yang discan |
| `status_sebelum` | `VARCHAR(50)`| Nullable | Status linen sebelum transaksi |
| `status_setelah` | `VARCHAR(50)`| Nullable | Status linen setelah transaksi |
| `waktu_scan` | `DATETIME` | Default: `utcnow` | Waktu spesifik scan item |
| `keterangan` | `VARCHAR(255)`| Nullable | Keterangan spesifik item |

#### **5. Tabel `riwayat_linen` (Log Pergerakan)**
Mencatat rekaman jejak perpindahan posisi setiap linen sepanjang masa pakainya.

| Kolom | Tipe Data | Constraint | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | PK, Autoincrement | ID riwayat |
| `linen_id` | `INTEGER` | FK: `linen.id` | Relasi ke linen terkait |
| `transaksi_id` | `INTEGER` | FK: `transaksi_linen.id` | Relasi ke transaksi (bisa nullable) |
| `event_type` | `VARCHAR(30)`| Not Null | Tipe event (`masuk`, `keluar`, `penerimaan`, `cuci`) |
| `lokasi_sebelumnya` | `VARCHAR(100)`| Nullable | Lokasi fisik sebelum perpindahan |
| `lokasi_sesudah` | `VARCHAR(100)`| Nullable | Lokasi fisik setelah perpindahan |
| `created_at` | `DATETIME` | Default: `utcnow` | Waktu kejadian perpindahan |

---

### **5.3 Konfigurasi Perangkat & Organisasi**

#### **6. Tabel `reader_device`**
Menyimpan konfigurasi profil hardware reader yang dialokasikan ke sistem klien.

| Kolom | Tipe Data | Constraint | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | PK, Autoincrement | ID profil perangkat |
| `nama_reader` | `VARCHAR(100)`| Not Null | Nama unit (misal: *Reader Laundry Gate 1*) |
| `port` | `VARCHAR(50)`| Not Null | Serial COM Port (misal: `COM3`, `COM4`) |
| `baudrate` | `INTEGER` | Default: `115200` | Kecepatan baud rate UART |
| `status` | `VARCHAR(30)`| Default: `"disconnected"`| Status perangkat (`connected`, `disconnected`, `active`) |
| `created_at` | `DATETIME` | Default: `utcnow` | Waktu pendaftaran profil |

#### **7. Tabel `rumah_sakit`**
Menyimpan data identitas instansi rumah sakit pengguna sistem klien.

| Kolom | Tipe Data | Constraint | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | PK, Autoincrement | ID profil RS |
| `nama` | `VARCHAR(150)`| Not Null | Nama lengkap rumah sakit |
| `kode` | `VARCHAR(50)`| Unique, Not Null | Kode registrasi rumah sakit |
| `alamat` | `VARCHAR(255)`| Nullable | Alamat domisili fisik rumah sakit |
| `created_at` | `DATETIME` | Default: `utcnow` | Waktu pencatatan data |

---

### **5.4 Buffer Sinkronisasi Offline**

#### **8. Tabel `pengiriman_temp`**
Menampung payload transaksi yang belum berhasil terkirim ke server Cloud saat kondisi internet offline.

| Kolom | Tipe Data | Constraint | Keterangan |
| :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | PK, Autoincrement | ID buffer antrean |
| `payload` | `VARCHAR(1000)`| Not Null | Serialized string JSON data transaksi yang akan dikirim |
| `status` | `VARCHAR(30)`| Default: `"pending"` | Status pengiriman (`pending`, `sent`) |
| `created_at` | `DATETIME` | Default: `utcnow` | Waktu pesan dimasukkan ke buffer |

---

### **5.5 Integrasi Cloud Database (Supabase)**

Sistem klien berinteraksi dengan tabel-tabel eksternal berikut di Supabase via REST API:

1. `pengiriman_linen`: Header paket kiriman linen baru dari supplier/pusat (`id`, `verification_code`, `supplier`, `status`, `tanggal`, `catatan`).
2. `detail_pengiriman_linen`: Rincian EPC dan item pada paket pengiriman (`id`, `pengiriman_id`, `epc`, `kategori`, `nama_linen`).
3. `sync_events`: Tabel log cloud tempat klien mengirimkan payload transaksi offline buffer (`push_record`).
