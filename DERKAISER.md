# DerKaiser

Scraper Python **multi-companie** pentru peViitor, derivat din Brewtality 3:16 (`scraper-py/`).
Stare: **doar local**, nepublicat. Nu exista repo GitHub, nu s-a facut push.

## Ce e gata

- `scraper/company_doc.py`: contractul documentului de companie, **11 campuri**:
  `id`, `company`, `brand`, `status`, `location[]`, `website[]`, `lastScraped`,
  `scraperFile`, `address[]`, `anafData[<un string JSON>]`, `existingJobsCount[<int>]`.
  - `id` = CIF cu zerouri in fata, **8 cifre** (`01559737`), aliniat cu flota.
    Documentele vechi Inviitor sunt nepadate (`1697454`) si produc duplicate.
  - `build_company_doc()` construieste documentul dintr-un record ANAF.
  - `validate_company_doc()` e strict: lipsa oricarui camp = invalid.
- `scraper/sources.py`: sursele de date.
  - **CUIScan** = detalii complete dupa CIF (umple documentul).
  - **CUIFirma** = cautare dupa nume (CIF/nume/status); nu poate umple singur un document.
  - Cautarea dupa nume accepta doar potrivire exacta, niciodata "primul rezultat".
  - CUI-ul intors de CUIScan trebuie sa fie cel cerut.
  - O sursa picata = firma sarita si raportata (`unavailable`), nu document pe jumatate.
  - `Throttle` intre cereri; lotul se opreste dupa N rezultate `unavailable` la rand.
  - Un singur incercari per sursa, fara retry (ca in `scraper.anaf`).
- Teste: `tests/test_company_doc.py` + `tests/test_sources.py` (131), fara retea.

## Ce NU e gata (blocat de raspunsurile lui Sebi)

1. Lista de CIF-uri de parcurs (sursa ei).
2. Sursa joburilor fiecarei firme.
3. Endpoint si autentificare pentru scrierea in Solr.
4. Codul `inviitor-ro-nodejs-scraper` (de portat).
5. Mod "multe firme" in `main.py` (template-ul e pe o singura firma, din `config/company.json`).
6. Limitele de rate si termenii CUIScan/CUIFirma pentru volume mari (de verificat).
7. Decizie `scraperFile`: acum `derkaiser-python-scraper` (ca Inviitor); flota pune URL-ul workflow-ului.

## Reguli pana la publicare

- Dry-run implicit; nu suprascrie documentele create de scraperele per companie.
- La publicare: layout `multi` in `fleet.json`, CODEOWNERS comment-only, Dependabot.

## Test live (2026-11-13, 3 cereri CUIScan + 2 CUIFirma, pauza 3s)

- CUIScan: 3/3 ok (SOBIS 52200796, FARMEC SA 199150 -> id 00199150, Continental 1559737 -> id 01559737).
- CUIFirma: cautarea 'FARMEC SA' intoarce pe primul loc FARMEC SRL (radiata); potrivirea exacta alege corect 00199150.
- Gasit live: CUIScan da adrese 'MUNICIPIUL BUCURESTI' (majuscule, sedila); normalizate la 'Municipiul Bucuresti' (virgula dedesubt). Teste adaugate.
- Nu s-a scris nimic nicaieri (doar GET-uri de citire).

## Regula de ignorare

- O firma care nu indeplineste **minim o conditie** este ignorata (status `ignored`), cu motivul raportat. Conditiile: toate cele 10 campuri cerute prezente si corecte, iar `status` exact `activ` (firma inactiva/suspendata/radiata se ignora).
- `website` nu e conditie (poate fi gol).
- Site picat = `unavailable` (se reia mai tarziu), nu `ignored`: nu e o conditie nerespectata.
- CIF gresit / CUI nepotrivit = `invalid`, tot sarit.


## Rezultate teste de uzura (2026-11-13)

### F0 (inchisa)
- **Simulat, 100.000 firme** (+ intrari murdare si duplicate): 10,8 s, varf de memorie 124 MB,
  0 id-uri duplicate. Rezultate: 81.812 ok, 4.943 not_found, 5.106 unavailable, 8.139 ignored, 2 invalid;
  fara abort la blip-uri izolate.
- **Live, 25 firme din `fleet.json`** (pauza 3 s): 25/25 ok. p50 0,66 s, p95 ~6 s (cateva raspunsuri lente).
- **Gasit:** memoria creste liniar cu numarul de firme cand rezultatele se tin in RAM
  (`process_cifs`); rezolvat in F2 prin scriere in flux (`batch.py`).
- **Gasit live:** adrese de tip `Sat Recea Com. Varsolt` -> acum `Comuna Varsolt`
  (la fel `Mun.`/`Ors.`); `MUNICIPIUL BUCURESTI` cu sedila -> `Municipiul Bucuresti`.

### F2 (inchisa, cu rezerve)
Mod multi-firma: `py -m scraper.derkaiser --cifs lista.txt --out folder` (doar local, dry-run).
- **Test live:** 56 intrari (46 CIF-uri din flota + murdarii + duplicate), proces ucis dupa 45 s
  (15 firme facute), reluat: 15 sarite, 0 refacute, 0 pierdute, 46 documente unice, toate cu id pe 8 cifre
  si status activ; 'abc' si '12-34' raportate invalid.
- **Gasit 1:** CUIScan raspunde `400` pentru un CIF care nu trece verificarea (testat cu CIF `1`);
  era tratat ca sursa picata (retry la infinit, numarat spre abort). Acum = `not_found`, final.
- **Gasit 2:** `0001559737` nu era recunoscut ca `01559737` (firma ceruta de doua ori, document dublat).
  Acum zerourile in plus se elimina inainte de padding.
- **Rezerve:** testul a avut 46 de firme reale, nu 200 (nu avem lista); abortul la cadere de sursa
  e testat doar cu simulare; `existingJobsCount` e inca 0 (placeholder pana la F3/F4);
  liniile goale din lista sunt sarite, nu raportate.
- 343 teste unit trec.



## Faza 4, partea offline: reconciliere cu documentele existente (2026-11-13)

`scraper/reconcile.py` decide ce s-ar face cu fiecare firma fata de documentele deja existente
(doar planificare; nu citeste si nu scrie in Solr). Folosire:
`py -m scraper.derkaiser --cifs lista.txt --out folder --existing existente.jsonl` => `plan.jsonl` + `plan` in `report.json`.

Reguli, in ordine:
1. niciun document pentru acest CUI -> `create`
2. exista doar un document **nepadat** (`1697454`) -> `conflict_unpadded` (un `01697454` nou ar dubla firma)
3. mai multe documente cu acelasi id -> `review`
4. documentul existent nu are `scraperFile` -> `review` (proprietar necunoscut)
5. apartine altui scraper (flota scrie URL-ul workflow-ului) -> `skip_foreign` (nu se suprascrie niciodata)
6. al nostru / Inviitor, fara diferente -> `skip_unchanged` (`lastScraped` si `existingJobsCount` nu conteaza)
7. al nostru / Inviitor, cu diferente -> `update` + lista campurilor

**De decis cu Sebi:** documentele create de Inviitor (`inviitor-ro-nodejs-scraper`) sunt acum considerate
actualizabile (DerKaiser ii preia rolul). Daca nu, se scot din `DEFAULT_UPDATABLE`.

**Test de uzura F4 (simulat, nu pe Solr real):** 1.000 firme CUI pe 7 cifre, 6 situatii amestecate:
0 decizii diferite de adevarul-de-referinta, 0 documente straine care ar fi modificate, 0 dubluri create
peste documente nepadate, 0,09 s. Rezerva: adevarul-de-referinta l-am scris eu; lipseste testul pe un export real.
Pasii ramasi din F4 (scriere reala, citire inapoi) cer endpoint si autentificare Solr.



## Pipeline complet, dry-run (2026-11-13)

Fluxul, fiecare pas local si reluabil (`--out run_discovery`):
1. `--discover` : CUIFirma si CUIScan se completeaza (snowball). CUIFirma da `is_active`; CUIScan nu are status de incredere in cautare.
2. `--screen` : ANAF v9, 100 CUI/cerere, 1 cerere la 1,5 s. Porti: stare activa, nu inactiv fiscal, forma juridica de societate,
   nu PFA/II/IF/AF/PF (dupa nume), CAEN != 6420/6820 (`scraper/quality.py`). Scrie `screened.jsonl`, `cifs_screened.txt`.
3. `--financials` : CUIScan `financials` (nrAngajati, cifraAfaceri). Poarta: >= 1 salariat, cifra de afaceri > 0, bilant din ultimii 3 ani.
   O cerere / 60 s (limita CUIScan 100/ora/IP, bugetul e comun cu descoperirea). Scrie `financials.jsonl`, `cifs_final.txt`.
4. `--cifs run_discovery/cifs_final.txt --out run_docs` : documentul de firma (CUIScan `company`) + regula "doar active".

Regula de ordine: ANAF (ieftin) inaintea CUIScan (scump). `company` nu are salariatii; doar `financials`.
Descoperirea si verificarea de salariati impart bugetul CUIScan: nu rulează simultan.

Rezultat live (primul lot, 2.622 firme descoperite din 103 interogari):
- 2.308 trimise la ANAF (restul = deja radiate dupa CUIFirma), 496 trec (21%).
- Motive de picare: stare (radiate etc.) 1.454, forma juridica 584, PFA/II dupa nume 408, inactiv fiscal 260, CAEN 6420/6820 48.
- Salariati: in curs (1 cerere/minut), ~8 ore pentru 496 firme.

Decizii Mihai: prag >= 1 salariat; firmele fara joburi se scrapeaza oricum; CAEN 6420 si 6820 excluse; lent si sigur.
Ramas: endpoint Solr + schema job (Sebi), sursa de joburi (ATS + JobPosting + pagina de cariere), `website`.


## Joburi + publicare (2026-11-13), offline, 404 teste trecute

- `scraper/jobs.py`: parsere ATS (Greenhouse, Lever, Workable, Recruitee, SmartRecruiters, Personio), detectie ATS din paginile
  de cariere, `JobPosting` JSON-LD, gasire pagina de cariere, formatul fleet (`job_title`, `job_link`, `company`, `country`,
  `city`, `county`, `remote`). Linkul anuntului e obligatoriu: normalizat (https, fara tracking), nu poate fi pagina-lista
  (`/careers`), iar la `JobPosting` fara `url` se foloseste adresa paginii doar daca e pagina unui singur anunt.
- `scraper/publish.py`: `run_jobs` (joburi + verificare ca linkul raspunde < 400 + `existingJobsCount` real; firmele fara joburi
  raman cu `[0]`), `payloads_by_company` (forma POST a flotei), `send_jobs` (refuza fara `confirm=True` si variabile
  `DOMAIN/TOKEN_ROUTE/ADD_JOBS_ROUTE/EMAIL`; netestat pe API real).
- CLI: `--jobs DOCS_JSONL [--websites cif,website.csv]`.
- Format preluat din repo-urile flotei (based_scraper_py, scrapers.js): vezi `ats_common.py` acolo.
- Oprit manual: rularea live de noapte (descoperire + salariati) a fost oprita la cererea lui Mihai; `run_discovery/` pastreaza
  2.622 firme descoperite, 496 trecute de ANAF, 2 verificate la salariati.

Neverificat live: formele Workable/Recruitee/SmartRecruiters/Personio; sursa de `website`; endpointul real peViitor.
