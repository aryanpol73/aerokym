"""Airports and runways from OurAirports (public domain).

The repository bundles a small subset (data/reference/) so the demo works
offline: medium and large airports inside the India box. Refresh it with:

    python -m aerokym.ingest.ourairports build airports.csv runways.csv

(download the two full files from https://ourairports.com/data/ first), and
load the bundled subset into the database with:

    python -m aerokym.ingest.ourairports load
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

# In Docker the repo's data/ folder is mounted at /data (see docker-compose.yml).
REFERENCE_DIR = Path(os.environ.get("AEROKYM_REFERENCE_DIR")
                     or Path(__file__).resolve().parents[3] / "data" / "reference")
INDIA_BOX = (6.0, 68.0, 36.0, 98.0)  # lamin, lomin, lamax, lomax
KEEP_TYPES = {"large_airport", "medium_airport"}
AIRPORT_FIELDS = ["ident", "type", "name", "latitude_deg", "longitude_deg", "elevation_ft",
                  "iso_country", "municipality", "iata_code"]
RUNWAY_FIELDS = ["id", "airport_ident", "length_ft", "surface", "closed", "le_ident",
                 "le_heading_degT", "he_ident", "he_heading_degT"]


def _is_icao(ident: str) -> bool:
    return len(ident) == 4 and ident.isalpha() and ident.isupper()


def build_subset(airports_csv: Path, runways_csv: Path, out_dir: Path = REFERENCE_DIR,
                 box: tuple[float, float, float, float] = INDIA_BOX) -> tuple[int, int]:
    """Write the trimmed airports.csv and runways.csv; returns the row counts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    keep: set[str] = set()
    with airports_csv.open(encoding="utf-8", newline="") as f, \
            (out_dir / "airports.csv").open("w", encoding="utf-8", newline="") as o:
        w = csv.DictWriter(o, AIRPORT_FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in csv.DictReader(f):
            try:
                lat, lon = float(r["latitude_deg"]), float(r["longitude_deg"])
            except ValueError:
                continue
            if (r["type"] in KEEP_TYPES and _is_icao(r["ident"])
                    and box[0] <= lat <= box[2] and box[1] <= lon <= box[3]):
                keep.add(r["ident"])
                w.writerow(r)
    n_rw = 0
    with runways_csv.open(encoding="utf-8", newline="") as f, \
            (out_dir / "runways.csv").open("w", encoding="utf-8", newline="") as o:
        w = csv.DictWriter(o, RUNWAY_FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in csv.DictReader(f):
            if r["airport_ident"] in keep and r.get("closed") != "1":
                w.writerow(r)
                n_rw += 1
    return len(keep), n_rw


def _num(v: str | None, cast=float):
    try:
        return cast(float(v)) if v not in (None, "") else None
    except ValueError:
        return None


def read_reference(ref_dir: Path = REFERENCE_DIR) -> tuple[list[tuple], list[tuple]]:
    """Rows ready for the `airports` and `runways` tables."""
    with (ref_dir / "airports.csv").open(encoding="utf-8", newline="") as f:
        airports = [(r["ident"], r["name"], float(r["longitude_deg"]), float(r["latitude_deg"]),
                     _num(r["elevation_ft"], int), r["iso_country"], r["municipality"] or None,
                     r["iata_code"] or None, r["type"]) for r in csv.DictReader(f)]
    with (ref_dir / "runways.csv").open(encoding="utf-8", newline="") as f:
        runways = [(int(r["id"]), r["airport_ident"], r["le_ident"] or None, r["he_ident"] or None,
                    _num(r["le_heading_degT"]), _num(r["he_heading_degT"]),
                    _num(r["length_ft"], int), r["surface"] or None) for r in csv.DictReader(f)]
    return airports, runways


def load(conn, ref_dir: Path = REFERENCE_DIR) -> tuple[int, int]:
    airports, runways = read_reference(ref_dir)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO airports (icao, name, geom, elevation_ft, country, city, iata, kind) "
            "VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s) "
            "ON CONFLICT (icao) DO UPDATE SET name = EXCLUDED.name, geom = EXCLUDED.geom, "
            "elevation_ft = EXCLUDED.elevation_ft, city = EXCLUDED.city, iata = EXCLUDED.iata, "
            "kind = EXCLUDED.kind", airports)
        cur.executemany(
            "INSERT INTO runways (id, airport_icao, le_ident, he_ident, le_heading_deg, "
            "he_heading_deg, length_ft, surface) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET le_heading_deg = EXCLUDED.le_heading_deg, "
            "he_heading_deg = EXCLUDED.he_heading_deg, length_ft = EXCLUDED.length_ft", runways)
    conn.commit()
    return len(airports), len(runways)


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "build":
        n_ap, n_rw = build_subset(Path(sys.argv[2]), Path(sys.argv[3]))
        print(f"wrote {n_ap} airports and {n_rw} runways to {REFERENCE_DIR}")
    elif len(sys.argv) == 2 and sys.argv[1] == "load":
        from aerokym.config import get_settings
        from aerokym.db.migrate import connect_with_retry

        with connect_with_retry(get_settings().database_url) as conn:
            n_ap, n_rw = load(conn)
        print(f"airports: loaded {n_ap} airports and {n_rw} runways")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
