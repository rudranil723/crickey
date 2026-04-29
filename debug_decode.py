import json, pathlib, base64, urllib.parse

sv3 = json.loads(pathlib.Path("debug_getSV3.json").read_text(encoding="utf-8"))
sc4 = json.loads(pathlib.Path("debug_getSC4.json").read_text(encoding="utf-8"))
bf  = json.loads(pathlib.Path("debug_getBallFeeds.json").read_text(encoding="utf-8"))

print("=== getSV3 suspicious fields ===")
for k in ['o', 'sf', 'wp', 'tp', 'r', 'A', 'F']:
    v = sv3.get(k)
    print(f"  {k!r} = {v!r}")

    # try base64
    if isinstance(v, str) and len(v) > 4:
        try:
            decoded = base64.b64decode(v + "==").decode("utf-8", errors="replace")
            print(f"       base64→ {decoded[:100]}")
        except:
            pass
        # try url decode
        ud = urllib.parse.unquote(str(v))
        if ud != v:
            print(f"       urldecode→ {ud[:100]}")

print("\n=== getSC4 innings[0] full bowling entry (b[0]) ===")
# bowling entries have format: "playerKey.runs.balls.4s.6s..."
for inn in sc4:
    print(f"\n  Innings st={inn.get('st')} d={inn.get('d')}")
    print(f"  batting (a): {inn.get('a', [])[:4]}")
    print(f"  bowling (b): {inn.get('b', [])[:4]}")
    print(f"  partnerships (p): {inn.get('p', [])[:3]}")
    print(f"  extras (e): {inn.get('e')}")
    print(f"  x (batters): {inn.get('x')}")

print("\n=== getBallFeeds — over summary entries (type='o') ===")
for item in bf:
    if item.get("type") == "o":
        print(f"  over={item.get('o')} team={item.get('team')} bowler={item.get('bowler')} p1={item.get('p1')} p2={item.get('p2')} runs={item.get('runs')} rb={item.get('rb')} s={item.get('s')}")

print("\n=== getBallFeeds — ball entries (type='b') sample ===")
count = 0
for item in bf:
    if item.get("type") == "b":
        print(f"  o={item.get('o')} b={item.get('b')} bf={item.get('bf')} pf={item.get('pf')} s={item.get('s')} c1={item.get('c1')!r}")
        count += 1
        if count >= 5:
            break