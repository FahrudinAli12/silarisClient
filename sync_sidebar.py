import re
import glob

# Read the perfect sidebar from laundry.html
with open('d:/ClientS/app/templates/laundry.html', 'r', encoding='utf-8') as f:
    laundry_content = f.read()

# Extract the sidebar block exactly (from <aside ...> to </aside>)
sidebar_match = re.search(r'(<aside class="sidebar" id="sidebar">.*?</aside>)', laundry_content, re.DOTALL)
if not sidebar_match:
    print("Could not find sidebar in laundry.html")
    exit(1)

base_sidebar = sidebar_match.group(1)

# Clean up active classes from the base sidebar
clean_sidebar = base_sidebar.replace('nav-group open', 'nav-group')
clean_sidebar = clean_sidebar.replace('nav-item active', 'nav-item')
clean_sidebar = clean_sidebar.replace('sub-item active', 'sub-item')

files_to_update = [
    'dashboard.html',
    'settings.html',
    'master_data.html',
    'data_linen.html',
    'data_ruangan.html'
]

for filename in files_to_update:
    filepath = f'd:/ClientS/app/templates/{filename}'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Custom active states per file
    custom_sidebar = clean_sidebar
    
    if filename == 'dashboard.html':
        custom_sidebar = custom_sidebar.replace('<a href="/dashboard" class="nav-item">', '<a href="/dashboard" class="nav-item active">')
    elif filename == 'settings.html':
        custom_sidebar = custom_sidebar.replace('<a href="/settings" class="nav-item">', '<a href="/settings" class="nav-item active">')
        custom_sidebar = custom_sidebar.replace('<div class="nav-group">\n            <a href="/settings"', '<div class="nav-group open">\n            <a href="/settings"')
    elif filename in ['master_data.html', 'data_linen.html', 'data_ruangan.html']:
        # Master Data has no href in the nav-item itself, it's a div
        custom_sidebar = custom_sidebar.replace('<div class="nav-item">\n              <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">\n                <path d="M14 2H6', '<div class="nav-item active">\n              <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">\n                <path d="M14 2H6')
        custom_sidebar = custom_sidebar.replace('<div class="nav-group">\n            <div class="nav-item active">', '<div class="nav-group open">\n            <div class="nav-item active">')

    # Replace the existing sidebar with the custom one
    new_content = re.sub(r'<aside class="sidebar" id="sidebar">.*?</aside>', custom_sidebar, content, flags=re.DOTALL)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f'Updated {filename}')
