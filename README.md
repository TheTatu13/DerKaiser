<h1 align="center">DerKaiser</h1>

<p align="center">
  Scraper local, <b>dry-run</b>, pentru joburile firmelor din România — construit pentru
  <a href="https://inviitor.ro">inviitor.ro</a>.<br>
  Pornește de la o firmă (CIF), îi găsește site-ul și anunțurile, fiecare cu linkul ei, apoi completează cu surse publice.
</p>

<p align="center">
  <img alt="teste" src="https://img.shields.io/badge/teste-536%20trec-brightgreen">
  <img alt="python" src="https://img.shields.io/badge/python-3-blue">
  <img alt="mod" src="https://img.shields.io/badge/mod-dry--run-orange">
  <img alt="licență" src="https://img.shields.io/badge/licen%C8%9B%C4%83-MIT-lightgrey">
</p>

> **Nu scrie nimic în inviitor.ro (Solr/baza lor).** Scrierea e dezactivată (`confirm=False`) până există endpoint, autentificare și aprobarea asociației.

---

## Rezultate

Rularea finală, doar din surse publice și permise:

| Sursă | Joburi distincte |
|---|---:|
| ANOFM (API public, după CIF) | 7.242 |
| posturi.gov.ro (ANFP, sectorul public) | 1.132 |
| Career boards ATS verificate (Greenhouse, Lever, Workable, Workday) | 257 |
| **Total** | **~8.600** (de la ~4.200 de firme) |

Fiecare job are propriul link de anunț. Lista e în `derkaiser_joburi.csv` (generată local, nu e în repo).

> **Ținta de 70.000 nu e atinsă cu surse legale.** Joburile mari stau pe eJobs, BestJobs, Undelucram, OLX și Publi24, iar
> termenii lor interzic preluarea automată fără acord scris. Detalii în [`docs/TOS.md`](docs/TOS.md);
> draft de cerere în [`docs/CERERI_ACORD.md`](docs/CERERI_ACORD.md).

## Cum funcționează

Straturi care se înlocuiesc unul pe altul, de la cel mai precis la cel mai larg („păpușile rusești"). Dacă un strat nu găsește
nimic sau rezultatul pare suspect, preia următorul:

```
rețetă învățată pe firmă  →  feed-uri ATS publice  →  JSON-LD JobPosting  →  listare generică  →  randare JS (opțional)
                                         + ANOFM, adăugat mereu
```

| Strat | Fișier | Ce citește |
|---|---|---|
| ANOFM | `model.py` | API-ul public mediere.anofm.ro, după CIF |
| Rețetă | `engine.py`, `learn.py` | `recipes/<cif>.json`: pagini de carieră, regex de link, paginare; se reînvață când site-ul se schimbă |
| ATS | `jobs.py`, `workday.py` | Greenhouse, Lever, Workable, Recruitee, SmartRecruiters, Personio, Workday |
| JSON-LD / listare | `jobs.py` | `JobPosting`; linkurile din listări sunt confirmate pe pagina anunțului |
| Randare JS | `render.py` | Chrome/Edge deja instalat (`--headless`), opțional |
| Stat | `posturi.py` | posturi.gov.ro (ANFP) |
| Boards ATS | `boards.py` | `config/ats_boards.json`: pagini de carieră verificate manual |
| Firme → site | `bridge.py` | găsește și **verifică** site-ul firmei (nume + CIF pe pagină) |
| Agregator | `jooble.py` | API-ul Jooble (necesită cheie gratuită; netestat cu cheie reală) |
| Deduplicare | `merge.py` | pe link normalizat și pe (firmă, titlu, oraș) |

`portals.py` (BestJobs) și `ejobs.py` (eJobs) există, dar **refuză să ruleze** fără `--i-have-written-permission`.

## Quick start — pornire rapidă

```powershell
py -m pytest tests -q                                           # 536 teste, offline

# joburile unor firme (docs.jsonl: {"id": "<CIF 8 cifre>", "company": "...", "website": []})
py -m scraper.derkaiser --jobs docs.jsonl --out out --workers 8 --interval 3 --learn --no-render --recipes recipes

py -m scraper.posturi --out posturi_jobs.jsonl --interval 3     # sectorul public
py -m scraper.boards  --out boards_jobs.jsonl                   # career boards ATS
py -m scraper.merge --src anofm=out/jobs.jsonl --src posturi=posturi_jobs.jsonl --src ats=boards_jobs.jsonl --out all_distinct.jsonl
```

Totul e reluabil (fișiere append-only), citește doar și lucrează lent intenționat.

### Pipeline pe firme (de la datele Ministerului Finanțelor la joburi)

```powershell
powershell -File tools\firms_pipeline.ps1
```

Listele „Situații financiare 2024" de pe data.gov.ro → filtru (≥ 3 angajați, cifră de afaceri > 0, fără CAEN 6420/6820) →
ANAF (activă, formă juridică de societate) → `bridge` (site verificat) → `derkaiser --jobs` doar pe firmele cu site.

Pilotul a arătat că metoda nu aduce volum: din 274 de firme mici, 28 au site și 0 joburi pe el; din 60 de firme cu peste
250 de angajați, 11 au site și doar 13 joburi de pe site-uri. Firmele își publică posturile pe portaluri, nu pe site-ul propriu.

## Plase de siguranță

Reîncercări cu backoff și `Retry-After` · pauză pe host (implicit 3 s) · circuit breaker pe host · `robots.txt` respectat ·
buget de cereri și timp pe firmă · `try/except` pe fiecare strat · verificare de sanitate (fără 90% titluri identice) ·
listă de gazde permise · validarea Brewtality · verificare live a fiecărui link · fișiere de ieșire append-only · cache pe disc.

## Format de ieșire

`derkaiser_joburi.csv` și `*.jsonl`, cu câte un job pe rând: `titlu, firmă, cif, localitate, județ, remote, link, sursă`.
Formatul intern al motorului e cel din șablonul Brewtality 3:16 (schema Solr peViitor); pentru alt import
(API, alt format de câmpuri) se adaugă un convertor în `scraper/publish.py`, odată ce schema destinației e cunoscută.

## Limite

- **Surse cu termeni restrictivi:** eJobs, BestJobs, Undelucram, OLX, Publi24 — vezi [`docs/TOS.md`](docs/TOS.md).
- Fără randare JS, site-urile cu anunțuri doar în JavaScript, PDF sau pe platforme interzise rămân doar cu ANOFM.
- Formele de payload pentru Workable, Recruitee, SmartRecruiters și Personio vin din referințe comunitare; de verificat pe răspunsuri reale.
- Scrierea în inviitor.ro nu a fost încercată pe API-ul real.

## Layout — structura proiectului

```
scraper/   motorul (engine, jobs, workday, posturi, boards, bridge, merge, ...)
config/    ats_boards.json — career boards verificate
docs/      TOS.md (stare termeni), CERERI_ACORD.md (draft cereri către portaluri)
tools/     scripturile pipeline-ului pe firme
tests/     teste unitare offline
HANDOFF.md predare: cum rulezi fiecare modul, ce e verificat și ce nu, deciziile asociației
```

## License — licență

MIT
