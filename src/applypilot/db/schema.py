"""Schema-from-DTO: auto table creation and migration from DTO definitions.

DTOs are the SINGLE SOURCE OF TRUTH for table schemas. No separate CREATE TABLE SQL.
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
import typing

from applypilot.db.dto import ALL_DTOS

log = logging.getLogger(__name__)

_TYPE_MAP: dict[type, str] = {
    str: "TEXT",
    int: "INTEGER",
    float: "REAL",
    bool: "INTEGER",
}


def _sql_type(annotation) -> str:
    """Map a Python type annotation to SQLite column type."""
    # Handle string annotations (from `from __future__ import annotations`)
    if isinstance(annotation, str):
        stripped = annotation.replace(" ", "")
        for py_type, sql_type in [("int", "INTEGER"), ("float", "REAL"), ("bool", "INTEGER"), ("str", "TEXT")]:
            if stripped == py_type or stripped.startswith(f"{py_type}|") or stripped == f"{py_type}|None":
                return sql_type
        return "TEXT"
    # Handle X | None (Python 3.10+ union syntax) and typing.Optional[X]
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", None)
    if args and (origin is typing.Union or type(annotation).__name__ == "UnionType"):
        inner = [a for a in args if a is not type(None)]
        return _sql_type(inner[0]) if inner else "TEXT"
    return _TYPE_MAP.get(annotation, "TEXT")


def schema_from_dto(conn: sqlite3.Connection) -> list[str]:
    """Introspect ALL registered DTOs → CREATE TABLE + indexes. Returns created table names."""
    created = []
    for dto_cls in ALL_DTOS:
        table = dto_cls.__table_name__
        config = dto_cls.__table_config__
        fields = dataclasses.fields(dto_cls)
        pk = config.get("primary_key")

        col_defs = []
        for f in fields:
            sql_t = _sql_type(f.type)
            col_def = f"{f.name} {sql_t}"
            if isinstance(pk, str) and f.name == pk:
                col_def += " PRIMARY KEY"
            col_defs.append(col_def)

        pk_clause = ""
        if isinstance(pk, tuple):
            pk_clause = f", PRIMARY KEY ({', '.join(pk)})"

        ddl = f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(col_defs)}{pk_clause})"
        conn.execute(ddl)
        created.append(table)

    conn.commit()
    return created


def _create_indexes(conn: sqlite3.Connection) -> None:
    """Create indexes and unique constraints for all DTOs."""
    for dto_cls in ALL_DTOS:
        table = dto_cls.__table_name__
        config = dto_cls.__table_config__

        for idx in config.get("indexes", []):
            cols = idx if isinstance(idx, tuple) else (idx,)
            idx_name = f"idx_{table}_{'_'.join(cols)}"
            try:
                conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({', '.join(cols)})")
            except sqlite3.OperationalError:
                pass  # Column may not exist yet in pre-migration tables

        for uniq in config.get("unique", []):
            cols = uniq if isinstance(uniq, tuple) else (uniq,)
            uq_name = f"uq_{table}_{'_'.join(cols)}"
            try:
                conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {uq_name} ON {table}({', '.join(cols)})")
            except sqlite3.OperationalError:
                pass

    conn.commit()


def migrate_from_dto(conn: sqlite3.Connection) -> list[str]:
    """Compare DTO fields vs existing columns, add missing ones. Returns added columns."""
    added = []
    for dto_cls in ALL_DTOS:
        table = dto_cls.__table_name__
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if not existing:
            continue
        for f in dataclasses.fields(dto_cls):
            if f.name not in existing:
                sql_t = _sql_type(f.type)
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {f.name} {sql_t}")
                added.append(f"{table}.{f.name}")
    if added:
        conn.commit()
        log.info("Migrated columns: %s", added)
    return added


def init_db(conn: sqlite3.Connection) -> None:
    """Full DB initialization: create tables, migrate columns, indexes, then versioned migrations."""
    schema_from_dto(conn)
    migrate_from_dto(conn)
    _create_indexes(conn)
    # Run versioned migrations (data transforms, backfills)
    from applypilot.db.migrations import run_pending_migrations

    applied = run_pending_migrations(conn)
    if applied:
        log.info("Applied %d migration(s): %s", len(applied), applied)
