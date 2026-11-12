"""M0 closing check: how densely sampled are adsb.lol globe_history traces?

readsb thins traces when it writes them: it stores a point when the aircraft
changes track, altitude or vertical rate noticeably, and otherwise only every
so often. So the spacing in *level cruise* (where turbulence detection runs)
may differ from the spacing in climbs. This script measures both.

Usage (from repo root):
    python backend/spikes/m0_trace_spacing.py day.tar.aa [day.tar.ab]
        [--demo-out data/demo/traces.tar.gz --demo-n 120]

The tar is streamed, never extracted. A partially downloaded archive works
too: files are bucketed by the last two hex digits of the ICAO address, so any
prefix of the archive is a random sample of aircraft. With --demo-out, a small
sample of long cruise flights is saved for the offline demo.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import statistics as st
import sys
import tarfile
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aerokym.ingest.trace import iter_trace_docs

CRUISE_FT = 20000
LEVEL_FPM = 256        # |vertical rate| below this = roughly level
SEGMENT_GAP_S = 120    # a longer gap is a coverage hole, not a sampling interval
REGIONS = {"india": (6, 68, 36, 98), "us": (25, -125, 49, -67)}


def region_of(lat: float, lon: float) -> str:
    for name, (la0, lo0, la1, lo1) in REGIONS.items():
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return name
    return "other"


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+", type=Path)
    ap.add_argument("--demo-out", type=Path)
    ap.add_argument("--demo-n", type=int, default=120, help="aircraft per region in the sample")
    a = ap.parse_args()

    gaps = defaultdict(list)       # (region, "level" | "all") -> intervals in seconds
    holes = defaultdict(int)       # region -> gaps longer than SEGMENT_GAP_S
    n_points = defaultdict(int)
    has_rate = defaultdict(int)
    aircraft = defaultdict(set)
    demo: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    n_files = 0
    try:
        for doc in iter_trace_docs(a.sources):
            n_files += 1
            cruise = [p for p in doc.get("trace", [])
                      if isinstance(p[3], (int, float)) and p[3] >= CRUISE_FT
                      and p[1] is not None and not (p[6] & 1)]
            per_region = defaultdict(int)
            for p in cruise:
                reg = region_of(p[1], p[2])
                per_region[reg] += 1
                n_points[reg] += 1
                has_rate[reg] += p[7] is not None
            for p, q in pairwise(cruise):
                reg = region_of(q[1], q[2])
                dt = q[0] - p[0]
                if dt <= 0:
                    continue
                if dt > SEGMENT_GAP_S:
                    holes[reg] += 1
                    continue
                gaps[reg, "all"].append(dt)
                level = all(isinstance(x[7], (int, float)) and abs(x[7]) <= LEVEL_FPM
                            for x in (p, q))
                if level:
                    gaps[reg, "level"].append(dt)
            for reg, n in per_region.items():
                aircraft[reg].add(doc["icao"])
                if reg in REGIONS and n >= 300:
                    demo[reg].append((n, doc))
    except (tarfile.ReadError, EOFError, OSError) as e:
        print(f"(archive ended early: {type(e).__name__}; using what was read)")

    print(f"trace files read: {n_files}")
    print(f"{'region':8} {'aircraft':>8} {'cruise pts':>10} {'rate %':>6} | "
          f"{'level cruise dt: med':>20} {'p75':>5} {'p90':>5} {'<=5s':>5} {'<=10s':>6} | "
          f"{'all cruise med':>14} {'p90':>5} | {'holes>120s':>10}")
    for reg in ("india", "us", "other"):
        lv, al = gaps[reg, "level"], gaps[reg, "all"]
        if not lv:
            continue
        print(f"{reg:8} {len(aircraft[reg]):>8} {n_points[reg]:>10} "
              f"{100 * has_rate[reg] / n_points[reg]:>5.0f}% | "
              f"{st.median(lv):>20.1f} {pct(lv, 75):>5.1f} {pct(lv, 90):>5.1f} "
              f"{100 * sum(x <= 5 for x in lv) / len(lv):>4.0f}% "
              f"{100 * sum(x <= 10 for x in lv) / len(lv):>5.0f}% | "
              f"{st.median(al):>14.1f} {pct(al, 90):>5.1f} | {holes[reg]:>10}")

    if a.demo_out:
        a.demo_out.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with tarfile.open(a.demo_out, "w:gz") as tar:
            for reg in REGIONS:
                for _, doc in sorted(demo[reg], key=lambda t: -t[0])[: a.demo_n]:
                    data = gzip.compress(json.dumps(doc, separators=(",", ":")).encode())
                    icao = str(doc["icao"]).lower()
                    info = tarfile.TarInfo(f"traces/{icao[-2:]}/trace_full_{icao}.json")
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                    n += 1
        print(f"demo sample: {n} aircraft -> {a.demo_out} "
              f"({a.demo_out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
