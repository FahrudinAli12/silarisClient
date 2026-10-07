import re

with open('d:/ClientS/app/templates/data_linen.html', 'rb') as f:
    content = f.read().decode('utf-8')

replacement_css = """        .linen-tab.active {
          background: linear-gradient(180deg, rgba(0, 194, 203, 0.9) 0%, rgba(0, 194, 203, 0.1) 100%);
          background-color: #0c2b42;
          border-color: rgba(0,200,255,0.4);
          color: #fff;
          box-shadow: none;
        }

        .linen-card {
          display: flex;
          flex-direction: column;
          background-color: var(--bg-card);
          border: 1px solid var(--border-color);
          border-top: 1px solid rgba(0,200,255,0.5);
          border-top-left-radius: 0;
          border-top-right-radius: 12px;
          border-bottom-left-radius: 12px;"""

# Replace the broken CSS
# Currently it looks like:
#         .linen-tab.active {
#           background: linear-gradient(180deg, rgba(0, 194, 203, 0.9) 0%, rgba(0, 194, 203, 0.1) 100%);
#           background-color: #0c2b42;
#           border-bottom-left-radius: 12px;
pattern = r'\.linen-tab\.active\s*\{[^}]*background-color:\s*#0c2b42;\s*border-bottom-left-radius:\s*12px;'

content = re.sub(pattern, replacement_css, content, flags=re.DOTALL)

with open('d:/ClientS/app/templates/data_linen.html', 'wb') as f:
    f.write(content.encode('utf-8'))
