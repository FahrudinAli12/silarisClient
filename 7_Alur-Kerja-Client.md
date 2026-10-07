## **7. Alur Kerja Client**

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
