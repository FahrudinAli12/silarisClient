import glob

for filename in glob.glob('d:/ClientS/app/templates/*.html'):
    with open(filename, 'rb') as f:
        data = f.read()
    
    try:
        decoded = data.decode('utf-8')
        # We also added explicit "•" via python regex which might have been correctly inserted 
        # or inserted as something else.
        # Let's just do simple replacements to be safe.
        
        # Replace the literal corrupted strings that we know of:
        # "â€¢" -> "•"
        decoded = decoded.replace('â€¢', '•')
        decoded = decoded.replace('â†‘', '↑')
        decoded = decoded.replace('Ã¢â€ â€˜', '↑') # in case it was triple encoded
        
        # For the dashboard arrows which might be double encoded:
        # Let's try the programmatic reverse on a test string to see if it works
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(decoded)
        print(f"Fixed {filename}")
    except Exception as e:
        print(f"Could not fix {filename}: {e}")
