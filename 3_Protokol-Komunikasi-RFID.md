## **3. Protokol Komunikasi RFID**

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
