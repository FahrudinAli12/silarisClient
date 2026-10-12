## **3. Protokol Komunikasi RFID**

Dokumentasi ini menjelaskan secara rinci protokol komunikasi serial UART antara modul hardware reader **Electron EL-UHF-RMT01** dengan backend service Python FastAPI (`app/services/rfid_service.py`), serta protokol WebSocket ke antarmuka web klien.

---

### **3.1 Spesifikasi Fisik & Serial UART**

* **Modul Reader:** Electron EL-UHF-RMT01 (UHF RFID EPC Gen2 / ISO18000-6C).
* **Interface:** TTL 3.3V Serial UART (dihubungkan via kabel Serial / USB-UART).
* **Baud Rate:** `115200 bps`.
* **Data Format:** 8 Data Bits, No Parity, 1 Stop Bit (`8N1`).
* **Timeout Buffer:** 0.1 detik (`timeout=0.1`).

---

### **3.2 Struktur Frame Data Serial**

Setiap pertukaran data (perintah, respon, dan notifikasi pembacaan) menggunakan format paket biner berbasis frame dengan struktur berikut:

| Byte Posisi | Nama Field | Panjang | Nilai Hex | Keterangan |
| :--- | :--- | :--- | :--- | :--- |
| **0** | **Header** | 1 Byte | `0xBB` | Penanda awal paket frame (*Preamble*) |
| **1** | **Type** | 1 Byte | `0x00` / `0x01` / `0x02` | `00` = Command, `01` = Response, `02` = Notification |
| **2** | **CMD** | 1 Byte | Kode Command | `0x27` (Start), `0x28` (Stop), `0x22` (EPC Read Tag) |
| **3 - 4** | **PL** | 2 Byte | `0x00 0xNN` | *Parameter Length* (Big-Endian MSB-LSB) |
| **5 .. (5+PL-1)**| **Parameter** | PL Byte | Dinamis | Parameter perintah atau payload notifikasi tag |
| **(5+PL)** | **CRC** | 1 Byte | Dinamis | Checksum (LSB dari penjumlahan byte `Type` sampai akhir `Parameter`) |
| **(5+PL+1)** | **End** | 1 Byte | `0x7E` | Penanda akhir frame |

---

### **3.3 Perintah Operasional Reader (*Commands*)**

1. **Start Multiple Inventory (`0x27`)**  
   * **Fungsi:** Menginstruksikan reader untuk memancarkan RF dan membaca seluruh tag EPC yang berada dalam jangkauan antena secara kontinu.  
   * **Frame Hex:**  
     ```
     BB 00 27 00 03 22 27 10 83 7E
     ```
   * **Rincian:**
     * Header: `BB`
     * Type: `00` (Command)
     * CMD: `27` (Start Inventory)
     * PL: `00 03` (Panjang parameter 3 byte)
     * Parameter: `22 27 10`
     * CRC: `83`
     * End: `7E`
   * **Auto-Refresh Periodik:** Backend menerapkan mekanisme auto-refresh setiap 240 detik (`INVENTORY_REFRESH_SECONDS = 240`) untuk menjaga reader tetap dalam kondisi active scanning tanpa mengalami stall/idle firmware.

2. **Stop Multiple Inventory (`0x28`)**  
   * **Fungsi:** Menghentikan pemancaran RF dan menempatkan reader pada status *Standby*.  
   * **Frame Hex:**  
     ```
     BB 00 28 00 00 28 7E
     ```
   * **Rincian:**
     * Header: `BB`
     * Type: `00` (Command)
     * CMD: `28` (Stop Inventory)
     * PL: `00 00` (Tanpa parameter)
     * CRC: `28`
     * End: `7E`

---

### **3.4 Parsing Notifikasi Tag EPC (`Type 0x02`)**

Saat tag terdeteksi, reader mengirimkan paket **Notification Frame** (`Type = 0x02`, `CMD = 0x22`):

#### **Contoh Frame Nyata:**
```
BB 02 22 00 11 C9 34 00 30 75 1F EB 70 5C 59 04 E3 D5 0D 70 3A 76 EF 7E
```

#### **Bedah Struktur Parameter:**
* **Total Frame Length:** 24 byte.
* `BB`: Header awal.
* `02`: Tipe Notification.
* `22`: Command Code pembacaan tag.
* `00 11`: Parameter Length (17 byte).
* **Area Parameter (`frame[5 : 5 + 17]`):**
  * Byte 0 (`0xC9`): Nilai RSSI (*Received Signal Strength Indicator*).
  * Byte 1 - 2 (`0x34 0x00`): Protocol Control (PC Bits).
  * Byte 3 - 14 (`30 75 1F EB 70 5C 59 04 E3 D5 0D 70`): **12-byte EPC Tag Data** (96-bit).
  * Byte 15 - 16: CRC16 bawaan tag.
* `EF`: CRC Frame.
* `7E`: End Frame.

#### **Ekstraksi EPC di Backend:**
```python
if frame_type == 0x02:
    param = frame[5 : 5 + param_len]
    if len(param) >= 15:
        epc_bytes = param[3:15]  # Ambil 12 byte EPC
        epc = epc_bytes.hex().upper()  # Hasil: '30751FEB705C5904E3D50D70'
```

---

### **3.5 Arsitektur Dual Reader & Active Scan Protection**

Sistem mengelola reader melalui class `RFIDServiceManager` dan `SingleRFIDReader`:
1. **Reader Masuk:** `RFID_LINEN_MASUK` (default port `COM3`).
2. **Reader Keluar:** `RFID_LINEN_KELUAR` (default port `COM4`).
3. **Mekanisme Proteksi Standby:**
   * Sebelum reader tujuan diaktifkan dengan `CMD_START_INVENTORY`, reader pasangannya dikirimkan `CMD_STOP_INVENTORY`.
   * Buffer serial reader yang dinonaktifkan langsung dibersihkan (`reset_input_buffer()` & `buffer.clear()`).
   * EPC yang masuk dari reader yang tidak sesuai dengan `active_transaction_mode` langsung diabaikan (*ignored*) sehingga tidak ada kebocoran EPC antar proses.

---

### **3.6 Integrasi WebSocket & REST API**

#### **1. WebSocket Endpoint (`ws://localhost:8000/ws/epc-live`)**
Klien frontend terhubung secara real-time ke endpoint ini untuk menerima update siaran.

* **Payload Event Scan EPC:**
  ```json
  {
    "type": "epc",
    "epc": "30751FEB705C5904E3D50D70",
    "reader_name": "RFID_LINEN_MASUK",
    "port": "COM3",
    "transaction_type": "LINEN_MASUK",
    "timestamp": "2026-10-11 13:45:00"
  }
  ```

* **Payload Status Hardware Reader:**
  ```json
  {
    "type": "rfid_status",
    "mode": "DUAL_READER",
    "mode_label": "Dual Reader Mode",
    "port": "COM3 + COM4",
    "active_reader": "COM3",
    "active_scanner_label": "COM3 - Linen Masuk",
    "transaction_type": "LINEN_MASUK",
    "status": "ACTIVE"
  }
  ```

* **Mekanisme Konsumsi Event EPC di Frontend:**
  * Saat event `type: "epc"` diterima browser, payload diteruskan ke fungsi `processScannedEpc(payload.epc)` untuk dimasukkan ke dalam Set deduplikasi `scannedEpcs` dan dirender pada tabel UI (*Cek Linen Scanned* dan *Daftar Linen*).
  * **Frontend TIDAK memanggil endpoint transaksi auto-scan di background.** Pemindaian tag berfungsi murni sebagai penampung preview di antarmuka web.
  * Transaksi resmi ke database dan penambahan hitungan cuci (+1) hanya dikirimkan ketika operator menekan tombol **Proses** melalui form submission REST API (`POST /api/transaksi/batch`).

#### **2. REST API Hardware Control & Transaksi**
* `GET /api/rfid/status`: Mengambil status konektivitas reader, mode reader (Single/Dual), daftar port yang tersedia, dan reader aktif.
* `POST /api/rfid/port`: Mengubah port serial secara dinamis melalui parameter JSON `{"port": "COM3"}`.
* `POST /api/rfid/scan/start`: Mengaktifkan scanning reader berdasarkan mode transaksi (`{"mode": "LINEN_MASUK"}` atau `{"mode": "LINEN_KELUAR"}`).
* `POST /api/rfid/scan/stop`: Menghentikan seluruh proses scanning reader.
* `POST /api/transaksi/batch`: Menyimpan transaksi batch resmi yang disubmit melalui formulir UI (memperbarui status, lokasi, dan menambah `total_cuci` untuk Linen Masuk).
* `POST /api/transaksi/auto-scan`: Endpoint mandiri untuk integrasi gate RFID hardware (menolak tag yang belum terdaftar di sistem dengan HTTP 400).
