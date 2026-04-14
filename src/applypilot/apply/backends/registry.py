"""Backend registry — discovery, resolution, and instantiation."""

from __future__ import annotations

import os
from typing import Mapping

from applypilot import config
from applypilot.apply.backends.base import AutoApplyBackend, InvalidBackendError
from applypilot.apply.backends.claude_backend import ClaudeAutoApplyBackend
from applypilot.apply.backends.codex_backend import CodexAutoApplyBackend

# Lazy import to avoid circular dependency
_opencode_cls = None


def _get_opencode_cls():
    global _opencode_cls
    if _opencode_cls is None:
        from applypilot.apply.backends.opencode_backend import OpenCodeAutoApplyBackend

        _opencode_cls = OpenCodeAutoApplyBackend
    return _opencode_cls


_BACKENDS: dict[str, AutoApplyBackend] | None = None


def _get_backends() -> dict[str, AutoApplyBackend]:
    global _BACKENDS
    if _BACKENDS is None:
        _BACKENDS = {
            "claude": ClaudeAutoApplyBackend(),
            "codex": CodexAutoApplyBackend(),
            "opencode": _get_opencode_cls()(),
        }
    # Lazy-register native
    if "native" not in _BACKENDS:
        from applypilot.apply.native_agent import NativePlaywrightBackend

        _BACKENDS["native"] = NativePlaywrightBackend()
    return _BACKENDS


VALID_BACKENDS: frozenset[str] = frozenset(["claude", "codex", "opencode", "native"])
DEFAULT_BACKEND = "claude"


def get_backend(agent: str | None = None) -> AutoApplyBackend:
    backends = _get_backends()
    agent = resolve_backend_name(agent)
    try:
        return backends[agent]
    except KeyError as exc:
        raise InvalidBackendError(agent, VALID_BACKENDS) from exc


def get_available_backends() -> frozenset[str]:
    return VALID_BACKENDS


def resolve_backend_name(backend_name: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    if backend_name is not None:
        raw = backend_name
    elif env.get("AUTO_APPLY_AGENT", "").strip() and env.get("AUTO_APPLY_AGENT", "").strip().lower() != "auto":
        raw = env.get("AUTO_APPLY_AGENT", "")
    else:
        raw = env.get("APPLY_BACKEND", DEFAULT_BACKEND)
    normalized = raw.lower().strip()
    if not normalized or normalized not in VALID_BACKENDS:
        raise InvalidBackendError(normalized or raw, VALID_BACKENDS)
    return normalized


def detect_backends() -> list[str]:
    backends = _get_backends()
    available: list[str] = []
    for key in ("codex", "claude", "opencode"):
        try:
            if backends[key].is_installed():
                available.append(key)
        except Exception:
            pass
    return available


def get_preferred_backend(environ: Mapping[str, str] | None = None) -> str | None:
    return config.resolve_auto_apply_agent(environ=environ).resolved


def resolve_default_model(backend_name: str, environ: Mapping[str, str] | None = None) -> str | None:
    return config.get_auto_apply_model_setting(backend_name, os.environ if environ is None else environ)


def resolve_default_agent(backend_name: str, environ: Mapping[str, str] | None = None) -> str | None:
    if backend_name == "opencode":
        return config.get_opencode_agent_setting(os.environ if environ is None else environ)
    return None
