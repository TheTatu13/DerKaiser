"""Screen the CIFs in new_cifs.txt through ANAF (100 per request). Resumable: stop and restart at any time."""
import sys; sys.path.insert(0, ".")
import json
from scraper.anaf_batch import screen_cifs
cifs=[c for c in open("run_firme/new_cifs.txt").read().split() if c]
print("screening", len(cifs), flush=True)
print(json.dumps(screen_cifs(cifs, "run_firme/screen"), ensure_ascii=False), flush=True)
