"""Codex CLI backend."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from applypilot import config
from applypilot.apply.backends.base import (
    AutoApplyBackend,
    BackendExecution,
    DISALLOWED_GMAIL_TOOLS,
    ProcessRegistrar,
    ProcessUnregister,
    job_log_path,
    log_header,
    shell_join,
    terminate_process,
    update_state,
)
from applypilot.apply.chrome import reset_worker_dir


def build_codex_command(*, worker_dir: Path, output_file: Path, port: int, model: str | None) -> list[str]:
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


def _build_codex_config_overrides(port: int) -> list[str]:
    gmail_tools = ",".join(f'"{t}"' for t in DISALLOWED_GMAIL_TOOLS)
    return [
        'mcp_servers.playwright.command="npx"',
        f'mcp_servers.playwright.args=["@playwright/mcp@latest","--cdp-endpoint=http://localhost:{port}","--viewport-size={config.DEFAULTS["viewport"]}"]',
        'mcp_servers.gmail.command="npx"',
        'mcp_servers.gmail.args=["-y","@gongrzhe/server-gmail-autoauth-mcp"]',
        f"mcp_servers.gmail.disabled_tools=[{gmail_tools}]",
    ]


class CodexAutoApplyBackend(AutoApplyBackend):
    key = "codex"
    label = "Codex CLI"

    def __init__(self) -> None:
        self._active_procs: dict[int, subprocess.Popen] = {}

    def build_command(self, *, worker_dir: Path, worker_id: int, port: int, model: str | None) -> list[str]:
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

        log_file = job_log_path(self.key, worker_id, job)
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

            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write(log_header(job, self.label))
                lf.write(f"$ {shell_join(cmd)}\n")
                update_state(worker_id, last_action="running Codex")
                try:
                    stdout, _ = proc.communicate(prompt, timeout=config.DEFAULTS["apply_timeout"])
                except subprocess.TimeoutExpired as exc:
                    if isinstance(exc.output, str) and exc.output:
                        lf.write(exc.output)
                    terminate_process(proc)
                    raise
                raw_output = stdout or ""
                if raw_output:
                    lf.write(raw_output)

            duration_ms = int((time.time() - start) * 1000)
            final_output = output_file.read_text(encoding="utf-8", errors="replace") if output_file.exists() else ""

            if final_output.strip():
                with open(log_file, "a", encoding="utf-8") as lf:
                    lf.write("\n--- final message ---\n")
                    lf.write(final_output)
                    if not final_output.endswith("\n"):
                        lf.write("\n")

            return BackendExecution(
                final_output=final_output,
                raw_output=raw_output,
                duration_ms=duration_ms,
                returncode=proc.returncode or 0,
                skipped=(proc.returncode or 0) < 0,
            )
        finally:
            unregister_process(worker_id)
