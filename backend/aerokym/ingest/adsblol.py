"""adsb.lol live trace sampler.

The trick that makes live detection affordable: instead of polling an area
(one request = one sample of each aircraft, far too sparse), ask for ONE
aircraft's trace. adsb.lol serves two per aircraft, both at ~20 s spacing in
level cruise (measured 12 Nov 2026):

  * /1/trace/full/{hex}    the whole UTC day, but written only now and then:
                           it was 17 to 100 minutes behind when measured.
  * /1/trace/recent/{hex}  only the last ~15 minutes, a few seconds old.

Neither is enough alone: `full` gives the long history the detector needs for
the aircraft's own baseline, `recent` gives "now". So each aircraft costs two
requests.

Division of labour in live mode:
  * OpenSky tells us WHO is in cruise over the region right now (cheap).
  * This sampler spends its small daily budget on dense traces for as many of
    those aircraft as it can, longest-observed first.

Budget: 500 requests/day without a key, 1,000 with a free "adhoc" key, 7,200
for feeders. At 900/day that is one aircraft every ~3 minutes, about 19 per
hour. The live map is therefore a thin sample of the traffic, never all of it.

Run: python -m aerokym.ingest.adsblol        (ADSBLOL_API_KEY optional)
"""

from __future__ import annotations

import time

import httpx
import psycopg

from aerokym.config import Settings, get_settings
from aerokym.ingest.opensky import MAX_BACKOFF_S, RateLimited
from aerokym.ingest.trace import decode_trace
from aerokym.models import Position

TRACE_URL = "https://api.adsb.lol/1/trace/{kind}/{icao}"
TRACE_KINDS = ("full", "recent")
USER_AGENT = "aerokym/0.1 (+https://github.com/aryanpol73/aerokym)"
MIN_ALT_FT = 15000          # keep climbs and descents near cruise so the detector can reject them
SEEN_WITHIN_MIN = 6         # candidate must have been seen by OpenSky this recently
REFETCH_AFTER_MIN = 45      # do not spend two more requests on the same aircraft sooner than this
MIN_SIGHTINGS = 8           # ~13 min of OpenSky sightings in cruise before spending a request

CANDIDATE_SQL = """
SELECT p.icao24
FROM positions p
LEFT JOIN trace_fetches f USING (icao24)
WHERE p.source = 'opensky'
  AND p.time > now() - interval '3 hours'
  AND p.baro_alt_ft >= 20000
  AND (f.fetched_at IS NULL OR f.fetched_at < now() - make_interval(mins => %(refetch)s))
GROUP BY p.icao24
HAVING max(p.time) > now() - make_interval(mins => %(seen)s) AND count(*) >= %(sightings)s
ORDER BY count(*) DESC
LIMIT 1
"""


def request_interval_s(daily_requests: int) -> float:
    """Seconds between requests that spends at most `daily_requests` per day."""
    return max(5.0, 86400 / max(daily_requests, 1))


def cruise_positions(doc: dict) -> list[Position]:
    return [p for p in decode_trace(doc) if (p.baro_alt_ft or 0) >= MIN_ALT_FT]


class AdsbLolClient:
    def __init__(self, api_key: str = "", http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=30)
        self._headers = {"User-Agent": USER_AGENT}
        if api_key:
            self._headers["x-api-key"] = api_key
        self.remaining: str | None = None

    def fetch_trace(self, icao24: str, kind: str) -> dict | None:
        """One aircraft's trace ("full" or "recent"), or None if adsb.lol has none."""
        r = self._http.get(TRACE_URL.format(kind=kind, icao=icao24), headers=self._headers)
        if r.status_code == 429:
            raise RateLimited(float(r.headers.get("Retry-After", "600")))
        if r.status_code == 404:
            return None
        r.raise_for_status()  # includes 403: a block is never retried around
        self.remaining = r.headers.get("x-ratelimit-remaining")
        return r.json()


def next_candidate(conn: psycopg.Connection) -> str | None:
    row = conn.execute(CANDIDATE_SQL, {"refetch": REFETCH_AFTER_MIN, "seen": SEEN_WITHIN_MIN,
                                       "sightings": MIN_SIGHTINGS}).fetchone()
    conn.commit()
    return row[0] if row else None


def mark_fetched(conn: psycopg.Connection, icao24: str, n_positions: int) -> None:
    conn.execute(
        "INSERT INTO trace_fetches (icao24, fetched_at, n_positions) VALUES (%s, now(), %s) "
        "ON CONFLICT (icao24) DO UPDATE SET fetched_at = now(), n_positions = EXCLUDED.n_positions",
        (icao24, n_positions))
    conn.commit()


def run(settings: Settings | None = None) -> None:
    from aerokym.db.migrate import connect_with_retry
    from aerokym.db.positions import enable_retention, insert_positions

    s = settings or get_settings()
    client = AdsbLolClient(s.adsblol_api_key)
    interval = request_interval_s(s.adsblol_daily_requests)
    print(f"adsblol: {'keyed' if s.adsblol_api_key else 'no key'}, "
          f"budget {s.adsblol_daily_requests}/day, one request every {interval:.0f}s, "
          f"two per aircraft")
    backoff = interval
    with connect_with_retry(s.database_url) as conn:
        enable_retention(conn, s.positions_retention_hours)
        while True:
            started = time.time()
            wait = interval
            try:
                icao = next_candidate(conn)
                if icao is None:
                    print("adsblol: no candidate aircraft (is the OpenSky poller running?)")
                else:
                    n_rows = n_new = 0
                    for i, kind in enumerate(TRACE_KINDS):
                        if i:
                            time.sleep(interval)  # every request is paced, not every aircraft
                        doc = client.fetch_trace(icao, kind)
                        rows = cruise_positions(doc) if doc else []
                        n_rows += len(rows)
                        n_new += insert_positions(conn, rows) if rows else 0
                    mark_fetched(conn, icao, n_rows)
                    print(f"adsblol: {icao} {n_rows} positions ({n_new} new), "
                          f"quota left {client.remaining}")
                    started = time.time()
                backoff = interval
            except RateLimited as e:
                wait = max(interval, e.retry_after_s)
                print(f"adsblol: {e}")
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    # A refused key or a block is a decision by the provider. Stop.
                    print(f"adsblol: HTTP {e.response.status_code}, stopping: "
                          f"{e.response.text[:200]}")
                    return
                backoff = min(backoff * 2, MAX_BACKOFF_S)
                wait = backoff
                print(f"adsblol: HTTP {e.response.status_code}; retrying in {wait:.0f}s")
            except (httpx.HTTPError, KeyError, ValueError) as e:
                backoff = min(backoff * 2, MAX_BACKOFF_S)
                wait = backoff
                print(f"adsblol: error {type(e).__name__}: {e}; retrying in {wait:.0f}s")
            time.sleep(max(0.0, wait - (time.time() - started)))


if __name__ == "__main__":
    run()
