with open('app/static/css/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

import re
matches = [m.start() for m in re.finditer(r'slideDown', css)]
for m in matches:
    print(css[max(0, m-50):min(len(css), m+200)])
    print("-" * 40)
