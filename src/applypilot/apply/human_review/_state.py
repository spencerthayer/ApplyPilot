"""Shared HITL session state."""

import threading

_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()

_hitl_chrome_proc = None
_hitl_chrome_lock = threading.Lock()
