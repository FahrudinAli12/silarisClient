import re

with open('d:/ClientS/app/templates/data_linen.html', 'rb') as f:
    content = f.read().decode('utf-8')

replacement_html = """        <!-- Content wrapper to merge tabs and card -->
        <div class="linen-tabs-wrapper">
          <!-- Tabs -->
          <div class="tab-nav-container">
            <button class="linen-tab active" onclick="switchTab('all')">Semua Linen</button>
            <button class="linen-tab" onclick="switchTab('dicuci')">Linen di Cuci</button>
            <button class="linen-tab" onclick="switchTab('dipakai')">Linen di Pakai</button>
          </div>

          <!-- Card Semua Linen -->
          <div class="linen-card" id="card-all">
            <div class="linen-table-container">
              <table class="linen-table">
                <thead>
                  <tr>
                    <th>No</th>
                    <th>EPC</th>
                    <th>Kategori</th>
                    <th>Nama Linen</th>
                    <th>Lokasi</th>
                    <th>Total Cuci</th>
                  </tr>
                </thead>
                <tbody id="linen-data-body">
                  <tr><td colspan="6" style="text-align:center; color:var(--text-sub); height: 48px;">Data linen kosong</td></tr>
                </tbody>
              </table>
            </div>
            <div class="pagination-container">
              <div class="pagination-info">Menampilkan 0 dari 0 data</div>
              <div class="pagination-controls">
                <button class="page-btn">&lt;</button>
                <button class="page-btn active">1</button>
                <button class="page-btn">&gt;</button>
              </div>
            </div>
          </div>

          <!-- Card Linen di Cuci -->
          <div class="linen-card" id="card-dicuci" style="display: none;">
            <div class="linen-table-container">
              <table class="linen-table">
                <thead>
                  <tr>
                    <th>No</th>
                    <th>EPC</th>
                    <th>Kategori</th>
                    <th>Nama Linen</th>
                    <th>Lokasi</th>
                  </tr>
                </thead>
                <tbody id="linen-dicuci-body">
                  <tr><td colspan="5" style="text-align:center; color:var(--text-sub); height: 48px;">Tidak ada linen dicuci</td></tr>
                </tbody>
              </table>
            </div>
            <div class="pagination-container">
              <div class="pagination-info">Menampilkan 0 dari 0 data</div>
              <div class="pagination-controls">
                <button class="page-btn">&lt;</button>
                <button class="page-btn active">1</button>
                <button class="page-btn">&gt;</button>
              </div>
            </div>
          </div>

          <!-- Card Linen di Pakai -->
          <div class="linen-card" id="card-dipakai" style="display: none;">
            <div class="linen-table-container">
              <table class="linen-table">
                <thead>
                  <tr>
                    <th>No</th>
                    <th>EPC</th>
                    <th>Kategori</th>
                    <th>Nama Linen</th>
                    <th>Lokasi</th>
                  </tr>
                </thead>
                <tbody id="linen-dipakai-body">
                  <tr><td colspan="5" style="text-align:center; color:var(--text-sub); height: 48px;">Tidak ada linen dipakai</td></tr>
                </tbody>
              </table>
            </div>
            <div class="pagination-container">
              <div class="pagination-info">Menampilkan 0 dari 0 data</div>
              <div class="pagination-controls">
                <button class="page-btn">&lt;</button>
                <button class="page-btn active">1</button>
                <button class="page-btn">&gt;</button>
              </div>
            </div>
          </div>
        </div>"""

pattern = r'<!-- Content wrapper to merge tabs and card -->\s*<div class="linen-tabs-wrapper">.*?</div>\s*</div>\s*</div>\s*</div>'

content = re.sub(pattern, replacement_html, content, flags=re.DOTALL)

with open('d:/ClientS/app/templates/data_linen.html', 'wb') as f:
    f.write(content.encode('utf-8'))
