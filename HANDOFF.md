# DerKaiser â€” predare pentru testare

Scraper local, multi-firmÄƒ, derivat din È™ablonul Brewtality 3:16. **Dry-run: nu scrie nimic Ã®n peViitor/Solr.**
PorneÈ™te de la firmÄƒ (CIF din ANOFM), Ã®i gÄƒseÈ™te site-ul È™i anunÈ›urile (fiecare cu linkul ei) È™i completeazÄƒ cu portaluri.

## Ce face (de la mic la mare, â€žpÄƒpuÈ™a ruseascÄƒ")
| Strat | FiÈ™ier | Ce citeÈ™te |
|---|---|---|
| ANOFM | `model.py` | API-ul public mediere.anofm.ro (CIF fÄƒrÄƒ zerouri la Ã®nceput) â€” anunÈ›uri + email de contact â†’ domeniul firmei |
| ReÈ›etÄƒ Ã®nvÄƒÈ›atÄƒ | `engine.py`, `learn.py` | `recipes/<cif>.json`: pagini de cariere, regex de link de anunÈ›, paginare; se reÃ®nvaÈ›Äƒ dacÄƒ site-ul se schimbÄƒ |
| ATS | `jobs.py`, `workday.py` | Greenhouse, Lever, Workable, Recruitee, SmartRecruiters, Personio, **Workday** |
| JSON-LD / listare | `jobs.py` | `JobPosting`; pagini de listare confirmate pe pagina de anunÈ› |
| Randare JS | `render.py` | Chrome/Edge deja instalat (`--headless`), opÈ›ional |
| BestJobs pe firmÄƒ | `portals.py` | firma â†’ pagina ei de profil â†’ anunÈ›urile active (potrivire exactÄƒ pe nume) |
| eJobs | `ejobs.py` | sitemap-urile publicate de ei pentru boÈ›i + `JobPosting` JSON-LD |
| Common Crawl | `cc.py` | index citit direct de pe data.commoncrawl.org (API-ul lor dÄƒ 504) |
| Deduplicare | `merge.py` | pe link normalizat È™i pe (firmÄƒ, titlu, oraÈ™) |

Plase de siguranÈ›Äƒ: reÃ®ncercÄƒri cu backoff, pauzÄƒ pe host (implicit 3 s), circuit breaker, robots.txt, buget de cereri/timp pe firmÄƒ,
try/except pe strat, filtru de â€žsanity" (fÄƒrÄƒ 90% titluri identice), listÄƒ de gazde permise, validarea Brewtality, verificare live a linkului,
fiÈ™iere de ieÈ™ire append-only (reluabile), cache pe disc.

## Cum se ruleazÄƒ (toate citesc doar; nimic nu se trimite)
```
py -m pytest tests -q                                   # 519 teste, offline
py -m scraper.derkaiser --jobs docs.jsonl --out out --workers 8 --interval 3 --learn --no-render --recipes recipes
py -m scraper.portals   --docs docs.jsonl --out portal_jobs.jsonl --interval 3     # BestJobs pe firme
py -m scraper.ejobs     --out ejobs_jobs.jsonl --interval 3                        # reia cu --skip-urls <fisier>.done
py -m scraper.cc        --docs out/docs_with_jobs.jsonl --out cc_jobs.jsonl --crawls 4
py -m scraper.merge --src anofm=out/jobs.jsonl --src bestjobs=portal_jobs.jsonl --src ejobs=ejobs_jobs.jsonl --out all_distinct.jsonl
```
`docs.jsonl`: cÃ¢te un rÃ¢nd `{"id": "<CIF 8 cifre>", "company": "...", "website": []}`.
`publish.py` are `send_jobs` / `send_company` cu gard de dry-run (`confirm=False`) â€” **netestate pe API-ul real**.

## Ce e verificat È™i ce nu
**Verificat (offline):** 519 teste trec (8 sar).
**Verificat live, pe eÈ™antioane mici:** forma rÄƒspunsului Workday (un tenant real), ANOFM, BestJobs (profil + anunÈ›, 15 firme), eJobs (14 anunÈ›uri, 11 din RomÃ¢nia),
accesul la indexul Common Crawl, 2 reÈ›ete Ã®nvÄƒÈ›ate corect (Valrom, Carrefour).
**Neverificat / limite:**
- Nimic nu a fost trimis la peViitor; `send_jobs`/`send_company` nu au fost rulate pe API-ul real. Cere mediu de test sau acord.
- Workday doar pe un tenant (NVIDIA); alte ATS-uri nu au fost verificate live de mine.
- Common Crawl: 0 anunÈ›uri pe 12 firme testate (puÈ›ine firme au `JobPosting`); sweep-ul e lent.
- Pe site-urile firmelor mici randamentul e mic (9 firme cu rezultat propriu din 630).
- OraÈ™ul e dedus din text: poate greÈ™i la localitÄƒÈ›i mici (ex.: â€žVoinesti" â†’ â€žTargoviste"). VerificaÈ›i Ã®nainte de publicare.
- ANOFM repetÄƒ uneori acelaÈ™i post Ã®n mai multe anunÈ›uri; `merge.py` le unificÄƒ (1.711 citite â†’ 1.377 distincte).
- AnunÈ›urile de pe portal poartÄƒ numele agenÈ›iei care posteazÄƒ (ex. ProHuman), nu al clientului.
- Rularea completÄƒ pe 3.692 de angajatori ANOFM nu a fost dusÄƒ la capÄƒt (am oprit-o la ~1.050 firme). Ritm: ~8 firme/min, deci ~8 ore.
- Numere parÈ›iale (dedup): ~2.100 distincte la ~650 firme + 1.000 anunÈ›uri eJobs + BestJobs. Estimare totalÄƒ: aprox. 15.000â€“20.000 distincte, **nu 70.000**.

## Decizii pentru voi (nu pot fi luate Ã®n cod)
1. **ToS eJobs / BestJobs / Undelucram / Hipo:** (vezi `docs/TOS.md`: BestJobs interzice scraping-ul la pct. 3 din termeni; eJobs neverificat) `robots.txt` permite citirea anunÈ›urilor (BestJobs, eJobs verificate), dar termenii pot interzice preluarea automatÄƒ.
   Decizia aparÈ›ine asociaÈ›iei. Modulele `ejobs.py` È™i `portals.py` citesc doar anunÈ›uri publice, 3 s Ã®ntre cereri, pÄƒstreazÄƒ doar titlu/firmÄƒ/oraÈ™/link.
2. **De unde vine È›inta de 70.000?** ANOFM raporteazÄƒ ~30â€“35.000 de *locuri vacante* (posturi, nu anunÈ›uri); noi am mÄƒsurat 8.141 anunÈ›uri = 33.563 posturi.
3. **Scriere Ã®n peViitor:** doar dupÄƒ mediu de test È™i aprobare; `confirm=True` e singurul comutator.
4. **OLX:** blocheazÄƒ boÈ›ii (403); nu a fost folosit. Agregatorii (Jooble, Jobrapido, Talent) nu au fost folosiÈ›i (duplicate, termeni stricÈ›i).

## FiÈ™iere de date (nu sunt Ã®n pachet)
`run_all/` (pe PC-ul lui Mihai): `out/jobs_by_firm.jsonl`, `out/jobs.jsonl`, `portal_jobs.jsonl`, `ejobs_jobs.jsonl`, `all_distinct.jsonl`.

