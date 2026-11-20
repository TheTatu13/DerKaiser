# König: roadmap cu test de uzura dupa fiecare faza

Stare: doar local. Nimic nu se publica si nu se scrie in Solr pana la faza 4.

**Regula:** o faza e inchisa doar cand trece si testul ei de uzura. Un test de uzura
nu cauta sa arate ca merge, ci sa strice lucrul: volum, intreruperi, rulari repetate,
date murdare. Pragurile de mai jos sunt propuneri, de confirmat.

**Raport standard de uzura** (acelasi la fiecare faza): nr. firme procesate, `ok / not_found /
unavailable / invalid / ambiguous`, top 10 motive `invalid`, cereri refuzate (429/403/5xx),
timp total si p95 pe cerere, memorie maxima, documente duplicate, ce s-a schimbat intre
doua rulari identice.

## Faza 0: Fundatie (GATA)
`company_doc.py` (11 campuri, id pe 8 cifre), `sources.py` (CUIScan + CUIFirma, throttle, abort la
cadere), 285 teste unit, test live pe 3 CIF-uri.
- **Test de uzura F0:** 100.000 CIF-uri simulate (fara retea): timp, memorie, 0 exceptii
  necontrolate. Apoi 25 CIF-uri live reale, pauza 3 s: rata de refuz a surselor.
- **Trece daca:** 0 crash; memorie constanta; < 5% `unavailable` live.

## Faza 1: Decizii si blocaje (Sebi)
Lista de CIF-uri (sursa), sursa joburilor, endpoint + autentificare Solr, codul
`inviitor-ro-nodejs-scraper`, limite/termeni CUIScan si CUIFirma, valoarea `scraperFile`.
- **Test de uzura F1 (validarea ipotezelor):** 20 documente reale din Solr comparate camp cu camp
  cu ce produce König pentru acelasi CIF.
- **Trece daca:** diferentele sunt explicate (nu necunoscute) pentru toate cele 20.

## Faza 2: Mod "multe firme" + reluare
`main.py` primeste lista de CIF-uri; dry-run implicit; checkpoint (reia de unde a ramas);
raport de rulare; plafon de rulare.
- **Test de uzura F2:** 200 CIF-uri live. Intrerupere fortata la mijloc (kill) si reluare.
  Cadere simulata a unei surse. CIF-uri murdare in lista (duplicate, litere, RO-prefix, goale).
- **Trece daca:** reluarea nu dubleaza si nu pierde firme; abort curat la cadere; 0 documente
  invalide scrise; intrarile murdare sunt raportate, nu ignorate.

## Faza 3: Joburi
Adaptoare pe sursa de joburi, deduplicare, `existingJobsCount` real.
- **Test de uzura F3:** 50 firme, rulate de 3 ori la rand; apoi o rulare dupa ce o sursa a
  schimbat pagina (fixture).
- **Trece daca:** a doua rulare = 0 modificari (idempotenta); joburile disparute sunt marcate,
  nu pierdute; schimbarea de pagina da alerta, nu document gol.

## Faza 4: Scriere sigura in Solr (cu aprobarea ta)
Verificare de proprietar pe `scraperFile` (nu suprascrie documentele celorlalte 46 de scrapere);
reconciliere id cu zerouri vs fara (`01697454` vs `1697454`); scriere pe lot mic; citire inapoi.
- **Test de uzura F4:** 1.000 CIF-uri in dry-run => raport de conflicte si duplicate; apoi 20
  firme reale scrise si recitite, apoi a doua scriere identica.
- **Trece daca:** 0 documente ale altor scrapere modificate; duplicatele detectate si rezolvate
  dupa regula agreata; scrierea a doua nu schimba nimic.

## Faza 5: Calitate si CI
Normalizare locatii/brand/website, E2E cu plasa de siguranta ANAF/CUIScan/CUIFirma, CI,
CODEOWNERS comment-only, Dependabot.
- **Test de uzura F5:** 5.000 CIF-uri in dry-run, rulat peste noapte in CI.
- **Trece daca:** top 20 de adrese nerecunoscute analizat si acoperit; `invalid` < 3%;
  fara memorie crescatoare; CI verde de 3 ori consecutiv.

## Faza 6: Publicare
Repo in org (dupa aprobare), layout `multi` in `fleet.json`, `SCRAPERS.md`, health summary.
- **Test de uzura F6:** 7 zile de rulari programate.
- **Trece daca:** 0 esecuri neexplicate; 0 mailuri de spam; health summary apare in fiecare
  luni.

## Faza 7: Extindere graduala
100 → 1.000 → 5.000 → toate firmele (~16.500).
- **Test de uzura F7:** dupa fiecare treapta, raportul standard + comparatie cu treapta
  anterioara.
- **Trece daca:** rata de erori nu creste cu volumul; nu exista cereri refuzate repetat; nimic
  nu inrautateste ce era deja in Solr.

## Reguli permanente
- Dry-run implicit; scriere doar cu aprobare, pe loturi mici.
- O sursa picata inseamna firma sarita si raportata, niciodata document pe jumatate.
- Potrivire exacta pe nume; niciodata "primul rezultat".

---
**Stare 2026-11-13:** F0 inchisa, F2 inchisa cu rezerve (vezi DERKAISER.md, 'Rezultate teste de uzura'). Urmatoarea: F1 (blocaje Sebi), apoi F3.

**Update:** F4 partea offline (reconcile + plan) gata si testata simulat; scrierea reala asteapta Solr (F1).

**Update 2026-11-13:** descoperire + ANAF + financials implementate si rulate live (dry-run). Urmeaza documentele si joburile.

**Update 2026-11-13 (seara):** F3 joburi implementat offline (ATS + JobPosting + link obligatoriu), publish dry-run, README real. Ce blocheaza finalul: Solr (endpoint/auth), schema job, sursa `website`.
