"""The numbers behind the accuracy page."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from aerokym.api.deps import At, Conn, _at, _meta
from aerokym.detect.classify import METHOD_VERSION, MODERATE_INDEX
from aerokym.validate.run import ratio

router = APIRouter(prefix="/api", tags=["accuracy"])

# The one-off calibration from docs/method.md. Shown separately and labelled
# as tuned on its own data, so it is never mistaken for independent validation.
CALIBRATION = {
    "date": "2026-11-11",
    "region": "United States",
    "match": "50 km / 1000 ft / 10 min",
    "n_matched": 429,
    "held_out_reports": 236,
    "precision": 0.51,
    "recall": 0.51,
    "naive_precision": 0.33,
    "auc": 0.79,
    "auc_interval": [0.74, 0.85],
    "caveat": "The method and thresholds were chosen on this same day, so these numbers "
              "are optimistic.",
}
CAVEATS = [
    ("Validated only where pilot reports are dense (the United States). Results over India "
    "and elsewhere are unvalidated."),
    "Only reports with a sampled aircraft within 50 km, 1000 ft and 10 minutes can be scored.",
    "Pilot reports are subjective and skewed: rough air is reported more readily than smooth.",
    "Not for flight planning or any operational use.",
]


@router.get("/accuracy")
def accuracy(conn: Conn, days: Annotated[int, Query(ge=1, le=365)] = 30, at: At = None) -> dict:
    """Daily validation against pilot reports, and the same counts pooled over `days`.

    "Positive" means moderate or greater. `naive_precision` is what you would
    get by calling every matched report moderate or greater.
    """
    ref = _at(at)
    rows = conn.execute(
        "SELECT run_date, region, n_pireps, n_matched, n_smooth, n_light, n_moderate_plus, "
        "tp, fp, fn, precision, recall, auc, n_aircraft, n_events, match_config, computed_at "
        "FROM validation_runs WHERE method_version = %s AND run_date <= %s::date "
        "AND run_date > %s::date - %s ORDER BY run_date DESC, region",
        (METHOD_VERSION, ref, ref, days)).fetchall()
    runs = [{
        "date": r[0].isoformat(), "region": r[1], "n_pireps": r[2], "n_matched": r[3],
        "n_smooth": r[4], "n_light": r[5], "n_moderate_plus": r[6], "tp": r[7], "fp": r[8],
        "fn": r[9], "precision": r[10], "recall": r[11], "auc": r[12], "n_aircraft": r[13],
        "n_windows": r[14], "match": r[15], "computed_at": r[16].isoformat(),
    } for r in rows]
    tp, fp, fn = (sum(r[k] for r in runs) for k in ("tp", "fp", "fn"))
    matched = sum(r["n_matched"] for r in runs)
    pooled = {
        "days": len({r["date"] for r in runs}),
        "n_pireps": sum(r["n_pireps"] for r in runs),
        "n_matched": matched,
        "n_moderate_plus": sum(r["n_moderate_plus"] for r in runs),
        "tp": tp, "fp": fp, "fn": fn,
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "naive_precision": ratio(tp + fn, matched),
    }
    return {"meta": _meta(ref, window_days=days), "threshold_index": MODERATE_INDEX,
            "pooled": pooled, "runs": runs, "calibration": CALIBRATION, "caveats": CAVEATS}
