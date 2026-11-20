# DerKaiser — predare pentru testare

Scraper local, multi-firmă, derivat din șablonul Brewtality 3:16. **Dry-run: nu scrie nimic în inviitor.ro (Solr/baza lor).**
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
| posturi.gov.ro | `posturi.py` | portalul statului (ANFP): posturi din sectorul public; `JobPosting` JSON-LD; robots.txt permite tot; fără termeni de reutilizare publicați |
| Deduplicare | `merge.py` | pe link normalizat și pe (firmă, titlu, oraș) |

Plase de siguranță: reîncercări cu backoff, pauză pe host (implicit 3 s), circuit breaker, robots.txt, buget de cereri/timp pe firmă,
try/except pe strat, filtru de „sanity" (fără 90% titluri identice), listă de gazde permise, validarea Brewtality, verificare live a linkului,
fișiere de ieșire append-only (reluabile), cache pe disc.

## Cum se rulează (toate citesc doar; nimic nu se trimite)
```
py -m pytest tests -q                                   # 536 teste, offline
py -m scraper.derkaiser --jobs docs.jsonl --out out --workers 8 --interval 3 --learn --no-render --recipes recipes
py -m scraper.portals   --docs docs.jsonl --out portal_jobs.jsonl --interval 3 --i-have-written-permission   # BestJobs: NU fără acord scris
py -m scraper.ejobs     --out ejobs_jobs.jsonl --interval 3 --i-have-written-permission   # eJobs: NU fără acord scris
py -m scraper.cc        --docs out/docs_with_jobs.jsonl --out cc_jobs.jsonl --crawls 4
py -m scraper.merge --src anofm=out/jobs.jsonl --src bestjobs=portal_jobs.jsonl --src ejobs=ejobs_jobs.jsonl --out all_distinct.jsonl
```
`docs.jsonl`: câte un rând `{"id": "<CIF 8 cifre>", "company": "...", "website": []}`.
`publish.py` are `send_jobs` / `send_company` cu gard de dry-run (`confirm=False`) — **netestate pe API-ul real**.

## Ce e verificat și ce nu
**Verificat (offline):** 536 teste trec (8 sar).
**Verificat live, pe eșantioane mici:** forma răspunsului Workday (un tenant real), ANOFM, BestJobs (profil + anunț, 15 firme), eJobs (14 anunțuri, 11 din România),
accesul la indexul Common Crawl, 2 rețete învățate corect (Valrom, Carrefour).
**Neverificat / limite:**
- Nimic nu a fost trimis la inviitor.ro; `send_jobs`/`send_company` nu au fost rulate pe API-ul real. Cere mediu de test sau acord.
- Workday doar pe un tenant (NVIDIA); alte ATS-uri nu au fost verificate live de mine.
- Common Crawl: 0 anunțuri pe 12 firme testate (puține firme au `JobPosting`); sweep-ul e lent.
- Pe site-urile firmelor mici randamentul e mic (9 firme cu rezultat propriu din 630).
- Orașul e dedus din text: poate greși la localități mici (ex.: „Voinesti" → „Targoviste"). Verificați înainte de publicare.
- ANOFM repetă uneori același post în mai multe anunțuri; `merge.py` le unifică (1.711 citite → 1.377 distincte).
- Anunțurile de pe portal poartă numele agenției care postează (ex. ProHuman), nu al clientului.
- Rularea completă pe 3.692 de angajatori ANOFM nu a fost dusă la capăt (am oprit-o la ~1.050 firme). Ritm: ~8 firme/min, deci ~8 ore.
- Numere parțiale (dedup): ~2.100 distincte la ~650 firme + 1.000 anunțuri eJobs + BestJobs. Estimare totală: aprox. 15.000–20.000 distincte, **nu 70.000**.

## Decizii pentru voi (nu pot fi luate în cod)
1. **ToS eJobs / BestJobs / Undelucram / Hipo:** (vezi `docs/TOS.md`: BestJobs interzice scraping-ul la pct. 3 din termeni; eJobs neverificat) `robots.txt` permite citirea anunțurilor (BestJobs, eJobs verificate), dar termenii pot interzice preluarea automată.
   Decizia aparține asociației. Modulele `ejobs.py` și `portals.py` citesc doar anunțuri publice, 3 s între cereri, păstrează doar titlu/firmă/oraș/link.
2. **De unde vine ținta de 70.000?** ANOFM raportează ~30–35.000 de *locuri vacante* (posturi, nu anunțuri); noi am măsurat 8.141 anunțuri = 33.563 posturi.
3. **Scriere în inviitor.ro:** doar după mediu de test și aprobare; `confirm=True` e singurul comutator.
4. **OLX:** blochează boții (403); nu a fost folosit. Agregatorii (Jooble, Jobrapido, Talent) nu au fost folosiți (duplicate, termeni stricți).

## Fișiere de date (nu sunt în pachet)
`run_all/` (pe PC-ul lui Mihai): `out/jobs_by_firm.jsonl`, `out/jobs.jsonl`, `portal_jobs.jsonl`, `ejobs_jobs.jsonl`, `all_distinct.jsonl`.



## Pipeline pe firme (rulat 2026-11-19, rezultate)
`tools/firms_pipeline.ps1` (ruleaza din radacina repo-ului, reluabil): listele Ministerului Finantelor (data.gov.ro, "Situatii financiare 2024": `CUI,CAEN,I1..I20`,
I13 = cifra de afaceri, I20 = nr. mediu de angajati) -> filtrare (>= 1 angajat, cifra > 0, fara CAEN 6420; prag in MIN_EMPLOYEES) -> ANAF (`anaf_batch.py`, activ, SRL/SA etc.) ->
`scraper.bridge` (gaseste si VERIFICA site-ul: numele firmei + CIF pe pagina) -> `scraper.derkaiser --jobs` doar pe firmele cu site.
Scripturile auxiliare sunt in `tools/firms_*.py` (cai relative la radacina repo-ului; datele in `run_firme/`, ignorate de git).

**Rezultate pilot (nu s-a facut rularea pe toate firmele):**
- 170.051 firme cu >= 3 angajati; 113.653 active (ANAF) cu > 3 angajati.
- Pilot 274 firme mici: 28 (10%) cu site verificat, 0 joburi pe site-uri.
- Pilot 60 firme cu > 250 angajati: 11 cu site, 41 joburi, din care doar 13 de pe site-urile proprii (restul vin din ANOFM).
- Concluzie: joburile sunt pe portaluri (eJobs, BestJobs, Undelucram, OLX, Publi24), nu pe site-urile firmelor. Rularea completa ar dura saptamani si ar aduce cel mult cateva sute de joburi noi.
- Viteza bridge: ~3-4 firme/min cu 12 workeri; acum 40 workeri, timeout 8 s, firmele mari primele (netestat la scara).

**Reparat 2026-11-19:** `net.urllib_opener` taia tacut orice raspuns la 2 MB; feed-urile Lever (Aleph 2 MB, Welo 7 MB, TSMG 35 MB) ajungeau JSON invalid si dadeau 0 joburi.
Acum `Net.max_bytes` decide (boards: 60 MB). `boards.py`: 185 -> 250 joburi.

## Ce ramane (decizii ale asociatiei)
- Volumul (70.000) cere eJobs/BestJobs/Undelucram/OLX/Publi24: termenii lor interzic preluarea automata fara acord scris (`docs/TOS.md`); draft de cerere in `docs/CERERI_ACORD.md`.
- `scraper/jooble.py`: sursa prin API-ul Jooble (cheie gratuita de la ro.jooble.org/api/about), netestata cu cheie reala; de verificat termenii lor inainte de republicare.
- Scrierea in inviitor.ro (Solr/baza lor) ramane dezactivata pana la aprobare sau mediu de test.

## Reparatii si unelte (2026-11-19)
- `quality.py`: `TRANSFER(SOSIRE)` = activ; CAEN 6820, cooperativele si "alte forme juridice" sunt admise (doar 6420 ramane exclus).
- `model.verify_site`: CIF-ul se cauta si pe /contact, /despre-noi, /termeni-si-conditii, /politica-de-confidentialitate (cu context CUI/CIF/Cod fiscal).
- `model.best_city`: adresa ANOFM ("Judet > Municipiul X > X SECT 1") se transforma in cel mai specific oras acceptat; sectoarele Bucurestiului devin Bucuresti.
  Datele deja exportate (8.631 joburi) pastreaza vechea locatie "Romania" pentru ~4.800 de joburi ANOFM pana la o noua rulare ANOFM.
- `tools/export_csv.py OUT.csv IN.jsonl [...]`: JSONL-urile de joburi -> CSV (titlu, firma, cif, locatie, judet, remote, link, sursa), fara linkuri duplicate.

## Bug hunt + polish (2026-11-20)
- **anaf_batch.screen_cifs**: raportul final reconstruia un set de toate CIF-urile verificate pentru fiecare CIF ramas (timp patratic) -> la 350.000 de CIF-uri procesul parea blocat dupa ce termina. Acum setul se construieste o data. Un CIF cu litere nu mai opreste rularea (este ignorat inainte de `int()`).
- **bridge**: pragul implicit de angajati este 1 (ca in criterii); fisierul de reluare se inchide corect.
- **publish**: importul `Net` pentru adnotare este acum `TYPE_CHECKING` (nu mai e nume nedefinit).
- Fisiere cu BOM (scrise din PowerShell) curatate; importuri moarte scoase.
- Docstring pe toate functiile publice din modulele DerKaiser (engine, jobs, model, net, bridge, boards, portals, posturi, ejobs, workday, financials, merge, discovery, jooble) si pe scripturile din `tools/`. Sabloanele mostenite de la Brewtality (api, validate_jobs, self_healing, learn, parse, main) au ramas neatinse.
- Teste noi: `tests/test_anaf_batch_regress.py`. Total: 547 trec, 8 sarite.
- Rulare joburi pe firme: pornita de test 35 min (2.400 firme procesate, 317 cu site, 467 joburi), apoi oprita la cererea utilizatorului; reia de unde a ramas cu `run_firme\chain_after_screen.ps1`.

## Baza de date PostgreSQL (2026-11-20)
- `db/schema.sql` + `scraper/pgexport.py`: joburile din jsonl -> un singur fisier `import.sql` (upsert dupa CIF/URL, tranzactie). Nu se conecteaza la nicio baza.
- Validat pe un Postgres 16 real cu datele reale: 8.824 joburi, 3.577 firme, incarcare 0,3 s, a doua rulare nu dubleaza nimic.
- Schema e a noastra; schema inviitor.ro nu e cunoscuta (baza lor e un PostgreSQL, dar nu avem structura).
- Descoperit la export: 14 joburi ANOFM aveau un CNP (13 cifre) in campul `cif` (angajatori persoane fizice). Sunt excluse din SQL si din CSV (`tools/export_csv.py`).
- `jobs.cif` e optional: 1.389 de joburi (posturi.gov.ro 1.132 + ATS) n-au CIF; pastreaza `company_name`.
- **Baza locala SQLite + cautare** (`scraper/jobdb.py`, `deschide_joburi.bat`): `py -m scraper.jobdb build|search|stats|serve`. Cautare fara diacritice (FTS5), filtre pe oras/judet/sursa/mod de lucru, pagina web read-only pe 127.0.0.1, fara dependinte. `derkaiser.db` e in .gitignore (se reconstruieste din jsonl).
- `db/search.sql`: cautare optionala pentru Postgres (`search_jobs('text')`, unaccent + pg_trgm).
- Pe datele reale: 8.824 joburi, 4.189 angajatori distincti; 4.887 joburi n-au oras (ANOFM care zice doar \"Romania\"; se repara cu rerularea ANOFM ~3 h).
