"""Match detector windows against pilot reports (PIREPs) and score the result.

A PIREP is one pilot's words about one moment, with a rough position. So a
"match" is necessarily loose: every analysis window from any aircraft that
passed close to the report in space, altitude and time counts as evidence for
that report.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

# Severity scale shared with the detector: 0 none, 1 light, 2 moderate, 3 severe.
_INTENSITY = [
    (re.compile(r"\b(EXTRM|EXTREME|SEV|SVR|SEVERE)\b"), 3),
    (re.compile(r"\b(MOD|MDT|MODERATE)\b"), 2),
    (re.compile(r"\b(LGT|LIGHT|LT)\b"), 1),
    (re.compile(r"\b(NEG|SMOOTH|SMTH|NONE|NIL)\b"), 0),
]
_FL = re.compile(r"/FL\s?(\d{3})")
EARTH_KM_PER_DEG = 111.2


def parse_intensity(text: str | None) -> int | None:
    """Worst turbulence intensity mentioned, or None if the report says nothing.

    "LGT-MOD" and "CONS LGT OCNL MOD" both count as moderate: we score the
    worst condition the pilot met.
    """
    if not text or text.strip().lower() == "none":
        return None
    up = text.upper().replace("-", " ")
    for pattern, level in _INTENSITY:
        if pattern.search(up):
            return level
    return None


@dataclass(frozen=True)
class Pirep:
    t: float            # unix seconds
    lat: float
    lon: float
    alt_ft: float
    intensity: int      # 0..3
    raw: str


def load_iem_csv(path: Path) -> list[Pirep]:
    """Read the Iowa State IEM PIREP archive CSV, keeping usable turbulence reports."""
    out: list[Pirep] = []
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            intensity = parse_intensity(row.get("TURBULENCE"))
            if intensity is None:
                continue
            fl = row.get("FL") or ""
            if fl.strip().isdigit():
                alt = float(fl)
            else:
                m = _FL.search(row.get("REPORT") or "")
                if not m:
                    continue
                alt = float(m.group(1)) * 100
            try:
                when = datetime.strptime(row["VALID"], "%Y%m%d%H%M").replace(tzinfo=UTC)
                lat, lon = float(row["LAT"]), float(row["LON"])
            except (KeyError, ValueError):
                continue
            out.append(Pirep(when.timestamp(), lat, lon, alt, intensity, row.get("REPORT") or ""))
    return out


@dataclass(frozen=True)
class MatchConfig:
    radius_km: float = 100.0
    alt_tol_ft: float = 2000.0
    time_tol_s: float = 20 * 60.0


def match_indices(
    pirep: Pirep, t: np.ndarray, lat: np.ndarray, lon: np.ndarray, alt: np.ndarray,
    cfg: MatchConfig | None = None,
) -> np.ndarray:
    """Indices of windows close to `pirep` in time, altitude and distance."""
    cfg = cfg or MatchConfig()
    near = (np.abs(t - pirep.t) <= cfg.time_tol_s) & (np.abs(alt - pirep.alt_ft) <= cfg.alt_tol_ft)
    idx = np.flatnonzero(near)
    if not len(idx):
        return idx
    # Flat-earth distance: fine at 100 km and much cheaper than haversine.
    dy = (lat[idx] - pirep.lat) * EARTH_KM_PER_DEG
    dx = (lon[idx] - pirep.lon) * EARTH_KM_PER_DEG * np.cos(np.radians(pirep.lat))
    return idx[np.hypot(dx, dy) <= cfg.radius_km]


def auc(negatives: np.ndarray, positives: np.ndarray) -> float:
    """Probability that a random positive scores above a random negative.

    0.5 = the score carries no information, 1.0 = perfect separation.
    """
    neg, pos = np.asarray(negatives, float), np.asarray(positives, float)
    if not len(neg) or not len(pos):
        return float("nan")
    greater = (pos[:, None] > neg[None, :]).mean()
    ties = (pos[:, None] == neg[None, :]).mean()
    return float(greater + 0.5 * ties)


def precision_recall(scores: np.ndarray, labels: np.ndarray, threshold: float) -> tuple[float, float]:
    """Precision and recall of `score >= threshold` against boolean labels."""
    pred = np.asarray(scores) >= threshold
    labels = np.asarray(labels, bool)
    tp = int(np.sum(pred & labels))
    precision = tp / int(pred.sum()) if pred.any() else float("nan")
    recall = tp / int(labels.sum()) if labels.any() else float("nan")
    return precision, recall
