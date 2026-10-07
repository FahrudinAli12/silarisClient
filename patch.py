with open("d:\\ClientS\\app\\templates\\dashboard.html", "r", encoding="utf-8") as f:
    content = f.read()

with open("d:\\ClientS\\recover.txt", "r", encoding="utf-8") as f:
    recover = f.read()

target = """  <style>
    /* Exact Typography & Layout overrides */
    body, .app-layout { background-color: #020b14 !important; color: #e6f0fa; }
          <div class="hero-text-box">"""

replacement = """  <style>
    /* Exact Typography & Layout overrides */
    body, .app-layout { background-color: #020b14 !important; color: #e6f0fa; }
""" + recover + """          <div class="hero-text-box">"""

new_content = content.replace(target, replacement)

with open("d:\\ClientS\\app\\templates\\dashboard.html", "w", encoding="utf-8") as f:
    f.write(new_content)
