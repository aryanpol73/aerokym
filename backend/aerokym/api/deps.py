"""Pieces shared by every API route."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated

import psycopg
from fastapi import Depends, Query

from aerokym.config import get_settings
from aerokym.detect.classify import METHOD_VERSION

DISCLAIMER = (
    "Aerokym is a research project. Not for operational use or flight planning; "
    "it gives no safety guidance. Detections are statistical estimates from sparse public data."
)
ATTRIBUTION = "ADS-B data: adsb.lol, ODbL v1.0. Derived data is share-alike under ODbL."

def get_conn() -> Iterator[psycopg.Connection]:
    # One short-lived connection per request: simple, and plenty for an API
    # whose responses are cached. Swap for a pool if it ever shows up in profiles.
    with psycopg.connect(get_settings().database_url, connect_timeout=5) as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]
At = Annotated[datetime | None, Query(description="Reference time (ISO 8601). Default: now.")]


def _meta(at: datetime, **extra) -> dict:
    return {"generated_at": datetime.now(UTC).isoformat(), "at": at.isoformat(),
            "method_version": METHOD_VERSION, "disclaimer": DISCLAIMER,
            "attribution": ATTRIBUTION, **extra}


def _at(at: datetime | None) -> datetime:
    if at is None:
        return datetime.now(UTC)
    return at if at.tzinfo else at.replace(tzinfo=UTC)
