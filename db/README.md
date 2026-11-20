# db/ — baza de date a joburilor König

Doua variante, cu aceleasi reguli de date:

1. **Baza locala SQLite (fara instalare)** — pentru lucru zilnic: cautare + pagina web. Vezi mai jos.
2. **PostgreSQL** — pentru un server (de ex. inviitor.ro). `schema.sql` + `import.sql`.

## Varianta 1: baza locala + pagina de cautare

Dublu-click pe `deschide_joburi.bat` (la radacina proiectului): construieste `derkaiser.db` prima oara si deschide in browser
o pagina de cautare (merge doar pe acest calculator, nu scrie nimic in baza).

```bash
py -m scraper.jobdb build derkaiser.db run_all/final_distinct.jsonl run_all/boards_jobs2.jsonl run_firme/full/out/jobs.jsonl --companies run_firme/full/docs.jsonl
py -m scraper.jobdb search derkaiser.db "asistent medical" --county arges     # din terminal
py -m scraper.jobdb stats derkaiser.db                                     # totaluri, surse, orase, angajatori
py -m scraper.jobdb serve derkaiser.db --open                              # pagina de cautare
```

Cautarea ignora diacriticele si majusculele si accepta inceput de cuvant ("asist med" gaseste "Asistent medical").
`build` se poate repeta pe acelasi fisier: joburile se actualizeaza dupa URL (fara dubluri), `first_seen` ramane.
`--deactivate-missing` marcheaza inactive joburile din sursele prezente in import care nu mai apar (sursele absente nu se ating).

## Varianta 2: PostgreSQL

`schema.sql` creeaza (idempotent) tabelele `companies` si `jobs` si view-ul `active_jobs`.
Nu exista nicio coloana pentru textul descrierii, CV-uri sau date personale (GDPR, minimizarea datelor).

## Cum se foloseste

```bash
# 1. genereaza un singur fisier SQL (nu se conecteaza la nicio baza de date)
py -m scraper.pgexport --out import.sql --companies run_firme/full/docs.jsonl \
   run_all/final_distinct.jsonl run_all/boards_jobs2.jsonl run_firme/full/out/jobs.jsonl

# 2. ruleaza-l in psql, pgAdmin sau DBeaver (e o tranzactie: ori intra tot, ori nimic)
psql -d NUME_BAZA -f import.sql
```

Rularea repetata e sigura: firmele se actualizeaza dupa CIF, joburile dupa URL, nimic nu se dubleaza.
`--deactivate-missing` marcheaza `active = false` joburile din sursele prezente care nu mai apar in import.

Cautare optionala in Postgres (ignora diacriticele): `psql -d NUME_BAZA -f db/search.sql`, apoi `SELECT * FROM search_jobs('asistent medical pitesti');`.
Necesita extensiile `unaccent` si `pg_trgm` (se pot crea de proprietarul bazei, PostgreSQL 13+).

## Reguli de date

- `jobs.cif` poate fi NULL (posturi.gov.ro si joburile de pe ATS nu au CIF in listele noastre); angajatorul ramane in `company_name`.
- Randurile al caror camp CIF are 11+ cifre sunt **excluse**: nu e CIF, e CNP al unei persoane fizice.
- `work_mode` si `city` se completeaza doar cand sursa le spune clar; `city` e NULL cand anuntul zice doar "Romania".

## Teste pe un Postgres real

```bash
DERKAISER_PG_URL=postgresql://user@host/baza_de_test py -m pytest tests/test_pgexport.py tests/test_jobdb.py
```
Fara variabila (sau fara `psql`), testele de integrare sunt sarite. Ele sterg tabelele `jobs`/`companies`, deci **nu** le indica o baza reala.

Structura asta e a noastra: nu stim schema inviitor.ro. Cand o aflam, se adapteaza maparea din `scraper/pgexport.py`.
