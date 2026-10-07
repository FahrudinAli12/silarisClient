// Service Worker DINONAKTIFKAN - gunakan network-first selalu
// Hanya berfungsi sebagai placeholder agar browser tidak error
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  // Hapus SEMUA cache lama
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.map(key => caches.delete(key))))
  );
  self.clients.claim();
});
// Tidak ada fetch handler - semua request langsung ke network
