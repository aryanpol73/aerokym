"""Headwind and crosswind components for a runway.

Wind is given as the direction it blows FROM. For a runway heading h and wind
from direction d at speed s:

    headwind  = s * cos(d - h)     (negative = tailwind)
    crosswind = s * sin(d - h)     (positive = from the right)

METAR wind directions and OurAirports runway headings are both in degrees
true, so no magnetic correction is needed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WindComponents:
    headwind_kt: float      # negative means tailwind
    crosswind_kt: float     # always >= 0
    crosswind_from: str     # "left", "right" or "" when there is none


def components(wind_dir_deg: float, wind_kt: float, runway_heading_deg: float) -> WindComponents:
    angle = math.radians(wind_dir_deg - runway_heading_deg)
    head = wind_kt * math.cos(angle)
    cross = wind_kt * math.sin(angle)
    side = "" if abs(cross) < 0.5 else ("right" if cross > 0 else "left")
    return WindComponents(round(head, 1), round(abs(cross), 1), side)


def heading_from_ident(ident: str | None) -> float | None:
    """Approximate heading from a runway name: "09L" -> 90, "27" -> 270.

    Fallback only: runway numbers are magnetic and rounded to 10 degrees.
    """
    m = re.match(r"^(\d{1,2})", ident or "")
    if not m:
        return None
    n = int(m.group(1))
    return float(n * 10 % 360) if 1 <= n <= 36 else None


def runway_wind(
    ident: str | None, heading_deg: float | None, wind_dir, wind_kt: float | None,
    gust_kt: float | None = None,
) -> dict:
    """Wind summary for one runway end, ready for the API.

    `wind_dir` may be a number, "VRB" (variable) or None.
    """
    heading = heading_deg if heading_deg is not None else heading_from_ident(ident)
    out: dict = {"ident": ident, "heading_deg": None if heading is None else round(heading)}
    if heading is None or wind_kt is None:
        return {**out, "status": "unknown"}
    if wind_kt < 1:
        return {**out, "status": "calm", "headwind_kt": 0.0, "crosswind_kt": 0.0}
    if not isinstance(wind_dir, (int, float)):
        # Variable direction: the whole wind could be across the runway.
        return {**out, "status": "variable", "max_crosswind_kt": float(gust_kt or wind_kt)}
    c = components(wind_dir, wind_kt, heading)
    out.update(status="ok", headwind_kt=c.headwind_kt, crosswind_kt=c.crosswind_kt,
               crosswind_from=c.crosswind_from)
    if gust_kt:
        g = components(wind_dir, gust_kt, heading)
        out.update(gust_headwind_kt=g.headwind_kt, gust_crosswind_kt=g.crosswind_kt)
    return out
