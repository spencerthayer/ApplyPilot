"""LLM client factory — thread-safe singleton with tier support."""

from __future__ import annotations

import atexit
import logging
import threading

from applypilot.llm.client import LLMClient
from applypilot.llm.config import LLMTier, resolve_llm_config

log = logging.getLogger(__name__)

_lock = threading.Lock()
_clients: dict[str, LLMClient] = {}


def get_client(quality: bool = False, *, tier: LLMTier | str | None = None) -> LLMClient:
    """Return a singleton LLMClient for the requested tier.

    Thread-safe double-checked locking. One client per tier.
    """
    if tier is not None:
        key = str(tier)
    elif quality:
        key = "quality"
    else:
        key = "cheap"

    client = _clients.get(key)
    if client is not None:
        return client

    with _lock:
        client = _clients.get(key)
        if client is not None:
            return client

        try:
            from applypilot.config import load_env

            load_env()
        except Exception:
            pass

        config = resolve_llm_config(quality=quality, tier=LLMTier(tier) if isinstance(tier, str) else tier)
        log.info("LLM provider: %s  model: %s  tier: %s", config.provider, config.model, key)
        client = LLMClient(config, quality=quality)
        atexit.register(client.close)
        _clients[key] = client
        return client


def get_cost_summary() -> dict:
    """Aggregate cost — current session (in-memory) + historical (DB)."""
    total: dict = {"calls": 0, "total_cost": 0.0, "total_tokens_in": 0, "total_tokens_out": 0, "by_model": {}}

    # Current session (in-memory)
    with _lock:
        for client in _clients.values():
            s = client._cost_tracker.summary()
            total["calls"] += s["calls"]
            total["total_cost"] += s["total_cost"]
            total["total_tokens_in"] += s["total_tokens_in"]
            total["total_tokens_out"] += s["total_tokens_out"]
            for model, cost in s["by_model"].items():
                total["by_model"][model] = total["by_model"].get(model, 0.0) + cost

    # Historical (DB) — all persisted llm_call events
    try:
        import json
        from applypilot.bootstrap import get_app
        from applypilot.llm.cost_tracker import _estimate_cost

        repo = get_app().container.analytics_repo
        events = repo.get_by_type("llm_call", limit=10000)
        for event in events:
            payload = json.loads(event.payload) if isinstance(event.payload, str) else event.payload
            tokens_in = payload.get("tokens_in", 0)
            tokens_out = payload.get("tokens_out", 0)
            model = payload.get("model", "unknown")
            cost = payload.get("cost_usd", 0.0)
            if cost == 0.0:
                cost = _estimate_cost(model, tokens_in, tokens_out)
            total["calls"] += 1
            total["total_cost"] += cost
            total["total_tokens_in"] += tokens_in
            total["total_tokens_out"] += tokens_out
            entry = total["by_model"].setdefault(model, {"cost": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0})
            if isinstance(entry, dict):
                entry["cost"] += cost
                entry["calls"] += 1
                entry["tokens_in"] += tokens_in
                entry["tokens_out"] += tokens_out
            else:
                total["by_model"][model] = {
                    "cost": entry + cost,
                    "calls": 1,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                }
    except Exception:
        pass

    return total


def reset_clients() -> None:
    """Close and clear all singletons — used in tests."""
    with _lock:
        for client in _clients.values():
            try:
                client.close()
            except Exception:
                pass
        _clients.clear()
