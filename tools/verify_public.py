#!/usr/bin/env python3
"""Offline verification of the PUBLIC sanitized Brakko evidence export.
Verifies disclosed files, sanitized hash chain and declared run facts, but NOT
OpenRouter server authenticity or omitted original private bytes.
"""
import hashlib, json, pathlib, sys
B=pathlib.Path(__file__).resolve().parents[1]
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    p=json.loads((B/'evidence'/'redacted_provenance.json').read_text(encoding='utf-8'))
    r=json.loads((B/'evidence'/'run_summary.json').read_text(encoding='utf-8'))
    errors=[]; prev=''
    for s in p['steps']:
        f=B/s['path']
        if not f.exists():errors.append(f'MISSING {s["path"]}');continue
        digest=h(f); curr=hashlib.sha256((prev+digest).encode()).hexdigest()
        if digest != s['sha256'] or curr != s['chain']:
            errors.append(f'MISMATCH {s["path"]}')
        d=json.loads(f.read_text('utf-8'))
        if d['n'] != s['n'] or d['kind'] != s['kind'] or d.get('lab') != s.get('lab'):
            errors.append(f'INVALID METADATA {s["path"]}')
        if d.get('model') != s.get('model'):
            errors.append(f'MODEL MISMATCH {s["path"]}')
        if (d.get('response') or {}).get('id') and (d.get('response') or {}).get('id') != s.get('generation_id'):
            errors.append(f'GENERATION ID MISMATCH {s["path"]}')
        prev=curr
    if prev != p['sanitized_chain_head']:errors.append('CHAIN HEAD MISMATCH')
    # Independently validate the unchanged historical provenance file's public commitments.
    orig=json.loads((B/'evidence'/'original_provenance.json').read_text(encoding='utf-8'))
    origprev=''
    if len(orig['steps']) != len(p['steps']):errors.append('ORIGINAL STEP COUNT MISMATCH')
    for i, st in enumerate(orig['steps']):
        original_claim=hashlib.sha256((origprev+st['sha256']).encode()).hexdigest()
        if st['chain'] != original_claim:errors.append(f'ORIGINAL CHAIN MATHEMATICS FAILED step {i+1}')
        if st['sha256'] != p['steps'][i]['original_sha256'] or st['chain'] != p['original_raw_sha256_commitments'][i]['chain']:
            errors.append(f'ORIGINAL COMMITMENT MISMATCH step {i+1}')
        candidate=B/'evidence'/'original_untouched'/pathlib.Path(st['file']).name
        if candidate.exists() and h(candidate)!=st['sha256']:
            errors.append(f'ORIGINAL UNMODIFIED RAW TRAIL HASH MISMATCH step {i+1}')
        origprev=original_claim
    if origprev!=orig['final_chain'] or origprev!=p['original_chain_head_claimed']:errors.append('ORIGINAL ROOT HASH MISMATCH')
    if r['result_name']!='Brakko' or r['mock'] is not False or len(r['labs'])!=7 or len(r['selected_memes'])!=9:errors.append('WRONG RUN SUMMARY')
    council=[s for s in p['steps'] if s['kind']=='council']
    if len(council)!=7 or len({s['lab'] for s in council})!=7:errors.append('SEVEN-LAB CHECK FAILED')
    director=json.loads((B/'evidence/trail/14_director_run.json').read_text(encoding='utf-8'))
    if (director.get('output') or {}).get('parsed',{}).get('final_name')!='Brakko':errors.append('DIRECTOR NAME NOT CONFIRMED')
    imageinfo=json.loads((B/'evidence/image_integrity.json').read_text(encoding='utf-8'))
    disp=B/imageinfo['display_file']
    if not disp.exists() or h(disp)!=imageinfo['display_sha256']:errors.append('DISPLAY IMAGE HASH MISMATCH')
    sums=B/'SHA256SUMS.txt'
    for line in sums.read_text('utf-8').splitlines():
        if not line.strip():continue
        digest, filename=line.split('  ',1)
        f=B/filename
        if not f.is_file() or h(f)!=digest:errors.append(f'FILE CHECKSUM MISMATCH {filename}')
    print(f"Brakko public evidence run {p['run_id']}: {len(p['steps'])} steps, {len(council)} models")
    if errors:
        for e in errors:print('FAIL:',e)
        return 1
    print('PASS: sanitized public files, ledger, hash chain, original provenance commitments,')
    print('      unmodified safe original steps, name and checksums are internally consistent.')
    print('LIMITATION: this does NOT independently prove that OpenRouter executed the calls.')
    print('LIMITATION: original private trail chain and exact final image bytes are NOT present.')
    return 0
if __name__=='__main__':sys.exit(main())
