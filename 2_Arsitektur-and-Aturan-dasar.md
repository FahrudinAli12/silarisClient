## **2. Arsitektur & Aturan Dasar**

---

### **2.1 Komponen Arsitektur Sistem**

```
+─────────────────────────────────────────────────────────────────────────────+
|                                FRONTEND WEB                                 |
|  - HTML5 & CSS3 Vanilla (Dark Mode Premium Hospital Grade #020b14)         |
|  - Vanilla JavaScript (app_core.js)                                         |
|  - Real-time Live Scan via WebSocket (ws://localhost:8000/ws/epc-live)     |
|  - Auto-reconnect Panel, Debounced Auto-Scan, Custom Dropdown Components     |
+───────────────────────────────▲─────────────────────────────────────────────+
                                │ WebSocket (Live EPC & Status) & REST API
+───────────────────────────────▼─────────────────────────────────────────────+
|                         BACKEND SERVICE (FastAPI)                           |
|  - Framework: Python 3.10+ FastAPI (app/main.py)                            |
|  - Routers: master_router, transaksi_router, setting_router, sync_router     |
|  - RFID Service Manager (rfid_service.py): Background Serial Thread         |
|  - Cloud Sync Service & Background Worker (cloud_sync_service.py)           |
+───────────────────────────────▲─────────────────────────────────────────────+
        │ Serial UART (115200 8N1)      │ SQLAlchemy ORM        │ REST API (HTTPS)
+───────▼───────────────+   +───────────▼───────────+   +───────▼─────────────+
|      RFID HARDWARE    |   |     DATABASE LOKAL    |   |    CLOUD DATABASE   |
|  Electron EL-UHF-RMT01|   |  SQLite (Local DB)    |   |       Supabase      |
|  - COM3 (Linen Masuk) |   |  Client_RumahSakit_   |   |  - pengiriman_linen |
|  - COM4 (Linen Keluar)|   |  Template.db          |   |  - detail_pengiriman|
|  Active Scan Control  |   |  Buffer offline temp  |   |  - sync_events      |
+───────────────────────+   +───────────────────────+   +─────────────────────+
```

1. **Frontend Web**
   * **Teknologi:** HTML5, CSS3 murni (Vanilla CSS tanpa Tailwind), Vanilla JavaScript modular (`app_core.js`).
   * **Tema Visual:** Dark Mode Hospital Grade dengan nuansa *Deep Navy & Blue* (`#020b14`, `#0B2038`), aksen *Electric Cyan* (`#00D4FF`), aksen status hijau (`#00D084`), amber (`#f59e0b`), dan merah (`#ed5968`), dilengkapi efek *glassmorphism* dan transisi lembut.
   * **Konektivitas:** Terhubung langsung ke backend via WebSocket (`/ws/epc-live`) untuk penerimaan event scanning secara instan, serta HTTP Fetch REST API untuk manajemen data.
   * **Fault Tolerance UI:** Dilengkapi *System Status Overlay* otomatis dengan *spinner* animasi dan counter retry per detik jika koneksi backend/server terputus.

2. **Backend Service**
   * **Teknologi:** Python FastAPI berbasis asynchronous ASGI server.
   * **Background Services:**
     * `RFIDServiceManager` (`app/services/rfid_service.py`): Berjalan di background thread independen, mendengarkan port serial secara non-stop, mem-parsing frame UART, dan menyiarkan hasil scan ke WebSocket client.
     * `SyncService` (`app/services/sync_service.py`): Berjalan di background thread untuk memeriksa dan menguras antrean buffer offline (`pengiriman_temp`) menuju Cloud.

3. **Database Lokal (SQLite)**
   * **File Database:** `Client_RumahSakit_Template.db`.
   * **ORM:** SQLAlchemy 2.0 declarative models (`app/models/`).
   * **Inisialisasi Otomatis:** Saat server start (`startup_event`), fungsi `init_db()` memvalidasi keberadaan skema tabel dan kolom. Jika belum ada atau skema berubah, tabel dibuat secara otomatis.

4. **Cloud Database (Supabase)**
   * **Protokol:** REST API HTTPS menggunakan `httpx`.
   * **Fungsi:** Mengambil data paket pengiriman linen dari supplier/pusat berdasarkan kode verifikasi (`pengiriman_linen` dan `detail_pengiriman_linen`), memperbarui status paket menjadi `received`, serta menerima event sinkronisasi transaksi klien (`sync_events`).

5. **Hardware RFID Reader**
   * **Model:** Electron EL-UHF-RMT01 UHF RFID Reader (ISO18000-6C, EPC Class 1 Gen 2).
   * **Koneksi:** UART TTL 3.3V via Serial Port / USB-to-UART Converter.
   * **Parameter Serial:** Baud rate 115200, 8 data bits, no parity, 1 stop bit (8N1).
   * **Topologi Dual Reader:**
     * Reader 1: `RFID_LINEN_MASUK` (default port `COM3`).
     * Reader 2: `RFID_LINEN_KELUAR` (default port `COM4`).
   * **Deteksi Dinamis:** Sistem secara otomatis mendeteksi apakah perangkat terhubung dalam *Single Reader Mode* atau *Dual Reader Mode*.

---

### **2.2 Aturan Dasar Sistem (*Core Rules*)**

1. **Active Scan Control Mode & Standby Reader Protection**
   * Saat operator memilih mode **Linen Masuk** (`POST /api/rfid/scan/start` dengan mode `LINEN_MASUK`):
     * Reader Linen Keluar (COM4) **wajib dihentikan** (`CMD_STOP_INVENTORY`), buffer UART-nya dikosongkan (`reset_input_buffer()`), dan statusnya menjadi `STANDBY`.
     * Reader Linen Masuk (COM3) **diaktifkan** (`CMD_START_INVENTORY`).
     * Semua tag EPC yang mungkin masih terbaca dari reader standby langsung di-drop agar tidak mencemari data transaksi masuk.
   * Saat operator memilih mode **Linen Keluar**, aturan berlaku sebaliknya: COM3 dimatikan dan COM4 diaktifkan.

2. **Offline-First & Buffer Lokal**
   * Klien selalu memprioritaskan database lokal SQLite. Operasional penerimaan linen kotor, distribusi linen keluar, dan lookup master data **tidak boleh terhenti** meskipun internet mati total.
   * Jika transmisi data ke Cloud gagal, payload transaksi disimpan ke tabel buffer lokal `pengiriman_temp` dengan status `pending` dan akan otomatis dikirim ulang saat jaringan pulih.

3. **Integritas Referensial Master Ruangan**
   * Data master ruangan tidak boleh dihapus secara sembarangan:
     * Ditolak jika nama ruangan sedang digunakan sebagai lokasi aktif linen manapun.
     * Ditolak jika nama ruangan sudah tercatat dalam riwayat header `transaksi_linen` guna menjaga keutuhan riwayat audit (*audit trail*).

4. **Idempotensi Verifikasi Penerimaan Linen Baru**
   * Satu kode verifikasi paket cloud hanya boleh diproses satu kali di klien.
   * Jika kode verifikasi sudah pernah disimpan pada riwayat transaksi lokal (`jenis_transaksi == "penerimaan"`), sistem akan menolak duplikasi dengan status HTTP 409 Conflict.

5. **Auto-Scan & Debouncing Protection**
   * Untuk transaksi live auto-scan individual (`POST /api/transaksi/auto-scan`), frontend menerapkan debounce map minimal 4000 ms per kombinasi jenis transaksi dan EPC agar scan berulang dalam hitungan milidetik tidak menghasilkan multi-transaksi duplikat.
