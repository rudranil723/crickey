import re
with open('debug_scorecard.html', encoding='utf-8') as f:
    html = f.read()

print('Any class with inning:')
print(set(re.findall(r'class="[^"]*inning[^"]*"', html)))

print('\nAny class with match-info:')
print(set(re.findall(r'class="[^"]*match-info[^"]*"', html)))

print('\nAny class wrapping scorecard-table:')
for m in re.findall(r'<div class="([^"]*)">\s*<div class="scorecard-table', html):
    print(m)
