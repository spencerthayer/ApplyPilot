"""Test bootstrap helpers."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ensure tests run with isolated ApplyPilot state."""

    monkeypatch.delenv("APPLY_BACKEND", raising=False)
    monkeypatch.setenv("APPLYPILOT_DIR", str(tmp_path / "applypilot"))
    (tmp_path / "applypilot" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "applypilot" / "apply-workers").mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _suppress_resource_warnings():
    """Suppress unclosed database ResourceWarnings in tests.

    These come from Container @property creating connections per access
    and state_machine fallback paths. Not a leak in production (connections
    are long-lived singletons), but noisy in tests.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed database")
        yield
