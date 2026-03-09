"""ApplyPilot configuration: paths, platform detection, user data."""

from collections.abc import Mapping
from dataclasses import dataclass
import os
import platform
import shutil
import subprocess
from pathlib import Path

from applypilot.llm_provider import has_llm_provider, llm_config_hint

# User data directory — all user-specific files live here
APP_DIR = Path(os.environ.get("APPLYPILOT_DIR", Path.home() / ".applypilot"))

# Core paths
DB_PATH = APP_DIR / "applypilot.db"
PROFILE_PATH = APP_DIR / "profile.json"
RESUME_PATH = APP_DIR / "resume.txt"
RESUME_PDF_PATH = APP_DIR / "resume.pdf"
SEARCH_CONFIG_PATH = APP_DIR / "searches.yaml"
ENV_PATH = APP_DIR / ".env"

# Generated output
TAILORED_DIR = APP_DIR / "tailored_resumes"
COVER_LETTER_DIR = APP_DIR / "cover_letters"
LOG_DIR = APP_DIR / "logs"

# Chrome worker isolation
CHROME_WORKER_DIR = APP_DIR / "chrome-workers"
APPLY_WORKER_DIR = APP_DIR / "apply-workers"

# Package-shipped config (YAML registries)
PACKAGE_DIR = Path(__file__).parent
CONFIG_DIR = PACKAGE_DIR / "config"

AUTO_APPLY_AGENT_CHOICES = ("auto", "codex", "claude")
AUTO_APPLY_AGENT_PRIORITY_CHOICES = ("codex", "claude")
AUTO_APPLY_AGENT_LABELS = {
    "auto": "Auto-detect",
    "codex": "Codex CLI",
    "claude": "Claude Code CLI",
}
DEFAULT_AUTO_APPLY_AGENT = "auto"
DEFAULT_AUTO_APPLY_AGENT_PRIORITY = ("codex", "claude")
DEFAULT_CLAUDE_AUTO_APPLY_MODEL = "haiku"


@dataclass(frozen=True)
class AutoApplyAgentStatus:
    """Availability details for an auto-apply browser agent."""

    key: str
    label: str
    binary_path: str | None
    available: bool
    note: str
    auth_ok: bool = False


@dataclass(frozen=True)
class AutoApplyAgentSelection:
    """Resolved auto-apply agent and model settings."""

    requested: str
    resolved: str | None
    model: str | None


def get_chrome_path() -> str:
    """Auto-detect Chrome/Chromium executable path, cross-platform.

    Override with CHROME_PATH environment variable.
    """
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    system = platform.system()

    if system == "Windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    else:  # Linux
        candidates = []
        for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    for c in candidates:
        if c and c.exists():
            return str(c)

    # Fall back to PATH search
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found

    raise FileNotFoundError(
        "Chrome/Chromium not found. Install Chrome or set CHROME_PATH environment variable."
    )


def get_chrome_user_data() -> Path:
    """Default Chrome user data directory, cross-platform."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        return Path.home() / ".config" / "google-chrome"


def ensure_dirs():
    """Create all required directories."""
    for d in [APP_DIR, TAILORED_DIR, COVER_LETTER_DIR, LOG_DIR, CHROME_WORKER_DIR, APPLY_WORKER_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_profile() -> dict:
    """Load user profile from ~/.applypilot/profile.json."""
    import json
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"Profile not found at {PROFILE_PATH}. Run `applypilot init` first."
        )
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def load_search_config() -> dict:
    """Load search configuration from ~/.applypilot/searches.yaml."""
    import yaml
    if not SEARCH_CONFIG_PATH.exists():
        # Fall back to package-shipped example
        example = CONFIG_DIR / "searches.example.yaml"
        if example.exists():
            return yaml.safe_load(example.read_text(encoding="utf-8"))
        return {}
    return yaml.safe_load(SEARCH_CONFIG_PATH.read_text(encoding="utf-8"))


def load_sites_config() -> dict:
    """Load sites.yaml configuration (sites list, manual_ats, blocked, etc.)."""
    import yaml
    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_manual_ats(url: str | None) -> bool:
    """Check if a URL routes through an ATS that requires manual application."""
    if not url:
        return False
    sites_cfg = load_sites_config()
    domains = sites_cfg.get("manual_ats", [])
    url_lower = url.lower()
    return any(domain in url_lower for domain in domains)


def load_blocked_sites() -> tuple[set[str], list[str]]:
    """Load blocked sites and URL patterns from sites.yaml.

    Returns:
        (blocked_site_names, blocked_url_patterns)
    """
    cfg = load_sites_config()
    blocked = cfg.get("blocked", {})
    sites = set(blocked.get("sites", []))
    patterns = blocked.get("url_patterns", [])
    return sites, patterns


def load_blocked_sso() -> list[str]:
    """Load blocked SSO domains from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("blocked_sso", [])


def load_base_urls() -> dict[str, str | None]:
    """Load site base URLs for URL resolution from sites.yaml."""
    cfg = load_sites_config()
    return cfg.get("base_urls", {})


# ---------------------------------------------------------------------------
# Default values — referenced across modules instead of magic numbers
# ---------------------------------------------------------------------------

DEFAULTS = {
    "min_score": 7,
    "max_apply_attempts": 3,
    "max_tailor_attempts": 5,
    "poll_interval": 60,
    "apply_timeout": 300,
    "viewport": "1280x900",
}


def load_env():
    """Load environment variables from ~/.applypilot/.env if it exists."""
    from dotenv import load_dotenv
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    # Also try CWD .env as fallback
    load_dotenv()


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def get_auto_apply_agent_setting(environ: Mapping[str, str] | None = None) -> str:
    """Return the configured auto-apply agent preference."""

    env = _env(environ)
    value = env.get("AUTO_APPLY_AGENT", DEFAULT_AUTO_APPLY_AGENT).strip().lower()
    if value not in AUTO_APPLY_AGENT_CHOICES:
        return DEFAULT_AUTO_APPLY_AGENT
    return value


def get_auto_apply_agent_priority(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return the fallback order used when AUTO_APPLY_AGENT=auto."""

    env = _env(environ)
    configured = env.get("AUTO_APPLY_AGENT_PRIORITY", "").strip()
    if not configured:
        return DEFAULT_AUTO_APPLY_AGENT_PRIORITY

    ordered: list[str] = []
    for raw_part in configured.split(","):
        part = raw_part.strip().lower()
        if part in AUTO_APPLY_AGENT_PRIORITY_CHOICES and part not in ordered:
            ordered.append(part)

    for part in DEFAULT_AUTO_APPLY_AGENT_PRIORITY:
        if part not in ordered:
            ordered.append(part)

    return tuple(ordered)


def get_auto_apply_model_setting(
    agent: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the configured auto-apply agent model override."""

    env = _env(environ)
    configured = env.get("AUTO_APPLY_MODEL", "").strip()
    if configured:
        return configured
    if agent == "claude":
        return DEFAULT_CLAUDE_AUTO_APPLY_MODEL
    return None


def get_codex_login_status(timeout: int = 10) -> tuple[bool, str]:
    """Return whether Codex CLI is logged in plus a short status note."""

    codex_bin = shutil.which("codex")
    if not codex_bin:
        return False, "Install Codex CLI and run `codex login`"

    try:
        result = subprocess.run(
            [codex_bin, "login", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Codex login check failed: {exc}"

    output_lines = [
        line.strip()
        for line in f"{result.stdout}\n{result.stderr}".splitlines()
        if line.strip()
    ]
    status_line = next((line for line in reversed(output_lines) if "Logged in" in line), "")
    if result.returncode == 0 and status_line:
        return True, status_line

    if output_lines:
        return False, output_lines[-1]
    return False, "Run `codex login`"


def get_auto_apply_agent_statuses() -> dict[str, AutoApplyAgentStatus]:
    """Return availability diagnostics for supported auto-apply backends."""

    codex_bin = shutil.which("codex")
    codex_logged_in, codex_note = get_codex_login_status() if codex_bin else (
        False,
        "Install Codex CLI and run `codex login`",
    )
    claude_bin = shutil.which("claude")

    return {
        "codex": AutoApplyAgentStatus(
            key="codex",
            label=AUTO_APPLY_AGENT_LABELS["codex"],
            binary_path=codex_bin,
            available=bool(codex_bin) and codex_logged_in,
            note=codex_note if codex_bin else "Install Codex CLI and run `codex login`",
            auth_ok=codex_logged_in,
        ),
        "claude": AutoApplyAgentStatus(
            key="claude",
            label=AUTO_APPLY_AGENT_LABELS["claude"],
            binary_path=claude_bin,
            available=claude_bin is not None,
            note=claude_bin or "Install from https://claude.ai/code",
            auth_ok=claude_bin is not None,
        ),
    }


def resolve_auto_apply_agent(
    preferred: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> AutoApplyAgentSelection:
    """Resolve the auto-apply backend and optional model override."""

    requested = preferred.lower() if preferred else get_auto_apply_agent_setting(environ)
    if requested not in AUTO_APPLY_AGENT_CHOICES:
        requested = DEFAULT_AUTO_APPLY_AGENT

    statuses = get_auto_apply_agent_statuses()
    resolved: str | None = None
    if requested == "auto":
        for candidate in get_auto_apply_agent_priority(environ):
            if statuses[candidate].available:
                resolved = candidate
                break
    elif statuses[requested].available:
        resolved = requested

    return AutoApplyAgentSelection(
        requested=requested,
        resolved=resolved,
        model=get_auto_apply_model_setting(resolved, environ),
    )


def has_auto_apply_backend() -> bool:
    """Return whether any supported auto-apply backend is ready to use."""

    statuses = get_auto_apply_agent_statuses()
    return any(status.available for status in statuses.values())


def describe_auto_apply_backend_requirement() -> str:
    """Return a short human-readable Tier 3 requirement hint."""

    return "supported auto-apply agent CLI (Codex logged in or Claude installed)"


# ---------------------------------------------------------------------------
# Tier system — feature gating by installed dependencies
# ---------------------------------------------------------------------------

TIER_LABELS = {
    1: "Discovery",
    2: "AI Scoring & Tailoring",
    3: "Full Auto-Apply",
}

TIER_COMMANDS: dict[int, list[str]] = {
    1: ["init", "run discover", "run enrich", "status", "dashboard"],
    2: ["run score", "run tailor", "run cover", "run pdf", "run"],
    3: ["apply"],
}


def get_tier() -> int:
    """Detect the current tier based on available dependencies.

    Tier 1 (Discovery):            Python + pip
    Tier 2 (AI Scoring & Tailoring): + LLM provider
    Tier 3 (Full Auto-Apply):       + auto-apply agent CLI + Chrome + Node.js
    """
    load_env()

    if not has_llm_provider():
        return 1

    has_agent = has_auto_apply_backend()
    has_npx = shutil.which("npx") is not None
    try:
        get_chrome_path()
        has_chrome = True
    except FileNotFoundError:
        has_chrome = False

    if has_agent and has_chrome and has_npx:
        return 3

    return 2


def check_tier(required: int, feature: str) -> None:
    """Raise SystemExit with a clear message if the current tier is too low.

    Args:
        required: Minimum tier needed (1, 2, or 3).
        feature: Human-readable description of the feature being gated.
    """
    current = get_tier()
    if current >= required:
        return

    from rich.console import Console
    _console = Console(stderr=True)

    missing: list[str] = []
    if required >= 2 and not has_llm_provider():
        missing.append(f"LLM provider — {llm_config_hint()}")
    if required >= 3:
        statuses = get_auto_apply_agent_statuses()
        if not any(status.available for status in statuses.values()):
            missing.append(
                "Auto-apply agent CLI — install Codex CLI and run `codex login`, "
                "or install Claude Code CLI from [bold]https://claude.ai/code[/bold]"
            )
            codex_status = statuses["codex"]
            claude_status = statuses["claude"]
            if codex_status.binary_path and not codex_status.available:
                missing.append(f"Codex CLI login — {codex_status.note}")
            if not codex_status.binary_path:
                missing.append("Codex CLI — install Codex CLI and run `codex login`")
            if not claude_status.available:
                missing.append(f"Claude Code CLI — {claude_status.note}")
        try:
            get_chrome_path()
        except FileNotFoundError:
            missing.append("Chrome/Chromium — install or set CHROME_PATH")
        if not shutil.which("npx"):
            missing.append("Node.js (npx) — install Node.js 18+")

    _console.print(
        f"\n[red]'{feature}' requires {TIER_LABELS.get(required, f'Tier {required}')} (Tier {required}).[/red]\n"
        f"Current tier: {TIER_LABELS.get(current, f'Tier {current}')} (Tier {current})."
    )
    if missing:
        _console.print("\n[yellow]Missing:[/yellow]")
        for m in missing:
            _console.print(f"  - {m}")
    _console.print()
    raise SystemExit(1)
