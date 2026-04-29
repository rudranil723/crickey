import re

def find_classes(html_file, keywords):
    with open(html_file, encoding='utf-8') as f:
        html = f.read()
    all_classes = re.findall(r'class=[\"\']([\w\s\-_]+)[\"\']', html)
    flat = set()
    for c in all_classes:
        for part in c.split():
            flat.add(part)
    print(f'\n=== {html_file} ===')
    for kw in keywords:
        matches = [c for c in flat if kw.lower() in c.lower()]
        print(f'  [{kw}]: {matches[:8]}')

find_classes('debug_live.html', [
    'live', 'score', 'commentary', 'ball', 'over', 'status',
    'batting', 'bowler', 'partnership', 'crr', 'overs'
])
