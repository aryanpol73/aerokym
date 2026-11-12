"""Load a historical day of adsb.lol traces into the `positions` table.

This is both the offline training/validation input and the demo mode: it needs
no internet and no API keys.

Examples:
    # the bundled demo sample, re-timed so it ends "now"
    python -m aerokym.ingest.replay data/demo/traces.tar.gz --shift-to-now

    # a full downloaded day (split tar parts, in order), central US, cruise only
    python -m aerokym.ingest.replay day.tar.aa day.tar.ab \
        --bbox 30,-105,45,-85 --min-alt-ft 15000
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aerokym.config import get_settings
from aerokym.db.migrate import connect_with_retry
from aerokym.db.positions import disable_retention, insert_positions
from aerokym.ingest.trace import decode_trace, iter_trace_docs
from aerokym.models import Position

BATCH_ROWS = 50_000
BBox = tuple[float, float, float, float]  # lamin, lomin, lamax, lomax


def keep(p: Position, bbox: BBox | None, min_alt_ft: int) -> bool:
    if (p.baro_alt_ft if p.baro_alt_ft is not None else -1) < min_alt_ft:
        return False
    if bbox is None:
        return True
    return bbox[0] <= p.lat <= bbox[2] and bbox[1] <= p.lon <= bbox[3]


def day_shift(doc: dict, now: datetime) -> timedelta:
    """Shift that makes the trace's UTC day end at `now` (whole seconds)."""
    day_end = datetime.fromtimestamp(float(doc["timestamp"]), tz=UTC) + timedelta(days=1)
    return timedelta(seconds=int((now - day_end).total_seconds()))


def positions_from_docs(
    docs: Iterable[dict], bbox: BBox | None, min_alt_ft: int, shift_to_now: bool
) -> Iterator[tuple[dict, list[Position]]]:
    """Yield (trace document, kept positions) for aircraft with at least one kept point."""
    shift: timedelta | None = None
    for doc in docs:
        if shift is None:
            # Computed once from the first file so all aircraft stay in sync.
            shift = day_shift(doc, datetime.now(UTC)) if shift_to_now else timedelta(0)
        rows = [p for p in decode_trace(doc, shift) if keep(p, bbox, min_alt_ft)]
        if rows:
            yield doc, rows


def upsert_aircraft(conn, docs: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO aircraft (icao24, registration, type_code) VALUES (%s, %s, %s) "
            "ON CONFLICT (icao24) DO UPDATE SET registration = EXCLUDED.registration, "
            "type_code = EXCLUDED.type_code, updated_at = now()",
            [(str(d["icao"]).lower().lstrip("~"), d.get("r"), d.get("t")) for d in docs],
        )
    conn.commit()


def load(sources: list[Path], bbox: BBox | None, min_alt_ft: int, shift_to_now: bool) -> int:
    t0 = time.time()
    total = n_aircraft = 0
    batch: list[Position] = []
    meta: list[dict] = []
    with connect_with_retry(get_settings().database_url) as conn:
        disable_retention(conn)

        def flush() -> None:
            nonlocal total
            if batch:
                total += insert_positions(conn, batch)
                upsert_aircraft(conn, meta)
                batch.clear()
                meta.clear()
                print(f"  {n_aircraft} aircraft, {total} new positions, {time.time() - t0:.0f}s")

        docs = iter_trace_docs(sources)
        for doc, rows in positions_from_docs(docs, bbox, min_alt_ft, shift_to_now):
            n_aircraft += 1
            batch.extend(rows)
            meta.append({k: doc.get(k) for k in ("icao", "r", "t")})
            if len(batch) >= BATCH_ROWS:
                flush()
        flush()
    print(f"done: {n_aircraft} aircraft, {total} new positions in {time.time() - t0:.0f}s")
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sources", nargs="+", type=Path,
                    help="a directory of trace files, or tar file(s) (split parts in order)")
    ap.add_argument("--bbox", help="lamin,lomin,lamax,lomax: keep only positions inside")
    ap.add_argument("--min-alt-ft", type=int, default=15000,
                    help="drop positions below this barometric altitude (default 15000). "
                         "Kept below cruise so the detector can see climbs and descents.")
    ap.add_argument("--shift-to-now", action="store_true",
                    help="re-time the day so it ends now (demo mode)")
    a = ap.parse_args()
    bbox = tuple(float(x) for x in a.bbox.split(",")) if a.bbox else None
    load(a.sources, bbox, a.min_alt_ft, a.shift_to_now)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
