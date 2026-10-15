#!/usr/bin/env python3
"""Optional: private OpenRouter cross-check. Never share or print your key."""
import json,os,urllib.request,urllib.parse,pathlib,sys
B=pathlib.Path(__file__).resolve().parents[1]
KEY=os.environ.get('OPENROUTER_API_KEY','').strip()
if not KEY:
    print('Set OPENROUTER_API_KEY locally; do not put it in the repo.')
    sys.exit(2)
p=json.loads((B/'evidence'/'redacted_provenance.json').read_text())
ids=[s for s in p['steps'] if s.get('generation_id')]
failed=0
for s in ids:
    url='https://openrouter.ai/api/v1/generation?id='+urllib.parse.quote(s['generation_id'])
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+KEY})
    try:
        with urllib.request.urlopen(req,timeout=20) as r:
            data=json.load(r).get('data',{})
        actual=data.get('model') or data.get('model_id')
        if actual is None:
            print('UNCLEAR',s['kind'],s['lab'] or '-',': record returned but model is unavailable')
        else:
            match = actual==s['model']
            print('MATCH' if match else 'CHECK MODEL ALIAS',s['kind'],s['lab'] or '-', 'logged:',s['model'],'remote:',actual)
            if not match:failed+=1
    except Exception as e:
        failed+=1
        print('NOT VERIFIED',s['kind'],s['lab'] or '-',type(e).__name__)
print('Checked',len(ids),'generation IDs. Provider records might be accessible only to the original key owner.')
sys.exit(1 if failed else 0)
