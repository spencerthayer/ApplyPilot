"""Shared base class for all SQLite repository implementations."""

from __future__ import annotations

import dataclasses
import sqlite3

from applypilot.db.sqlite.connection import get_connection
from applypilot.db.sqlite.write_retry import write_with_retry


class SqliteBaseRepo:
    """Shared helpers: row↔DTO conversion, write-with-retry."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        self._conn = conn or get_connection()

    def _row_to_dto(self, row: sqlite3.Row, dto_cls):
        """Convert sqlite3.Row → frozen DTO, ignoring extra DB columns."""
        field_names = {f.name for f in dataclasses.fields(dto_cls)}
        return dto_cls(**{k: row[k] for k in field_names if k in row.keys()})

    def _dto_to_params(self, dto) -> dict:
        """Convert DTO → dict for INSERT/UPDATE."""
        return dataclasses.asdict(dto)

    def _write(self, fn, *args, **kwargs):
        write_with_retry(self._conn, fn, *args, **kwargs)
