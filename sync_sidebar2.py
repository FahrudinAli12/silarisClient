import re

files_to_update = [
    'dashboard.html',
    'settings.html',
    'master_data.html',
    'data_linen.html',
    'data_ruangan.html',
    'laundry.html'
]

# Get base sidebar from laundry.html
with open('d:/ClientS/app/templates/laundry.html', 'r', encoding='utf-8') as f:
    laundry_content = f.read()

sidebar_match = re.search(r'(<aside class="sidebar" id="sidebar">.*?</aside>)', laundry_content, re.DOTALL)
base_sidebar = sidebar_match.group(1)

# Clean active and open
clean_sidebar = base_sidebar.replace('nav-group open', 'nav-group')
clean_sidebar = clean_sidebar.replace('nav-item active', 'nav-item')
clean_sidebar = clean_sidebar.replace('sub-item active', 'sub-item')

# Add bullets to Data Linen and Data Ruangan if not present
clean_sidebar = re.sub(r'(<a href="/master-data/linen"[^>]*>)(?:[•?]+\s*)?Data Linen</a>', r'\1• Data Linen</a>', clean_sidebar)
clean_sidebar = re.sub(r'(<a href="/master-data/ruangan"[^>]*>)(?:[•?]+\s*)?Data Ruangan</a>', r'\1• Data Ruangan</a>', clean_sidebar)

for filename in files_to_update:
    filepath = f'd:/ClientS/app/templates/{filename}'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    custom_sidebar = clean_sidebar
    
    # Dashboard
    if filename == 'dashboard.html':
        custom_sidebar = re.sub(r'(<a href="/dashboard"\s*class="nav-item)(">)', r'\1 active\2', custom_sidebar)
        
    # Master Data
    elif filename in ['master_data.html', 'data_linen.html', 'data_ruangan.html']:
        # Find the nav-group that contains Master Data span
        custom_sidebar = re.sub(r'(<div class="nav-group)(">\s*<div class="nav-item">.*?<span>Master Data</span>)', r'\1 open\2', custom_sidebar, flags=re.DOTALL)
        custom_sidebar = re.sub(r'(<div class="nav-item)(">\s*<svg.*?<span>Master Data</span>)', r'\1 active\2', custom_sidebar, flags=re.DOTALL)
        
        # also highlight the specific sub-item
        if filename == 'data_linen.html':
            custom_sidebar = re.sub(r'(<a href="/master-data/linen"[^>]*class="sub-item)(">)', r'\1 active\2', custom_sidebar)
        elif filename == 'data_ruangan.html':
            custom_sidebar = re.sub(r'(<a href="/master-data/ruangan"[^>]*class="sub-item)(">)', r'\1 active\2', custom_sidebar)

    # Laundry
    elif filename == 'laundry.html':
        custom_sidebar = re.sub(r'(<div class="nav-group)(">\s*<a href="/laundry".*?<span>Laundry</span>)', r'\1 open\2', custom_sidebar, flags=re.DOTALL)
        custom_sidebar = re.sub(r'(<a href="/laundry"\s*class="nav-item)(">)', r'\1 active\2', custom_sidebar, flags=re.DOTALL)
        
    # Settings
    elif filename == 'settings.html':
        custom_sidebar = re.sub(r'(<div class="nav-group)(">\s*<a href="/settings".*?<span>Settings</span>)', r'\1 open\2', custom_sidebar, flags=re.DOTALL)
        custom_sidebar = re.sub(r'(<a href="/settings"\s*class="nav-item)(">)', r'\1 active\2', custom_sidebar, flags=re.DOTALL)

    # Replace the existing sidebar
    new_content = re.sub(r'<aside class="sidebar" id="sidebar">.*?</aside>', custom_sidebar, content, flags=re.DOTALL)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f'Updated {filename}')
print("Done!")
