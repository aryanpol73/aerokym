"""Roll detector windows up into hex cells for the map.

SQL does the grouping; Python turns each group into a severity, a confidence
and a GeoJSON hexagon. The Python half is pure, so it is unit tested without a
database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import h3
import psycopg

from aerokym.detect.classify import METHOD_VERSION, Severity, severity

MAP_H3_RES = 4            # hexes about 45 km across, close to the 50 km calibration radius
WINDOWS_MIN = (15, 30, 60)
BANDS = ("FL200-290", "FL300-390", "FL400+")
FULL_CONFIDENCE_AIRCRAFT = 3
SEVERITY_LABEL = {
    Severity.NONE: "none",
    Severity.LIGHT: "light",
    Severity.MODERATE_OR_GREATER: "moderate_or_greater",
}

# Each aircraft counts once per cell (its own mean), so one aircraft with many
# windows cannot outvote three others.
CELLS_SQL = """
WITH e AS (
    SELECT h3_cell_to_parent(h3_cell, %(res)s) AS cell,
           CASE WHEN alt_ft < 30000 THEN 'FL200-290'
                WHEN alt_ft < 40000 THEN 'FL300-390'
                ELSE 'FL400+' END AS band,
           icao24, roughness_index, confidence, time
    FROM turb_events
    WHERE method_version = %(method)s
      AND time > %(at)s - make_interval(mins => %(minutes)s) AND time <= %(at)s
), per_aircraft AS (
    SELECT cell, band, icao24, avg(roughness_index) AS idx, avg(confidence) AS conf,
           count(*) AS n, max(time) AS latest
    FROM e GROUP BY cell, band, icao24
)
SELECT cell::text, band, count(*), sum(n), avg(idx), max(idx), avg(conf), max(latest)
FROM per_aircraft
WHERE %(band)s::text IS NULL OR band = %(band)s
GROUP BY cell, band
"""


@dataclass(frozen=True)
class Cell:
    h3_cell: str
    band: str
    n_aircraft: int
    n_windows: int
    mean_index: float
    max_index: float
    window_confidence: float
    latest: datetime

    @property
    def severity(self) -> Severity:
        return severity(self.mean_index)

    @property
    def confidence(self) -> float:
        """0..1. One aircraft is an anecdote; three that agree are evidence."""
        share = min(1.0, self.n_aircraft / FULL_CONFIDENCE_AIRCRAFT)
        return round(self.window_confidence * share, 2)


def fetch_cells(
    conn: psycopg.Connection, at: datetime, minutes: int, band: str | None = None,
    res: int = MAP_H3_RES, method: str = METHOD_VERSION,
) -> list[Cell]:
    rows = conn.execute(
        CELLS_SQL, {"res": res, "method": method, "at": at, "minutes": minutes, "band": band}
    ).fetchall()
    return [Cell(r[0], r[1], int(r[2]), int(r[3]), float(r[4]), float(r[5]), float(r[6]), r[7])
            for r in rows]


def hexagon(cell: str) -> list[list[list[float]]]:
    """GeoJSON polygon coordinates ([lon, lat], closed ring) for an H3 cell."""
    ring = [[round(lng, 5), round(lat, 5)] for lat, lng in h3.cell_to_boundary(cell)]
    return [[*ring, ring[0]]]


def cell_feature(c: Cell) -> dict:
    return {
        "type": "Feature",
        "id": f"{c.h3_cell}:{c.band}",
        "geometry": {"type": "Polygon", "coordinates": hexagon(c.h3_cell)},
        "properties": {
            "h3_cell": c.h3_cell,
            "alt_band": c.band,
            "severity": int(c.severity),
            "severity_label": SEVERITY_LABEL[c.severity],
            "confidence": c.confidence,
            "n_aircraft": c.n_aircraft,
            "n_windows": c.n_windows,
            "mean_index": round(c.mean_index, 2),
            "max_index": round(c.max_index, 2),
            "latest": c.latest.isoformat(),
        },
    }
