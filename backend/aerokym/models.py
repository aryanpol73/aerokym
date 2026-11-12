"""Plain data types shared by ingest, storage and (later) detection."""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple


class Position(NamedTuple):
    """One ADS-B sample. Field order matches the `positions` table columns."""

    time: datetime  # UTC, when the position was received
    icao24: str  # 24-bit ICAO address, lower-case hex
    lat: float
    lon: float
    baro_alt_ft: int | None
    geom_alt_ft: int | None
    baro_rate_fpm: int | None
    geom_rate_fpm: int | None
    gs_kt: float | None
    track_deg: float | None
    on_ground: bool
    source: str  # 'adsblol_trace' | 'adsblol' | 'opensky'


POSITION_COLUMNS = Position._fields
