with open('config.py', 'r', encoding='utf-8') as f:
    config_content = f.read()

import re
matches = [m.start() for m in re.finditer(r'always_eager', config_content, re.IGNORECASE)]
for m in matches:
    print(config_content[max(0, m-50):min(len(config_content), m+150)])
    print("-" * 40)
if not matches:
    print("No matches for always_eager")
