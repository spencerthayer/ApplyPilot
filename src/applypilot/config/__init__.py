"""ApplyPilot configuration — re-exports from decomposed modules.

All public names are re-exported here so existing `from applypilot.config import X`
continues to work. New code should import from the specific submodule.
"""

# ── paths ────────────────────────────────────────────────────────────
from applypilot.config.paths import (  # noqa: F401
    APP_DIR,
    DB_PATH,
    PROFILE_PATH,
    RESUME_JSON_PATH,
    RESUME_PATH,
    RESUME_PDF_PATH,
    SEARCH_CONFIG_PATH,
    ENV_PATH,
    TAILORED_DIR,
    COVER_LETTER_DIR,
    TRACKING_DIR,
    LOG_DIR,
    CHROME_WORKER_DIR,
    APPLY_WORKER_DIR,
    OPENCODE_CONFIG_DIR,
    OPENCODE_CONFIG_PATH,
    SESSIONS_DIR,
    FILES_DIR,
    PACKAGE_DIR,
    CONFIG_DIR,
    ensure_dirs,
)

# ── profile / resume loading ────────────────────────────────────────
from applypilot.config.profile_loader import (  # noqa: F401
    get_resume_source,
    load_resume_json,
    load_profile,
    load_resume_text,
)
from applypilot.resume_json import ResumeJsonError, CanonicalResumeSource  # noqa: F401

# ── search config ───────────────────────────────────────────────────
from applypilot.config.search_config import load_search_config  # noqa: F401

# ── sites config ────────────────────────────────────────────────────
from applypilot.config.sites import (  # noqa: F401
    load_sites_config,
    is_manual_ats,
    load_blocked_sites,
    load_blocked_sso,
    load_no_signup_domains,
    load_base_urls,
)

# ── defaults / env ──────────────────────────────────────────────────
from applypilot.config.defaults import (  # noqa: F401
    DEFAULTS,
    get_runtime_defaults,
    load_env,
    _env,
)

# ── re-export llm_provider helpers used by tests ────────────────────
from applypilot.llm_provider import has_llm_provider, llm_config_hint  # noqa: F401

# ── chrome / agents / tiers ─────────────────────────────────────────
import shutil  # noqa: F401 — re-exported for test monkeypatch compat
from applypilot.config.resume_config import (  # noqa: F401
    AUTO_APPLY_AGENT_CHOICES,
    AUTO_APPLY_AGENT_LABELS,
    DEFAULT_AUTO_APPLY_AGENT,
    DEFAULT_AUTO_APPLY_AGENT_PRIORITY,
    DEFAULT_CLAUDE_AUTO_APPLY_MODEL,
    DEFAULT_OPENCODE_AUTO_APPLY_MODEL,
    DEFAULT_OPENCODE_AUTO_APPLY_AGENT,
    OPENCODE_REQUIRED_MCP_SERVERS,
    AutoApplyAgentStatus,
    AutoApplyAgentSelection,
    get_chrome_path,
    get_chrome_user_data,
    get_auto_apply_agent_setting,
    get_auto_apply_agent_priority,
    get_auto_apply_model_setting,
    get_opencode_agent_setting,
    get_codex_login_status,
    get_opencode_binary_path,
    get_opencode_mcp_servers,
    get_auto_apply_agent_statuses,
    resolve_auto_apply_agent,
    has_auto_apply_backend,
    describe_auto_apply_backend_requirement,
    TIER_LABELS,
    TIER_COMMANDS,
    get_tier,
    check_tier,
    AUTO_APPLY_AGENT_PRIORITY_CHOICES,
)
