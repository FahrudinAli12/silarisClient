## **2. Arsitektur & Aturan Dasar**

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
