import json, pathlib

for name in ['getSV3', 'getSC4', 'getBallFeeds']:
    p = pathlib.Path(f'debug_{name}.json')
    if not p.exists():
        print(f'\n===== {name} — FILE NOT FOUND =====')
        continue
    data = json.loads(p.read_text(encoding='utf-8'))
    print(f'\n===== {name} =====')
    raw = json.dumps(data, indent=2)
    print(raw[:3000])
    if len(raw) > 3000:
        print('...(truncated)')