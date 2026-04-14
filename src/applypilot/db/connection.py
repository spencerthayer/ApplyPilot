"""SQLite connection management — re-exports from canonical location.

The canonical implementation lives in db/sqlite/connection.py.
This file exists for backward compatibility with code that imports
from applypilot.db.connection.
"""

from applypilot.db.sqlite.connection import (
    close_all_connections,
    close_connection,
    get_connection,
)

__all__ = ["get_connection", "close_connection", "close_all_connections"]
