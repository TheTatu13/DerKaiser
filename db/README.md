# db/ — PostgreSQL pentru joburile DerKaiser

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

## Reguli de date

- `jobs.cif` poate fi NULL (posturi.gov.ro si joburile de pe ATS nu au CIF in listele noastre); angajatorul ramane in `company_name`.
- Randurile al caror camp CIF are 11+ cifre sunt **excluse**: nu e CIF, e CNP al unei persoane fizice.
- `work_mode` si `city` se completeaza doar cand sursa le spune clar; `city` e NULL cand anuntul zice doar "Romania".

## Teste pe un Postgres real

```bash
DERKAISER_PG_URL=postgresql://user@host/baza_de_test py -m pytest tests/test_pgexport.py
```
Fara variabila (sau fara `psql`), testele de integrare sunt sarite. Ele sterg tabelele `jobs`/`companies`, deci **nu** le indica o baza reala.

Structura asta e a noastra: nu stim schema inviitor.ro. Cand o aflam, se adapteaza maparea din `scraper/pgexport.py`.
