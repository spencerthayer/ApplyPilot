"""Base types and shared utilities for auto-apply backends."""

from __future__ import annotations

import abc
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from applypilot import config
from applypilot.apply.chrome import BASE_CDP_PORT, reset_worker_dir  # noqa: F401
from applypilot.apply.dashboard import get_state, update_state  # noqa: F401

ProcessRegistrar = Callable[[int, subprocess.Popen], None]
ProcessUnregister = Callable[[int], None]

DISALLOWED_GMAIL_TOOLS = [
    "draft_email",
    "modify_email",
    "delete_email",
    "download_attachment",
    "batch_modify_emails",
    "batch_delete_emails",
    "create_label",
    "update_label",
    "delete_label",
    "get_or_create_label",
    "list_email_labels",
    "create_filter",
    "list_filters",
    "get_filter",
    "delete_filter",
]

_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")


class BackendError(Exception):
    """Raised when backend operations fail."""


class InvalidBackendError(BackendError, ValueError):
    def __init__(self, backend: str, available: frozenset[str]) -> None:
        self.backend = backend
        self.available = available
        super().__init__(
            f"Invalid backend '{backend}'. Supported backends: {', '.join(sorted(available))}. "
            "Set via AUTO_APPLY_AGENT, APPLY_BACKEND, or the apply --agent flag."
        )


@dataclass(frozen=True)
class BackendExecution:
    final_output: str
    raw_output: str
    duration_ms: int
    returncode: int
    skipped: bool = False


class AutoApplyBackend(abc.ABC):
    key: str
    label: str

    @classmethod
    def is_installed(cls) -> bool:
        return shutil.which(cls.key) is not None

    @classmethod
    def get_version(cls) -> str | None:
        binary = shutil.which(cls.key)
        if not binary:
            return None
        try:
            result = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
            )
        except OSError:
            return None
        output = f"{result.stdout}\n{result.stderr}".strip()
        return next((l.strip() for l in output.splitlines() if l.strip()), None)

    @abc.abstractmethod
    def build_command(self, *, worker_dir: Path, worker_id: int, port: int, model: str | None) -> list[str]:
        ...

    @abc.abstractmethod
    def run(
            self,
            *,
            job: dict,
            port: int,
            worker_id: int,
            prompt: str,
            model: str | None,
            register_process: ProcessRegistrar,
            unregister_process: ProcessUnregister,
    ) -> BackendExecution:
        ...

    def build_manual_command(self, prompt_file: Path, worker_id: int, model: str | None) -> str:
        worker_dir = config.APPLY_WORKER_DIR / f"worker-{worker_id}"
        cmd = self.build_command(
            worker_dir=worker_dir, worker_id=worker_id, port=BASE_CDP_PORT + worker_id, model=model
        )
        return f"{shell_join(cmd)} < {shlex.quote(str(prompt_file))}"

    @property
    def name(self) -> str:
        return self.key

    def get_active_proc(self, worker_id: int) -> subprocess.Popen | None:
        return getattr(self, "_active_procs", {}).get(worker_id)

    def list_mcp_servers(self) -> list[str]:
        return []

    def add_mcp_server(self, *args: object, **kwargs: object) -> None:
        raise BackendError(f"{self.label} does not support MCP server registration via ApplyPilot.")

    def setup(self, import_from: str | None = None) -> dict[str, object]:
        del import_from
        return {"success": True, "servers_added": [], "servers_existing": self.list_mcp_servers(), "errors": []}

    def _register_internal_process(self, worker_id: int, proc: subprocess.Popen) -> None:
        self._active_procs[worker_id] = proc

    def _unregister_internal_process(self, worker_id: int) -> None:
        self._active_procs.pop(worker_id, None)

    def run_job(
            self,
            job: dict[str, Any],
            port: int,
            worker_id: int,
            model: str,
            agent: str | None,
            dry_run: bool,
            prompt: str,
            mcp_config_path: Path,
            worker_dir: Path,
            required_mcp_servers: Sequence[str] | None = None,
            update_callback: Any | None = None,
    ) -> tuple[str, int]:
        del mcp_config_path, worker_dir, required_mcp_servers, update_callback
        try:
            execution = self.run(
                job=job,
                port=port,
                worker_id=worker_id,
                prompt=prompt,
                model=model,
                register_process=self._register_internal_process,
                unregister_process=self._unregister_internal_process,
            )
        except subprocess.TimeoutExpired:
            return "failed:timeout", config.DEFAULTS["apply_timeout"] * 1000
        except Exception as exc:
            return f"failed:{str(exc)[:100]}", 0
        if execution.skipped:
            return "skipped", execution.duration_ms
        combined = "\n".join(p.strip() for p in (execution.final_output, execution.raw_output) if p and p.strip())
        result = extract_result_status(execution.final_output or "")
        if not result:
            result = extract_result_status(combined)
        if result:
            return result, execution.duration_ms
        return f"failed:{fallback_failure_reason(combined, execution.returncode, self.key)}", execution.duration_ms


# ── Shared helpers ──────────────────────────────────────────────────


def extract_result_status(output: str) -> str | None:
    token_re = re.compile(
        r"RESULT:(APPLIED|EXPIRED|CAPTCHA|LOGIN_ISSUE|NEEDS_HUMAN(?::[A-Za-z0-9_.:-]+)?|FAILED(?::[A-Za-z0-9_.:-]+)?)"
    )
    matches = list(token_re.finditer(output))
    if not matches:
        return None
    token = matches[-1].group(1).strip()
    if token in {"APPLIED", "EXPIRED", "CAPTCHA", "LOGIN_ISSUE"}:
        return token.lower()
    if token.startswith("NEEDS_HUMAN"):
        reason = token.split(":", 1)[-1].strip() if ":" in token else "agent_stuck"
        return f"needs_human:{reason}"
    reason = token.split(":", 1)[-1].strip() if ":" in token else "unknown"
    reason = re.sub(r"[^A-Za-z0-9_.:-]+$", "", reason).strip() or "unknown"
    if reason in {"captcha", "expired", "login_issue"}:
        return reason
    return f"failed:{reason}"


def make_mcp_config(cdp_port: int) -> dict:
    return {
        "mcpServers": {
            "playwright": {
                "command": "npx",
                "args": [
                    "@playwright/mcp@latest",
                    f"--cdp-endpoint=http://localhost:{cdp_port}",
                    f"--viewport-size={config.DEFAULTS['viewport']}",
                ],
            },
            "gmail": {"command": "npx", "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"]},
        }
    }


def describe_tool_use(block: dict) -> str:
    name = block.get("name", "").replace("mcp__playwright__", "").replace("mcp__gmail__", "gmail:")
    inp = block.get("input", {})
    if "url" in inp:
        return f"{name} {inp['url'][:60]}"
    if "ref" in inp:
        return f"{name} {inp.get('element', inp.get('text', ''))}"[:50]
    if "fields" in inp:
        return f"{name} ({len(inp['fields'])} fields)"
    if "paths" in inp:
        return f"{name} upload"
    return name


def job_log_path(agent: str, worker_id: int, job: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    site = _sanitize_log_site(str(job.get("site") or job.get("company") or "unknown"))
    return config.LOG_DIR / f"agent_{agent}_{ts}_w{worker_id}_{site}.txt"


def _sanitize_log_site(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]", "", value.replace("/", " ").replace("\\", " ").replace(":", " "))
    return re.sub(r"\s+", " ", cleaned).strip()[:40].strip() or "unknown"


def log_header(job: dict, label: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"\n{'=' * 60}\n[{ts}] {label}: {job['title']} @ {job.get('site', '')}\n"
        f"URL: {job.get('application_url') or job['url']}\nScore: {job.get('fit_score', 'N/A')}/10\n{'=' * 60}\n"
    )


def shell_join(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def terminate_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            return


def fallback_failure_reason(output: str, returncode: int, agent: str) -> str:
    if returncode:
        last_line = next((l.strip() for l in reversed(output.splitlines()) if l.strip()), "")
        if last_line:
            cleaned = re.sub(r"[^a-zA-Z0-9._:-]+", "_", last_line.lower()).strip("_")
            return f"{agent}_runtime_error:{cleaned[:60] or returncode}"
        return f"{agent}_runtime_error:{returncode}"
    return "no_result_line"
