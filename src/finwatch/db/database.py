"""SQLite connection factory + fresh-schema installer.

This is a lean prototype: there is one current schema and no migration ladder. A blank
database is initialized from ``schema.sql``; a database created by an older finwatch
schema is rejected (``SchemaVersionError``) so the new code never runs against a legacy
layout. Back up the data directory and start fresh to upgrade.
"""
from __future__ import annotations

import importlib.resources
import os
import sqlite3
from pathlib import Path

# Bump SCHEMA_VERSION whenever schema.sql changes shape. APPLICATION_ID ("FWL1") marks a
# finwatch-lean database so a same-version file from another tool is still rejected.
SCHEMA_VERSION = 4
APPLICATION_ID = 0x46574C31


class SchemaVersionError(RuntimeError):
    """The database was created by a different/older schema and must not be opened."""


def _schema_sql() -> str:
    return (
        importlib.resources.files("finwatch.db")
        .joinpath("schema.sql")
        .read_text(encoding="utf-8")
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


def _install_or_verify_schema(conn: sqlite3.Connection) -> None:
    """Install the current schema on a blank database, accept a current one, and reject
    anything else. A blank database has application_id == 0 and user_version == 0."""
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if app_id == 0 and version == 0:
        conn.executescript(
            f"BEGIN;\n{_schema_sql()}\n"
            f"PRAGMA application_id = {APPLICATION_ID};\n"
            f"PRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
        )
        return
    if app_id != APPLICATION_ID or version != SCHEMA_VERSION:
        raise SchemaVersionError(
            "This data directory was created by a different finwatch schema and cannot "
            "be opened by this build. Back up the directory and start fresh "
            f"(expected application_id={APPLICATION_ID:#010x} user_version={SCHEMA_VERSION}, "
            f"found {app_id:#010x}/{version})."
        )


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Connect and install/verify the schema. Returns the open connection."""
    conn = connect(db_path)
    _install_or_verify_schema(conn)
    return conn
