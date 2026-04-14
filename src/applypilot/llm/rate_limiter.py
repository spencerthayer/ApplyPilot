"""OpenRouter rate limiting — pacing and cooldown for free-tier models."""

from __future__ import annotations

import threading
import time

_OPENROUTER_FREE_MIN_INTERVAL_SECONDS = 3.5
_OPENROUTER_RATE_LIMIT_COOLDOWN_SECONDS = 20.0

_openrouter_lock = threading.Lock()
_openrouter_next_allowed_at = 0.0
_openrouter_last_call_at = 0.0


def is_openrouter_free_model(model_name: str) -> bool:
    return model_name.startswith("openrouter/") and model_name.endswith(":free")


def apply_openrouter_pacing(model_name: str) -> None:
    """Serialize free-tier OpenRouter calls to avoid per-minute bursts."""
    global _openrouter_last_call_at
    if not is_openrouter_free_model(model_name):
        return
    with _openrouter_lock:
        now = time.time()
        wait = max(0.0, _openrouter_last_call_at + _OPENROUTER_FREE_MIN_INTERVAL_SECONDS - now)
        if wait > 0:
            time.sleep(wait)
        _openrouter_last_call_at = time.time()


def respect_openrouter_cooldown(model_name: str) -> None:
    """Block until shared cooldown expires after a free-tier 429 burst."""
    if not is_openrouter_free_model(model_name):
        return
    with _openrouter_lock:
        now = time.time()
        if now < _openrouter_next_allowed_at:
            time.sleep(_openrouter_next_allowed_at - now)


def note_openrouter_rate_limit(model_name: str) -> None:
    """Move all free-tier OpenRouter callers into a short cooldown window."""
    global _openrouter_next_allowed_at
    if not is_openrouter_free_model(model_name):
        return
    with _openrouter_lock:
        _openrouter_next_allowed_at = max(
            _openrouter_next_allowed_at,
            time.time() + _OPENROUTER_RATE_LIMIT_COOLDOWN_SECONDS,
        )
