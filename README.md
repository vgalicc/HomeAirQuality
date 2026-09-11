# Kvaliteta zraka — Zaprešić

Scraper za [indikativno praćenje kvalitete zraka Grada Zaprešića](https://zapresic.hr/indikativno-pracenje-kvalitete-zraka/).

## Kako to radi

Stranica Grada ne sadrži nikakve vrijednosti — samo ugrađuje tri
`smart-airq.com` widgeta. Widget endpoint **vraća SVG sliku**, a mjerenja su
tekstualni čvorovi u toj slici, pa ih scraper čita iz SVG-a. Privatni JSON API
(`/api/gateway/...`) traži autorizaciju i nije javno dostupan.

Po postaji se dohvaćaju dva widgeta:

| Widget | Čemu služi |
|---|---|
| `type=vertical` | vrijeme mjerenja + sva onečišćenja + meteo/buka |
| `type=aqi-details&aqiType=eaqi` | opisna EAQI kategorija (`Dobro`, `Prihvatljivo`, …) |

`aqiType=eaqi` se namjerno **ne** šalje na `vertical`: s njim widget prikaže
samo pet EAQI onečišćenja i izostavi ostale senzore (npr. CO).

## Postaje i parametri

| Postaja | Parametri |
|---|---|
| Trg Ivana Pavla II | NO2, O3, CO, SO2, PM10, PM2.5, temperatura, vlažnost, buka |
| Trg žrtava fašizma 8 | NO2, O3, CO, SO2, PM10, PM2.5, temperatura, vlažnost, buka |
| Kolodvorska ulica | CH3SH, SO2, NH3, H2S, temperatura, vlažnost, vjetar, oborine |

Kolodvorska je senzor za neugodne mirise, otud drugačiji set plinova.

## Pokretanje

```powershell
pip install -r requirements.txt
python scrape.py
```

Podaci se osvježavaju **jednom na sat**, a novo očitanje postaje dostupno
nekoliko minuta nakon punog sata. Češće pokretanje nema smisla.

## Podaci

- `data/YYYY/MM/YYYY-MM-DD.csv` — **mjerodavan zapis**, dnevne particije
- `data/kvaliteta_zraka.db` — SQLite za upite, *izveden* i negitan
- `data/measurements.csv` — objedinjeni izvoz (UTF-8 BOM, za Excel), izveden

Baza i objedinjeni CSV se u svakom trenutku obnavljaju iz particija:

```powershell
python build_db.py
```

Dnevne particije su namjerne: satni commit tada dira samo par kilobajta.
Jedna velika CSV datoteka — a pogotovo binarni `.db` — u satnom bi ritmu vrlo
brzo napuhala git povijest.

Shema je "dugačka" (jedan redak po parametru), jer postaje nemaju isti set
senzora:

| stupac | opis |
|---|---|
| `station_id`, `station_name` | identifikator i naziv postaje |
| `measured_at` | vrijeme mjerenja iz widgeta (`Ažurirano: …`), lokalno, ISO |
| `parameter` | npr. `NO2`, `PM2.5`, `Temperatura`, `EAQI kategorija` |
| `value` | brojčana vrijednost (prazno ako senzor nema podatak) |
| `text_value` | tekstualna vrijednost, koristi se za `EAQI kategorija` |
| `unit` | `µg/m³`, `°C`, `%`, `dB`, `mm` |
| `fetched_at` | kada je scraper dohvatio podatak, s vremenskom zonom |

Ključ je `(station_id, measured_at, parameter)`, pa je ponovno pokretanje
unutar istog sata bezopasno — zadržava se prvo očitanje za taj sat.

```sql
-- zadnjih 24 h PM2.5 po postaji
SELECT measured_at, station_name, value
FROM measurements
WHERE parameter = 'PM2.5'
ORDER BY measured_at DESC
LIMIT 72;
```

## Raspored

### U oblaku — GitHub Actions (besplatno)

[`.github/workflows/scrape.yml`](.github/workflows/scrape.yml) pokreće scraper
svaki sat i commita nova očitanja natrag u repo. Za **javni repozitorij**
GitHub Actions je besplatan bez ograničenja minuta; privatni ima 2 000
minuta/mjesec, što je dovoljno (~730 min/mjesec), ali bez rezerve.

```bash
git init && git add . && git commit -m "Scraper kvalitete zraka"
gh repo create KvalitetaZraka --public --source=. --push
```

Workflow traži samo `contents: write`; nikakvi tajni ključevi nisu potrebni.
Prvi put ga je zgodno pokrenuti ručno (*Actions → Kvaliteta zraka → Run
workflow*) da se potvrdi da prolazi.

Što treba znati:

- **Cron nije točan.** GitHub izričito navodi da se `schedule` zna odgoditi pri
  velikom opterećenju i da je početak punog sata vršno vrijeme — zato je
  raspored na `:20`. Poneko očitanje se zna preskočiti; rupa u satu nije bug.
- **Javni repo se gasi nakon 60 dana neaktivnosti.** Scheduled workflowi se u
  javnom repou automatski onemoguće ako 60 dana nema aktivnosti. Ovaj workflow
  commita svaki sat, pa u praksi ostaje živ; ako ga GitHub ipak onemogući,
  vraća se jednim klikom u *Actions*.
- **Vremenska zona.** Cron je UTC, ali `measured_at` dolazi iz widgeta kao
  lokalno vrijeme, pa ljetno/zimsko računanje ne utječe na podatke.

### Lokalno — Task Scheduler

```powershell
powershell -ExecutionPolicy Bypass -File register_task.ps1
```

Registrira zadatak `KvalitetaZraka-Zapresic` koji se vrti svaki sat u :20.
Uklanjanje:

```powershell
Unregister-ScheduledTask -TaskName 'KvalitetaZraka-Zapresic' -Confirm:$false
```

Lokalno i u oblaku se ne isplati voziti istovremeno — dobiti ćeš isti zapis na
dva mjesta koja se razilaze.

## Napomene

- Widget vraća blago različite vrijednosti između dva dohvata unutar istog
  sata (zaglađivanje na izvoru). Sprema se prvo očitanje po satu.
- Ako se `measured_at` ne uspije pročitati, postaja se preskače umjesto da se
  upiše sumnjiv redak.
- `WIDGET_KEY` u `scrape.py` je javni ključ ugrađen u stranicu Grada.
