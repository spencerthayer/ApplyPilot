"""Auto-apply browser agent backends for Codex CLI and Claude Code CLI."""

from __future__ import annotations

import abc
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from applypilot import config
from applypilot.apply.chrome import BASE_CDP_PORT, reset_worker_dir
from applypilot.apply.dashboard import get_state, update_state

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


class BackendError(Exception):
    """Raised when backend operations fail."""


class InvalidBackendError(BackendError, ValueError):
    """Raised when a backend identifier is not supported."""

    def __init__(self, backend: str, available: frozenset[str]) -> None:
        self.backend = backend
        self.available = available
        super().__init__(
            f"Invalid backend '{backend}'. Supported backends: {', '.join(sorted(available))}. "
            "Set via AUTO_APPLY_AGENT, APPLY_BACKEND, or the apply --agent flag."
        )


@dataclass(frozen=True)
class BackendExecution:
    """Captured output from an auto-apply agent run."""

    final_output: str
    raw_output: str
    duration_ms: int
    returncode: int
    skipped: bool = False


class AutoApplyBackend(abc.ABC):
    """Common interface for browser-agent backends."""

    key: str
    label: str

    @classmethod
    def is_installed(cls) -> bool:
        """Return whether the backend CLI is installed."""

        return shutil.which(cls.key) is not None

    @classmethod
    def get_version(cls) -> str | None:
        """Return a short CLI version string when available."""

        binary = shutil.which(cls.key)
        if not binary:
            return None
        try:
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError:
            return None
        output = f"{result.stdout}\n{result.stderr}".strip()
        return next((line.strip() for line in output.splitlines() if line.strip()), None)

    @abc.abstractmethod
    def build_command(
        self,
        *,
        worker_dir: Path,
        worker_id: int,
        port: int,
        model: str | None,
    ) -> list[str]:
        """Return the subprocess argv for this backend."""

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
        """Execute the backend for a single job."""

    def build_manual_command(self, prompt_file: Path, worker_id: int, model: str | None) -> str:
        """Return a shell command suitable for manual debugging."""

        worker_dir = config.APPLY_WORKER_DIR / f"worker-{worker_id}"
        cmd = self.build_command(
            worker_dir=worker_dir,
            worker_id=worker_id,
            port=BASE_CDP_PORT + worker_id,
            model=model,
        )
        return f"{_shell_join(cmd)} < {shlex.quote(str(prompt_file))}"

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
        return {
            "success": True,
            "servers_added": [],
            "servers_existing": self.list_mcp_servers(),
            "errors": [],
        }

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
        """Compatibility wrapper used by the dev-branch backend tests."""

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

        combined_output = "\n".join(
            part.strip()
            for part in (execution.final_output, execution.raw_output)
            if part and part.strip()
        )
        result = extract_result_status(combined_output)
        if result:
            return result, execution.duration_ms
        return (
            f"failed:{_fallback_failure_reason(combined_output, execution.returncode, self.key)}",
            execution.duration_ms,
        )


def build_claude_command(mcp_config_path: Path, model: str | None) -> list[str]:
    """Build the Claude Code command line."""

    effective_model = model or config.DEFAULT_CLAUDE_AUTO_APPLY_MODEL
    return [
        "claude",
        "--model",
        effective_model,
        "-p",
        "--mcp-config",
        str(mcp_config_path),
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
        "--disallowedTools",
        ",".join(f"mcp__gmail__{tool}" for tool in DISALLOWED_GMAIL_TOOLS),
        "--output-format",
        "stream-json",
        "--verbose",
        "-",
    ]


def build_codex_command(
    *,
    worker_dir: Path,
    output_file: Path,
    port: int,
    model: str | None,
) -> list[str]:
    """Build the Codex exec command line."""

    cmd = [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--ephemeral",
        "--skip-git-repo-check",
        "-C",
        str(worker_dir),
        "--add-dir",
        str(config.APP_DIR),
        "--output-last-message",
        str(output_file),
    ]
    if model:
        cmd.extend(["--model", model])
    for override in _build_codex_config_overrides(port):
        cmd.extend(["-c", override])
    return cmd


def extract_result_status(output: str) -> str | None:
    """Parse the normalized RESULT code from an agent transcript."""
    token_re = re.compile(
        r"RESULT:(APPLIED|EXPIRED|CAPTCHA|LOGIN_ISSUE|FAILED(?::[A-Za-z0-9_.:-]+)?)"
    )
    matches = list(token_re.finditer(output))
    if not matches:
        return None

    token = matches[-1].group(1).strip()
    if token in {"APPLIED", "EXPIRED", "CAPTCHA", "LOGIN_ISSUE"}:
        return token.lower()

    # RESULT:FAILED or RESULT:FAILED:<reason>
    reason = token.split(":", 1)[-1].strip() if ":" in token else "unknown"
    reason = re.sub(r"[^A-Za-z0-9_.:-]+$", "", reason).strip() or "unknown"
    if reason in {"captcha", "expired", "login_issue"}:
        return reason
    return f"failed:{reason}"


def build_manual_command(agent: str, prompt_file: Path, worker_id: int, model: str | None) -> str:
    """Build a manual debug command for the selected backend."""

    backend = get_backend(agent)
    return backend.build_manual_command(prompt_file=prompt_file, worker_id=worker_id, model=model)


class ClaudeAutoApplyBackend(AutoApplyBackend):
    """Claude Code CLI backend."""

    key = "claude"
    label = "Claude Code CLI"

    def __init__(self) -> None:
        self._active_procs: dict[int, subprocess.Popen] = {}
        self._config_dir = Path.home() / ".claude"
        self._config_path = self._config_dir / "claude.json"

    def build_command(
        self,
        *,
        worker_dir: Path,
        worker_id: int,
        port: int,
        model: str | None,
    ) -> list[str]:
        mcp_config_path = config.APP_DIR / f".mcp-apply-{worker_id}.json"
        mcp_config_path.write_text(json.dumps(_make_mcp_config(port)), encoding="utf-8")
        return build_claude_command(mcp_config_path, model)

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
        worker_dir = reset_worker_dir(worker_id)
        cmd = self.build_command(worker_dir=worker_dir, worker_id=worker_id, port=port, model=model)

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)

        job_log = _job_log_path(self.key, worker_id, job)
        start = time.time()
        stats: dict[str, float] = {}
        proc = None
        text_parts: list[str] = []
        raw_parts: list[str] = []

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(worker_dir),
            )
            register_process(worker_id, proc)

            if proc.stdin is not None:
                proc.stdin.write(prompt)
                proc.stdin.close()

            with open(job_log, "a", encoding="utf-8") as lf:
                lf.write(_log_header(job, self.label))
                lf.write(f"$ {_shell_join(cmd)}\n")

                if proc.stdout is not None:
                    for line in proc.stdout:
                        raw_parts.append(line)
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            msg = json.loads(stripped)
                        except json.JSONDecodeError:
                            lf.write(line)
                            continue

                        msg_type = msg.get("type")
                        if msg_type == "assistant":
                            for block in msg.get("message", {}).get("content", []):
                                block_type = block.get("type")
                                if block_type == "text":
                                    text = block.get("text", "")
                                    text_parts.append(text)
                                    lf.write(text + "\n")
                                elif block_type == "tool_use":
                                    desc = _describe_tool_use(block)
                                    lf.write(f"  >> {desc}\n")
                                    ws = get_state(worker_id)
                                    current_actions = ws.actions if ws else 0
                                    update_state(
                                        worker_id,
                                        actions=current_actions + 1,
                                        last_action=desc[:35],
                                    )
                        elif msg_type == "result":
                            stats = {
                                "cost_usd": msg.get("total_cost_usd", 0) or 0,
                            }
                            text_parts.append(msg.get("result", ""))

            proc.wait(timeout=config.DEFAULTS["apply_timeout"])
            duration_ms = int((time.time() - start) * 1000)
            returncode = proc.returncode or 0

            if stats:
                ws = get_state(worker_id)
                prev_cost = ws.total_cost if ws else 0.0
                update_state(worker_id, total_cost=prev_cost + float(stats["cost_usd"]))

            combined_text = "\n".join(part for part in text_parts if part)
            return BackendExecution(
                final_output=combined_text,
                raw_output="".join(raw_parts),
                duration_ms=duration_ms,
                returncode=returncode,
                skipped=returncode < 0,
            )
        finally:
            unregister_process(worker_id)


class CodexAutoApplyBackend(AutoApplyBackend):
    """Codex CLI backend."""

    key = "codex"
    label = "Codex CLI"

    def __init__(self) -> None:
        self._active_procs: dict[int, subprocess.Popen] = {}
        self._config_dir = config.APP_DIR
        self._config_path = config.APP_DIR / ".codex"

    def build_command(
        self,
        *,
        worker_dir: Path,
        worker_id: int,
        port: int,
        model: str | None,
    ) -> list[str]:
        output_file = worker_dir / "codex-last-message.txt"
        return build_codex_command(worker_dir=worker_dir, output_file=output_file, port=port, model=model)

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
        worker_dir = reset_worker_dir(worker_id)
        output_file = worker_dir / "codex-last-message.txt"
        cmd = build_codex_command(worker_dir=worker_dir, output_file=output_file, port=port, model=model)
        timeout_seconds = config.DEFAULTS["apply_timeout"]

        job_log = _job_log_path(self.key, worker_id, job)
        start = time.time()
        proc = None
        raw_output = ""

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(worker_dir),
            )
            register_process(worker_id, proc)

            with open(job_log, "a", encoding="utf-8") as lf:
                lf.write(_log_header(job, self.label))
                lf.write(f"$ {_shell_join(cmd)}\n")
                update_state(worker_id, last_action="running Codex")
                try:
                    stdout, _ = proc.communicate(prompt, timeout=timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    partial = exc.output if isinstance(exc.output, str) else ""
                    if partial:
                        lf.write(partial)
                    _terminate_process(proc)
                    raise
                raw_output = stdout or ""
                if raw_output:
                    lf.write(raw_output)

            duration_ms = int((time.time() - start) * 1000)
            returncode = proc.returncode or 0
            final_output = ""
            if output_file.exists():
                final_output = output_file.read_text(encoding="utf-8", errors="replace")

            if final_output.strip():
                with open(job_log, "a", encoding="utf-8") as lf:
                    lf.write("\n--- final message ---\n")
                    lf.write(final_output)
                    if not final_output.endswith("\n"):
                        lf.write("\n")

            return BackendExecution(
                final_output=final_output,
                raw_output=raw_output,
                duration_ms=duration_ms,
                returncode=returncode,
                skipped=returncode < 0,
            )
        finally:
            unregister_process(worker_id)


class OpenCodeAutoApplyBackend(AutoApplyBackend):
    """OpenCode CLI backend."""

    key = "opencode"
    label = "OpenCode CLI"

    def __init__(self) -> None:
        self._active_procs: dict[int, subprocess.Popen] = {}
        self._config_dir = config.OPENCODE_CONFIG_DIR
        self._config_path = config.OPENCODE_CONFIG_PATH

    def _find_binary(self) -> str:
        binary = shutil.which("opencode")
        if binary:
            return binary

        default_binary = Path.home() / ".opencode" / "bin" / "opencode"
        if default_binary.exists():
            return str(default_binary)

        raise BackendError(
            "OpenCode CLI not found on PATH. Install OpenCode CLI and configure playwright+gmail MCP servers."
        )

    def _prepare_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        opencode_bin_dir = str(Path.home() / ".opencode" / "bin")
        current_path = env.get("PATH", "")
        if opencode_bin_dir not in current_path:
            env["PATH"] = f"{opencode_bin_dir}:{current_path}" if current_path else opencode_bin_dir
        if not env.get("TERM"):
            env["TERM"] = "xterm-256color"
        return env

    def _build_command(
        self,
        model: str | None,
        worker_dir: Path,
        agent_name: str | None = None,
    ) -> list[str]:
        cmd = [
            self._find_binary(),
            "run",
            "--format",
            "json",
            "--dir",
            str(worker_dir),
        ]
        if model:
            cmd.extend(["--model", model])
        if agent_name:
            cmd.extend(["--agent", agent_name])
        variant = os.environ.get("APPLY_OPENCODE_VARIANT", "").strip()
        if variant:
            cmd.extend(["--variant", variant])
        return cmd

    def build_command(
        self,
        *,
        worker_dir: Path,
        worker_id: int,
        port: int,
        model: str | None,
    ) -> list[str]:
        del worker_id, port
        return self._build_command(
            model=model,
            worker_dir=worker_dir,
            agent_name=config.get_opencode_agent_setting(),
        )

    def _list_mcp_servers(self) -> set[str]:
        proc = subprocess.run(
            [self._find_binary(), "mcp", "list"],
            capture_output=True,
            text=True,
            check=False,
            env=self._prepare_environment(),
            cwd=str(config.APP_DIR),
        )
        output = _ANSI_ESCAPE_RE.sub("", f"{proc.stdout}\n{proc.stderr}")
        servers: set[str] = set()
        for raw_line in output.splitlines():
            line = raw_line.strip()
            match = re.match(
                r"^[●*]?\s*[✓x]?\s*([A-Za-z0-9_-]+)\s+(connected|disconnected|error)\b",
                line,
            )
            if match:
                servers.add(match.group(1))
        return servers

    def list_mcp_servers(self) -> list[str]:
        return sorted(self._list_mcp_servers())

    def _ensure_required_mcp_servers(self, required: Sequence[str] | None) -> None:
        if not required:
            return
        configured = self._list_mcp_servers()
        missing = [name for name in required if name not in configured]
        if missing:
            raise BackendError(
                "Missing server(s): "
                + ", ".join(missing)
                + ". Configure OpenCode with playwright+gmail MCP servers before auto-apply."
            )

    def setup(self, import_from: str | None = None) -> dict[str, object]:
        del import_from
        config.OPENCODE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not config.OPENCODE_CONFIG_PATH.exists():
            config.OPENCODE_CONFIG_PATH.write_text("{\n  \"mcp\": {}\n}\n", encoding="utf-8")
        existing = self.list_mcp_servers()
        return {
            "success": True,
            "servers_added": [],
            "servers_existing": existing,
            "errors": [],
        }

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
        del port
        worker_dir = reset_worker_dir(worker_id)
        agent_name = config.get_opencode_agent_setting()
        cmd = self._build_command(model=model, worker_dir=worker_dir, agent_name=agent_name)

        job_log = _job_log_path(self.key, worker_id, job)
        start = time.time()
        proc = None
        raw_parts: list[str] = []
        text_parts: list[str] = []

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._prepare_environment(),
                cwd=str(worker_dir),
            )
            register_process(worker_id, proc)

            if proc.stdin is not None:
                proc.stdin.write(prompt)
                proc.stdin.close()

            with open(job_log, "a", encoding="utf-8") as lf:
                lf.write(_log_header(job, self.label))
                lf.write(f"$ {_shell_join(cmd)}\n")
                update_state(worker_id, last_action="running OpenCode")

                if proc.stdout is not None:
                    for line in proc.stdout:
                        raw_parts.append(line)
                        lf.write(line)
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            msg = json.loads(stripped)
                        except json.JSONDecodeError:
                            text_parts.append(stripped)
                            continue

                        msg_type = msg.get("type")
                        if msg_type == "text":
                            text = msg.get("part", {}).get("text", "")
                            if text:
                                text_parts.append(text)
                        elif msg_type == "tool_use":
                            ws = get_state(worker_id)
                            current_actions = ws.actions if ws else 0
                            update_state(
                                worker_id,
                                actions=current_actions + 1,
                                last_action="tool use",
                            )

            proc.wait(timeout=config.DEFAULTS["apply_timeout"])
            duration_ms = int((time.time() - start) * 1000)
            returncode = proc.returncode or 0

            final_output = "\n".join(part for part in text_parts if part).strip()
            return BackendExecution(
                final_output=final_output,
                raw_output="".join(raw_parts),
                duration_ms=duration_ms,
                returncode=returncode,
                skipped=returncode < 0,
            )
        finally:
            unregister_process(worker_id)

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
        """Compatibility wrapper that preserves the dev-branch OpenCode contract."""

        del dry_run, mcp_config_path, update_callback
        try:
            self._ensure_required_mcp_servers(required_mcp_servers)
            cmd = self._build_command(
                model=model,
                worker_dir=worker_dir,
                agent_name=agent or config.get_opencode_agent_setting(),
            )
            job_log = _job_log_path(self.key, worker_id, job)
            start = time.time()
            raw_parts: list[str] = []
            text_parts: list[str] = []
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._prepare_environment(),
                cwd=str(worker_dir),
            )
            self._register_internal_process(worker_id, proc)

            if proc.stdin is None or proc.stdout is None:
                raise BackendError("OpenCode backend process streams unavailable")

            proc.stdin.write(prompt)
            proc.stdin.close()

            with open(job_log, "a", encoding="utf-8") as lf:
                lf.write(_log_header(job, self.label))
                lf.write(f"$ {_shell_join(cmd)}\n")
                update_state(worker_id, last_action="running OpenCode")

                for line in proc.stdout:
                    raw_parts.append(line)
                    lf.write(line)
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        msg = json.loads(stripped)
                    except json.JSONDecodeError:
                        text_parts.append(stripped)
                        continue

                    msg_type = msg.get("type")
                    if msg_type == "text":
                        text = msg.get("part", {}).get("text", "")
                        if text:
                            text_parts.append(text)
                    elif msg_type == "tool_use":
                        ws = get_state(worker_id)
                        current_actions = ws.actions if ws else 0
                        update_state(
                            worker_id,
                            actions=current_actions + 1,
                            last_action="tool use",
                        )

            proc.wait(timeout=config.DEFAULTS["apply_timeout"])
            duration_ms = int((time.time() - start) * 1000)
            returncode = proc.returncode or 0
            if returncode < 0:
                return "skipped", duration_ms

            final_output = "\n".join(part for part in text_parts if part).strip()
            combined_output = "\n".join(
                part.strip()
                for part in (final_output, "".join(raw_parts))
                if part and part.strip()
            )
            result = extract_result_status(combined_output)
            if result:
                return result, duration_ms
            return f"failed:{_fallback_failure_reason(combined_output, returncode, self.key)}", duration_ms
        except subprocess.TimeoutExpired:
            return "failed:timeout", config.DEFAULTS["apply_timeout"] * 1000
        except Exception as exc:
            return f"failed:{str(exc)[:100]}", 0
        finally:
            self._unregister_internal_process(worker_id)


BACKENDS: dict[str, AutoApplyBackend] = {
    "claude": ClaudeAutoApplyBackend(),
    "codex": CodexAutoApplyBackend(),
    "opencode": OpenCodeAutoApplyBackend(),
}

VALID_BACKENDS: frozenset[str] = frozenset(BACKENDS)
DEFAULT_BACKEND = "claude"


def get_backend(agent: str | None = None) -> AutoApplyBackend:
    """Return the backend implementation for the given agent key."""

    agent = resolve_backend_name(agent)
    try:
        return BACKENDS[agent]
    except KeyError as exc:
        raise InvalidBackendError(agent, VALID_BACKENDS) from exc


def get_available_backends() -> frozenset[str]:
    return VALID_BACKENDS


def resolve_backend_name(backend_name: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    if backend_name is not None:
        raw = backend_name
    elif env.get("AUTO_APPLY_AGENT", "").strip() and env.get("AUTO_APPLY_AGENT", "").strip().lower() != "auto":
        raw = env.get("AUTO_APPLY_AGENT", "")
    else:
        raw = env.get("APPLY_BACKEND", DEFAULT_BACKEND)

    normalized = raw.lower().strip()
    if not normalized:
        raise InvalidBackendError(raw, VALID_BACKENDS)
    if normalized not in VALID_BACKENDS:
        raise InvalidBackendError(normalized, VALID_BACKENDS)
    return normalized


def detect_backends() -> list[str]:
    """Return installed backend CLI names, preserving canonical ordering."""

    available: list[str] = []
    for key in ("codex", "claude", "opencode"):
        backend = BACKENDS[key]
        try:
            installed = backend.is_installed()
        except Exception:
            installed = False
        if installed:
            available.append(key)
    return available


def get_preferred_backend(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the first ready backend according to configured preference rules."""

    selection = config.resolve_auto_apply_agent(environ=environ)
    return selection.resolved


def resolve_default_model(backend_name: str, environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    return config.get_auto_apply_model_setting(backend_name, env)


def resolve_default_agent(backend_name: str, environ: Mapping[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    if backend_name == "opencode":
        return config.get_opencode_agent_setting(env)
    return None


def _build_codex_config_overrides(port: int) -> list[str]:
    gmail_tools = ",".join(f'"{tool}"' for tool in DISALLOWED_GMAIL_TOOLS)
    return [
        'mcp_servers.playwright.command="npx"',
        (
            'mcp_servers.playwright.args=['
            f'"@playwright/mcp@latest","--cdp-endpoint=http://localhost:{port}",'
            f'"--viewport-size={config.DEFAULTS["viewport"]}"'
            "]"
        ),
        'mcp_servers.gmail.command="npx"',
        'mcp_servers.gmail.args=["-y","@gongrzhe/server-gmail-autoauth-mcp"]',
        f"mcp_servers.gmail.disabled_tools=[{gmail_tools}]",
    ]


def _make_mcp_config(cdp_port: int) -> dict:
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
            "gmail": {
                "command": "npx",
                "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
            },
        }
    }


def _describe_tool_use(block: dict) -> str:
    name = (
        block.get("name", "")
        .replace("mcp__playwright__", "")
        .replace("mcp__gmail__", "gmail:")
    )
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


def _job_log_path(agent: str, worker_id: int, job: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    site = _sanitize_log_site(str(job.get("site") or job.get("company") or "unknown"))
    return config.LOG_DIR / f"agent_{agent}_{ts}_w{worker_id}_{site}.txt"


def _sanitize_log_site(value: str) -> str:
    cleaned = value.replace("/", " ").replace("\\", " ").replace(":", " ")
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned[:40].strip() or "unknown")


def _log_header(job: dict, label: str) -> str:
    ts_header = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"\n{'=' * 60}\n"
        f"[{ts_header}] {label}: {job['title']} @ {job.get('site', '')}\n"
        f"URL: {job.get('application_url') or job['url']}\n"
        f"Score: {job.get('fit_score', 'N/A')}/10\n"
        f"{'=' * 60}\n"
    )


def _shell_join(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def _terminate_process(proc: subprocess.Popen) -> None:
    """Best-effort process termination helper used on backend timeouts."""
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


def _fallback_failure_reason(output: str, returncode: int, agent: str) -> str:
    if returncode:
        last_line = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "")
        if last_line:
            cleaned = re.sub(r"[^a-zA-Z0-9._:-]+", "_", last_line.lower()).strip("_")
            return f"{agent}_runtime_error:{cleaned[:60] or returncode}"
        return f"{agent}_runtime_error:{returncode}"
    return "no_result_line"


_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")

# Compatibility aliases for dev-branch imports/tests.
AgentBackend = AutoApplyBackend
AgentBackendError = BackendError
ClaudeBackend = ClaudeAutoApplyBackend
CodexBackend = CodexAutoApplyBackend
OpenCodeBackend = OpenCodeAutoApplyBackend
