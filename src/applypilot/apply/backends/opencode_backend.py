"""OpenCode CLI backend."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from applypilot import config
from applypilot.apply.backends.base import (
    AutoApplyBackend,
    BackendError,
    BackendExecution,
    ProcessRegistrar,
    ProcessUnregister,
    _ANSI_ESCAPE_RE,
    extract_result_status,
    fallback_failure_reason,
    get_state,
    job_log_path,
    log_header,
    shell_join,
    update_state,
)
from applypilot.apply.chrome import reset_worker_dir


class OpenCodeAutoApplyBackend(AutoApplyBackend):
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
        default = Path.home() / ".opencode" / "bin" / "opencode"
        if default.exists():
            return str(default)
        raise BackendError("OpenCode CLI not found on PATH.")

    def _prepare_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        oc_bin = str(Path.home() / ".opencode" / "bin")
        path = env.get("PATH", "")
        if oc_bin not in path:
            env["PATH"] = f"{oc_bin}:{path}" if path else oc_bin
        if not env.get("TERM"):
            env["TERM"] = "xterm-256color"
        return env

    def _build_command(self, model: str | None, worker_dir: Path, agent_name: str | None = None) -> list[str]:
        cmd = [self._find_binary(), "run", "--format", "json", "--dir", str(worker_dir)]
        if model:
            cmd.extend(["--model", model])
        if agent_name:
            cmd.extend(["--agent", agent_name])
        variant = os.environ.get("APPLY_OPENCODE_VARIANT", "").strip()
        if variant:
            cmd.extend(["--variant", variant])
        return cmd

    def build_command(self, *, worker_dir: Path, worker_id: int, port: int, model: str | None) -> list[str]:
        del worker_id, port
        return self._build_command(model=model, worker_dir=worker_dir, agent_name=config.get_opencode_agent_setting())

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
        for line in output.splitlines():
            m = re.match(r"^[●*]?\s*[✓x]?\s*([A-Za-z0-9_-]+)\s+(connected|disconnected|error)\b", line.strip())
            if m:
                servers.add(m.group(1))
        return servers

    def list_mcp_servers(self) -> list[str]:
        return sorted(self._list_mcp_servers())

    def _ensure_required_mcp_servers(self, required: Sequence[str] | None) -> None:
        if not required:
            return
        missing = [n for n in required if n not in self._list_mcp_servers()]
        if missing:
            raise BackendError("Missing server(s): " + ", ".join(missing))

    def setup(self, import_from: str | None = None) -> dict[str, object]:
        del import_from
        config.OPENCODE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not config.OPENCODE_CONFIG_PATH.exists():
            config.OPENCODE_CONFIG_PATH.write_text('{\n  "mcp": {}\n}\n', encoding="utf-8")
        return {"success": True, "servers_added": [], "servers_existing": self.list_mcp_servers(), "errors": []}

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
        cmd = self._build_command(model=model, worker_dir=worker_dir, agent_name=config.get_opencode_agent_setting())

        log_file = job_log_path(self.key, worker_id, job)
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

            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write(log_header(job, self.label))
                lf.write(f"$ {shell_join(cmd)}\n")
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
                        if msg.get("type") == "text":
                            text = msg.get("part", {}).get("text", "")
                            if text:
                                text_parts.append(text)
                        elif msg.get("type") == "tool_use":
                            ws = get_state(worker_id)
                            update_state(worker_id, actions=(ws.actions if ws else 0) + 1, last_action="tool use")

            proc.wait(timeout=config.DEFAULTS["apply_timeout"])
            duration_ms = int((time.time() - start) * 1000)
            return BackendExecution(
                final_output="\n".join(p for p in text_parts if p).strip(),
                raw_output="".join(raw_parts),
                duration_ms=duration_ms,
                returncode=proc.returncode or 0,
                skipped=(proc.returncode or 0) < 0,
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
        del dry_run, mcp_config_path, update_callback
        try:
            self._ensure_required_mcp_servers(required_mcp_servers)
            cmd = self._build_command(
                model=model, worker_dir=worker_dir, agent_name=agent or config.get_opencode_agent_setting()
            )
            log_file = job_log_path(self.key, worker_id, job)
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
            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write(log_header(job, self.label))
                lf.write(f"$ {shell_join(cmd)}\n")
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
                    if msg.get("type") == "text":
                        text = msg.get("part", {}).get("text", "")
                        if text:
                            text_parts.append(text)
                    elif msg.get("type") == "tool_use":
                        ws = get_state(worker_id)
                        update_state(worker_id, actions=(ws.actions if ws else 0) + 1, last_action="tool use")
            proc.wait(timeout=config.DEFAULTS["apply_timeout"])
            duration_ms = int((time.time() - start) * 1000)
            if (proc.returncode or 0) < 0:
                return "skipped", duration_ms
            final = "\n".join(p for p in text_parts if p).strip()
            combined = "\n".join(p.strip() for p in (final, "".join(raw_parts)) if p and p.strip())
            result = extract_result_status(final) or extract_result_status(combined)
            if result:
                return result, duration_ms
            return f"failed:{fallback_failure_reason(combined, proc.returncode or 0, self.key)}", duration_ms
        except subprocess.TimeoutExpired:
            return "failed:timeout", config.DEFAULTS["apply_timeout"] * 1000
        except Exception as exc:
            return f"failed:{str(exc)[:100]}", 0
        finally:
            self._unregister_internal_process(worker_id)
