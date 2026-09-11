# -*- coding: utf-8 -*-
"""
Obnovi SQLite bazu i objedinjeni CSV iz dnevnih particija.

CSV particije (data/YYYY/MM/*.csv) su mjerodavan zapis; baza je izvedena i
može se u svakom trenutku baciti i ponovno izgraditi:

    python build_db.py
"""

from __future__ import annotations

import csv
import sqlite3

from scrape import CSV_COLUMNS, DATA_DIR, DB_PATH, SCHEMA

COMBINED_CSV = DATA_DIR / "measurements.csv"


def iter_rows():
    for path in sorted(DATA_DIR.glob("*/*/*.csv")):
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                # prazan string -> NULL, da brojčani stupci ostanu brojčani
                yield tuple(row[c] if row[c] != "" else None for c in CSV_COLUMNS)


def main() -> int:
    DB_PATH.unlink(missing_ok=True)
    rows = list(iter_rows())

    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            f"""INSERT INTO measurements ({", ".join(CSV_COLUMNS)})
                VALUES ({", ".join("?" * len(CSV_COLUMNS))})
                ON CONFLICT (station_id, measured_at, parameter) DO NOTHING""",
            rows,
        )
        total = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]

        # objedinjeni izvoz s BOM-om, da se uredno otvori u Excelu
        cur = conn.execute(
            f"""SELECT {", ".join(CSV_COLUMNS)} FROM measurements
                ORDER BY measured_at, station_name, parameter"""
        )
        with open(COMBINED_CSV, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(CSV_COLUMNS)
            w.writerows(cur)

    print(f"{DB_PATH.name}: {total} redaka (iz {len(rows)} pročitanih)")
    print(f"{COMBINED_CSV.name}: objedinjeni izvoz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
