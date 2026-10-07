import re

# 1. Update riwayat_schema.py to include created_at
with open('d:/ClientS/app/schemas/riwayat_schema.py', 'rb') as f:
    riwayat = f.read().decode('utf-8')

# Ensure datetime is imported
if 'from datetime import datetime' not in riwayat:
    riwayat = 'from datetime import datetime\n' + riwayat

riwayat_resp = """class RiwayatResponse(RiwayatCreate):
    id: int
    created_at: datetime"""

riwayat = re.sub(r'class RiwayatResponse\(RiwayatCreate\):\s*id: int', riwayat_resp, riwayat)

with open('d:/ClientS/app/schemas/riwayat_schema.py', 'wb') as f:
    f.write(riwayat.encode('utf-8'))


# 2. Update app_core.js renderHistoryTable
with open('d:/ClientS/app/static/app_core.js', 'rb') as f:
    js = f.read().decode('utf-8')

new_render = """function renderHistoryTable(items) {
  // Logic for Dashboard history
  const tbody = document.getElementById('recent-history-body');
  if (!tbody) return;
  const recent = items.slice(0, 5);
  tbody.innerHTML = recent.length
    ? recent.map((item) => {
        const isMasuk = item.event_type.toLowerCase().includes('masuk') || item.event_type.toLowerCase() === 'verifikasi';
        const iconColor = isMasuk ? '#00D084' : '#3b82f6';
        const iconBg = isMasuk ? 'rgba(0,208,132,0.1)' : 'rgba(59,130,246,0.1)';
        const icon = isMasuk 
          ? '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="'+iconColor+'" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>'
          : '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="'+iconColor+'" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>';
          
        let dateStr = '-';
        if (item.created_at) {
          const d = new Date(item.created_at);
          dateStr = d.toLocaleString('id-ID', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' });
        }
        
        return `<div class="activity-item">
            <div style="background: ${iconBg}; padding: 10px; border-radius: 50%; display: flex;">
              ${icon}
            </div>
            <div style="display: flex; flex-direction: column; gap: 4px;">
              <span style="color: #fff; font-weight: 600; font-size: 0.9rem; text-transform: capitalize;">${item.event_type}</span>
              <span style="color: #8297b0; font-size: 0.75rem;">${item.lokasi_sesudah || '-'} &bull; ${dateStr}</span>
            </div>
          </div>`;
      }).join('')
    : '<div class="activity-item" style="justify-content: center; background: transparent; border: none; color: #8297b0;">Belum ada aktivitas hari ini.</div>';
}"""

js = re.sub(r'function renderHistoryTable\(items\) \{[\s\S]*?\}', new_render, js, count=1)

with open('d:/ClientS/app/static/app_core.js', 'wb') as f:
    f.write(js.encode('utf-8'))
