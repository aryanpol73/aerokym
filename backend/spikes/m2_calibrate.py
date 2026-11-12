"""M2 calibration: does the roughness index track pilot reports?

Usage (from repo root):
    python backend/spikes/m2_calibrate.py PIREPS.csv day.tar.aa [day.tar.ab]

PIREPS.csv is the Iowa State IEM archive for the same UTC day:
    https://mesonet.agron.iastate.edu/cgi-bin/request/gis/pireps.py
        ?sts=2026-11-11T00:00Z&ets=2026-11-12T00:00Z&fmt=csv

Steps:
  1. Level-cruise window features and the roughness index for every aircraft
     over the US (cached next to the CSV as .windows.pkl).
  2. Match windows to cruise-level PIREPs at three distance tolerances.
  3. AUC of each score: smooth reports vs turbulence reports, with a bootstrap
     95% interval.
  4. Thresholds fitted on a random half of the reports, scored on the other.
"""

from __future__ import annotations

import pickle
import sys
import tarfile
from dataclasses import fields
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aerokym.detect.roughness import WindowFeatures, roughness_index, track_features
from aerokym.detect.segments import track_from_positions
from aerokym.ingest.trace import decode_trace, iter_trace_docs
from aerokym.validate.pirep_match import (
    MatchConfig,
    auc,
    load_iem_csv,
    match_indices,
    precision_recall,
)

US = (24.0, -126.0, 50.0, -66.0)
SCORES = ("index", "baro_rate_std", "rate_range", "alt_std", "dense_frac")
MIN_PIREP_ALT = 20000
MATCHES = {
    "100 km / 2000 ft / 20 min": MatchConfig(100, 2000, 1200),
    "50 km / 1000 ft / 10 min": MatchConfig(50, 1000, 600),
    "30 km / 1000 ft / 10 min": MatchConfig(30, 1000, 600),
}
THRESHOLD_MATCH = "50 km / 1000 ft / 10 min"
# Thresholds are set by how often they fire on reports of SMOOTH air.
FALSE_ALARM = {"light": 0.25, "moderate": 0.10}


def collect_windows(sources: list[Path]) -> tuple[dict[str, np.ndarray], int]:
    rows: list[WindowFeatures] = []
    index: list[float] = []
    n_aircraft = 0
    try:
        for doc in iter_trace_docs(sources):
            ps = [p for p in decode_trace(doc)
                  if US[0] <= p.lat <= US[2] and US[1] <= p.lon <= US[3]
                  and (p.baro_alt_ft or 0) >= 18000]
            if len(ps) < 20:
                continue
            feats = list(track_features(track_from_positions(ps)))
            if feats:
                n_aircraft += 1
                rows.extend(feats)
                index.extend(roughness_index(feats))
    except (tarfile.ReadError, EOFError, OSError) as e:
        print(f"(archive ended early: {type(e).__name__}; using what was read)")
    cols = {f.name: np.array([getattr(r, f.name) for r in rows])
            for f in fields(WindowFeatures)}
    cols["index"] = np.array(index)
    return cols, n_aircraft


def boot_auc(neg: np.ndarray, pos: np.ndarray, n: int = 400) -> tuple[float, float]:
    rng = np.random.default_rng(1)
    vals = [auc(rng.choice(neg, len(neg)), rng.choice(pos, len(pos))) for _ in range(n)]
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def report_scores(w: dict, matched: list, name: str) -> np.ndarray:
    """Per-report score = mean over all matched windows (what a map cell would show)."""
    return np.array([np.nanmean(w[name][i]) if not np.all(np.isnan(w[name][i])) else np.nan
                     for _, i in matched])


def main() -> None:
    pirep_path, sources = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
    cache = pirep_path.with_suffix(".windows.pkl")
    if cache.exists():
        w, n_aircraft = pickle.loads(cache.read_bytes())
    else:
        w, n_aircraft = collect_windows(sources)
        cache.write_bytes(pickle.dumps((w, n_aircraft)))
    print(f"windows: {len(w['t_mid'])} from {n_aircraft} aircraft; "
          f"median sampling interval {np.median(w['median_dt_s']):.1f} s")
    for f in SCORES:
        v = w[f][~np.isnan(w[f])]
        print(f"  {f:14} p50 {np.percentile(v, 50):7.2f}  p90 {np.percentile(v, 90):7.2f}  "
              f"p99 {np.percentile(v, 99):7.2f}")

    pireps = [p for p in load_iem_csv(pirep_path)
              if p.alt_ft >= MIN_PIREP_ALT and US[0] <= p.lat <= US[2] and US[1] <= p.lon <= US[3]]
    print(f"\ncruise PIREPs with a turbulence remark over the US: {len(pireps)}; by intensity "
          f"{ {k: sum(p.intensity == k for p in pireps) for k in range(4)} }")

    kept = {}
    for label, cfg in MATCHES.items():
        matched = [(p, i) for p in pireps
                   if len(i := match_indices(p, w["t_mid"], w["lat"], w["lon"], w["alt_ft"], cfg))]
        y = np.array([p.intensity for p, _ in matched])
        kept[label] = (matched, y)
        print(f"\nmatch within {label}: {len(matched)} reports "
              f"(smooth {int((y == 0).sum())}, light {int((y == 1).sum())}, "
              f"moderate+ {int((y >= 2).sum())})")
        print(f"  {'score':14} {'AUC any vs smooth':>18} {'AUC moderate+ vs smooth':>24}  95% interval")
        for name in SCORES:
            s = report_scores(w, matched, name)
            ok = ~np.isnan(s)
            neg, pos = s[ok & (y == 0)], s[ok & (y >= 2)]
            lo, hi = boot_auc(neg, pos)
            print(f"  {name:14} {auc(neg, s[ok & (y >= 1)]):>18.3f} {auc(neg, pos):>24.3f}"
                  f"  {lo:.2f}-{hi:.2f}")

    matched, y = kept[THRESHOLD_MATCH]
    s = report_scores(w, matched, "index")
    ok = ~np.isnan(s)
    half = np.random.default_rng(0).random(len(s)) < 0.5
    train, test = ok & half, ok & ~half
    print(f"\nthresholds ({THRESHOLD_MATCH}); fitted on {int(train.sum())} reports, "
          f"scored on the other {int(test.sum())}")
    for sev, far in FALSE_ALARM.items():
        thr = float(np.quantile(s[train & (y == 0)], 1 - far))
        label = y >= (1 if sev == "light" else 2)
        p, r = precision_recall(s[test], label[test], thr)
        far_test = float(np.mean(s[test & (y == 0)] >= thr))
        print(f"  {sev:8} index >= {thr:.2f}: precision {p:.2f}, recall {r:.2f}, "
              f"fires on {100 * far_test:.0f}% of smooth reports | naive 'always yes': "
              f"precision {label[test].mean():.2f}, recall 1.00")
    print(f"  held-out AUC moderate+ vs smooth: "
          f"{auc(s[test & (y == 0)], s[test & (y >= 2)]):.3f} "
          f"({int((test & (y >= 2)).sum())} vs {int((test & (y == 0)).sum())} reports)")
    full = {sev: round(float(np.quantile(s[ok & (y == 0)], 1 - far)), 2)
            for sev, far in FALSE_ALARM.items()}
    print(f"  thresholds from ALL smooth reports (for classify.py): {full}")


if __name__ == "__main__":
    main()
