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
