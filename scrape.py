# -*- coding: utf-8 -*-
"""
Scraper za indikativno praćenje kvalitete zraka - Grad Zaprešić.

Izvor: https://zapresic.hr/indikativno-pracenje-kvalitete-zraka/
Stranica samo ugrađuje smart-airq.com widgete koji vraćaju SVG sliku;
mjerene vrijednosti su tekstualni čvorovi unutar tog SVG-a.

Za svaku postaju dohvaćaju se dva widgeta:
  * type=vertical     -> vrijeme mjerenja + sva onečišćenja + meteo/buka
  * type=aqi-details  -> opisna EAQI kategorija (Dobro, Prihvatljivo, ...)

Očitanja se dopisuju u dnevne CSV particije (data/YYYY/MM/YYYY-MM-DD.csv),
koje su mjerodavan zapis i jedino što se commita - dovoljno su male da satni
commit dira samo par kilobajta. Lokalno se uz to osvježava i SQLite
(data/kvaliteta_zraka.db) radi lakšeg upitovanja; on se ne commita i može se
u svakom trenutku obnoviti iz CSV-a pomoću build_db.py.

Zapisi su idempotentni po (station_id, measured_at, parameter), pa višestruko
pokretanje unutar istog sata ne stvara duplikate.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "kvaliteta_zraka.db"
LOG_PATH = DATA_DIR / "scrape.log"

CSV_COLUMNS = [
    "station_id", "station_name", "measured_at", "parameter",
    "value", "text_value", "unit", "fetched_at",
]

WIDGET_URL = "https://www.smart-airq.com/api/widget/gateway/{gateway}"
WIDGET_KEY = "7eb22aef7922389da3cd096e8cc6ecc5"

STATIONS = [
    {"id": "BpoVZnko4a", "name": "Trg Ivana Pavla II"},
    {"id": "y0wgpaembO", "name": "Trg žrtava fašizma 8"},
    {"id": "rWwjvJVwyR", "name": "Kolodvorska ulica"},
]

# class="<x>_icon" u SVG-u označava meteo/buka senzor koji nema tekstualnu oznaku
ICON_PARAMS = {
    "t_icon": "Temperatura",
    "h_icon": "Vlažnost",
    "sound_icon": "Buka",
    "wind_icon": "Vjetar",
    "rain_icon": "Oborine",
    "p_icon": "Tlak",
    "co2_icon": "CO2",
}

POLLUTANT_RE = re.compile(r"^[A-Z][A-Za-z0-9.]*$")
UPDATED_RE = re.compile(
    r"A[zž]urirano:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\.?\s*u\s*(\d{1,2}):(\d{2})"
)
FONT_RE = re.compile(r"src:\s*url\(data:[^)]*\)")
TOKEN_RE = re.compile(r'class="([a-z0-9_]+_icon)"|<text[^>]*>([^<]*)</text>')
VALUE_UNIT_RE = re.compile(r"^\s*(-{2,}|[-+]?\d+(?:[.,]\d+)?)\s*(.*)$")

log = logging.getLogger("kvaliteta-zraka")


def fetch_widget(
    gateway: str, widget_type: str, *, extra: dict | None = None,
    retries: int = 3, timeout: int = 30,
) -> str:
    """Dohvati SVG widget; ponovi uz eksponencijalni backoff."""
    params = {"type": widget_type, "key": WIDGET_KEY, "theme": "dark", "width": "300"}
    params.update(extra or {})
    url = WIDGET_URL.format(gateway=gateway)
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "kvaliteta-zraka-scraper/1.0"},
            )
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as err:  # mreža / HTTP / timeout
            last_err = err
            log.warning(
                "dohvat %s/%s pokušaj %d/%d nije uspio: %s",
                gateway, widget_type, attempt, retries, err,
            )
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"dohvat {gateway}/{widget_type} nije uspio: {last_err}")


def tokenize(svg: str) -> list[tuple[str, str]]:
    """SVG -> lista ('icon'|'text', vrijednost) u redoslijedu dokumenta."""
    svg = FONT_RE.sub("src: F", svg)  # ukloni ~80 kB base64 fontova
    tokens: list[tuple[str, str]] = []
    for m in TOKEN_RE.finditer(svg):
        if m.group(1):
            tokens.append(("icon", m.group(1)))
        else:
            text = m.group(2).strip()
            if text:
                tokens.append(("text", text))
    return tokens


def parse_number(raw: str) -> float | None:
    """'14.8' -> 14.8 ; '--' / '' -> None (senzor bez podatka)."""
    raw = raw.strip().replace(",", ".")
    if not raw or set(raw) <= {"-", "."}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def split_value_unit(raw):
    """'14.8 °C' -> (14.8, '°C') ; '14.8' -> (14.8, '') ; '--' -> (None, '')."""
    m = VALUE_UNIT_RE.match(raw)
    if not m:
        return None, raw.strip()
    return parse_number(m.group(1)), m.group(2).strip()


def parse_widget(svg):
    """Raščlani SVG widget: vrijeme mjerenja, očitanja, AQI indeks i kategorija.

    Radi za oba tipa widgeta; razlikuju se samo po tome dolaze li vrijednost
    i jedinica kao dva odvojena teksta ('13.2', '°C') ili kao jedan ('13.2 °C').
    """
    tokens = tokenize(svg)
    measured_at = None
    readings = []
    aqi_index = None
    aqi_category = None

    i = 0
    while i < len(tokens):
        kind, val = tokens[i]

        if kind == "text":
            m = UPDATED_RE.search(val)
            if m:
                d, mo, y, hh, mm = (int(x) for x in m.groups())
                measured_at = dt.datetime(y, mo, d, hh, mm).isoformat(timespec="minutes")
                i += 1
                continue

        # Ikona (meteo / buka / AQI): slijedi ju najviše dva tekstualna čvora.
        # Vjetar bez podatka nema jedinicu, pa se staje na sljedećoj ikoni.
        if kind == "icon":
            following = []
            j = i + 1
            while j < len(tokens) and tokens[j][0] == "text" and len(following) < 2:
                following.append(tokens[j][1])
                j += 1

            # aqi_icon nosi brojčani indeks pa kategoriju; eaqi_icon samo kategoriju
            if val.endswith("aqi_icon"):
                for text in following:
                    number = parse_number(text)
                    if number is not None:
                        aqi_index = number
                    elif text.strip():
                        aqi_category = text.strip()
            else:
                value, unit = split_value_unit(following[0]) if following else (None, "")
                if not unit and len(following) > 1:
                    unit = following[1].strip()
                readings.append({
                    "parameter": ICON_PARAMS.get(val, val[: -len("_icon")]),
                    "value": value,
                    "unit": unit,
                })
            i = j
            continue

        # Onečišćenje: oznaka, pa vrijednost, pa jedinica.
        if (
            kind == "text"
            and POLLUTANT_RE.match(val)
            and i + 2 < len(tokens)
            and tokens[i + 1][0] == "text"
            and tokens[i + 2][0] == "text"
        ):
            raw_value = tokens[i + 1][1].strip()
            value = parse_number(raw_value)
            if value is not None or raw_value == "--":
                readings.append({
                    "parameter": val,
                    "value": value,
                    "unit": tokens[i + 2][1].strip(),
                })
                i += 3
                continue

        i += 1

    return {
        "measured_at": measured_at,
        "readings": readings,
        "aqi_index": aqi_index,
        "aqi_category": aqi_category,
    }


def scrape_station(station: dict) -> list[dict]:
    fetched_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    # 'vertical' nosi vrijeme mjerenja i sve senzore (bez aqiType, inače
    # widget prikaže samo pet EAQI onečišćenja i izostavi npr. CO).
    vertical = parse_widget(fetch_widget(station["id"], "vertical"))
    measured_at = vertical["measured_at"]

    if not measured_at:
        log.warning("[%s] nema vremena mjerenja u widgetu - preskačem", station["name"])
        return []

    def row(parameter, value=None, text_value=None, unit=""):
        return {
            "station_id": station["id"],
            "station_name": station["name"],
            "measured_at": measured_at,
            "parameter": parameter,
            "value": value,
            "text_value": text_value,
            "unit": unit,
            "fetched_at": fetched_at,
        }

    rows = [row(r["parameter"], r["value"], unit=r["unit"]) for r in vertical["readings"]]

    # 'aqi-details' s aqiType=eaqi daje EAQI indeks i opisnu kategoriju,
    # onako kako su prikazani na stranici Grada.
    try:
        details = parse_widget(
            fetch_widget(station["id"], "aqi-details", extra={"aqiType": "eaqi"})
        )
    except Exception as err:
        log.warning("[%s] EAQI nedostupan: %s", station["name"], err)
        details = {"aqi_index": None, "aqi_category": None}

    if details["aqi_index"] is not None:
        rows.append(row("EAQI indeks", value=details["aqi_index"]))
    if details["aqi_category"]:
        rows.append(row("EAQI kategorija", text_value=details["aqi_category"]))

    log.info(
        "[%s] %s -> %d parametara%s",
        station["name"], measured_at, len(rows),
        f" (EAQI: {details['aqi_category']})" if details["aqi_category"] else "",
    )
    return rows


SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements (
    station_id   TEXT NOT NULL,
    station_name TEXT NOT NULL,
    measured_at  TEXT NOT NULL,
    parameter    TEXT NOT NULL,
    value        REAL,
    text_value   TEXT,
    unit         TEXT,
    fetched_at   TEXT NOT NULL,
    PRIMARY KEY (station_id, measured_at, parameter)
);
CREATE INDEX IF NOT EXISTS ix_measurements_time ON measurements (measured_at);
CREATE INDEX IF NOT EXISTS ix_measurements_param ON measurements (parameter, measured_at);
"""


def save(rows: list[dict]) -> int:
    """Upiši u SQLite; vrati broj stvarno novih redaka."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        before = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        conn.executemany(
            """INSERT INTO measurements
                 (station_id, station_name, measured_at, parameter,
                  value, text_value, unit, fetched_at)
               VALUES (:station_id, :station_name, :measured_at, :parameter,
                       :value, :text_value, :unit, :fetched_at)
               ON CONFLICT (station_id, measured_at, parameter) DO NOTHING""",
            rows,
        )
        after = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
    return after - before


def csv_path_for(measured_at: str) -> Path:
    """Dnevna particija, npr. data/2026/09/2026-09-11.csv.

    Male dnevne datoteke znače da svaki satni commit dira samo nekoliko
    kilobajta - jedna velika CSV datoteka (ili binarni .db) bi u satnom
    ritmu vrlo brzo napuhala git povijest.
    """
    day = measured_at[:10]
    return DATA_DIR / day[:4] / day[5:7] / f"{day}.csv"


def existing_keys(path: Path) -> set[tuple[str, str, str]]:
    """Već zapisani (station_id, measured_at, parameter) u toj particiji."""
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as fh:
        return {
            (r["station_id"], r["measured_at"], r["parameter"])
            for r in csv.DictReader(fh)
        }


def save_csv(rows: list[dict]) -> int:
    """Dopiši očitanja u dnevne CSV particije; vrati broj novih redaka."""
    by_day: dict[Path, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[csv_path_for(r["measured_at"])].append(r)

    written = 0
    for path, day_rows in by_day.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        seen = existing_keys(path)
        fresh = [
            r for r in day_rows
            if (r["station_id"], r["measured_at"], r["parameter"]) not in seen
        ]
        if not fresh:
            continue
        write_header = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as fh:
            # Izričit LF: particije moraju biti identične na Windowsu i na
            # Linux runneru, inače se u istoj datoteci pomiješaju CRLF i LF.
            w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
            if write_header:
                w.writeheader()
            w.writerows(fresh)
        written += len(fresh)
    return written


def setup_logging(verbose: bool) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=handlers,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape kvalitete zraka - Zaprešić")
    ap.add_argument("--no-db", action="store_true",
                    help="ne osvježavaj lokalni SQLite (koristi se u CI-ju)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)

    all_rows: list[dict] = []
    failed = 0
    for station in STATIONS:
        try:
            all_rows.extend(scrape_station(station))
        except Exception as err:
            failed += 1
            log.error("[%s] neuspjeh: %s", station["name"], err)

    if not all_rows:
        log.error("nijedna postaja nije vratila podatke")
        return 1

    new = save_csv(all_rows)
    if not args.no_db:
        save(all_rows)
    log.info(
        "gotovo: %d očitanja, %d novih redaka, %d postaja s greškom",
        len(all_rows), new, failed,
    )
    return 1 if failed == len(STATIONS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
