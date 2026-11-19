"""Join the ANAF screening result with the candidate list: keep the firms that pass every gate, with their employee counts
(output firme_active_ge3.jsonl; the file name is legacy, the threshold is now MIN_EMPLOYEES)."""
import json
emp={}; 
for l in open("run_firme/firme_candidate.jsonl",encoding="utf-8"):
    r=json.loads(l); emp[r["cui"].zfill(8)]=r["employees"]
n=0
with open("run_firme/firme_active_ge3.jsonl","w",encoding="utf-8") as f:
    for l in open("run_firme/screen/screened.jsonl",encoding="utf-8"):
        r=json.loads(l)
        if r.get("ok") and r["cif"] in emp:
            f.write(json.dumps({"cif":r["cif"],"company":r["anaf"]["name"],"employees":emp[r["cif"]]},ensure_ascii=False)+"\n"); n+=1
print("active candidate firms:",n)
