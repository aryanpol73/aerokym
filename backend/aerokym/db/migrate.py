"""Tiny SQL migration runner.

Applies db/migrations/NNNN_*.sql in order, once each, and records them in
`schema_migrations`. Chosen over Alembic for now: the schema leans on raw SQL
(hypertables, PostGIS, h3) that an ORM cannot express anyway, and this is
short enough to read in a minute.

Run: python -m aerokym.db.migrate
"""

from __future__ import annotations

import time
from pathlib import Path

import psycopg

from aerokym.config import get_settings

MIGRATIONS = Path(__file__).parent / "migrations"


def connect_with_retry(url: str, attempts: int = 45) -> psycopg.Connection:
    """The database container restarts once during first boot; wait for it."""
    for i in range(attempts):
        try:
            return psycopg.connect(url)
        except psycopg.OperationalError:
            if i == attempts - 1:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def migrate(url: str | None = None) -> list[str]:
    applied_now: list[str] = []
    with connect_with_retry(url or get_settings().database_url) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        conn.commit()
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            # One transaction per file: a failing migration leaves no half state.
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
    return applied_now


if __name__ == "__main__":
    names = migrate()
    print("applied:", ", ".join(names) if names else "nothing (already up to date)")
