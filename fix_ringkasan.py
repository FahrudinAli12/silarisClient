import re

with open('d:/ClientS/app/templates/dashboard.html', 'rb') as f:
    html = f.read().decode('utf-8')

# 1. Fix top cards IDs
html = html.replace('<strong id="linen-dipakai" class="stat-value">0</strong>', '<strong id="stat-masuk" class="stat-value">0</strong>')
html = html.replace('<strong id="total-ruangan" class="stat-value">0</strong>', '<strong id="stat-keluar" class="stat-value">0</strong>')

# 2. Add IDs to Ringkasan
# Center number
html = re.sub(
    r'<strong style="font-size: 1.8rem; color: #fff; line-height: 1.2; font-weight: 700;">0</strong>',
    '<strong id="summary-total" style="font-size: 1.8rem; color: #fff; line-height: 1.2; font-weight: 700;">0</strong>',
    html
)
# Legend values
html = re.sub(
    r'<div class="legend-item"><span class="legend-label"><span class="legend-dot" style="background: #00D084; box-shadow: 0 0 8px #00D084;"></span> Linen Masuk</span><span class="legend-value" style="font-weight: 600; color: #fff;">0 \(0%\)</span></div>',
    '<div class="legend-item"><span class="legend-label"><span class="legend-dot" style="background: #00D084; box-shadow: 0 0 8px #00D084;"></span> Linen Masuk</span><span id="summary-masuk" class="legend-value" style="font-weight: 600; color: #fff;">0 (0%)</span></div>',
    html
)
html = re.sub(
    r'<div class="legend-item"><span class="legend-label"><span class="legend-dot" style="background: #3b82f6; box-shadow: 0 0 8px #3b82f6;"></span> Linen Keluar</span><span class="legend-value" style="font-weight: 600; color: #fff;">0 \(0%\)</span></div>',
    '<div class="legend-item"><span class="legend-label"><span class="legend-dot" style="background: #3b82f6; box-shadow: 0 0 8px #3b82f6;"></span> Linen Keluar</span><span id="summary-keluar" class="legend-value" style="font-weight: 600; color: #fff;">0 (0%)</span></div>',
    html
)
html = re.sub(
    r'<div class="legend-item"><span class="legend-label"><span class="legend-dot" style="background: #a855f7; box-shadow: 0 0 8px #a855f7;"></span> Total Linen</span><span class="legend-value" style="font-weight: 600; color: #fff;">0 \(0%\)</span></div>',
    '<div class="legend-item"><span class="legend-label"><span class="legend-dot" style="background: #a855f7; box-shadow: 0 0 8px #a855f7;"></span> Total Linen</span><span id="summary-total-side" class="legend-value" style="font-weight: 600; color: #fff;">0 (0%)</span></div>',
    html
)

with open('d:/ClientS/app/templates/dashboard.html', 'wb') as f:
    f.write(html.encode('utf-8'))

with open('d:/ClientS/app/static/app_core.js', 'rb') as f:
    js = f.read().decode('utf-8')

# 3. Update refreshDashboard logic
new_refresh = """  const refreshDashboard = async () => {
    const linen = await requestJson('/api/master/linen').catch(() => []);
    
    const totalLinenCount = linen.length;
    const linenMasukCount = linen.filter(item => item.status === 'dicuci' || item.status === 'tercatat').length;
    const linenKeluarCount = linen.filter(item => item.status === 'dipakai').length;
    
    // Top Cards
    const totalLinenEl = document.getElementById('total-linen');
    const statMasukEl = document.getElementById('stat-masuk');
    const statKeluarEl = document.getElementById('stat-keluar');
    
    if (totalLinenEl) totalLinenEl.textContent = totalLinenCount;
    if (statMasukEl) statMasukEl.textContent = linenMasukCount;
    if (statKeluarEl) statKeluarEl.textContent = linenKeluarCount;
    
    // Ringkasan Section
    const sumTotalEl = document.getElementById('summary-total');
    const sumMasukEl = document.getElementById('summary-masuk');
    const sumKeluarEl = document.getElementById('summary-keluar');
    const sumTotalSideEl = document.getElementById('summary-total-side');
    
    if (sumTotalEl) sumTotalEl.textContent = totalLinenCount;
    
    const calcPct = (val) => totalLinenCount > 0 ? Math.round((val / totalLinenCount) * 100) : 0;
    const masukPct = calcPct(linenMasukCount);
    const keluarPct = calcPct(linenKeluarCount);
    
    if (sumMasukEl) sumMasukEl.textContent = `${linenMasukCount} (${masukPct}%)`;
    if (sumKeluarEl) sumKeluarEl.textContent = `${linenKeluarCount} (${keluarPct}%)`;
    if (sumTotalSideEl) sumTotalSideEl.textContent = `${totalLinenCount} (100%)`;
  };"""

js = re.sub(r'const refreshDashboard = async \(\) => \{[\s\S]*?if \(totalRuangan\) totalRuangan\.textContent = ruangan\.length;\s*\};', new_refresh, js)

with open('d:/ClientS/app/static/app_core.js', 'wb') as f:
    f.write(js.encode('utf-8'))
