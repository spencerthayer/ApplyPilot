"""Auto-apply browser agent backends — re-exports from decomposed modules."""

from applypilot import config  # noqa: F401 — re-exported for test monkeypatch compat
import subprocess  # noqa: F401 — re-exported for test monkeypatch compat

# ── Base types ──────────────────────────────────────────────────────
from applypilot.apply.backends.base import (  # noqa: F401
    AutoApplyBackend,
    BackendError,
    BackendExecution,
    InvalidBackendError,
    ProcessRegistrar,
    ProcessUnregister,
    DISALLOWED_GMAIL_TOOLS,
    extract_result_status,
    make_mcp_config,
    shell_join,
    fallback_failure_reason,
    terminate_process,
    job_log_path,
    log_header,
    describe_tool_use,
)
from applypilot.apply.chrome import reset_worker_dir  # noqa: F401

# ── Concrete backends ───────────────────────────────────────────────
from applypilot.apply.backends.claude_backend import (  # noqa: F401
    ClaudeAutoApplyBackend,
    build_claude_command,
)
from applypilot.apply.backends.codex_backend import (  # noqa: F401
    CodexAutoApplyBackend,
    build_codex_command,
)
from applypilot.apply.backends.opencode_backend import (  # noqa: F401
    OpenCodeAutoApplyBackend,
)

# ── Registry ────────────────────────────────────────────────────────
from applypilot.apply.backends.registry import (  # noqa: F401
    get_backend,
    get_available_backends,
    resolve_backend_name,
    detect_backends,
    get_preferred_backend,
    resolve_default_model,
    resolve_default_agent,
    VALID_BACKENDS,
    DEFAULT_BACKEND,
)

# ── Compatibility aliases ───────────────────────────────────────────
AgentBackend = AutoApplyBackend
AgentBackendError = BackendError
ClaudeBackend = ClaudeAutoApplyBackend
CodexBackend = CodexAutoApplyBackend
OpenCodeBackend = OpenCodeAutoApplyBackend

# Re-export _fallback_failure_reason under old name for launcher compat
_fallback_failure_reason = fallback_failure_reason
_make_mcp_config = make_mcp_config


def build_manual_command(agent: str, prompt_file, worker_id: int, model=None) -> str:
    backend = get_backend(agent)
    return backend.build_manual_command(prompt_file=prompt_file, worker_id=worker_id, model=model)
