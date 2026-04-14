"""LLM cost tracker (LLD §12.4)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Bedrock pricing per 1K tokens (USD) — updated 2026-04
# Source: https://aws.amazon.com/bedrock/pricing/
_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_1k, output_per_1k)
    # Anthropic
    "anthropic.claude-opus-4-6": (0.015, 0.075),
    "anthropic.claude-opus-4-5": (0.015, 0.075),
    "anthropic.claude-opus-4-1": (0.015, 0.075),
    "anthropic.claude-sonnet-4-6": (0.003, 0.015),
    "anthropic.claude-sonnet-4-5": (0.003, 0.015),
    "anthropic.claude-sonnet-4": (0.003, 0.015),
    "anthropic.claude-haiku-4-5": (0.0008, 0.004),
    # Amazon Nova
    "amazon.nova-micro": (0.000035, 0.00014),
    "amazon.nova-lite": (0.00006, 0.00024),
    "amazon.nova-pro": (0.0008, 0.0032),
    # Meta Llama
    "meta.llama3-3-70b": (0.00072, 0.00072),
    "meta.llama3-1-70b": (0.00072, 0.00072),
    "meta.llama3-1-8b": (0.00022, 0.00022),
    "meta.llama4-scout-17b": (0.00017, 0.00017),
    "meta.llama4-maverick-17b": (0.00017, 0.00017),
    # Mistral
    "mistral.mistral-large-3-675b": (0.002, 0.006),
    "mistral.mistral-small": (0.0001, 0.0003),
    "mistral.devstral": (0.0001, 0.0003),
    # DeepSeek
    "deepseek.r1": (0.00135, 0.00548),
    "deepseek.v3.2": (0.00027, 0.0011),
    # Qwen
    "qwen.qwen3-32b": (0.00016, 0.00016),
    "qwen.qwen3-coder-30b": (0.00016, 0.00016),
    # Google
    "google.gemma-3-27b": (0.0002, 0.0002),
    "google.gemma-3-12b": (0.0001, 0.0001),
    # NVIDIA
    "nvidia.nemotron-super-3-120b": (0.00078, 0.00078),
}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate cost from model name and token counts using pricing table."""
    model_lower = model.lower()
    # Strip prefixes: bedrock/, global., version suffixes
    for prefix in ("bedrock/", "global.", "openrouter/"):
        model_lower = model_lower.replace(prefix, "")

    # Try exact match first, then prefix match
    for key, (in_price, out_price) in _PRICING.items():
        if key in model_lower:
            return (tokens_in / 1000 * in_price) + (tokens_out / 1000 * out_price)
    return 0.0


@dataclass
class _Record:
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost: float  # from API (often 0 for Bedrock)
    estimated_cost: float = 0.0  # our calculation


class CostTracker:
    """Thread-safe LLM cost accumulator with estimated pricing."""

    def __init__(self) -> None:
        self._records: list[_Record] = []
        self._lock = threading.Lock()

    def record(self, provider: str, model: str, tokens_in: int, tokens_out: int, cost: float) -> None:
        estimated = _estimate_cost(model, tokens_in, tokens_out) if cost == 0.0 else cost
        with self._lock:
            self._records.append(_Record(provider, model, tokens_in, tokens_out, cost, estimated))

    def summary(self) -> dict:
        with self._lock:
            total_cost = sum(r.estimated_cost for r in self._records)
            total_in = sum(r.tokens_in for r in self._records)
            total_out = sum(r.tokens_out for r in self._records)
            by_model: dict[str, dict] = {}
            for r in self._records:
                entry = by_model.setdefault(r.model, {"cost": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0})
                entry["cost"] += r.estimated_cost
                entry["calls"] += 1
                entry["tokens_in"] += r.tokens_in
                entry["tokens_out"] += r.tokens_out
            return {
                "total_cost": total_cost,
                "total_tokens_in": total_in,
                "total_tokens_out": total_out,
                "calls": len(self._records),
                "by_model": by_model,
            }
