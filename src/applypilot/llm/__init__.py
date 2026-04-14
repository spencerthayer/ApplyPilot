"""LLM module — re-exports from decomposed files."""

from applypilot.llm.client import litellm  # noqa: F401 — re-exported for test compat
from applypilot.llm.config import (  # noqa: F401
    LLMTier,
    LLMConfig,
    ModelEntry,
    ChatMessage,
    LiteLLMExtra,
    resolve_llm_config,
)
from applypilot.llm.fallback import build_fallback_chain as _build_fallback_chain  # noqa: F401
from applypilot.llm.rate_limiter import (  # noqa: F401
    is_openrouter_free_model as _is_openrouter_free_model,
    apply_openrouter_pacing as _apply_openrouter_pacing,
    respect_openrouter_cooldown as _respect_openrouter_cooldown,
    note_openrouter_rate_limit as _note_openrouter_rate_limit,
)
from applypilot.llm.cost_tracker import CostTracker  # noqa: F401
from applypilot.llm.client import LLMClient, _detect_provider  # noqa: F401
from applypilot.llm.factory import get_client, get_cost_summary  # noqa: F401
