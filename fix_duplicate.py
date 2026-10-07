with open("d:\\ClientS\\app\\templates\\dashboard.html", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = lines[:239] + lines[550:]

with open("d:\\ClientS\\app\\templates\\dashboard.html", "w", encoding="utf-8") as f:
    f.writelines(new_lines)
