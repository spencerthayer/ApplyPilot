"""Chrome detection, auto-apply agent resolution, and tier system."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from applypilot.config.defaults import _env
from applypilot.llm_provider import has_llm_provider, llm_config_hint

AUTO_APPLY_AGENT_CHOICES = ("auto", "codex", "claude", "opencode", "native")
AUTO_APPLY_AGENT_PRIORITY_CHOICES = ("codex", "claude", "opencode")
AUTO_APPLY_AGENT_LABELS = {
    "auto": "Auto-detect",
    "codex": "Codex CLI",
    "claude": "Claude Code CLI",
    "opencode": "OpenCode CLI",
    "native": "Native Playwright Agent",
}
DEFAULT_AUTO_APPLY_AGENT = "auto"
DEFAULT_AUTO_APPLY_AGENT_PRIORITY = ("codex", "claude", "opencode")
DEFAULT_CLAUDE_AUTO_APPLY_MODEL = "haiku"
DEFAULT_OPENCODE_AUTO_APPLY_MODEL = "gpt-4o-mini"
DEFAULT_OPENCODE_AUTO_APPLY_AGENT = "applypilot-apply"
OPENCODE_REQUIRED_MCP_SERVERS = ("playwright", "gmail")
_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")


@dataclass(frozen=True)
class AutoApplyAgentStatus:
    key: str
    label: str
    binary_path: str | None
    available: bool
    note: str
    auth_ok: bool = False


@dataclass(frozen=True)
class AutoApplyAgentSelection:
    requested: str
    resolved: str | None
    model: str | None


def get_chrome_path() -> str:
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    system = platform.system()
    if system == "Windows":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    else:
        candidates = []
        for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    for c in candidates:
        if c and c.exists():
            return str(c)
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("Chrome/Chromium not found. Install Chrome or set CHROME_PATH environment variable.")


def get_chrome_user_data() -> Path:
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


def get_codex_login_status(timeout: int = 10) -> tuple[bool, str]:
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
    output_lines = [l.strip() for l in f"{result.stdout}\n{result.stderr}".splitlines() if l.strip()]
    status_line = next((l for l in reversed(output_lines) if "Logged in" in l), "")
    if result.returncode == 0 and status_line:
        return True, status_line
    return False, output_lines[-1] if output_lines else "Run `codex login`"


def get_opencode_binary_path() -> str | None:
    binary = shutil.which("opencode")
    if binary:
        return binary
    default_binary = Path.home() / ".opencode" / "bin" / "opencode"
    return str(default_binary) if default_binary.exists() else None


def get_opencode_mcp_servers(timeout: int = 10) -> tuple[set[str], str | None]:
    opencode_bin = get_opencode_binary_path()
    if not opencode_bin:
        return set(), "Install OpenCode CLI and configure playwright+gmail MCP servers"
    try:
        result = subprocess.run(
            [opencode_bin, "mcp", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return set(), f"OpenCode MCP check failed: {exc}"
    output = _ANSI_ESCAPE_RE.sub("", f"{result.stdout}\n{result.stderr}")
    servers: set[str] = set()
    for raw_line in output.splitlines():
        match = re.match(r"^[●*]?\s*[✓x]?\s*([A-Za-z0-9_-]+)\s+(connected|disconnected|error)\b", raw_line.strip())
        if match:
            servers.add(match.group(1))
    if result.returncode != 0 and not servers:
        last = next((l for l in reversed(output.splitlines()) if l.strip()), "")
        return set(), last or "OpenCode MCP check failed"
    return servers, None


def get_auto_apply_agent_setting(environ: Mapping[str, str] | None = None) -> str:
    value = _env(environ).get("AUTO_APPLY_AGENT", DEFAULT_AUTO_APPLY_AGENT).strip().lower()
    return value if value in AUTO_APPLY_AGENT_CHOICES else DEFAULT_AUTO_APPLY_AGENT


def get_auto_apply_agent_priority(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    configured = _env(environ).get("AUTO_APPLY_AGENT_PRIORITY", "").strip()
    if not configured:
        return DEFAULT_AUTO_APPLY_AGENT_PRIORITY
    ordered = []
    for part in (p.strip().lower() for p in configured.split(",")):
        if part in AUTO_APPLY_AGENT_PRIORITY_CHOICES and part not in ordered:
            ordered.append(part)
    for part in DEFAULT_AUTO_APPLY_AGENT_PRIORITY:
        if part not in ordered:
            ordered.append(part)
    return tuple(ordered)


def get_auto_apply_model_setting(agent: str | None = None, environ: Mapping[str, str] | None = None) -> str | None:
    env = _env(environ)
    configured = env.get("AUTO_APPLY_MODEL", "").strip()
    if configured:
        return configured
    if agent == "claude":
        return env.get("APPLY_CLAUDE_MODEL", "").strip() or DEFAULT_CLAUDE_AUTO_APPLY_MODEL
    if agent == "opencode":
        return (
                env.get("APPLY_OPENCODE_MODEL", "").strip()
                or env.get("LLM_MODEL", "").strip()
                or DEFAULT_OPENCODE_AUTO_APPLY_MODEL
        )
    return None


def get_opencode_agent_setting(environ: Mapping[str, str] | None = None) -> str:
    return _env(environ).get("APPLY_OPENCODE_AGENT", "").strip() or DEFAULT_OPENCODE_AUTO_APPLY_AGENT


def get_auto_apply_agent_statuses() -> dict[str, AutoApplyAgentStatus]:
    codex_bin = shutil.which("codex")
    codex_ok, codex_note = get_codex_login_status() if codex_bin else (False, "Install Codex CLI and run `codex login`")
    claude_bin = shutil.which("claude")
    opencode_bin = get_opencode_binary_path()
    oc_servers, oc_err = get_opencode_mcp_servers()
    oc_missing = [n for n in OPENCODE_REQUIRED_MCP_SERVERS if n not in oc_servers]
    if opencode_bin and not oc_err and not oc_missing:
        oc_note, oc_avail = f"{opencode_bin} (MCP ready: {', '.join(OPENCODE_REQUIRED_MCP_SERVERS)})", True
    elif opencode_bin and not oc_err:
        oc_note, oc_avail = "Missing MCP servers: " + ", ".join(oc_missing), False
    else:
        oc_note, oc_avail = oc_err or "Install OpenCode CLI and configure playwright+gmail MCP servers", False
    return {
        "codex": AutoApplyAgentStatus(
            "codex",
            AUTO_APPLY_AGENT_LABELS["codex"],
            codex_bin,
            bool(codex_bin) and codex_ok,
            codex_note if codex_bin else "Install Codex CLI and run `codex login`",
            codex_ok,
        ),
        "claude": AutoApplyAgentStatus(
            "claude",
            AUTO_APPLY_AGENT_LABELS["claude"],
            claude_bin,
            claude_bin is not None,
            claude_bin or "Install from https://claude.ai/code",
            claude_bin is not None,
        ),
        "opencode": AutoApplyAgentStatus(
            "opencode", AUTO_APPLY_AGENT_LABELS["opencode"], opencode_bin, oc_avail, oc_note, oc_avail
        ),
        "native": AutoApplyAgentStatus(
            "native",
            AUTO_APPLY_AGENT_LABELS["native"],
            None,
            True,
            "In-process LLM + Playwright MCP (no external CLI)",
            True,
        ),
    }


import applypilot.config as _config_module


def resolve_auto_apply_agent(
        preferred: str | None = None, environ: Mapping[str, str] | None = None
) -> AutoApplyAgentSelection:
    env = _env(environ)
    if preferred is not None:
        requested = preferred.lower().strip()
    elif env.get("AUTO_APPLY_AGENT", "").strip():
        requested = get_auto_apply_agent_setting(env)
    else:
        requested = env.get("APPLY_BACKEND", "").strip().lower() or DEFAULT_AUTO_APPLY_AGENT
    if requested not in AUTO_APPLY_AGENT_CHOICES:
        requested = DEFAULT_AUTO_APPLY_AGENT
    statuses = _config_module.get_auto_apply_agent_statuses()
    resolved = None
    if requested == "auto":
        for c in get_auto_apply_agent_priority(env):
            if statuses[c].available:
                resolved = c
                break
    elif statuses[requested].available:
        resolved = requested
    return AutoApplyAgentSelection(
        requested, resolved, get_auto_apply_model_setting(resolved or (requested if requested != "auto" else None), env)
    )


def has_auto_apply_backend() -> bool:
    return any(s.available for s in _config_module.get_auto_apply_agent_statuses().values())


def describe_auto_apply_backend_requirement() -> str:
    return "supported auto-apply agent CLI (Codex logged in, Claude installed, or OpenCode installed with playwright+gmail MCP servers)"


# ── Tier system ─────────────────────────────────────────────────────

TIER_LABELS = {1: "Discovery", 2: "AI Scoring & Tailoring", 3: "Full Auto-Apply"}
TIER_COMMANDS: dict[int, list[str]] = {
    1: ["init", "run discover", "run enrich", "status", "dashboard"],
    2: ["run score", "run tailor", "run cover", "run pdf", "run", "track"],
    3: ["apply"],
}


def get_tier() -> int:
    _config_module.load_env()
    if not _config_module.has_llm_provider():
        return 1
    has_agent = _config_module.has_auto_apply_backend()
    has_npx = _config_module.shutil.which("npx") is not None
    try:
        _config_module.get_chrome_path()
        has_chrome = True
    except FileNotFoundError:
        has_chrome = False
    return 3 if (has_agent and has_chrome and has_npx) else 2


def check_tier(required: int, feature: str) -> None:
    current = _config_module.get_tier()
    if current >= required:
        return
    from rich.console import Console

    _console = Console(stderr=True)
    missing: list[str] = []
    if required >= 2 and not has_llm_provider():
        missing.append(f"LLM provider — {llm_config_hint()}")
    if required >= 3:
        statuses = _config_module.get_auto_apply_agent_statuses()
        if not any(s.available for s in statuses.values()):
            missing.append("Auto-apply agent CLI — install Codex/Claude/OpenCode")
        cs, cls, ocs = statuses["codex"], statuses["claude"], statuses["opencode"]
        if cs.binary_path and not cs.available:
            missing.append(f"Codex CLI login — {cs.note}")
        if not cs.binary_path:
            missing.append("Codex CLI — install and run `codex login`")
        if not cls.available:
            missing.append(f"Claude Code CLI — {cls.note}")
        if not ocs.available:
            missing.append(f"OpenCode CLI — {ocs.note}")
        try:
            get_chrome_path()
        except FileNotFoundError:
            missing.append("Chrome/Chromium — install or set CHROME_PATH")
        if not shutil.which("npx"):
            missing.append("Node.js (npx) — install Node.js 18+")
    _console.print(
        f"\n[red]'{feature}' requires {TIER_LABELS.get(required, f'Tier {required}')} (Tier {required}).[/red]\nCurrent tier: {TIER_LABELS.get(current, f'Tier {current}')} (Tier {current})."
    )
    if missing:
        _console.print("\n[yellow]Missing:[/yellow]")
        for m in missing:
            _console.print(f"  - {m}")
    _console.print()
    raise SystemExit(1)
