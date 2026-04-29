import json, pathlib
data = json.loads(pathlib.Path('output/schedule.json').read_text(encoding='utf-8'))
for m in data[:5]:
    print(m.get('team_a'), "vs", m.get('team_b'))
    print("  status:", m.get('status'), "| start:", m.get('start_time'), "| series:", m.get('series_name'))
    print()