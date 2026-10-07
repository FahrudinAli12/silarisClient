## **4. Modul & Fitur Utama**

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
