import json, pathlib

sv3 = json.loads(pathlib.Path("debug_getSV3.json").read_text(encoding="utf-8"))

# Print ALL keys with their values to find player map
print("=== FULL getSV3 ===")
for k, v in sv3.items():
    val_str = json.dumps(v)
    print(f"  {k!r}: {val_str[:200]}")