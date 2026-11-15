# DerKaiser — predare pentru testare

Scraper local, multi-firmă, derivat din șablonul Brewtality 3:16. **Dry-run: nu scrie nimic în peViitor/Solr.**
Pornește de la firmă (CIF din ANOFM), îi găsește site-ul și anunțurile (fiecare cu linkul ei) și completează cu portaluri.

## Ce face (de la mic la mare, „păpușa rusească")
| Strat | Fișier | Ce citește |
|---|---|---|
| ANOFM | `model.py` | API-ul public mediere.anofm.ro (CIF fără zerouri la început) — anunțuri + email de contact → domeniul firmei |
| Rețetă învățată | `engine.py`, `learn.py` | `recipes/<cif>.json`: pagini de cariere, regex de link de anunț, paginare; se reînvață dacă site-ul se schimbă |
| ATS | `jobs.py`, `workday.py` | Greenhouse, Lever, Workable, Recruitee, SmartRecruiters, Personio, **Workday** |
| JSON-LD / listare | `jobs.py` | `JobPosting`; pagini de listare confirmate pe pagina de anunț |
| Randare JS | `render.py` | Chrome/Edge deja instalat (`--headless`), opțional |
| BestJobs pe firmă | `portals.py` | firma → pagina ei de profil → anunțurile active (potrivire exactă pe nume) |
| eJobs | `ejobs.py` | sitemap-urile publicate de ei pentru boți + `JobPosting` JSON-LD |
| Common Crawl | `cc.py` | index citit direct de pe data.commoncrawl.org (API-ul lor dă 504) |
| Deduplicare | `merge.py` | pe link normalizat și pe (firmă, titlu, oraș) |

Plase de siguranță: reîncercări cu backoff, pauză pe host (implicit 3 s), circuit breaker, robots.txt, buget de cereri/timp pe firmă,
try/except pe strat, filtru de „sanity" (fără 90% titluri identice), listă de gazde permise, validarea Brewtality, verificare live a linkului,
fișiere de ieșire append-only (reluabile), cache pe disc.

## Cum se rulează (toate citesc doar; nimic nu se trimite)
```
py -m pytest tests -q                                   # 519 teste, offline
py -m scraper.derkaiser --jobs docs.jsonl --out out --workers 8 --interval 3 --learn --no-render --recipes recipes
py -m scraper.portals   --docs docs.jsonl --out portal_jobs.jsonl --interval 3     # BestJobs pe firme
py -m scraper.ejobs     --out ejobs_jobs.jsonl --interval 3                        # reia cu --skip-urls <fisier>.done
py -m scraper.cc        --docs out/docs_with_jobs.jsonl --out cc_jobs.jsonl --crawls 4
py -m scraper.merge --src anofm=out/jobs.jsonl --src bestjobs=portal_jobs.jsonl --src ejobs=ejobs_jobs.jsonl --out all_distinct.jsonl
```
`docs.jsonl`: câte un rând `{"id": "<CIF 8 cifre>", "company": "...", "website": []}`.
`publish.py` are `send_jobs` / `send_company` cu gard de dry-run (`confirm=False`) — **netestate pe API-ul real**.

## Ce e verificat și ce nu
**Verificat (offline):** 519 teste trec (8 sar).
**Verificat live, pe eșantioane mici:** forma răspunsului Workday (un tenant real), ANOFM, BestJobs (profil + anunț, 15 firme), eJobs (14 anunțuri, 11 din România),
accesul la indexul Common Crawl, 2 rețete învățate corect (Valrom, Carrefour).
**Neverificat / limite:**
- Nimic nu a fost trimis la peViitor; `send_jobs`/`send_company` nu au fost rulate pe API-ul real. Cere mediu de test sau acord.
- Workday doar pe un tenant (NVIDIA); alte ATS-uri nu au fost verificate live de mine.
- Common Crawl: 0 anunțuri pe 12 firme testate (puține firme au `JobPosting`); sweep-ul e lent.
- Pe site-urile firmelor mici randamentul e mic (9 firme cu rezultat propriu din 630).
- Orașul e dedus din text: poate greși la localități mici (ex.: „Voinesti" → „Targoviste"). Verificați înainte de publicare.
- ANOFM repetă uneori același post în mai multe anunțuri; `merge.py` le unifică (1.711 citite → 1.377 distincte).
- Anunțurile de pe portal poartă numele agenției care postează (ex. ProHuman), nu al clientului.
- Rularea completă pe 3.692 de angajatori ANOFM nu a fost dusă la capăt (am oprit-o la ~1.050 firme). Ritm: ~8 firme/min, deci ~8 ore.
- Numere parțiale (dedup): ~2.100 distincte la ~650 firme + 1.000 anunțuri eJobs + BestJobs. Estimare totală: aprox. 15.000–20.000 distincte, **nu 70.000**.

## Decizii pentru voi (nu pot fi luate în cod)
1. **ToS eJobs / BestJobs / Undelucram / Hipo:** `robots.txt` permite citirea anunțurilor (BestJobs, eJobs verificate), dar termenii pot interzice preluarea automată.
   Decizia aparține asociației. Modulele `ejobs.py` și `portals.py` citesc doar anunțuri publice, 3 s între cereri, păstrează doar titlu/firmă/oraș/link.
2. **De unde vine ținta de 70.000?** ANOFM raportează ~30–35.000 de *locuri vacante* (posturi, nu anunțuri); noi am măsurat 8.141 anunțuri = 33.563 posturi.
3. **Scriere în peViitor:** doar după mediu de test și aprobare; `confirm=True` e singurul comutator.
4. **OLX:** blochează boții (403); nu a fost folosit. Agregatorii (Jooble, Jobrapido, Talent) nu au fost folosiți (duplicate, termeni stricți).

## Fișiere de date (nu sunt în pachet)
`run_all/` (pe PC-ul lui Mihai): `out/jobs_by_firm.jsonl`, `out/jobs.jsonl`, `portal_jobs.jsonl`, `ejobs_jobs.jsonl`, `all_distinct.jsonl`.
