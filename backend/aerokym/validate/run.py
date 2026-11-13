"""Daily validation: compare stored detector windows with pilot reports.

    python -m aerokym.validate.run                      # yesterday (UTC), validation box
    python -m aerokym.validate.run --date 2026-11-11
    python -m aerokym.validate.run --loop 3600          # live: refresh today and yesterday
    python -m aerokym.validate.run --date 2026-11-11 --pireps-csv pireps.csv
        # backfill: first load that day's reports from an Iowa State IEM archive CSV

What is measured, per UTC day and region:
  * every cruise-level PIREP with a turbulence remark is matched to the
    detector windows of any aircraft within 50 km, 1000 ft and 10 minutes;
  * the report's score is the mean roughness index of those windows (what a
    map cell would show);
  * "we said moderate or greater" = score >= MODERATE_INDEX, compared with
    whether the pilot said moderate or worse.

Reports with no aircraft nearby cannot be scored and are only counted.
"""

from __future__ import annotations

import argparse
import hashlib
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import psycopg

from aerokym.config import get_settings
from aerokym.detect.classify import METHOD_VERSION, MODERATE_INDEX
from aerokym.validate.pirep_match import (
    MatchConfig,
    Pirep,
    auc,
    load_iem_csv,
    match_indices,
    parse_intensity,
)

MATCH = MatchConfig(radius_km=50, alt_tol_ft=1000, time_tol_s=600)
MIN_PIREP_ALT_FT = 20000
MIN_FOR_AUC = 10   # smooth and moderate+ reports each, before an AUC means anything
DEFAULT_REGION = "us-central"
BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class DayScore:
    n_pireps: int          # cruise turbulence reports in the region
    n_matched: int         # of those, with a detector window nearby
    n_smooth: int
    n_light: int
    n_moderate_plus: int
    tp: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    auc: float | None


def ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def score_day(
    pireps: list[Pirep], t: np.ndarray, lat: np.ndarray, lon: np.ndarray, alt: np.ndarray,
    index: np.ndarray, threshold: float = MODERATE_INDEX, cfg: MatchConfig = MATCH,
) -> DayScore:
    """Pure scoring: reports and window arrays in, counts out."""
    scores, levels = [], []
    for p in pireps:
        idx = match_indices(p, t, lat, lon, alt, cfg)
        if len(idx):
            scores.append(float(np.mean(index[idx])))
            levels.append(p.intensity)
    s, y = np.array(scores), np.array(levels, dtype=int)
    said, was = s >= threshold, y >= 2
    tp, fp, fn = int(np.sum(said & was)), int(np.sum(said & ~was)), int(np.sum(~said & was))
    n_smooth, n_mod = int(np.sum(y == 0)), int(np.sum(was))
    a = auc(s[y == 0], s[was]) if min(n_smooth, n_mod) >= MIN_FOR_AUC else None
    return DayScore(
        n_pireps=len(pireps), n_matched=len(s), n_smooth=n_smooth,
        n_light=int(np.sum(y == 1)), n_moderate_plus=n_mod, tp=tp, fp=fp, fn=fn,
        precision=ratio(tp, tp + fp), recall=ratio(tp, tp + fn),
        auc=None if a is None else round(a, 4),
    )


# Database side -------------------------------------------------------------------

def load_pireps(conn: psycopg.Connection, day: date, box: BBox) -> list[Pirep]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    rows = conn.execute(
        "SELECT extract(epoch FROM obs_time), ST_Y(geom), ST_X(geom), alt_ft, turb_intensity, raw "
        "FROM pireps WHERE obs_time >= %s AND obs_time < %s AND alt_ft >= %s "
        "AND ST_Y(geom) BETWEEN %s AND %s AND ST_X(geom) BETWEEN %s AND %s",
        (start, start + timedelta(days=1), MIN_PIREP_ALT_FT, box[0], box[2], box[1], box[3]),
    ).fetchall()
    out = []
    for t, lat, lon, alt, turb, raw in rows:
        level = parse_intensity(turb)
        if level is not None:   # a report that does not mention turbulence says nothing
            out.append(Pirep(float(t), lat, lon, float(alt), level, raw or ""))
    return out


def load_windows(conn: psycopg.Connection, day: date, box: BBox, method: str):
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    pad = timedelta(seconds=MATCH.time_tol_s)
    rows = conn.execute(
        "SELECT extract(epoch FROM time), ST_Y(geom), ST_X(geom), alt_ft, roughness_index, icao24 "
        "FROM turb_events WHERE method_version = %s AND time >= %s AND time < %s "
        "AND ST_Y(geom) BETWEEN %s AND %s AND ST_X(geom) BETWEEN %s AND %s",
        (method, start - pad, start + timedelta(days=1) + pad,
         box[0] - 1, box[2] + 1, box[1] - 1, box[3] + 1),
    ).fetchall()
    a = np.array([r[:5] for r in rows], dtype=float).reshape(-1, 5)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], len({r[5] for r in rows})


def insert_iem_pireps(conn: psycopg.Connection, path: Path) -> int:
    """Backfill the pireps table from an IEM archive CSV (same parser as calibration)."""
    rows = []
    for p in load_iem_csv(path):
        pid = "iem-" + hashlib.sha1(f"{p.t}|{p.raw}".encode()).hexdigest()[:16]
        label = {0: "NEG", 1: "LGT", 2: "MOD", 3: "SEV"}[p.intensity]
        rows.append((pid, datetime.fromtimestamp(p.t, tz=UTC), p.lon, p.lat, int(p.alt_ft),
                     label, p.raw))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO pireps (id, obs_time, geom, alt_ft, turb_intensity, raw) VALUES "
            "(%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING", rows)
    conn.commit()
    return len(rows)


def validate_day(conn: psycopg.Connection, day: date, region: str, box: BBox,
                 method: str = METHOD_VERSION) -> DayScore:
    pireps = load_pireps(conn, day, box)
    t, lat, lon, alt, index, n_aircraft = load_windows(conn, day, box, method)
    score = score_day(pireps, t, lat, lon, alt, index)
    d = asdict(score)
    conn.execute(
        "INSERT INTO validation_runs (run_date, region, method_version, precision, recall, "
        "n_pireps, n_events, n_matched, n_smooth, n_light, n_moderate_plus, tp, fp, fn, auc, "
        "n_aircraft, match_config, computed_at) VALUES (%(day)s, %(region)s, %(method)s, "
        "%(precision)s, %(recall)s, %(n_pireps)s, %(n_events)s, %(n_matched)s, %(n_smooth)s, "
        "%(n_light)s, %(n_moderate_plus)s, %(tp)s, %(fp)s, %(fn)s, %(auc)s, %(n_aircraft)s, "
        "%(match)s, now()) ON CONFLICT (run_date, region, method_version) DO UPDATE SET "
        "precision = EXCLUDED.precision, recall = EXCLUDED.recall, n_pireps = EXCLUDED.n_pireps, "
        "n_events = EXCLUDED.n_events, n_matched = EXCLUDED.n_matched, "
        "n_smooth = EXCLUDED.n_smooth, n_light = EXCLUDED.n_light, "
        "n_moderate_plus = EXCLUDED.n_moderate_plus, tp = EXCLUDED.tp, fp = EXCLUDED.fp, "
        "fn = EXCLUDED.fn, auc = EXCLUDED.auc, n_aircraft = EXCLUDED.n_aircraft, "
        "match_config = EXCLUDED.match_config, computed_at = now()",
        {**d, "day": day, "region": region, "method": method, "n_events": len(t),
         "n_aircraft": n_aircraft,
         "match": f"{MATCH.radius_km:.0f} km / {MATCH.alt_tol_ft:.0f} ft / "
                  f"{MATCH.time_tol_s / 60:.0f} min"})
    conn.commit()
    return score


def main() -> None:
    from aerokym.db.migrate import connect_with_retry

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", type=date.fromisoformat, help="UTC day (default: yesterday)")
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument("--bbox", help="lamin,lomin,lamax,lomax (default: AEROKYM_VALIDATION_BBOX)")
    ap.add_argument("--pireps-csv", type=Path, help="load an IEM PIREP archive CSV first")
    ap.add_argument("--loop", type=float, default=0.0,
                    help="repeat every N seconds, refreshing today and yesterday")
    a = ap.parse_args()
    s = get_settings()
    box = tuple(float(x) for x in a.bbox.split(",")) if a.bbox else s.validation_bbox
    if box is None:
        raise SystemExit("no validation box: set AEROKYM_VALIDATION_BBOX or pass --bbox")

    with connect_with_retry(s.database_url) as conn:
        if a.pireps_csv:
            print(f"validate: loaded {insert_iem_pireps(conn, a.pireps_csv)} reports from CSV")
        while True:
            t0 = time.time()
            today = datetime.now(UTC).date()
            days = [a.date] if a.date else [today - timedelta(days=1)]
            if a.loop > 0 and not a.date:
                days.append(today)   # today's row grows through the day
            for day in days:
                sc = validate_day(conn, day, a.region, box)  # type: ignore[arg-type]
                print(f"validate: {day} {a.region}: {sc.n_pireps} reports, {sc.n_matched} matched, "
                      f"tp {sc.tp} fp {sc.fp} fn {sc.fn}, precision {sc.precision}, "
                      f"recall {sc.recall}, auc {sc.auc}")
            if a.loop <= 0:
                break
            time.sleep(max(0.0, a.loop - (time.time() - t0)))


if __name__ == "__main__":
    main()
