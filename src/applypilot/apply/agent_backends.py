"""Auto-apply browser agent backends for Codex CLI and Claude Code CLI."""

from __future__ import annotations

import abc
import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

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
        "--full-auto",
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

    for result_status in ("APPLIED", "EXPIRED", "CAPTCHA", "LOGIN_ISSUE"):
        if f"RESULT:{result_status}" in output:
            return result_status.lower()

    for out_line in output.splitlines():
        if "RESULT:FAILED" not in out_line:
            continue
        reason = (
            out_line.split("RESULT:FAILED:", 1)[-1].strip()
            if "RESULT:FAILED:" in out_line
            else "unknown"
        )
        reason = re.sub(r'[*`"]+$', "", reason).strip() or "unknown"
        if reason in {"captcha", "expired", "login_issue"}:
            return reason
        return f"failed:{reason}"

    return None


def build_manual_command(agent: str, prompt_file: Path, worker_id: int, model: str | None) -> str:
    """Build a manual debug command for the selected backend."""

    backend = get_backend(agent)
    return backend.build_manual_command(prompt_file=prompt_file, worker_id=worker_id, model=model)


class ClaudeAutoApplyBackend(AutoApplyBackend):
    """Claude Code CLI backend."""

    key = "claude"
    label = "Claude Code CLI"

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

        worker_log = _worker_log_path(worker_id)
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

            with open(worker_log, "a", encoding="utf-8") as lf:
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
            _write_job_log(self.key, worker_id, job, combined_text or "".join(raw_parts))
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

        worker_log = _worker_log_path(worker_id)
        start = time.time()
        proc = None
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
                cwd=str(worker_dir),
            )
            register_process(worker_id, proc)

            if proc.stdin is not None:
                proc.stdin.write(prompt)
                proc.stdin.close()

            with open(worker_log, "a", encoding="utf-8") as lf:
                lf.write(_log_header(job, self.label))
                lf.write(f"$ {_shell_join(cmd)}\n")
                update_state(worker_id, last_action="running Codex")

                if proc.stdout is not None:
                    for line in proc.stdout:
                        raw_parts.append(line)
                        lf.write(line)

            proc.wait(timeout=config.DEFAULTS["apply_timeout"])
            duration_ms = int((time.time() - start) * 1000)
            returncode = proc.returncode or 0
            final_output = ""
            if output_file.exists():
                final_output = output_file.read_text(encoding="utf-8", errors="replace")

            log_output = final_output if final_output.strip() else "".join(raw_parts)
            _write_job_log(self.key, worker_id, job, log_output)

            return BackendExecution(
                final_output=final_output,
                raw_output="".join(raw_parts),
                duration_ms=duration_ms,
                returncode=returncode,
                skipped=returncode < 0,
            )
        finally:
            unregister_process(worker_id)


BACKENDS: dict[str, AutoApplyBackend] = {
    "claude": ClaudeAutoApplyBackend(),
    "codex": CodexAutoApplyBackend(),
}


def get_backend(agent: str) -> AutoApplyBackend:
    """Return the backend implementation for the given agent key."""

    try:
        return BACKENDS[agent]
    except KeyError as exc:
        raise ValueError(f"Unsupported auto-apply agent: {agent}") from exc


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


def _worker_log_path(worker_id: int) -> Path:
    return config.LOG_DIR / f"worker-{worker_id}.log"


def _write_job_log(agent: str, worker_id: int, job: dict, output: str) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_log = config.LOG_DIR / f"agent_{agent}_{ts}_w{worker_id}_{job.get('site', 'unknown')[:20]}.txt"
    job_log.write_text(output, encoding="utf-8")


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
