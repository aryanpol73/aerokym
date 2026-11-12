"""M0 data spike: how often does each public source really update an aircraft?

The turbulence detector needs a dense vertical-rate time series per aircraft.
This script measures what the free sources actually deliver, so the detection
method is chosen from numbers instead of hope.

Usage (from repo root):
    python backend/spikes/m0_sampling.py collect   # ~20 min of polite polling
    python backend/spikes/m0_sampling.py analyze   # print the tables

Raw responses go to data/spike/ (git-ignored). Only stdlib + httpx needed.

What we measure per aircraft in cruise (>= FL200):
  * position age  = `seen_pos` (seconds since the feed last heard a position)
  * update interval = gap between *distinct* position timestamps we observed.
    Position timestamp = response `now` - `seen_pos`. If we poll every 5 s and
    the feed is fresh, this is ~5 s: the poll rate is the limit. If coverage is
    thin, it is larger: the receivers are the limit.
  * how often vertical rate (baro_rate / geom_rate) is present at all.
"""

from __future__ import annotations

import itertools
import json
import statistics as st
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[2] / "data" / "spike"
UA = {"User-Agent": "aerokym-m0-spike/0.1 (open-source research; low volume)"}

# adsb.lol /v2/point takes a centre and a radius of at most 250 nm.
ADSBLOL_REGIONS = {
    "india_west_mumbai_pune": (19.0, 73.5),
    "india_north_delhi": (28.5, 77.1),
    "india_south_bengaluru": (13.0, 77.6),
    "us_central_kansas": (39.0, -95.0),
}
# OpenSky bounding boxes: lamin, lomin, lamax, lomax
OPENSKY_BOXES = {
    "india": (6.0, 68.0, 36.0, 98.0),
    "us_central": (34.0, -102.0, 44.0, -88.0),
}
CRUISE_FT = 20000


def poll_adsblol(client: httpx.Client, name: str, interval: float, n: int) -> None:
    lat, lon = ADSBLOL_REGIONS[name]
    url = f"https://api.adsb.lol/v2/point/{lat}/{lon}/250"
    path = OUT / f"adsblol_{name}_{int(interval)}s.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for i in range(n):
            t0 = time.time()
            try:
                r = client.get(url, timeout=20)
                rec = {"t": t0, "status": r.status_code, "elapsed": r.elapsed.total_seconds()}
                if i == 0:
                    rec["headers"] = dict(r.headers)
                if r.status_code == 200:
                    body = r.json()
                    rec["now"] = body.get("now")
                    keep = ("hex", "alt_baro", "alt_geom", "baro_rate", "geom_rate",
                            "lat", "lon", "seen_pos", "seen", "type", "gs")
                    rec["ac"] = [{k: a[k] for k in keep if k in a} for a in body.get("ac", [])]
                else:
                    # Never hammer a source that says no: record why and stop.
                    rec["headers"] = dict(r.headers)
                    rec["body"] = r.text[:300]
                    f.write(json.dumps(rec) + "\n")
                    print(f"  HTTP {r.status_code} after {i} polls, stopping {name}")
                    break
            except httpx.HTTPError as e:
                rec = {"t": t0, "status": -1, "error": repr(e)}
            f.write(json.dumps(rec) + "\n")
            f.flush()
            time.sleep(max(0.0, interval - (time.time() - t0)))
    print(f"  wrote {path.name}")


def poll_opensky(client: httpx.Client, name: str, interval: float, n: int) -> None:
    lamin, lomin, lamax, lomax = OPENSKY_BOXES[name]
    url = "https://opensky-network.org/api/states/all"
    params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}
    path = OUT / f"opensky_{name}_{int(interval)}s.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for i in range(n):
            t0 = time.time()
            try:
                r = client.get(url, params=params, timeout=30)
                rec = {"t": t0, "status": r.status_code}
                if i == 0:
                    rec["headers"] = dict(r.headers)
                if r.status_code == 200:
                    body = r.json()
                    rec["time"] = body.get("time")
                    # state vector indices: 0 icao24, 3 time_position, 4 last_contact,
                    # 7 baro_altitude (m), 11 vertical_rate (m/s)
                    rec["ac"] = [[s[0], s[3], s[4], s[7], s[11]] for s in (body.get("states") or [])]
                else:
                    rec["body"] = r.text[:300]
            except httpx.HTTPError as e:
                rec = {"t": t0, "status": -1, "error": repr(e)}
            f.write(json.dumps(rec) + "\n")
            f.flush()
            time.sleep(max(0.0, interval - (time.time() - t0)))
    print(f"  wrote {path.name}")


def collect() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers=UA) as c:
        # The unkeyed adsb.lol API allows 500 requests/day plus a short burst
        # limit (first run: HTTP 429 after 13 polls at 5 s). So we poll gently:
        # 20 polls @ 10 s per region, and stop a region on the first refusal.
        for name in ADSBLOL_REGIONS:
            print(f"adsb.lol {name}: 20 polls @ 10 s")
            poll_adsblol(c, name, 10, 20)
            time.sleep(60)
        for name in OPENSKY_BOXES:
            print(f"OpenSky {name}: 8 polls @ 10 s (anonymous)")
            poll_opensky(c, name, 10, 8)


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def analyze_adsblol(path: Path) -> None:
    recs = [json.loads(line) for line in path.open(encoding="utf-8")]
    ok = [r for r in recs if r.get("status") == 200]
    print(f"\n== {path.stem}: {len(ok)}/{len(recs)} OK, "
          f"statuses={sorted({r['status'] for r in recs})}")
    if not ok:
        return
    hdr = recs[0].get("headers", {})
    rl = {k: v for k, v in hdr.items() if "rate" in k.lower() or "retry" in k.lower()}
    print(f"   rate-limit headers: {rl or 'none'};  median latency "
          f"{st.median(r['elapsed'] for r in ok):.2f}s")

    stamps: dict[str, list[float]] = defaultdict(list)   # hex -> position timestamps
    ages, n_all, n_cruise, has_baro, has_geom, rows = [], [], [], 0, 0, 0
    for r in ok:
        now = r["now"] / 1000.0
        cruise = [a for a in r["ac"] if isinstance(a.get("alt_baro"), (int, float))
                  and a["alt_baro"] >= CRUISE_FT and "seen_pos" in a]
        n_all.append(len(r["ac"]))
        n_cruise.append(len(cruise))
        for a in cruise:
            rows += 1
            has_baro += "baro_rate" in a
            has_geom += "geom_rate" in a
            ages.append(a["seen_pos"])
            ts = round(now - a["seen_pos"], 1)
            if not stamps[a["hex"]] or ts - stamps[a["hex"]][-1] > 0.25:
                stamps[a["hex"]].append(ts)
    gaps = [b - a for s in stamps.values() for a, b in itertools.pairwise(s)]
    print(f"   aircraft per poll: all median {st.median(n_all):.0f}, "
          f"cruise(>=FL200) median {st.median(n_cruise):.0f}, unique cruise {len(stamps)}")
    if not rows:
        return
    print(f"   vertical rate present: baro {100*has_baro/rows:.0f}%  geom {100*has_geom/rows:.0f}%")
    print(f"   position age seen_pos (s): median {st.median(ages):.1f}  "
          f"p90 {pct(ages, 90):.1f}  share<2s {100*sum(a < 2 for a in ages)/len(ages):.0f}%  "
          f"share>15s {100*sum(a > 15 for a in ages)/len(ages):.0f}%")
    if gaps:
        print(f"   update interval (s):       median {st.median(gaps):.1f}  "
              f"p75 {pct(gaps, 75):.1f}  p90 {pct(gaps, 90):.1f}  max {max(gaps):.0f}  (n={len(gaps)})")
        # Share of cruise aircraft that we could follow continuously.
        span = ok[-1]["t"] - ok[0]["t"]
        dense = sum(1 for s in stamps.values() if len(s) >= 0.8 * len(ok))
        print(f"   aircraft with a fresh position in >=80% of polls: "
              f"{dense}/{len(stamps)} ({100*dense/len(stamps):.0f}%) over {span:.0f}s")


def analyze_opensky(path: Path) -> None:
    recs = [json.loads(line) for line in path.open(encoding="utf-8")]
    ok = [r for r in recs if r.get("status") == 200]
    print(f"\n== {path.stem}: {len(ok)}/{len(recs)} OK, "
          f"statuses={sorted({r['status'] for r in recs})}")
    hdr = recs[0].get("headers", {})
    print("   rate-limit headers:",
          {k: v for k, v in hdr.items() if "rate" in k.lower() or "retry" in k.lower()} or "none")
    if not ok:
        print("   first body:", recs[0].get("body") or recs[0].get("error"))
        return
    stamps: dict[str, list[float]] = defaultdict(list)
    n_cruise, has_vr, rows = [], 0, 0
    for r in ok:
        cruise = [s for s in r["ac"] if s[3] is not None and s[3] * 3.28084 >= CRUISE_FT
                  and s[1] is not None]
        n_cruise.append(len(cruise))
        for icao, tpos, _lc, _alt, vr in cruise:
            rows += 1
            has_vr += vr is not None
            if not stamps[icao] or tpos > stamps[icao][-1]:
                stamps[icao].append(tpos)
    gaps = [b - a for s in stamps.values() for a, b in itertools.pairwise(s)]
    print(f"   server time step between polls: "
          f"{sorted({b['time'] - a['time'] for a, b in itertools.pairwise(ok)})} s")
    print(f"   cruise aircraft per poll: median {st.median(n_cruise):.0f}; "
          f"vertical_rate present {100*has_vr/max(rows,1):.0f}%")
    if gaps:
        print(f"   update interval (s): median {st.median(gaps):.1f}  "
              f"p90 {pct(gaps, 90):.1f}  max {max(gaps):.0f}  (n={len(gaps)})")


def analyze() -> None:
    for p in sorted(OUT.glob("adsblol_*.jsonl")):
        analyze_adsblol(p)
    for p in sorted(OUT.glob("opensky_*.jsonl")):
        analyze_opensky(p)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "collect":
        collect()
    elif cmd == "analyze":
        analyze()
    else:
        print(__doc__)
