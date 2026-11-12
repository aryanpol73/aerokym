"""Run the detector over positions in the database and store the windows.

    python -m aerokym.detect.run                 # last 24 h, once
    python -m aerokym.detect.run --loop 120      # repeat every 2 minutes (live)

Each run recomputes the whole look-back period. That is deliberate: an
aircraft's baseline needs its whole flight, so earlier windows can change a
little as more of the flight arrives. Rows are upserted, so re-running is safe.

Note: the detector needs samples about every 20 s. OpenSky snapshots (one per
~96 s) produce no windows; only replayed traces or a denser live feed do.
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime, timedelta

import h3
import numpy as np
import psycopg

from aerokym.config import get_settings
from aerokym.db.migrate import connect_with_retry
from aerokym.detect.classify import Detection, detect
from aerokym.detect.segments import Track

EVENT_H3_RES = 5
MIN_POSITIONS = 20


def load_track(conn: psycopg.Connection, icao24: str, since: datetime) -> Track:
    rows = conn.execute(
        "SELECT extract(epoch FROM time), lat, lon, baro_alt_ft, baro_rate_fpm, geom_rate_fpm "
        "FROM positions WHERE icao24 = %s AND time > %s AND NOT on_ground ORDER BY time",
        (icao24, since),
    ).fetchall()
    a = np.array(rows, dtype=float).reshape(-1, 6)  # None becomes NaN
    return Track(icao24, a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])


def event_row(d: Detection) -> tuple:
    f = d.features
    return (
        f.icao24,
        datetime.fromtimestamp(f.t_mid, tz=UTC),
        f.lon,
        f.lat,
        round(f.alt_ft),
        h3.latlng_to_cell(f.lat, f.lon, EVENT_H3_RES),
        d.index,
        int(d.severity),
        d.confidence,
        f.median_dt_s,
        f.n,
        d.method_version,
    )


def store(conn: psycopg.Connection, detections: list[Detection]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO turb_events (icao24, time, geom, alt_ft, h3_cell, roughness_index, "
            "severity, confidence, sample_dt_s, n_samples, method_version) "
            "VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s::h3index, %s, %s, %s, "
            "%s, %s, %s) "
            "ON CONFLICT (icao24, time, method_version) DO UPDATE SET "
            "roughness_index = EXCLUDED.roughness_index, severity = EXCLUDED.severity, "
            "confidence = EXCLUDED.confidence",
            [event_row(d) for d in detections],
        )
    conn.commit()


def run_once(conn: psycopg.Connection, hours: float) -> tuple[int, int]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    icaos = [r[0] for r in conn.execute(
        "SELECT icao24 FROM positions WHERE time > %s AND baro_alt_ft >= 20000 "
        "GROUP BY icao24 HAVING count(*) >= %s", (since, MIN_POSITIONS))]
    n_events = 0
    for icao in icaos:
        detections = detect(load_track(conn, icao, since))
        if detections:
            store(conn, detections)
            n_events += len(detections)
    return len(icaos), n_events


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hours", type=float, default=24.0, help="look-back period")
    ap.add_argument("--loop", type=float, default=0.0, help="repeat every N seconds (0 = once)")
    a = ap.parse_args()
    with connect_with_retry(get_settings().database_url) as conn:
        while True:
            t0 = time.time()
            n_ac, n_ev = run_once(conn, a.hours)
            print(f"detect: {n_ac} aircraft, {n_ev} windows stored in {time.time() - t0:.1f}s")
            if a.loop <= 0:
                break
            time.sleep(max(0.0, a.loop - (time.time() - t0)))


if __name__ == "__main__":
    main()
