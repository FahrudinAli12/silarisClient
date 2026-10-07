import re

with open("d:\\ClientS\\app\\templates\\master_data.html", "r", encoding="utf-8") as f:
    master_html = f.read()

header_match = re.search(r'(<!DOCTYPE html>.*?</header>)', master_html, re.DOTALL)
if not header_match:
    print("Could not extract header from master_data.html")
    exit(1)
clean_header = header_match.group(1)

with open("d:\\ClientS\\recover.txt", "r", encoding="utf-8") as f:
    css = f.read()

css_lines = css.split('\n')
filtered_css = []
skip = False
for line in css_lines:
    if ".hero-banner {" in line or ".sidebar {" in line or ".sidebar.collapsed" in line or ".sidebar-brand" in line or ".top-header" in line or ".header-left" in line or ".header-right" in line or ".hero-img-box {" in line:
        skip = True
    if skip and "}" in line:
        skip = False
        continue
    if not skip:
        filtered_css.append(line)

final_css = "\n".join(filtered_css)

css_injection = """
  <style>
    /* Exact Typography & Layout overrides */
    body, .app-layout { background-color: #020b14 !important; color: #e6f0fa; }
""" + final_css + """
  </style>
</head>
"""
clean_header = clean_header.replace("</head>", css_injection)

# Change title and active menu
clean_header = clean_header.replace("<title>Master Data - Client Checking & Laundry Linen RS</title>", "<title>Dashboard - Client Checking & Laundry Linen RS</title>")
# We also need to fix the active menu in sidebar!
clean_header = clean_header.replace('class="nav-item active"\n            <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">\n              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>', 'class="nav-item"\n            <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">\n              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>')
clean_header = clean_header.replace('href="/dashboard" class="nav-item"', 'href="/dashboard" class="nav-item active"')
clean_header = clean_header.replace('<h1 class="header-title">Master Data Linen & Ruangan</h1>', '<h1 class="header-title">Dashboard</h1>')
clean_header = clean_header.replace('<p class="header-subtitle">Kelola dan kelola seluruh master data linen rumah sakit serta lokasi ruangan</p>', '<p class="header-subtitle">Monitor aktivitas linen secara real-time</p>')

with open("d:\\ClientS\\app\\templates\\dashboard.html", "r", encoding="utf-8") as f:
    bad_dashboard = f.read()

main_match = re.search(r'(<div class="hero-text-box">.*)', bad_dashboard, re.DOTALL)
if not main_match:
    print("Could not extract main content from dashboard.html")
    exit(1)

main_content = main_match.group(1)

final_dashboard = clean_header + """
      <!-- PAGE CONTENT -->
      <main class="dashboard-content" style="padding: 12px 16px 12px 16px; display: flex; flex-direction: column; height: 100vh; max-height: 100vh; overflow: hidden;">
        
        <!-- Hero Banner -->
        <section class="hero-banner">
""" + main_content

with open("d:\\ClientS\\app\\templates\\dashboard.html", "w", encoding="utf-8") as f:
    f.write(final_dashboard)
print("Dashboard recovered successfully!")
