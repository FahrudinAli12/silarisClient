import glob

replacements = {
    'â€¢': '•',
    'â†‘': '↑',
    'â†“': '↓',
    'âœœ': '✚',
    'ï¿½': '•'
}

for file in glob.glob('d:/ClientS/app/templates/*.html'):
    try:
        with open(file, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        
        # Note: I removed '?' replacement so we don't break query strings like '?tab=masuk'
        
        for old, new in replacements.items():
            content = content.replace(old, new)
            
        with open(file, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'Processed {file}')
    except Exception as e:
        print(f'Error with {file}: {e}')
print('Done!')
