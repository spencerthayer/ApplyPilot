"""Claude Code CLI backend."""

from __future__ import annotations

import json
import os
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
    describe_tool_use,
    get_state,
    job_log_path,
    log_header,
    make_mcp_config,
    shell_join,
    update_state,
)


def build_claude_command(mcp_config_path: Path, model: str | None) -> list[str]:
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
        ",".join(f"mcp__gmail__{t}" for t in DISALLOWED_GMAIL_TOOLS),
        "--output-format",
        "stream-json",
        "--verbose",
        "-",
    ]


class ClaudeAutoApplyBackend(AutoApplyBackend):
    key = "claude"
    label = "Claude Code CLI"

    def __init__(self) -> None:
        self._active_procs: dict[int, subprocess.Popen] = {}
        self._config_dir = Path.home() / ".claude"
        self._config_path = self._config_dir / "claude.json"

    def build_command(self, *, worker_dir: Path, worker_id: int, port: int, model: str | None) -> list[str]:
        mcp_config_path = config.APP_DIR / f".mcp-apply-{worker_id}.json"
        mcp_config_path.write_text(json.dumps(make_mcp_config(port)), encoding="utf-8")
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
        from applypilot.apply.chrome import reset_worker_dir

        worker_dir = reset_worker_dir(worker_id)
        cmd = self.build_command(worker_dir=worker_dir, worker_id=worker_id, port=port, model=model)

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)

        log_file = job_log_path(self.key, worker_id, job)
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

            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write(log_header(job, self.label))
                lf.write(f"$ {shell_join(cmd)}\n")
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
                                if block.get("type") == "text":
                                    text = block.get("text", "")
                                    text_parts.append(text)
                                    lf.write(text + "\n")
                                elif block.get("type") == "tool_use":
                                    desc = describe_tool_use(block)
                                    lf.write(f"  >> {desc}\n")
                                    ws = get_state(worker_id)
                                    update_state(
                                        worker_id, actions=(ws.actions if ws else 0) + 1, last_action=desc[:35]
                                    )
                        elif msg_type == "result":
                            stats = {"cost_usd": msg.get("total_cost_usd", 0) or 0}
                            text_parts.append(msg.get("result", ""))

            proc.wait(timeout=config.DEFAULTS["apply_timeout"])
            duration_ms = int((time.time() - start) * 1000)

            if stats:
                ws = get_state(worker_id)
                update_state(worker_id, total_cost=(ws.total_cost if ws else 0.0) + float(stats["cost_usd"]))

            return BackendExecution(
                final_output="\n".join(p for p in text_parts if p),
                raw_output="".join(raw_parts),
                duration_ms=duration_ms,
                returncode=proc.returncode or 0,
                skipped=(proc.returncode or 0) < 0,
            )
        finally:
            unregister_process(worker_id)
