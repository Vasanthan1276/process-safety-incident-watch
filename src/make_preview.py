"""Create a portable preview from stored data. No network requests or deployment."""
import argparse
import base64
import gzip
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def make_preview(output):
    public=ROOT/'public'
    payload={f'./data/{name}':base64.b64encode(gzip.compress((public/'data'/name).read_bytes())).decode()
             for name in ['review_index.json','run_diagnostics.json','latest_report.json','incidents.json']}
    html=(public/'index.html').read_text()
    html=html.replace('<link rel="stylesheet" href="./styles.css">','<style>'+(public/'styles.css').read_text()+'</style>')
    html=html.replace('<script src="./app.js" defer></script>','')
    html=html.replace('href="./classic.html"','href="https://vasanthan1276.github.io/process-safety-incident-watch/"')
    html=html.replace('YOUR INCIDENT REVIEW DESK','LOCAL PREVIEW · NOT PUBLISHED')
    app=(public/'app.js').read_text()
    needle="async function loadJson(path) {"
    app=app.replace(needle,'''async function loadJson(path) {
 const encoded = previewResources[path];
 if(encoded){ const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0)); return JSON.parse(await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text()); }
''')
    # Preview downloads use embedded bytes; do not leave broken relative links.
    download_script='''
 document.querySelectorAll('.downloads a[download]').forEach(a=>a.addEventListener('click',async e=>{
 e.preventDefault(); const path=a.getAttribute('href'), data=await loadJson(path), u=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
 const x=document.createElement('a');x.href=u;x.download=path.split('/').pop();x.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
 }));
'''
    html=html.replace('</body>', '<script>const previewResources='+json.dumps(payload)+';\n'+app+download_script+'</script></body>')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(html)
    print(f'{output}: {output.stat().st_size} bytes')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output');args=parser.parse_args();make_preview(args.output)
