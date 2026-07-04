"""SQLite connection factory + migration runner.

Schema versioning uses SQLite's ``PRAGMA user_version`` (no meta table). Migration
1 is ``schema.sql`` (CLAUDE.md §6 verbatim); future migrations append to the list.
"""
from __future__ import annotations

import importlib.resources
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1


def _schema_sql() -> str:
    return (
        importlib.resources.files("finwatch.db")
        .joinpath("schema.sql")
        .read_text(encoding="utf-8")
    )


def _migrations() -> list[tuple[int, str]]:
    """Ordered (version, sql) migrations. Append new versions; never edit old ones."""
    return [(1, _schema_sql())]


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with Row factory and foreign keys enforced."""
    db_str = str(db_path)
    if db_str != ":memory:":
        Path(db_str).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_str)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Apply all pending migrations in order; return the resulting schema version.

    Each migration runs atomically: the DDL and the ``user_version`` bump commit
    together (SQLite has transactional DDL and a transactional ``user_version``).
    A crash mid-migration rolls back to a clean, replayable state (no tables,
    ``user_version`` unchanged) rather than a half-built schema.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in _migrations():
        if version > current:
            # version is a trusted int (from _migrations), safe to interpolate.
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
            current = version
    return current


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Connect and bring the schema up to date. Returns the open connection."""
    conn = connect(db_path)
    apply_migrations(conn)
    return conn
