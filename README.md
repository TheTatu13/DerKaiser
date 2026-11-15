# DerKaiser

Scraper Python care construieste, pentru peViitor.ro, documente de **firma** (dupa CIF) si gaseste **joburile** fiecarei firme,
fiecare cu linkul propriului anunt. Derivat din Brewtality 3:16. **Local, dry-run: nu scrie nimic in Solr** pana nu exista
endpoint si autentificare aprobate.

## Ce face

1. **Descopera CIF-uri** (CUIFirma si CUIScan se completeaza reciproc, pornind de la judete / cuvinte din nume).
2. **Filtreaza prin ANAF** (100 CIF / cerere): firma activa, nu inactiva fiscal, forma juridica de societate (fara PFA/II/IF),
   CAEN diferit de 6420 (holdinguri) si 6820 (inchirieri proprii).
3. **Verifica salariatii** (CUIScan `financials`): >= 1 salariat, cifra de afaceri > 0, bilant din ultimii 3 ani.
4. **Construieste documentul de firma** (11 campuri, id = CIF cu 8 cifre) si aplica regula "doar active".
5. **Gaseste joburile** firmei: feed-uri publice ATS (Greenhouse, Lever, Workable, Recruitee, SmartRecruiters, Personio),
   altfel date `JobPosting` din paginile de cariere. Un job fara link valid de anunt se arunca. Firmele fara joburi se pastreaza
   (`existingJobsCount = [0]`).
6. **Reconciliaza** cu documentele existente (create / update / skip / conflict pentru id nepadat); nu atinge niciodata
   documentele altui scraper.

## Rulare (totul local, reluabil; pauze lente intentionat)

```powershell
py -m scraper.derkaiser --discover --out run_discovery --max 600        # 1. CIF-uri noi
py -m scraper.derkaiser --screen --out run_discovery                    # 2. ANAF
py -m scraper.derkaiser --financials --out run_discovery                # 3. salariati (1 cerere / 60 s)
py -m scraper.derkaiser --cifs run_discovery/cifs_final.txt --out run_docs      # 4. documente de firma
py -m scraper.derkaiser --jobs run_docs/docs.jsonl --out run_docs [--websites siteuri.csv]   # 5. joburi
```

`--websites` e un CSV `cif,website`, pentru firmele al caror document nu are `website` (sursele de CIF nu il dau).

## Limite respectate

| Sursa | Limita | Ritm implicit |
|---|---|---|
| CUIScan | 100 cereri / ora / IP | 1 / 60 s, buget 60 / ora |
| CUIFirma | 60 cereri / minut | 1 / 6 s |
| ANAF `PlatitorTvaRest/v9` | 1 cerere / s, 100 CUI / cerere | 1 / 1,5 s |
| site-uri de firme / ATS | fara limita publica | 1 / 2 s, user-agent propriu |

Descoperirea si verificarea de salariati impart bugetul CUIScan: nu le rula in acelasi timp.

## Ce lipseste (nu poate fi facut singur)

- Endpoint Solr de scriere, autentificare si schema job confirmata (Sebi). `publish.send_jobs` exista, refuza fara `confirm=True`
  si nu a fost incercat pe API-ul real.
- Sursa pentru `website` (CUIScan/ANAF nu il au).
- Formele payload-ului pentru Workable, Recruitee, SmartRecruiters, Personio sunt din referinte comunitare: de verificat pe un
  raspuns real inainte de incredere (Greenhouse si Lever urmeaza codul flotei).
- Termenii CUIFirma interzic "scraping masiv": ritmul e lent, dar merita confirmare.

## Teste

```powershell
py -m pytest tests -q --ignore=tests/test_browser.py -k "not consistency and not e2e and not integration"
```

Detalii de stare si decizii: `DERKAISER.md`, planul pe faze: `ROADMAP.md`.

## Quick start

Vezi sectiunea 'Rulare' de mai sus (python -m scraper.derkaiser --help). Totul e dry-run implicit; scrierea in peViitor cere confirm=True.

## Layout

- scraper/ - discovery, ANAF, financials, jobs (+ model.py: formatul Brewtality, ANOFM), publish.py
- tests/ - teste unitare offline

## License

MIT


## Motorul de joburi (păpușile ruse)

`--jobs` rulează `scraper/engine.py`: scrapere încuiate unul într-altul, de la cel mai mic la cel mai larg. Fiecare e complet de sine stătător; dacă nu găsește nimic, greșește sau rezultatul arată suspect, preia următorul:

1. **recipe** - rețetă per firmă (`recipes/<CIF>.json`): ATS+slug sau pagini de carieră + regex de link.
2. **ats** - feeduri ATS publice găsite pe paginile de carieră.
3. **jsonld** - `JobPosting` schema.org.
4. **listing** - generic: linkuri de anunț de pe paginile de carieră, fiecare CONFIRMAT pe propria pagină (JSON-LD sau titlu potrivit cu URL-ul + cuvinte de anunț).

ANOFM nu e strat: se adaugă mereu (joburi după CIF + domeniul firmei din `contact_email`, verificat pe homepage).

Plase de siguranță: retry cu backoff și `Retry-After`; ritm per host (`--interval`, implicit 3 s) cu firme în paralel (`--workers`); circuit breaker per host; robots.txt; buget de cereri și timp per firmă; fiecare strat în propriul try/except; verificare de sanitate (fără inundații cu același titlu); doar linkuri de pe site-ul firmei / ATS / ANOFM; regulile de validare Brewtality; verificare live GET + markere de expirare; canary de istoric (`suspect_zero`); rezultate append-only, reluabile; cache de pagini (`--cache`). Totul dry-run.

Limite: HTML static (fără randare JavaScript); site-urile cu anunțuri doar în JS, PDF sau pe platforme interzise rămân doar cu ANOFM.

### Randare JavaScript si website

Cand straturile statice nu gasesc nimic, motorul randeaza paginile de cariera cu Chrome/Edge deja instalat (headless, nimic de instalat; dezactivare: --no-render, alt browser: DERKAISER_BROWSER). Website-ul firmei: dat manual > domeniul din contact_email ANOFM (verificat pe homepage) > ghicit din nume (verificare stricta: numele complet + CIF sau .ro + 'romania' pe pagina).


## DerKaiser overview

DerKaiser is a local, dry-run multi-company job engine for peViitor.ro, built on the Brewtality 3:16 template.
It starts from a company (CIF from ANOFM), finds the company's website, then its job ads (each with its own link),
and complements them with job portals. **It never writes to peViitor/Solr** (`confirm=False` is the default guard).

Source layers, smallest doll first: learned per-firm recipe -> public ATS feeds (Greenhouse, Lever, Workable, Recruitee,
SmartRecruiters, Personio, Workday) -> JSON-LD `JobPosting` -> generic listing scraper -> optional JavaScript rendering;
ANOFM is always added. Separate modules: `portals.py` (BestJobs, firm first), `ejobs.py` (eJobs sitemaps), `cc.py`
(Common Crawl), `merge.py` (deduplication across sources).

See [HANDOFF.md](HANDOFF.md) for how to run each module, what is verified and what is not, and the decisions that belong to
the association (portal terms of service, the 70,000 target, writing to production). Tests: `py -m pytest tests -q`.
