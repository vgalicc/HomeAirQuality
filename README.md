# Kvaliteta zraka

Scraper koji čita mjerenja kvalitete zraka iz `smart-airq.com` SVG widgeta i sprema ih u dnevne CSV particije (`data/YYYY/MM/…`).

## Pokretanje
```powershell
pip install -r requirements.txt
python scrape.py
python build_db.py
```
