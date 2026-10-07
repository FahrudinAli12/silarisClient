import glob

for filename in glob.glob('d:/ClientS/app/templates/*.html'):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
        
    content = content.replace('class="sub-item">• Sync Data</a>', 'class="sub-item" id="sub-nav-sync">• Sync Data</a>')
    content = content.replace('class="sub-item">• Log Penerimaan Linen</a>', 'class="sub-item" id="sub-nav-log">• Log Penerimaan Linen</a>')
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)
print("Fixed IDs")
