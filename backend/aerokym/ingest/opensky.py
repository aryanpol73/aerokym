"""OpenSky live poller (OAuth2 client credentials).

One request returns every aircraft in the bounding box. M0 showed this is the
only free source that can refresh all of India in under two minutes, at about
one sample per aircraft per poll: enough for "where is everyone now", too
sparse for per-aircraft RMSVA.

Budget: a registered account gets 4,000 credits/day and a request costs 1-4
credits depending on box area. The poll interval is derived from that, so we
cannot overspend by configuration mistake.

Run: python -m aerokym.ingest.opensky        (needs OPENSKY_CLIENT_ID/SECRET)
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import httpx

from aerokym.config import Settings, get_settings
from aerokym.models import Position

TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network"
    "/protocol/openid-connect/token"
)
STATES_URL = "https://opensky-network.org/api/states/all"
SOURCE = "opensky"
M_TO_FT = 3.28084
MS_TO_FPM = 196.850394
MS_TO_KT = 1.943844
MAX_BACKOFF_S = 900


def credit_cost(bbox: tuple[float, float, float, float]) -> int:
    """Credits one /states/all call costs, by box area in square degrees."""
    area = abs(bbox[2] - bbox[0]) * abs(bbox[3] - bbox[1])
    if area <= 25:
        return 1
    if area <= 100:
        return 2
    if area <= 400:
        return 3
    return 4


def poll_interval_s(bbox: tuple[float, float, float, float], daily_credits: int) -> float:
    """Seconds between polls that spends at most `daily_credits` per day.

    Never faster than 10 s: OpenSky's own data only changes every 5-10 s.
    """
    return max(10.0, 86400 * credit_cost(bbox) / max(daily_credits, 1))


def cycle_interval_s(
    bbox: tuple[float, float, float, float],
    validation_bbox: tuple[float, float, float, float] | None,
    validation_every: int,
    daily_credits: int,
) -> float:
    """Poll interval when a second (validation) box is polled on every Nth cycle.

    Average cost per cycle = main box + validation box / N.
    """
    cost = float(credit_cost(bbox))
    if validation_bbox is not None and validation_every > 0:
        cost += credit_cost(validation_bbox) / validation_every
    return max(10.0, 86400 * cost / max(daily_credits, 1))


def parse_states(body: dict) -> list[Position]:
    """Convert an OpenSky /states/all response into Position rows (SI -> aviation units).

    State vector indices: 0 icao24, 3 time_position, 5 lon, 6 lat,
    7 baro_altitude m, 8 on_ground, 9 velocity m/s, 10 true_track deg,
    11 vertical_rate m/s, 13 geo_altitude m.
    """
    rows: list[Position] = []
    for s in body.get("states") or []:
        if len(s) < 14 or s[3] is None or s[5] is None or s[6] is None:
            continue  # no position fix
        rows.append(
            Position(
                time=datetime.fromtimestamp(s[3], tz=UTC),
                icao24=str(s[0]).lower(),
                lat=float(s[6]),
                lon=float(s[5]),
                baro_alt_ft=round(s[7] * M_TO_FT) if s[7] is not None else None,
                geom_alt_ft=round(s[13] * M_TO_FT) if s[13] is not None else None,
                # OpenSky does not say whether vertical_rate is barometric or
                # geometric. We store it as barometric and rely on `source`.
                baro_rate_fpm=round(s[11] * MS_TO_FPM) if s[11] is not None else None,
                geom_rate_fpm=None,
                gs_kt=round(s[9] * MS_TO_KT, 1) if s[9] is not None else None,
                track_deg=float(s[10]) if s[10] is not None else None,
                on_ground=bool(s[8]),
                source=SOURCE,
            )
        )
    return rows


class RateLimited(Exception):
    def __init__(self, retry_after_s: float):
        super().__init__(f"rate limited, retry after {retry_after_s:.0f}s")
        self.retry_after_s = retry_after_s


class OpenSkyClient:
    """Fetches and caches the access token, and calls /states/all."""

    def __init__(self, client_id: str, client_secret: str, http: httpx.Client | None = None):
        if not client_id or not client_secret:
            raise ValueError("OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET must be set")
        self._id, self._secret = client_id, client_secret
        self._http = http or httpx.Client(timeout=30)
        self._token: str | None = None
        self._token_expiry = 0.0
        self.credits_remaining: int | None = None

    def _get_token(self) -> str:
        # Tokens last about 30 minutes; refresh a minute early.
        if self._token is None or time.time() > self._token_expiry - 60:
            r = self._http.post(
                TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._id,
                    "client_secret": self._secret,
                },
            )
            r.raise_for_status()
            body = r.json()
            self._token = body["access_token"]
            self._token_expiry = time.time() + float(body.get("expires_in", 1800))
        return self._token

    def fetch_states(self, bbox: tuple[float, float, float, float]) -> list[Position]:
        params = {"lamin": bbox[0], "lomin": bbox[1], "lamax": bbox[2], "lomax": bbox[3]}
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._get_token()}"}
            r = self._http.get(STATES_URL, params=params, headers=headers)
            if r.status_code == 401 and attempt == 1:
                self._token = None  # token expired or was revoked: fetch a new one, once
                continue
            break
        if r.status_code == 429:
            retry = r.headers.get("X-Rate-Limit-Retry-After-Seconds", "300")
            raise RateLimited(float(retry))
        r.raise_for_status()
        remaining = r.headers.get("X-Rate-Limit-Remaining")
        self.credits_remaining = int(remaining) if remaining and remaining.isdigit() else None
        return parse_states(r.json())


def run(settings: Settings | None = None) -> None:
    """Poll forever. Imports of the database layer are local so the parsing
    code above can be tested without Postgres installed."""
    from aerokym.db.migrate import connect_with_retry
    from aerokym.db.positions import enable_retention, insert_positions

    s = settings or get_settings()
    client = OpenSkyClient(s.opensky_client_id, s.opensky_client_secret)
    interval = cycle_interval_s(s.opensky_bbox, s.validation_bbox, s.opensky_validation_every,
                                s.opensky_daily_credits)
    print(f"opensky: bbox={s.opensky_bbox} every {interval:.0f}s; validation box "
          f"{s.validation_bbox} every {s.opensky_validation_every} cycles")
    cycle = 0
    backoff = interval
    with connect_with_retry(s.database_url) as conn:
        enable_retention(conn, s.positions_retention_hours)
        while True:
            started = time.time()
            wait = interval
            try:
                rows = client.fetch_states(s.opensky_bbox)
                cycle += 1
                if s.validation_bbox and cycle % max(s.opensky_validation_every, 1) == 0:
                    rows += client.fetch_states(s.validation_bbox)
                new = insert_positions(conn, rows)
                print(f"opensky: {len(rows)} aircraft, {new} new positions, "
                      f"credits left {client.credits_remaining}")
                backoff = interval
            except RateLimited as e:
                # The server tells us when to come back. Obey it, never retry early.
                wait = max(interval, e.retry_after_s)
                print(f"opensky: {e}")
            except (httpx.HTTPError, KeyError, ValueError) as e:
                # Network trouble or a bad response: back off exponentially.
                backoff = min(backoff * 2, MAX_BACKOFF_S)
                wait = backoff
                print(f"opensky: error {type(e).__name__}: {e}; retrying in {wait:.0f}s")
            time.sleep(max(0.0, wait - (time.time() - started)))


if __name__ == "__main__":
    run()
