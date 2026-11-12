"""Settings, read from environment variables (see .env.example).

A plain dataclass on purpose: no settings framework to learn, and every knob
is visible on one screen.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    # Raw positions are short-lived in live mode. Replayed days keep their
    # original (old) timestamps, so only the live pollers switch retention on.
    positions_retention_hours: int
    # OpenSky OAuth2 client credentials. Never commit these; put them in .env.
    opensky_client_id: str
    opensky_client_secret: str
    # Bounding box as (lamin, lomin, lamax, lomax). Default: India.
    opensky_bbox: tuple[float, float, float, float]
    # Credits we allow ourselves per day. Registered accounts get 4,000; we
    # stay below that so manual testing never pushes the account over.
    opensky_daily_credits: int
    # adsb.lol: optional key (sent as x-api-key) and our self-imposed daily budget.
    # 500/day without a key, 1,000 with a free adhoc key; stay a little below.
    adsblol_api_key: str
    adsblol_daily_requests: int


def get_settings() -> Settings:
    env = os.environ.get
    bbox = tuple(float(x) for x in env("OPENSKY_BBOX", "6,68,36,98").split(","))
    if len(bbox) != 4:
        raise ValueError("OPENSKY_BBOX must be 'lamin,lomin,lamax,lomax'")
    return Settings(
        database_url=env(
            "AEROKYM_DATABASE_URL", "postgresql://aerokym:aerokym@localhost:5432/aerokym"
        ),
        positions_retention_hours=int(env("AEROKYM_POSITIONS_RETENTION_HOURS", "48")),
        opensky_client_id=env("OPENSKY_CLIENT_ID", ""),
        opensky_client_secret=env("OPENSKY_CLIENT_SECRET", ""),
        opensky_bbox=bbox,  # type: ignore[arg-type]
        opensky_daily_credits=int(env("OPENSKY_DAILY_CREDITS", "3600")),
        adsblol_api_key=env("ADSBLOL_API_KEY", ""),
        adsblol_daily_requests=int(env("ADSBLOL_DAILY_REQUESTS", "450")),
    )
