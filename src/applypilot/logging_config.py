"""Structured JSON logging with PII redaction (LLD §17.1).

Configurable via:
  - CLI flag: --log-level DEBUG
  - Env var: LOG_LEVEL=DEBUG
  - config.yaml: logging.level: DEBUG
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

# Correlation ID — set per-job to trace across stages
correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")

# PII patterns to redact from log output
_PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "<email>"),
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "<phone>"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "<ssn>"),
    (re.compile(r"(?:api[_-]?key|token|secret|password|credential)[=:]\s*\S+", re.IGNORECASE), "<redacted-credential>"),
]


def redact_pii(text: str) -> str:
    """Redact PII patterns from text."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class StructuredFormatter(logging.Formatter):
    """JSON log formatter with PII redaction."""

    def format(self, record: logging.LogRecord) -> str:
        msg = redact_pii(record.getMessage())
        entry: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
        }
        cid = correlation_id.get()
        if cid:
            entry["correlation_id"] = cid
        for attr in ("stage", "event_type", "duration_ms", "provider", "model", "tokens_in", "tokens_out", "cost_usd"):
            if (val := getattr(record, attr, None)) is not None:
                entry[attr] = val
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = redact_pii(self.formatException(record.exc_info))
        return json.dumps({k: v for k, v in entry.items() if v is not None}, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Human-readable formatter with PII redaction."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        record.msg = redact_pii(str(record.msg))
        return super().format(record)


def configure_logging(
        level: str | None = None,
        json_output: bool = False,
) -> None:
    """Configure root logger.

    Precedence: explicit level > LOG_LEVEL env > INFO default.
    """
    resolved_level = level or os.environ.get("LOG_LEVEL", "INFO")
    root = logging.getLogger()
    root.setLevel(getattr(logging, resolved_level.upper(), logging.INFO))
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(StructuredFormatter() if json_output else HumanFormatter())
    root.addHandler(handler)

    # Keep noisy libraries quiet
    for name in ("LiteLLM", "litellm", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


# Backward compat
configure_structured_logging = configure_logging
