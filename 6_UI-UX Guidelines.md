## **6. UI/UX Guidelines**

Panduan desain dan tata letak antarmuka pengguna sistem klien rumah sakit dirancang untuk memaksimalkan efisiensi kerja operator laundry dan tim logistik rumah sakit, menjamin keterbacaan tinggi (*high readability*), serta menghadirkan estetika modern bertaraf *Hospital Grade*.

---

### **6.1 Tema Visual & Palet Warna (*Color Palette*)**

Sistem mengadopsi tema **Dark Mode Premium** dengan perpaduan warna *Deep Navy Blue* dan aksen fungsional kontras:

| Elemen / Token | Kode Warna Hex | Peruntukan Visual |
| :--- | :--- | :--- |
| **Main Background** | `#020b14` / `#051324` | Latar belakang utama aplikasi dan body layout |
| **Card Background** | `#0B2038` | Wadah card, panel tabel, dan modal |
| **Card Border** | `#173554` | Garis batas tipis untuk memisahkan kontainer |
| **Accent Cyan** | `#00D4FF` | Aksen primer, tombol aksi utama, header span, active tab highlight |
| **Status Green (Success)**| `#00D084` | Indikator LED *CONNECTED*, badge *Linen Masuk*, tag *Aktif* |
| **Status Amber (Warning)**| `#FBBF24` / `#f59e0b`| Badge *Linen Dipakai*, indikator legend distribusi keluar |
| **Status Purple** | `#a855f7` / `#8b5cf6`| Badge *Linen Dicuci*, segmen donut chart linen masuk |
| **Status Red (Danger)** | `#ed5968` / `#be123c`| Indikator LED *DISCONNECTED*, tombol *Stop Scan*, tombol *Hapus* |
| **Text Primary** | `#ffffff` / `#e6f0fa` | Judul, angka statistik tebal, nilai EPC |
| **Text Muted / Sub** | `#8297b0` | Sub-label, header kolom tabel, timestamp, keterangan |

---

### **6.2 Layout Utama (*Master Layout Architecture*)**

Aplikasi menggunakan layout *single-page application feel* tanpa scrolling halaman penuh yang membingungkan:

```
+─────────────────────────────────────────────────────────────────────────────+
| [Side-Bar] | TOP HEADER: [=] Title / Subtitle    [Port: COM3] [CONNECTED] @ |
|------------+----------------------------------------------------------------|
| Brand Logo | HERO BANNER (Identitas Halaman & Foto Rumah Sakit)             |
| MENU:      +----------------------------------------------------------------|
| >Dashboard | STAT CARDS GRID: [ Total: 150 ] [ Masuk: 45 ] [ Keluar: 105 ]  |
| vMasterData+----------------------------------------------------------------|
|  * Linen   | MAIN CONTENT AREA:                                             |
|  * Ruangan | [ Live Scan Stream / Form Input ]  [ Main Table & Pagination ] |
| vLaundry   |                                                                |
|  * Masuk   |                                                                |
|  * Keluar  |                                                                |
|  * History |                                                                |
| vSettings  |                                                                |
|------------|                                                                |
| Reader Box |                                                                |
+─────────────────────────────────────────────────────────────────────────────+
```

1. **Sidebar Kiri (Collapsible):**
   * **Brand Section:** Memuat ikon *Medical Cross* dengan teks tebal *Client Checking - HOSPITAL LINEN SYSTEM*.
   * **Navigasi Accordion:** Menu utama (*Dashboard*, *Master Data*, *Laundry*, *Settings*) dilengkapi sub-menu yang dapat dibuka/tutup secara halus (*smooth transition*).
   * **Widget Reader Status:** Di bagian paling bawah sidebar, menampilkan informasi real-time mengenai status reader, mode reader (*Single/Dual*), *Active Scanner*, port COM, waktu sync terakhir, serta tombol aksi **Test Connection**.

2. **Top Header Global:**
   * Tombol *Hamburger* untuk mengecilkan atau melebarkan sidebar secara responsif.
   * Judul halaman dan deskripsi konteks yang berubah sesuai rute yang dibuka.
   * **Custom Dropdown Port Reader:** Dropdown modern di kanan atas untuk memonitor atau mengganti port serial aktif secara on-the-fly.
   * **Status Badge LED:** Kapsul status kecil beranimasi kedip (*glowing LED dot*) yang memberikan kejelasan status hardware (*CONNECTED* hijau atau *DISCONNECTED* merah).
   * Icon notifikasi dan avatar admin.

---

### **6.3 Komponen Antarmuka Khusus (*Custom UI Components*)**

1. **Custom Dropdown (DIV-Based Dropdown):**
   * Menggantikan tag `<select>` HTML bawaan browser yang kaku dengan elemen DIV bertema dark mode custom.
   * Mendukung penutupan otomatis (*auto-dismiss*) saat pengguna mengklik di luar area dropdown.

2. **Tombol Kontrol Scan Interaktif (*Toggle Scan Button*):**
   * **Kondisi Standby:** Berwarna Cyan `#00D4FF` dengan teks "Start Scan" dan ikon *Play* SVG.
   * **Kondisi Scanning:** Otomatis berubah warna menjadi Merah `#ed5968` dengan teks "Stop Scan", ikon *Stop* kotak, dan animasi getar/kedip lembut (*is-scanning*).

3. **Status Badge Kategori Linen (*Category Badges*):**
   * Diberi styling kapsul dengan warna semi-transparan yang membedakan tipe barang secara sekilas (misal: badge Sprei warna biru muda, Selimut warna cyan, Sarung Bantal warna ungu, Baju Pasien warna amber).

4. **Tabel Data & Pagination:**
   * Garis tabel bersih tanpa border vertikal tebal; setiap baris memiliki efek *hover* tipis berwarna cyan (`rgba(0, 212, 255, 0.05)`).
   * Kolom EPC diberikan ruang visual lebar (32%) dan menggunakan font bertipe monospaced agar mudah dibaca dan diverifikasi oleh mata operator.
   * Kontrol pagination (*Prev*, *Active Page*, *Next*) diletakkan rapi di pojok kanan bawah wadah tabel.

5. **Wadah Data Kosong (*Empty State Illustration*):**
   * Saat data scan belum tersedia, tabel menampilkan box ilustrasi kotak scanner RFID SVG dengan teks panduan ramah (contoh: *"Belum ada data linen masuk. Silakan scan linen..."*).

6. **Interactive Donut Chart:**
   * Grafik donat visual berbasis vektor SVG murni dengan perhitungan persentase dinamis untuk menyajikan ringkasan rasio aktivitas dalam 7 hari terakhir.

7. **System Status Reconnect Overlay:**
   * Jika komunikasi backend terputus, layar otomatis di-cover panel *glassmorphism blur* (`backdrop-filter: blur(12px)`) yang memuat spinner putar, titik LED merah berdenyut (*pulse animation*), serta penghitung waktu percobaan reconnect tiap 1 detik. Panel hilang seketika saat backend kembali online.
