# 🏥 Sistem Informasi Client Tracking & Laundry Linen RS (RFID)

Sistem Informasi Client Rumah Sakit (*Client Checking & Laundry Linen RS*) adalah platform manajemen operasional terpadu berbasis **RFID UHF (EPC Gen2)**. Sistem ini dirancang untuk memantau, mencatat, dan mengotomasi pergerakan linen rumah sakit secara real-time, akurat, dan berkeandalan tinggi dengan arsitektur **Offline-First**.

---

## 📌 1. Penjelasan Singkat Aplikasi

Aplikasi ini menggabungkan antarmuka web modern (*Dark Mode Hospital Grade*) dengan servis background Python yang terhubung langsung ke pembaca RFID serial hardware (**Electron EL-UHF-RMT01**). 

### Masalah yang Diselesaikan:
* Menghilangkan proses hitung manual linen kotor dan linen bersih yang memakan waktu dan rentan selisih (*human error*).
* Mencegah kehilangan linen antar ruangan dengan pencatatan lokasi fisik yang jelas.
* Mengetahui riwayat keausan bahan melalui pencatatan akumulasi siklus cuci (*total_cuci*) per helai linen.
* Menjamin operasional rumah sakit tetap 100% berjalan normal meskipun koneksi internet terputus berkat basis data lokal.

---

## 🏗️ 2. Arsitektur Sistem

Sistem beroperasi dengan memadukan komunikasi serial UART perangkat keras, pertukaran data dua arah via WebSocket, basis data lokal SQLite, dan sinkronisasi berkala ke cloud database Supabase.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                FRONTEND WEB                                 │
│  - HTML5, CSS3 Vanilla (Dark Mode Premium #020b14), Vanilla JS (app_core.js)│
│  - Real-time Live Scan via WebSocket (ws://localhost:8000/ws/epc-live)      │
│  - System Status Reconnect Overlay & Debounced Auto-Scan Processing         │
└───────────────────────────────▲─────────────────────────────────────────────┘
                                │ WebSocket (Push Event) & REST API (HTTP)
┌───────────────────────────────▼─────────────────────────────────────────────┐
│                          BACKEND SERVICE (FastAPI)                          │
│  - Web & API Server: Python 3.10+ FastAPI (app/main.py)                     │
│  - RFID Service Manager (app/services/rfid_service.py): Serial Listener     │
│  - Cloud Sync Service (app/services/cloud_sync_service.py): Background Sync │
└───────────────────────────────▲─────────────────────────────────────────────┘
        │ Serial UART (115200 8N1)      │ SQLAlchemy ORM        │ HTTPS REST API
┌───────▼───────────────┐   ┌───────────▼───────────┐   ┌───────▼─────────────┐
│      RFID HARDWARE    │   │     DATABASE LOKAL    │   │    CLOUD DATABASE   │
│  Electron EL-UHF-RMT01│   │  SQLite (Local Engine)│   │       Supabase      │
│  - COM3 (Linen Masuk) │   │  Client_RumahSakit_   │   │  - pengiriman_linen │
│  - COM4 (Linen Keluar)│   │  Template.db          │   │  - detail_pengiriman│
│  Active Scan Control  │   │  Buffer: pengiriman_  │   │  - sync_events      │
│  & Standby Protection │   │  temp                 │   │                     │
└───────────────────────┘   └───────────────────────┘   └─────────────────────┘
```

### Prinsip Utama Sistem:
1. **Active Scan Control & Standby Protection:** Saat mode *Linen Masuk* aktif, reader *Linen Keluar* otomatis dinonaktifkan (*standby*) dan buffer serialnya dikosongkan agar data scan tidak tercampur.
2. **Offline-First:** Seluruh transaksi langsung dicatat ke SQLite lokal. Jika koneksi internet terputus, data diantrekan ke tabel buffer `pengiriman_temp`.
3. **Idempotensi & Safe Delete:** Kode verifikasi cloud tidak dapat diproses ganda (HTTP 409), dan data ruangan tidak dapat dihapus jika masih terikat pada linen aktif atau riwayat transaksi.

---

## 🔄 3. Alur Kerja Inti (*Core Life Cycle*)

```
[ Supplier / Cloud Pusat ]
          │ (1. Kode Verifikasi Pengiriman)
          ▼
┌─────────────────────────────────┐
│ 1. Verifikasi Penerimaan Linen  │ ──► Didaftarkan ke Database Lokal (Status: "tersedia")
└─────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────┐
│ 2. Distribusi Linen Keluar      │ ──► Dikirim ke Ruangan Pasien (Status: "dipakai")
└─────────────────────────────────┘
          │ (Linen kotor setelah dipakai)
          ▼
┌─────────────────────────────────┐
│ 3. Laundry Masuk (Linen Kotor)  │ ──► Diterima di R. Laundry (Status: "dicuci", Cuci +1)
└─────────────────────────────────┘
          │ (Proses Cuci, Kering, Setrika)
          ▼
┌─────────────────────────────────┐
│ 4. Laundry Selesai (Bersih)     │ ──► Siap didistribusikan ulang ke Ruangan
└─────────────────────────────────┘
          ▲                               │
          └───────────────────────────────┘ (Siklus berulang)
```

1. **Penerimaan Linen Baru (*Settings > Verifikasi*):**  
   Operator memasukkan kode verifikasi dari supplier -> sistem mengambil manifest dari Supabase -> operator scan fisik linen -> sistem mencocokkan EPC valid -> disimpan ke SQLite lokal.
2. **Distribusi Keluar (*Laundry > Linen Keluar*):**  
   Linen bersih discan -> operator memilih ruangan tujuan (misal: *Ruang Mawar 01*) -> status berubah menjadi `dipakai`.
3. **Penerimaan Masuk (*Laundry > Linen Masuk*):**  
   Linen kotor ditarik dari ruangan -> discan di unit laundry -> status berubah menjadi `dicuci`, lokasi menjadi `R. Laundry`, dan nilai akumulator `total_cuci` bertambah (+1).
4. **Monitoring & Audit (*Dashboard & History*):**  
   Operator dan manajemen memantau live scan stream, statistik persentase cuci vs pakai, serta audit detail setiap helai linen per transaksi.

---

## 💻 4. Teknologi Utama (*Tech Stack*)

| Lapisan | Komponen / Library | Kegunaan |
| :--- | :--- | :--- |
| **Backend** | Python 3.10+, FastAPI, Uvicorn | Web framework performa tinggi, REST API, & WebSocket handler |
| **ORM & DB** | SQLAlchemy 2.0, SQLite | Manajemen basis data lokal (`Client_RumahSakit_Template.db`) |
| **Hardware I/O** | `pyserial` | Komunikasi serial UART (115200 8N1) ke RFID Reader |
| **HTTP Client** | `httpx` | Komunikasi asinkronus ke Cloud API Supabase |
| **Frontend** | HTML5, Vanilla CSS3, Vanilla JavaScript | Antarmuka interaktif, WebSocket live stream, responsif tanpa framework berat |
| **Cloud Service**| Supabase REST API (PostgreSQL) | Manifest paket pengiriman linen dan penampung sinkronisasi event |
| **Testing** | `pytest`, `pytest-asyncio` | Unit & integration testing untuk transaksi, rfid, dan sinkronisasi |

---

## 📑 5. Daftar Halaman & Rute Antarmuka

Aplikasi memiliki struktur navigasi terintegrasi dengan Sidebar Accordion dan Top Header:

| Halaman | URL / Rute | Fungsionalitas Utama |
| :--- | :--- | :--- |
| **Dashboard** | `/dashboard` atau `/` | * Metric counter (Total Linen, Linen Masuk, Linen Keluar)<br>* Streaming tabel *Live Scan RFID* real-time<br>* Donut chart perbandingan rasio cuci vs pakai<br>* 5 riwayat aktivitas operasional terkini<br>* Widget status konektivitas reader & tombol *Test Connection* |
| **Data Linen** | `/master-data/linen` | * 3 Tab: *Semua Linen*, *Linen di Cuci*, dan *Linen di Pakai*<br>* Kolom EPC lebar (32%) & badge kategori warna-warni<br>* Atribut akumulasi *Total Cuci*<br>* Pencarian instan dan pagination |
| **Data Ruangan** | `/master-data/ruangan` | * Master daftar ruangan perawatan / poliklinik RS<br>* Form tambah & edit ruangan inline<br>* Proteksi integritas referensial (*safe delete*) |
| **Linen Masuk** | `/laundry?tab=masuk` | * Tombol toggle Start/Stop Scan (Play/Stop animasi)<br>* Tabel live cek EPC yang terbaca<br>* Form proses transaksi masuk ke `R. Laundry`<br>* Tabel riwayat daftar linen masuk |
| **Linen Keluar** | `/laundry?tab=keluar` | * Scan linen bersih yang akan didistribusikan<br>* Custom Dropdown pemilihan ruangan tujuan<br>* Pemrosesan distribusi ke ruangan pasien |
| **History Linen** | `/laundry?tab=history` | * Metrik ringkasan masuk/keluar & grafik aktivitas 7 hari<br>* Dual table: Log Linen Masuk & Log Linen Keluar<br>* Panel kanan: Detail rincian EPC item per transaksi |
| **Sync Data** | `/settings?tab=sync` | * Counter Linen Terdaftar vs Tidak Terdaftar<br>* Form verifikasi paket baru supplier via Cloud Supabase<br>* Fitur Cek Linen & pemetaan EPC otomatis<br>* Tombol simpan data atomik ke SQLite lokal |
| **Log Penerimaan**| `/settings?tab=log` | * Riwayat transaksi penerimaan paket dari supplier<br>* Detail item linen yang diterima per paket |

---

## ⚙️ 6. Konfigurasi Environment & Sync

Aplikasi membaca variabel lingkungan melalui sistem operasi atau file konfigurasi `.env`. Jika variabel tidak diset, sistem otomatis menggunakan konfigurasi default yang aman.

### Daftar Environment Variables:

| Variabel | Default Value | Deskripsi |
| :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite:///Client_RumahSakit_Template.db` | URL koneksi basis data SQLite lokal |
| `RFID_MASUK_PORT` | `COM3` | Port serial UART untuk reader Linen Masuk |
| `RFID_KELUAR_PORT` | `COM4` | Port serial UART untuk reader Linen Keluar |
| `RFID_DEMO_MODE` | `0` | Set ke `1` untuk menyimulasikan scan EPC dummy tanpa hardware fisik |
| `SUPABASE_URL` | `https://suhdejzcneeaozlmtguf.supabase.co` | Endpoint REST API Cloud Supabase |
| `SUPABASE_ANON_KEY`| *(Anon Key Publishable Supabase)* | Token autentikasi API Supabase |

### Cara Kerja Sinkronisasi Cloud:
1. **Verifikasi Paket Baru (Cloud ke Lokal):**  
   Saat tombol *Get Data* ditekan di menu Settings, sistem memanggil endpoint Supabase `/rest/v1/pengiriman_linen` dan `/rest/v1/detail_pengiriman_linen`. Setelah barang diverifikasi dan disimpan di lokal, sistem memperbarui status paket di Supabase menjadi `received`.
2. **Buffer Sinkronisasi Offline (Lokal ke Cloud):**  
   Jika koneksi internet gagal saat transaksi dibuat, payload disimpan ke tabel lokal `pengiriman_temp`. Thread background `SyncService` berjalan setiap 5 detik; ketika internet kembali tersedia, data otomatis dikirim ke tabel Supabase `sync_events`.

---

## 🚀 7. Cara Menjalankan Aplikasi

### Prasyarat Sistem:
* **Python:** Versi 3.10 atau yang lebih baru (disarankan Python 3.11+).
* **OS:** Windows 10/11 (mendukung Serial COM Port).
* **Browser:** Google Chrome, Brave, atau Microsoft Edge.

### Langkah Instalasi:

1. **Clone Repository & Masuk ke Direktori:**
   ```bash
   git clone https://github.com/username/silarisClient.git
   cd silarisClient
   ```

2. **Buat dan Aktifkan Virtual Environment:**
   * Di Windows (Command Prompt / PowerShell):
     ```powershell
     python -m venv .venv
     .venv\Scripts\activate
     ```

3. **Install Dependensi:**
   ```bash
   pip install fastapi uvicorn sqlalchemy pyserial httpx pydantic websockets pytest
   ```

4. **Jalankan Aplikasi:**
   ```bash
   python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

5. **Akses Antarmuka Web:**
   Buka browser dan navigasikan ke:
   ```
   http://localhost:8000
   ```

6. **(Opsional) Menjalankan Unit Test:**
   Untuk memverifikasi seluruh fungsi backend, database, dan protokol RFID:
   ```bash
   python -m pytest
   ```
