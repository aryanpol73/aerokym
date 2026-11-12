"""Writing positions to Postgres."""

from __future__ import annotations

from collections.abc import Iterable

import psycopg

from aerokym.models import POSITION_COLUMNS, Position

_COLS = ", ".join(POSITION_COLUMNS)


def insert_positions(conn: psycopg.Connection, rows: Iterable[Position]) -> int:
    """Bulk-insert positions, silently skipping ones we already have.

    COPY is far faster than INSERT for bulk rows but cannot skip duplicates,
    so we COPY into a temporary table and move the rows across with
    ON CONFLICT DO NOTHING. Returns the number of new rows.
    """
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE IF NOT EXISTS positions_stage "
            "(LIKE positions INCLUDING DEFAULTS) ON COMMIT DELETE ROWS"
        )
        with cur.copy(f"COPY positions_stage ({_COLS}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        cur.execute(
            f"INSERT INTO positions ({_COLS}) "
            f"SELECT DISTINCT ON (icao24, time) {_COLS} FROM positions_stage "
            "ON CONFLICT (icao24, time) DO NOTHING"
        )
        inserted = cur.rowcount
    conn.commit()
    return inserted


def enable_retention(conn: psycopg.Connection, hours: int) -> None:
    """Live mode: drop raw positions older than `hours` (whole chunks at a time)."""
    conn.execute(
        "SELECT add_retention_policy('positions', make_interval(hours => %s), "
        "if_not_exists => true)",
        (hours,),
    )
    conn.commit()


def disable_retention(conn: psycopg.Connection) -> None:
    """Replay mode: historical timestamps must survive."""
    conn.execute("SELECT remove_retention_policy('positions', if_exists => true)")
    conn.commit()
