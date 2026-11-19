"""Collect the firms that already have a verified website from run_firme/full/docs.jsonl, so the jobs run reads only those."""
import json
seen=set(); out=[]
for l in open("run_firme/full/docs.jsonl",encoding="utf-8"):
    if not l.strip(): continue
    d=json.loads(l)
    if d.get("website") and d["id"] not in seen: seen.add(d["id"]); out.append(d)
open("run_firme/full/docs_site.jsonl","w",encoding="utf-8").write("".join(json.dumps(d,ensure_ascii=False)+"\n" for d in out))
print("docs with site:",len(out))
