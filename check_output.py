"""
check_output.py - inspect what was actually saved for a match
"""
import json, pathlib

SLUG = "nep-vs-oma-100th-match-mens-cwc-league-2-2023-27-match-updates-11HD"
base = pathlib.Path(f"output/{SLUG}")

files = {
    "match_info":  base / "match_info.json",
    "squads":      base / "squads.json",
    "scorecard":   sorted((base / "scorecard").glob("*.json"))[-1] if (base / "scorecard").exists() else None,
    "live":        sorted((base / "live").glob("*.json"))[-1] if (base / "live").exists() else None,
}

for name, path in files.items():
    if path is None or not path.exists():
        print(f"\n=== {name.upper()} — FILE NOT FOUND ===")
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"\n=== {name.upper()} ({path.name}) ===")
    # Print top-level keys and a preview
    if isinstance(data, dict):
        print(f"  Keys: {list(data.keys())}")
        for k, v in list(data.items())[:5]:
            preview = str(v)[:120]
            print(f"  {k}: {preview}")
    elif isinstance(data, list):
        print(f"  List of {len(data)} items")
        if data:
            print(f"  First item keys: {list(data[0].keys()) if isinstance(data[0], dict) else data[0]}")
    print()