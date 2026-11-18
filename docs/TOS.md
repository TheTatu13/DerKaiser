# ToS: eJobs / BestJobs (stare la 2026-11-16)

Nu e aviz juridic. Decizia aparține asociației.

## BestJobs (https://www.bestjobs.eu/legal/terms)
- **Pct. 3:** colectarea neautorizată de date (scraping) este interzisă; colectarea/transferul automat e permis doar prin **API-ul oficial sau Webhooks**.
- **Pct. 4:** bazele de date și conținutul platformei sunt protejate; reproducerea/distribuirea fără permisiune este interzisă.
- `robots.txt` permite accesul (verificat), dar termenii sunt mai stricți decât robots.txt.
- **Concluzie:** `scraper/portals.py` (BestJobs) intră în conflict cu pct. 3. Nu se rulează fără acordul scris al BestJobs sau acces la API-ul lor.

## eJobs (termeni actualizați 02.10.2026, citiți din textul complet)
- **Pct. 7.5:** interzise mijloacele automate (script-uri, roboți, bot, spider, crawler). Preluarea sistematică de conținut sau date și extragerea de text și date, inclusiv indexarea și colectarea de conținut, sunt interzise fără acordul scris prealabil.
- **Pct. 7.3:** baza de date de CV-uri și anunțuri e protejată (drept sui generis, Legea 8/1996). Extragerea sau reutilizarea unei părți substanțiale, inclusiv a anunțurilor, e interzisă fără acord scris.
- **Pct. 7.2:** reproducerea informațiilor din anunțurile de recrutare, integral sau parțial, e interzisă fără acord scris. Cererile de permisiune se trimit la contact@ejobs.ro.
- `robots.txt` permite sitemap-urile, dar termenii interzic explicit exact ce face `scraper/ejobs.py`.
- **Concluzie:** `ejobs.py` nu se rulează fără acord scris de la eJobs.
## Recomandare
1. Cerere de acord/API către BestJobs și eJobs (dacă asociația are relații cu ele).
2. Până atunci, `portals.py` și `ejobs.py` nu se rulează în producție; sursele rămân ANOFM + site-urile firmelor.
3. Același lucru de verificat pentru Undelucram și Hipo.

## posturi.gov.ro
- robots.txt permite tot (verificat 2026-11-16). Nu am găsit termeni de reutilizare publicați, doar mențiunea că navigarea implică acceptarea termenilor.
- Date publice de stat (H.G. 1336/2022), cea mai curată sursă nouă. Confirmați cu ANFP (posturi@gov.ro) dacă vreți certitudine.
- Ritm: 3 s între cereri; ~24 anunțuri/pagină, ~3.000 anunțuri active => cca. 2,5 ore.

## OLX.ro (conditii de utilizare iunie 2026, https://blog.olx.ro/wp-content/uploads/2026/06/Conditii-de-utilizare-Iunie.pdf)
- **Pct. 2.3:** continutul site-ului, inclusiv anunturile, nu se poate folosi fara acord scris; agregarea si prelucrarea datelor pentru redistribuire pe alte site-uri e interzisa.
- Nu apare cuvantul "scraping", dar pct. 2.3 acopera exact folosirea noastra (peViitor republica anunturile).
- Pct. 13.1: interzis sa afectezi functionarea serviciului sau sa-l folosesti abuziv. Citit in intregime doar partial (text trunchiat); clauzele 15.8 si 17.4/17.6 nu au fost verificate.
- **Concluzie:** nu se scrapeaza fara acord scris de la OLX.

## Publi24.ro (https://ajutor.publi24.ro/termeni-si-conditii/)
- Nu interzice explicit roboti/crawler, dar permite doar uz personal necomercial: interzis sa vinzi, modifici, expui public sau distribui materialul; compania isi rezerva drepturile exclusive.
- robots.txt blocheaza /Search/, /DetailAd/, /firma/ (paginile de anunt individual), deci nici tehnic nu putem citi anunturile.
- ~13.000 anunturi de joburi, din care 7.776 de la persoane fizice, fara angajator/CIF.
- **Concluzie:** zona gri spre interzis; necesita acord scris.

## Lajumate.ro / JobZZ
- robots.txt permite crawling (cu exceptii), blocheaza Jooblebot, trovitBot si multe unelte de copiere.
- Paginile de termeni (ajutor.lajumate.ro) nu s-au putut deschide din tool; **neverificat**. De citit manual inainte de orice rulare.

## Undelucram
- Vezi sectiunea anterioara: extragere interzisa (4.10); un feed API oferit de angajator e permis (2.2.2).

## Concluzie generala (2026-11-19)
Pilotul pe site-urile proprii ale firmelor (274 firme mici + 60 mari) a dat practic 0 joburi noi: joburile sunt publicate pe portaluri, nu pe site-urile firmelor. Portalurile cu volum (eJobs, BestJobs, Undelucram, OLX, Publi24) cer acord scris. Calea legala spre volum mare este acordul/API-ul lor; vezi docs/CERERI_ACORD.md.
