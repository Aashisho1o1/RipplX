"""SQLite connection factory + migration runner.

Schema versioning uses SQLite's ``PRAGMA user_version`` (no meta table). Migration
1 is ``schema.sql`` (CLAUDE.md §6 verbatim); future migrations append to the list.
"""
from __future__ import annotations

import importlib.resources
import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3


class MigrationError(RuntimeError):
    """A migration cannot be applied without an explicit data repair."""


def _schema_sql() -> str:
    return (
        importlib.resources.files("finwatch.db")
        .joinpath("schema.sql")
        .read_text(encoding="utf-8")
    )


def _migration_sql(name: str) -> str:
    return importlib.resources.files("finwatch.db").joinpath(name).read_text(encoding="utf-8")


def _migrations() -> list[tuple[int, str]]:
    """Ordered (version, sql) migrations. Append new versions; never edit old ones."""
    return [
        (1, _schema_sql()),
        (2, _migration_sql("migration_002_filing_stage_runs.sql")),
        (3, _migration_sql("migration_003_unique_holding_cik.sql")),
    ]


def _check_migration_preconditions(conn: sqlite3.Connection, version: int) -> None:
    """Fail closed when a migration would otherwise make an arbitrary data choice."""
    if version != 3:
        return
    duplicates = conn.execute(
        """SELECT cik, COUNT(*) AS count
             FROM holdings
            GROUP BY cik
           HAVING COUNT(*) > 1
            ORDER BY cik
            LIMIT 10"""
    ).fetchall()
    if not duplicates:
        return
    summary = ", ".join(f"{row[0]} ({row[1]} rows)" for row in duplicates)
    raise MigrationError(
        "cannot enforce one holding per CIK: duplicate holdings exist for "
        f"{summary}. Back up the database, choose the one row to retain for each CIK, "
        "delete the duplicates, and restart finwatch. No migration changes were applied."
    )


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open an operational connection with the shared SQLite safety policy."""
    db_str = str(db_path)
    if db_str != ":memory:":
        path = Path(db_str)
        parent = path.parent
        parent_created = not parent.exists()
        parent.mkdir(parents=True, exist_ok=True)
        # Only chmod a directory we created. A caller may intentionally place the DB
        # in an existing shared directory (including /tmp); changing that directory's
        # mode would be a dangerous, process-wide side effect.
        if os.name == "posix" and parent_created:
            parent.chmod(0o700)
        if os.name == "posix" and not path.exists():
            # sqlite3.connect() would otherwise create the file under the process
            # umask and only then let us tighten it, leaving a short exposure window.
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                # Another local initializer won the creation race; both will still
                # enforce 0600 below before using the database.
                pass
            else:
                os.close(fd)
    conn = sqlite3.connect(db_str, timeout=5.0)
    if db_str != ":memory:" and os.name == "posix":
        # The database may pre-date this hardening, so enforce the mode on every open.
        # SQLite creates rollback/WAL companions from the database's permissions.
        Path(db_str).chmod(0o600)
    conn.row_factory = sqlite3.Row
    # A short bounded wait converts ordinary single-writer contention into latency
    # instead of an immediate failure. WAL lets browser reads continue while the one
    # background sync/analysis writer commits. Both pragmas are connection-local or
    # idempotent, so CLI and web callers receive the same behavior.
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
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
            _check_migration_preconditions(conn, version)
            # version is a trusted int (from _migrations), safe to interpolate.
            try:
                conn.executescript(
                    f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;"
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                if version == 3:
                    # A concurrent pre-v3 writer could create a duplicate after the
                    # preflight query but before the unique index statement.
                    raise MigrationError(
                        "cannot enforce one holding per CIK because duplicate holdings "
                        "were detected while applying migration 3. Back up the database, "
                        "retain one row per CIK, delete the duplicates, and restart finwatch. "
                        "No migration changes were applied."
                    ) from exc
                raise
            except sqlite3.Error:
                # executescript leaves an explicit BEGIN active if a statement fails.
                # Roll it back so callers never inherit a half-applied transaction.
                conn.rollback()
                raise
            current = version
    return current


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Connect and bring the schema up to date. Returns the open connection."""
    conn = connect(db_path)
    apply_migrations(conn)
    return conn
