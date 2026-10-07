import os

docs = {
    "1_Tujuan-sistem.md": """## **1. Tujuan Sistem**

* Melacak linen, status, dan lokasi secara real-time.  
* Mengelola proses laundry, distribusi ke ruangan, dan pengembalian.  
* Mendukung operasional offline dengan buffer lokal dan sinkronisasi cloud.  
* Memberikan laporan inventaris, distribusi, riwayat transaksi, dan verifikasi linen baru.
""",
    
    "2_Arsitektur-and-Aturan-dasar.md": """## **2. Arsitektur & Aturan Dasar**

* **Frontend Web:** HTML, CSS, Vanilla JS, live update via WebSocket. UI Dark Mode Premium.
* **Backend Service:** Python FastAPI, background service nonstop mendengar port Serial RFID.  
* **Database Lokal:** SQLite, menyimpan master linen, riwayat transaksi, buffer offline.  
* **Cloud Database:** Supabase, menyimpan data rumah sakit dan pengiriman (read-only di client).  
* **RFID Reader:** Electron EL-UHF-RMT01 (EPC Gen2, ISO18000-6C), interface TTL 3.3V, baud rate 115200.  
* **Browser Target:** Brave / Chrome / Edge.  
* **Aturan Dasar:**  
  * Backend mendengarkan serial port nonstop.  
  * Frontend hanya menerima EPC via WebSocket.  
  * Semua transaksi disimpan ke SQLite lokal untuk operasional offline.  
  * Sinkronisasi cloud dilakukan saat koneksi tersedia.
""",

    "3_Protokol-Komunikasi-RFID.md": """## **3. Protokol Komunikasi RFID**

### **3.1 Struktur Frame (UART)**

* Format: 8 data bits, no parity, 1 stop bit (8N1).  
* Frame (hexadecimal):  
  * `Header` (1 Byte) = `BB`  
  * `Type` (1 Byte): `00`=Command, `01`=Response, `02`=Notification  
  * `CMD` / Command Code (1 Byte)  
  * `PL` / Parameter Length (2 Bytes)  
  * `Parameter` (N Bytes)  
  * `CRC` (1 Byte, LSB dari sum Type Parameter)  
  * `End` (1 Byte) = `7E`

### **3.2 Perintah Massal**

* **Start Multiple Inventory (`0x27`)**: memulai scan massal.  
  * Payload contoh: `BB 00 27 00 03 22 27 10 83 7E`  
* **Stop Multiple Inventory (`0x28`)**: menghentikan scan massal.  
  * Payload contoh: `BB 00 28 00 00 28 7E`

### **3.3 Parsing Balasan Sensor**

* **Notification Frame (Type `0x02`)**: mengandung EPC yang terbaca.  
  * Contoh: `BB 02 22 00 11 C9 34 00 30 75 1F EB 70 5C 59 04 E3 D5 0D 70 3A 76 EF 7E`  
  * `C9` = RSSI (sinyal), `30 75 . 70` = EPC.  
* **Error Frame (`0x15`)**: jika tag tidak terbaca, abaikan tapi service tetap jalan.  
  * Contoh: `BB 01 FF 00 01 15 16 7E`

### **3.4 Logika Backend & Integrasi UI**

* Thread atau async task di FastAPI untuk koneksi serial.  
* Kirim command `0x27` saat live scan dimulai.  
* Baca buffer serial, parse frame, ekstrak EPC push ke frontend via WebSocket.
* Frontend Web terhubung menggunakan **WebSocket** (`ws://localhost:8000/ws`).
""",

    "4_Modul-and-Fitur-utama.md": """## **4. Modul & Fitur Utama**

### **4.1 Dashboard**

* Ringkasan total linen, linen dicuci, linen dipakai, total ruangan.  
* Indikator status reader (Port + Status CONNECTED/DISCONNECTED) menggunakan badge LED.  
* Visual counter besar untuk cepat dibaca.

### **4.2 Master Data**

* **Data Linen:** Tersedia 3 tab (Semua Linen, Linen di Cuci, Linen di Pakai).  
  * EPC, Kategori, Nama Linen, Lokasi. (Atribut "Total Cuci" khusus ada di Semua Linen).
  * Kolom EPC dilebarkan untuk pembacaan RFID yang mudah.
* **Data Ruangan:** Kode Ruangan, Nama Ruangan, Keterangan.  
* Tombol aksi: Tambah, Edit, Delete.  
* Filtering cepat untuk lookup data dengan pagination di kanan bawah.

### **4.3 Laundry**

* **Linen Masuk:** Start Scan / Stop Scan / Reset Table, EPC live via WebSocket.  
* **Linen Keluar:** Scan keluar ke ruangan (menggunakan dropdown ruangan).  
* **History Linen:** Semua log dalam satu tabel dengan filter jenis transaksi.

### **4.4 Setting / Verifikasi (Sync)**

* **Verifikasi Linen:** Modal pop-up dan form scan untuk memverifikasi EPC linen kiriman baru dari pusat agar masuk ke database lokal (Linen Masuk).
* **Sinkronisasi Data Cloud:** Upload otomatis saat online, buffer `pengiriman_temp` saat offline.  
* **Log Pengiriman Data:** Penerimaan linen dan detail EPC.  
* Konfigurasi Port Reader via Custom Dropdown.

### **4.5 Backend & Hardware Service**

* FastAPI Python sebagai background service nonstop mendengar port Serial EL-UHF-RMT01.  
* Parsing Multiple Inventory (0x27) dan Stop Multiple Inventory (0x28).  
* EPC hasil scan dikirim real-time ke WebSocket frontend.  
* REST API untuk sinkronisasi cloud.

### **4.6 Error Handling**

* Hardware disconnected: Banner error / status badge merah (DISCONNECTED).  
* Frame rusak: toast kuning, proses tidak terhenti.  
* Internet offline: buffer `pengiriman_temp` + indikator offline.  
* EPC duplikat: highlight merah, audio beep.  
* WebSocket disconnected: overlay spinner + auto reconnect.
""",

    "6_UI-UX Guidelines.md": """## **6. UI/UX Guidelines**

1. **Prinsip Dasar:** Efisiensi operator, teks jelas, tombol besar, minim klik.  
2. **Tema Visual:** Dark Mode Premium (Deep Blue/Teal) dengan efek *Glassmorphism*. Latar warna `#051324`.  
3. **Layout & Navigasi:** Single-page interface, Sidebar kiri (collapsible/animasi halus), Top Header (memuat Custom Dropdown Port), tanpa sidebar kanan.  
4. **Live Scanning EPC:** Scroll otomatis, ikon animasi play/stop, audio feedback.  
5. **Form & Input:** Inline di atas area tabel, custom dropdowns untuk estetika, tombol aksi di bawah form.  
6. **Manajemen Data Master:** Highlight saat edit, inline Edit/Delete, search & pagination di pojok kanan bawah tabel.  
7. **History & Log:** Satu tabel filter jenis transaksi, color coding tipis, scrollable panel.  
8. **Setting / Sync:** Form Verifikasi Linen Terintegrasi, Custom indikator port reader.
""",

    "7_Alur-Kerja-Client.md": """## **7. Alur Kerja Client**

1. **Verifikasi Penerimaan Linen Baru**  
   * Buka **Settings > Verifikasi Linen**.
   * Masukkan kode, Scan EPC barang baru dari pusat, validasi, dan simpan agar terdaftar di SQLite lokal.
2. **Laundry Masuk (Linen Kotor)**  
   * Scan linen kotor di **Laundry > Linen Masuk**, update status menjadi PROSES_CUCI.  
3. **Laundry Selesai**  
   * Proses cuci internal laundry (increment total_cuci terjadi saat keluar).  
4. **Distribusi (Linen Bersih)**  
   * Kirim linen dari laundry ke ruangan di **Laundry > Linen Keluar**.
   * Pilih ruangan, scan barang, update status DIGUNAKAN (Lokasi = Ruangan tujuan).  
5. **Pengembalian**  
   * Linen kembali dari ruangan ke laundry. Proses kembali ke nomor 2.  
6. **Sinkronisasi Cloud**  
   * Upload ke Supabase, gunakan buffer `pengiriman_temp` saat offline.  
7. **Monitoring & Laporan**  
   * Dashboard + History Linen + Log Penerimaan Linen.
"""
}

# The database file 5_Database is already good enough, but I will restore it to original format
docs["5_Database-Lokal-Client-(SQLite).md"] = """## **5. Database Lokal Client (SQLite)**

### **5.1 Master & Transaksional**

* `Linen`: epc, kategori, nama, status, lokasi, total_cuci, timestamp.  
* `Ruangan`: kode_ruangan, nama, keterangan.  
* `Transaksi_Linen`: id, jenis_transaksi, tanggal, petugas, status.  
* `Detail_Transaksi_Linen`: id, id_transaksi, epc, status_sebelum, status_setelah, waktu_scan.  
* `Riwayat_Linen`: log lengkap linen masuk/keluar.  
* `Reader_Device`: info port serial, status, lokasi.

### **5.2 Cloud Read-Only**

* `rumah_sakit`: rs_id, kode_rs, nama_rs, alamat, kontak.

### **5.3 Buffer Offline**

* `pengiriman_temp`: temp_id, rs_id, daftar_epc (JSON), timestamp.
"""

for filename, content in docs.items():
    with open(os.path.join('d:/ClientS', filename), 'w', encoding='utf-8') as f:
        f.write(content)

print("Restored with full technical specs and UI updates")
