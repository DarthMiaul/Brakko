"""
verify.py - re-check a MergeMeme run without trusting the UI.

    python verify.py output/<run-id>

What it checks
  1. every trail/NN_*.json file still hashes to the sha256 recorded in provenance.json
  2. the hash chain (each step's chain = sha256(prev_chain + file_sha256)) is intact
  3. every image file (sketches, final.png, final_card.png) hashes to the recorded sha256
  4. the image sha256 recorded inside the image step's trail file matches the file on disk
  5. lists the OpenRouter generation ids so anyone with the key can pull the server-side record
     (GET https://openrouter.ai/api/v1/generation?id=<id>) and compare model, tokens and cost

Exit code 0 = everything matches, 1 = mismatch.
"""
import hashlib
import json
import os
import sys


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(run_dir):
    prov_path = os.path.join(run_dir, "provenance.json")
    if not os.path.exists(prov_path):
        print("no provenance.json in", run_dir)
        return 1
    prov = json.load(open(prov_path, encoding="utf-8"))
    ok = True
    print(f"run {prov.get('run_id')}  created {prov.get('created')}  mock={prov.get('mock')}")
    print(f"characters: {', '.join(prov.get('characters', []))}")
    print(f"labs: {', '.join(prov.get('labs', []))}")
    print()
    prev = ""
    for st in prov.get("steps", []):
        fp = os.path.join(run_dir, st["file"])
        if not os.path.exists(fp):
            print(f"MISSING {st['file']}")
            ok = False
            continue
        h = sha256_file(fp)
        chain = hashlib.sha256((prev + h).encode()).hexdigest()
        good = h == st["sha256"] and chain == st["chain"]
        ok &= good
        gen = st.get("generation_id") or "-"
        print(f"{'OK ' if good else 'BAD'} {st['n']:02d} {st['kind']:<9} {st.get('lab') or '':<10} {st.get('model') or '':<40} gen={gen}")
        if not good:
            print(f"      file sha256 {h} expected {st['sha256']}")
            print(f"      chain       {chain} expected {st['chain']}")
        d = json.load(open(fp, encoding="utf-8"))
        img = (d.get("output") or {}).get("image")
        if img:
            ip = os.path.join(run_dir, img["file"])
            if os.path.exists(ip):
                ih = sha256_file(ip)
                good_img = ih == img["sha256"]
                ok &= good_img
                print(f"      image {img['file']} {'OK' if good_img else 'BAD'} {ih[:16]}")
            else:
                print(f"      image {img['file']} MISSING")
                ok = False
        prev = chain
    print()
    for name, rec in (prov.get("files") or {}).items():
        p = os.path.join(run_dir, name)
        if not os.path.exists(p):
            print(f"MISSING file {name}")
            ok = False
            continue
        h = sha256_file(p)
        good = h == rec["sha256"]
        ok &= good
        print(f"{'OK ' if good else 'BAD'} {name} {h[:16]}")
    print()
    print("final chain:", prov.get("final_chain"))
    print("RESULT:", "VERIFIED" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
