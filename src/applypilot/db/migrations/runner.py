"""Migration runner — discovers per-file migrations and executes them in order.

Each migration is a file named `mNNN_description.py` with a `run(conn)` function.
The schema_version table tracks which have been applied.
"""

from __future__ import annotations

import importlib
import logging
import re
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

_MIGRATION_DIR = Path(__file__).parent
_MIGRATION_PATTERN = re.compile(r"^m(\d{3})_.*\.py$")


def _discover_migrations() -> dict[int, str]:
    """Scan the migrations directory for mNNN_*.py files.

    Returns:
        {version_int: module_name} sorted by version.
    """
    found: dict[int, str] = {}
    for f in sorted(_MIGRATION_DIR.iterdir()):
        m = _MIGRATION_PATTERN.match(f.name)
        if m:
            version = int(m.group(1))
            module_name = f"applypilot.db.migrations.{f.stem}"
            found[version] = module_name
    return found


def _get_current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] or 0 if row else 0
    except sqlite3.OperationalError:
        return 0


def _record_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (version,))


def run_pending_migrations(conn: sqlite3.Connection) -> list[int]:
    """Run all migrations newer than the current schema version.

    Returns list of applied migration numbers.
    """
    current = _get_current_version(conn)
    migrations = _discover_migrations()
    applied: list[int] = []

    for version in sorted(migrations):
        if version <= current:
            continue
        module_name = migrations[version]
        log.info("Running migration %03d: %s", version, module_name.split(".")[-1])
        try:
            mod = importlib.import_module(module_name)
            mod.run(conn)
            _record_version(conn, version)
            conn.commit()
            applied.append(version)
        except Exception:
            log.exception("Migration %03d failed", version)
            conn.rollback()
            break

    return applied
