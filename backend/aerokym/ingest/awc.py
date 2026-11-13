"""Official aviation weather from the aviationweather.gov Data API.

Four requests per cycle (METAR, TAF, international SIGMET, PIREP), every ten
minutes by default: far below the API's limits. No key is needed and the data
is US-government public domain.

    python -m aerokym.ingest.awc                    # fetch once and store
    python -m aerokym.ingest.awc --loop 600         # keep refreshing (live)
    python -m aerokym.ingest.awc --save FILE        # fetch and write a snapshot file
    python -m aerokym.ingest.awc --load FILE --shift-to-now --reset    # demo, offline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from psycopg.types.json import Jsonb

from aerokym.config import get_settings
from aerokym.weather.decode import ceiling_ft, flight_category, visibility_km

BASE = "https://aviationweather.gov/api/data"
USER_AGENT = "aerokym/0.1 (+https://github.com/aryanpol73/aerokym)"
KEEP_DAYS = 7
SM_PER_KM = 1 / 1.609344


def ts(epoch) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=UTC)


def fetch_snapshot(bbox: tuple[float, float, float, float],
                   http: httpx.Client | None = None,
                   pirep_bbox: tuple[float, float, float, float] | None = None) -> dict:
    """One fetch of everything, as plain JSON (also the demo snapshot format).

    `pirep_bbox` adds pilot reports from a second box: the validation region,
    where reports are dense enough to measure the detector against.
    """
    http = http or httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT})
    box = ",".join(str(x) for x in bbox)

    def get(path: str, **params) -> list:
        r = http.get(f"{BASE}/{path}", params={"format": "json", **params})
        r.raise_for_status()
        return r.json() if r.content.strip() else []  # "no data" is an empty body

    return {
        "fetched_at": time.time(),
        "metars": get("metar", bbox=box),
        "tafs": get("taf", bbox=box),
        "sigmets": get("isigmet"),
        "pireps": get("pirep", bbox=box, age=3) + (
            get("pirep", bbox=",".join(str(x) for x in pirep_bbox), age=3) if pirep_bbox else []),
    }


def shift_snapshot(snap: dict, now: float | None = None) -> dict:
    """Move every timestamp so the snapshot looks as if it was fetched now."""
    delta = int((now or time.time()) - snap["fetched_at"])

    def bump(obj: dict, *keys: str) -> None:
        for k in keys:
            if isinstance(obj.get(k), (int, float)):
                obj[k] += delta

    for m in snap["metars"]:
        bump(m, "obsTime")
    for t in snap["tafs"]:
        bump(t, "validTimeFrom", "validTimeTo")
        t["issueEpoch"] = _issue_epoch(t) + delta
        for f in t.get("fcsts") or []:
            bump(f, "timeFrom", "timeTo", "timeBec")
    for s in snap["sigmets"]:
        bump(s, "validTimeFrom", "validTimeTo")
    for p in snap["pireps"]:
        bump(p, "obsTime")
    snap["fetched_at"] += delta
    return snap


def _issue_epoch(t: dict) -> float:
    if "issueEpoch" in t:
        return t["issueEpoch"]
    issue = t.get("issueTime")
    if issue:
        return datetime.fromisoformat(issue).timestamp()
    return float(t["validTimeFrom"])


# Row builders (pure) ------------------------------------------------------------

def metar_row(m: dict) -> tuple | None:
    if not m.get("icaoId") or m.get("obsTime") is None:
        return None
    km, _ = visibility_km(m.get("visib"))
    wdir = m.get("wdir")
    return (
        m["icaoId"], ts(m["obsTime"]), m.get("rawOb") or "",
        int(wdir) if isinstance(wdir, (int, float)) else None,
        m.get("wspd"), m.get("wgst"),
        None if km is None else round(km * SM_PER_KM, 2),
        ceiling_ft(m.get("clouds")),
        m.get("fltCat") or flight_category(m.get("visib"), m.get("clouds")),
        Jsonb(m),
    )


def taf_row(t: dict) -> tuple | None:
    if not t.get("icaoId") or t.get("validTimeFrom") is None:
        return None
    return (t["icaoId"], ts(_issue_epoch(t)), ts(t["validTimeFrom"]), ts(t["validTimeTo"]),
            t.get("rawTAF") or "", Jsonb(t))


def _ring(coords: list) -> str | None:
    pts = [(c["lon"], c["lat"]) for c in coords
           if isinstance(c, dict) and isinstance(c.get("lon"), (int, float))
           and isinstance(c.get("lat"), (int, float))]
    if len(pts) < 3:
        return None
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return "((" + ", ".join(f"{x} {y}" for x, y in pts) + "))"


def polygon_wkt(coords: list | None) -> str | None:
    """WKT for a SIGMET outline.

    `coords` is normally one list of points; a SIGMET covering several separate
    areas has a list of such lists, which becomes a MULTIPOLYGON.
    """
    if not coords:
        return None
    if isinstance(coords[0], list):
        rings = [r for r in (_ring(c) for c in coords) if r]
        if not rings:
            return None
        return "POLYGON" + rings[0] if len(rings) == 1 else "MULTIPOLYGON(" + ", ".join(rings) + ")"
    ring = _ring(coords)
    return "POLYGON" + ring if ring else None


def sigmet_row(s: dict) -> tuple | None:
    wkt = polygon_wkt(s.get("coords"))
    if wkt is None or s.get("validTimeFrom") is None or s.get("validTimeTo") is None:
        return None
    sid = f"{s.get('icaoId')}-{s.get('seriesId')}-{int(s['validTimeFrom'])}"

    def alt(v):
        return int(v) if isinstance(v, (int, float)) else None

    return (sid, ts(s["validTimeFrom"]), ts(s["validTimeTo"]), s.get("hazard"), wkt,
            alt(s.get("base")), alt(s.get("top")), s.get("rawSigmet"), s.get("firName"),
            s.get("qualifier"))


def pirep_row(p: dict) -> tuple | None:
    if p.get("obsTime") is None or p.get("lat") is None or p.get("lon") is None:
        return None
    raw = p.get("rawOb") or ""
    pid = hashlib.sha1(f"{p['obsTime']}|{raw}".encode()).hexdigest()[:16]
    lvl = p.get("fltLvl")
    return (pid, ts(p["obsTime"]), p["lon"], p["lat"],
            int(lvl) * 100 if isinstance(lvl, (int, float)) and lvl > 0 else None,
            p.get("tbInt1") or None, p.get("tbType1") or None, raw, p.get("acType") or None)


def _rows(items: list, builder) -> list[tuple]:
    return [r for r in (builder(x) for x in items) if r is not None]


def store(conn: psycopg.Connection, snap: dict) -> dict[str, int]:
    metars, tafs = _rows(snap["metars"], metar_row), _rows(snap["tafs"], taf_row)
    sigmets, pireps = _rows(snap["sigmets"], sigmet_row), _rows(snap["pireps"], pirep_row)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO metars (station, obs_time, raw, wind_dir, wind_kt, gust_kt, vis_sm, "
            "ceiling_ft, flight_cat, data) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (station, obs_time) DO NOTHING", metars)
        cur.executemany(
            "INSERT INTO tafs (station, issue_time, valid_from, valid_to, raw, data) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (station, issue_time) DO NOTHING", tafs)
        cur.executemany(
            "INSERT INTO sigmets (id, valid_from, valid_to, hazard, geom, alt_lo_ft, alt_hi_ft, "
            "raw, fir, qualifier) VALUES (%s,%s,%s,%s, ST_CollectionExtract(ST_MakeValid(ST_GeomFromText(%s, 4326)), 3), "
            "%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING", sigmets)
        cur.executemany(
            "INSERT INTO pireps (id, obs_time, geom, alt_ft, turb_intensity, turb_type, raw, "
            "aircraft_type) VALUES (%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326), %s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO NOTHING", pireps)
        # Retention: weather is only interesting for a few days.
        cur.execute("DELETE FROM metars WHERE obs_time < now() - make_interval(days => %s)",
                    (KEEP_DAYS,))
        cur.execute("DELETE FROM tafs WHERE valid_to < now() - make_interval(days => %s)",
                    (KEEP_DAYS,))
        cur.execute("DELETE FROM sigmets WHERE valid_to < now() - make_interval(days => %s)",
                    (KEEP_DAYS,))
    conn.commit()
    return {"metars": len(metars), "tafs": len(tafs), "sigmets": len(sigmets),
            "pireps": len(pireps)}


def main() -> None:
    from aerokym.db.migrate import connect_with_retry

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--loop", type=float, default=0.0, help="repeat every N seconds")
    ap.add_argument("--save", type=Path, help="write the fetched snapshot to this file and exit")
    ap.add_argument("--load", type=Path, help="store a saved snapshot instead of fetching")
    ap.add_argument("--shift-to-now", action="store_true",
                    help="with --load: re-time the snapshot so it looks current")
    ap.add_argument("--reset", action="store_true",
                    help="with --load: empty the weather tables first. Demo mode re-times the "
                         "snapshot on every start, so without this each restart adds a copy.")
    a = ap.parse_args()
    s = get_settings()

    if a.save:
        snap = fetch_snapshot(s.opensky_bbox)
        a.save.write_text(json.dumps(snap, separators=(",", ":")), encoding="utf-8")
        print("awc: saved", {k: len(v) for k, v in snap.items() if isinstance(v, list)})
        return

    with connect_with_retry(s.database_url) as conn:
        if a.load:
            if a.reset:
                conn.execute("TRUNCATE metars, tafs, sigmets, pireps")
                conn.commit()
            snap = json.loads(a.load.read_text(encoding="utf-8"))
            print("awc: loaded snapshot", store(conn, shift_snapshot(snap) if a.shift_to_now
                                                else snap))
            return
        while True:
            t0 = time.time()
            try:
                print("awc:", store(conn, fetch_snapshot(s.opensky_bbox,
                                                         pirep_bbox=s.validation_bbox)))
            except (httpx.HTTPError, ValueError) as e:
                # Weather is not critical: skip this cycle and try again next time.
                conn.rollback()
                print(f"awc: error {type(e).__name__}: {e}")
            if a.loop <= 0:
                break
            time.sleep(max(0.0, a.loop - (time.time() - t0)))


if __name__ == "__main__":
    main()
