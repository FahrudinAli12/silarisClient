## **5. Database Lokal Client (SQLite)**

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
