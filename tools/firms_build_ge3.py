import csv, json
screened=set()
for l in open("run_firme/screen/screened.jsonl",encoding="utf-8"):
    screened.add(json.loads(l)["cif"])
rows={}
for fn in ("run_firme/bilant2024.txt","run_firme/uu2024.txt"):
    with open(fn,encoding="utf-8",errors="replace",newline="") as f:
        for r in csv.DictReader(f):
            try: emp=int(float(r["I20"] or 0)); turn=float(r["I13"] or 0)
            except ValueError: continue
            cui=r["CUI"].strip()
            if emp>=3 and turn>0 and r["CAEN"] not in ("6420","6820") and cui:
                rows[cui.zfill(8)]={"cui":cui,"caen":r["CAEN"],"employees":emp,"turnover":r["I13"]}
print("ge3 firms:",len(rows))
new=[c for c in rows if c not in screened]
print("already screened:",len(rows)-len(new),"new to screen:",len(new))
with open("run_firme/firme_ge3.jsonl","w",encoding="utf-8") as f:
    for r in rows.values(): f.write(json.dumps(r)+"\n")
with open("run_firme/new_cifs.txt","w") as f: f.write("\n".join(new))
