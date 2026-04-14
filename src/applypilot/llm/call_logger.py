"""LLM call audit logging with PII redaction (LLD §17.2).

Logs provider, model, token counts, latency, cost, and prompt hash.
Never logs prompt text or response text at INFO level.
"""

from __future__ import annotations

import hashlib
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass

log = logging.getLogger("applypilot.llm.calls")


@dataclass
class LLMCallRecord:
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    prompt_hash: str = ""
    success: bool = True
    error: str | None = None


def hash_prompt(messages: list[dict]) -> str:
    """SHA-256 hash of prompt messages. No PII in output."""
    import json

    raw = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def log_call(record: LLMCallRecord) -> None:
    """Emit structured log entry for an LLM call."""
    log.info(
        "llm_call %s/%s tokens=%d/%d latency=%dms cost=$%.4f%s",
        record.provider,
        record.model,
        record.tokens_in,
        record.tokens_out,
        record.latency_ms,
        record.cost_usd,
        f" ERROR={record.error}" if record.error else "",
        extra={
            "event_type": "llm_call",
            "provider": record.provider,
            "model": record.model,
            "tokens_in": record.tokens_in,
            "tokens_out": record.tokens_out,
            "duration_ms": record.latency_ms,
            "cost_usd": record.cost_usd,
            "prompt_hash": record.prompt_hash,
        },
    )


@contextmanager
def track_llm_call(provider: str, model: str, messages: list[dict] | None = None):
    """Context manager that times an LLM call and logs the result."""
    record = LLMCallRecord(
        provider=provider,
        model=model,
        prompt_hash=hash_prompt(messages) if messages else "",
    )
    t0 = time.time()
    try:
        yield record
    except Exception as e:
        record.success = False
        record.error = type(e).__name__
        raise
    finally:
        record.latency_ms = int((time.time() - t0) * 1000)
        log_call(record)
