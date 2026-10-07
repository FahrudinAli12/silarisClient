import re

with open('d:/ClientS/app/templates/data_linen.html', 'rb') as f:
    content = f.read().decode('utf-8')

fixed_card_all = """            <button class="linen-tab" onclick="switchTab('dipakai')">Linen di Pakai</button>
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
          </div>"""

pattern = r'            <button class="linen-tab" onclick="switchTab\(\'dipakai\'\)">Linen di Pakai</button>\s*<button class="page-btn active">1</button>\s*<button class="page-btn">&gt;</button>\s*</div>\s*</div>\s*</div>'

content = re.sub(pattern, fixed_card_all, content)

with open('d:/ClientS/app/templates/data_linen.html', 'wb') as f:
    f.write(content.encode('utf-8'))
