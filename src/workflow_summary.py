"""Write operational health to Actions without treating partial feeds as an outage."""
import json
import os
from pathlib import Path

root=Path(__file__).resolve().parents[1]
diag=json.loads((root/'data/run_diagnostics.json').read_text())
lines=['## Incident Watch discovery', '', f"Health: **{diag.get('health','unknown')}**", '',
       '| Provider | Successful queries | Failed queries | Articles |', '| --- | ---: | ---: | ---: |']
for name,p in diag.get('providers',{}).items():
    lines.append(f"| {name} | {p.get('queries_succeeded',0)} | {p.get('queries_failed',0)} | {p.get('articles_returned',0)} |")
if diag.get('health')!='healthy':
    print('::warning::Discovery incomplete or unavailable. Inspect dashboard Source health; preserved records remain available.')
    lines += ['', 'Existing reports remain available. Data collection timestamps are not refreshed during a total outage.']
text='\n'.join(lines)+'\n'
if os.environ.get('GITHUB_STEP_SUMMARY'):
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(text)
print(text)
