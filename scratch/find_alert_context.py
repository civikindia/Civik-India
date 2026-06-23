with open('app/static/css/style.css', 'r', encoding='utf-8') as f:
    css = f.readlines()

for i, line in enumerate(css):
    if '.alert {' in line:
        start = max(0, i-25)
        end = min(len(css), i+25)
        print(f"Match on line {i+1}:")
        print("".join(css[start:end]))
        print("-" * 60)
