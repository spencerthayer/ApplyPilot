from __future__ import annotations

import io
import subprocess
import shutil
from pathlib import Path

from rich.console import Console

from applypilot import cli, config, llm_provider
from applypilot.apply import backends as agent_backends, launcher
from applypilot.apply.backends import BackendExecution
from applypilot.wizard import init as wizard_init


def _agent_status(
    key: str,
    *,
    available: bool,
    binary: str | None = None,
    note: str = "",
) -> config.AutoApplyAgentStatus:
    return config.AutoApplyAgentStatus(
        key=key,
        label=config.AUTO_APPLY_AGENT_LABELS[key],
        binary_path=binary,
        available=available,
        note=note or (binary or f"{key}-missing"),
        auth_ok=available,
    )


def test_resolve_auto_apply_agent_prefers_codex_then_claude(monkeypatch) -> None:
    monkeypatch.setattr(
        config,
        "get_auto_apply_agent_statuses",
        lambda: {
            "codex": _agent_status("codex", available=True, binary="/opt/homebrew/bin/codex", note="Logged in"),
            "claude": _agent_status("claude", available=True, binary="/usr/local/bin/claude"),
        },
    )

    selection = config.resolve_auto_apply_agent(environ={"AUTO_APPLY_AGENT": "auto", "AUTO_APPLY_MODEL": "gpt-5.4"})
    assert selection.requested == "auto"
    assert selection.resolved == "codex"
    assert selection.model == "gpt-5.4"

    monkeypatch.setattr(
        config,
        "get_auto_apply_agent_statuses",
        lambda: {
            "codex": _agent_status(
                "codex", available=False, binary="/opt/homebrew/bin/codex", note="Run `codex login`"
            ),
            "claude": _agent_status("claude", available=True, binary="/usr/local/bin/claude"),
        },
    )
    selection = config.resolve_auto_apply_agent(environ={"AUTO_APPLY_AGENT": "auto"})
    assert selection.resolved == "claude"
    assert selection.model == config.DEFAULT_CLAUDE_AUTO_APPLY_MODEL


def test_resolve_auto_apply_agent_honors_priority_env(monkeypatch) -> None:
    monkeypatch.setattr(
        config,
        "get_auto_apply_agent_statuses",
        lambda: {
            "codex": _agent_status("codex", available=True, binary="/opt/homebrew/bin/codex", note="Logged in"),
            "claude": _agent_status("claude", available=True, binary="/usr/local/bin/claude"),
        },
    )

    selection = config.resolve_auto_apply_agent(
        environ={
            "AUTO_APPLY_AGENT": "auto",
            "AUTO_APPLY_AGENT_PRIORITY": "claude,codex",
        }
    )

    assert selection.resolved == "claude"


def test_get_auto_apply_agent_priority_ignores_invalid_and_appends_missing_defaults() -> None:
    assert config.get_auto_apply_agent_priority({"AUTO_APPLY_AGENT_PRIORITY": "invalid,claude,claude"}) == (
        "claude",
        "codex",
        "opencode",
    )


def test_auto_apply_model_setting_is_separate_from_llm_model() -> None:
    env = {
        "AUTO_APPLY_MODEL": "gpt-5.4",
        "LLM_MODEL": "gemini-2.0-flash",
    }
    assert config.get_auto_apply_model_setting("codex", env) == "gpt-5.4"
    assert (
            llm_provider.detect_llm_provider(
                {
                    "GEMINI_API_KEY": "key",
                    "LLM_MODEL": "gemini-2.0-flash",
                }
            ).model
            == "gemini-2.0-flash"
    )


def test_get_tier_counts_codex_as_tier_three(monkeypatch) -> None:
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setattr(config, "has_llm_provider", lambda: True)
    monkeypatch.setattr(config, "get_chrome_path", lambda: "/Applications/Google Chrome.app")
    monkeypatch.setattr(
        config,
        "get_auto_apply_agent_statuses",
        lambda: {
            "codex": _agent_status("codex", available=True, binary="/opt/homebrew/bin/codex", note="Logged in"),
            "claude": _agent_status("claude", available=False),
        },
    )
    monkeypatch.setattr(config.shutil, "which", lambda name: "/opt/homebrew/bin/npx" if name == "npx" else None)

    assert config.get_tier() == 3


def test_build_codex_command_includes_required_flags_and_overrides(tmp_path: Path) -> None:
    output_file = tmp_path / "last.txt"
    cmd = agent_backends.build_codex_command(
        worker_dir=tmp_path,
        output_file=output_file,
        port=9333,
        model="gpt-5.4",
    )

    assert cmd[:2] == ["codex", "exec"]
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--ephemeral" in cmd
    assert "--skip-git-repo-check" in cmd
    assert ["-C", str(tmp_path)] == cmd[cmd.index("-C"): cmd.index("-C") + 2]
    assert ["--output-last-message", str(output_file)] == cmd[
        cmd.index("--output-last-message"): cmd.index("--output-last-message") + 2
    ]
    assert ["--model", "gpt-5.4"] == cmd[cmd.index("--model"): cmd.index("--model") + 2]
    joined = " ".join(cmd)
    assert 'mcp_servers.playwright.command="npx"' in joined
    assert "--cdp-endpoint=http://localhost:9333" in joined
    assert "mcp_servers.gmail.disabled_tools" in joined


def test_build_claude_command_preserves_existing_contract(tmp_path: Path) -> None:
    cmd = agent_backends.build_claude_command(tmp_path / ".mcp.json", None)

    assert cmd[:2] == ["claude", "--model"]
    assert config.DEFAULT_CLAUDE_AUTO_APPLY_MODEL in cmd
    assert "--mcp-config" in cmd
    assert "--output-format" in cmd
    assert "stream-json" in cmd


def test_extract_result_status_handles_success_and_failure() -> None:
    assert agent_backends.extract_result_status("hello\nRESULT:APPLIED") == "applied"
    assert agent_backends.extract_result_status("RESULT:CAPTCHA") == "captcha"
    assert agent_backends.extract_result_status("RESULT:FAILED:not_eligible_location") == "failed:not_eligible_location"
    assert agent_backends.extract_result_status("RESULT:FAILED:captcha") == "captcha"
    assert agent_backends.extract_result_status("no result") is None


def test_extract_result_status_uses_last_result_token() -> None:
    output = "\n".join(
        [
            "Instructions:",
            "RESULT:APPLIED",
            "RESULT:EXPIRED",
            "Agent concluded:",
            "RESULT:FAILED:stuck",
        ]
    )
    assert agent_backends.extract_result_status(output) == "failed:stuck"


def test_codex_backend_run_reads_last_message_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent_backends, "reset_worker_dir", lambda worker_id: tmp_path)
    monkeypatch.setattr(agent_backends.config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(agent_backends.config, "APP_DIR", tmp_path)

    class FakeStdin:
        def __init__(self) -> None:
            self.buffer = ""

        def write(self, text: str) -> int:
            self.buffer += text
            return len(text)

        def close(self) -> None:
            return None

    class FakePopen:
        def __init__(self, cmd: list[str], **_: object) -> None:
            self.cmd = cmd
            self.stdin = FakeStdin()
            self.returncode = 0

        def communicate(self, input: str | None = None, timeout: int | None = None) -> tuple[str, str]:
            assert input == "PROMPT"
            assert timeout is not None
            output_path = Path(self.cmd[self.cmd.index("--output-last-message") + 1])
            output_path.write_text("RESULT:APPLIED\n", encoding="utf-8")
            return ("progress line\n", "")

        def poll(self) -> int:
            return self.returncode

    monkeypatch.setattr(agent_backends.subprocess, "Popen", FakePopen)

    registered: list[int] = []
    backend = agent_backends.CodexAutoApplyBackend()
    result = backend.run(
        job={"title": "Engineer", "site": "Example", "url": "https://example.com"},
        port=9222,
        worker_id=0,
        prompt="PROMPT",
        model="gpt-5.4",
        register_process=lambda worker_id, proc: registered.append(worker_id),
        unregister_process=lambda worker_id: registered.append(-worker_id),
    )

    assert result.final_output == "RESULT:APPLIED\n"
    assert "progress line" in result.raw_output
    assert registered == [0, 0]
    logs = sorted(tmp_path.glob("agent_codex_*_w0_Example.txt"))
    assert len(logs) == 1
    log_text = logs[0].read_text(encoding="utf-8")
    assert "progress line" in log_text
    assert "RESULT:APPLIED" in log_text
    assert not (tmp_path / "worker-0.log").exists()


def test_codex_backend_run_raises_timeout_when_process_hangs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent_backends, "reset_worker_dir", lambda worker_id: tmp_path)
    monkeypatch.setattr(agent_backends.config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(agent_backends.config, "APP_DIR", tmp_path)
    monkeypatch.setitem(agent_backends.config.DEFAULTS, "apply_timeout", 1)

    class FakeStdin:
        def __init__(self) -> None:
            self.buffer = ""

        def write(self, text: str) -> int:
            self.buffer += text
            return len(text)

        def close(self) -> None:
            return None

    class FakePopen:
        def __init__(self, cmd: list[str], **_: object) -> None:
            self.cmd = cmd
            self.stdin = FakeStdin()
            self.returncode = None
            self._terminated = False

        def communicate(self, input: str | None = None, timeout: int | None = None) -> tuple[str, str]:
            assert input == "PROMPT"
            raise subprocess.TimeoutExpired(cmd=self.cmd, timeout=timeout or 0, output="partial output")

        def poll(self) -> int | None:
            if self._terminated:
                return -15
            return None

        def terminate(self) -> None:
            self._terminated = True
            self.returncode = -15

        def wait(self, timeout: int | None = None) -> int:
            if not self._terminated:
                raise subprocess.TimeoutExpired(cmd=self.cmd, timeout=timeout or 0)
            return -15

        def kill(self) -> None:
            self._terminated = True
            self.returncode = -9

    monkeypatch.setattr(agent_backends.subprocess, "Popen", FakePopen)

    backend = agent_backends.CodexAutoApplyBackend()
    try:
        backend.run(
            job={"title": "Engineer", "site": "Example", "url": "https://example.com"},
            port=9222,
            worker_id=0,
            prompt="PROMPT",
            model="gpt-5.4",
            register_process=lambda worker_id, proc: None,
            unregister_process=lambda worker_id: None,
        )
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("Expected subprocess.TimeoutExpired")


def test_doctor_reports_auto_apply_agent_layer(monkeypatch, tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    resume_json = tmp_path / "resume.json"
    resume = tmp_path / "resume.txt"
    searches = tmp_path / "searches.yaml"
    profile.write_text("{}", encoding="utf-8")
    resume.write_text("resume", encoding="utf-8")
    searches.write_text("queries: []\n", encoding="utf-8")

    buffer = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buffer, force_terminal=False, width=200))
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setattr(config, "PROFILE_PATH", profile)
    monkeypatch.setattr(config, "RESUME_JSON_PATH", resume_json)
    monkeypatch.setattr(config, "RESUME_PATH", resume)
    monkeypatch.setattr(config, "RESUME_PDF_PATH", tmp_path / "resume.pdf")
    monkeypatch.setattr(config, "SEARCH_CONFIG_PATH", searches)
    monkeypatch.setattr(config, "get_chrome_path", lambda: "/Applications/Google Chrome.app")
    monkeypatch.setattr(config, "get_auto_apply_agent_setting", lambda environ=None: "auto")
    monkeypatch.setattr(
        config,
        "resolve_auto_apply_agent",
        lambda preferred=None, environ=None: config.AutoApplyAgentSelection(
            requested="auto",
            resolved="codex",
            model="gpt-5.4",
        ),
    )
    monkeypatch.setattr(
        config,
        "get_auto_apply_agent_statuses",
        lambda: {
            "codex": _agent_status(
                "codex", available=True, binary="/opt/homebrew/bin/codex", note="Logged in using ChatGPT"
            ),
            "claude": _agent_status("claude", available=False, note="Install from https://claude.ai/code"),
        },
    )
    monkeypatch.setattr(config, "get_tier", lambda: 3)
    monkeypatch.setattr(llm_provider, "format_llm_provider_status", lambda environ=None: "Gemini (gemini-2.0-flash)")
    monkeypatch.setattr(llm_provider, "llm_config_hint", lambda: "unused")
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/npx" if name == "npx" else None)

    cli.doctor()
    output = buffer.getvalue()

    assert "Built-in LLM" in output
    assert "Auto-apply agent" in output
    assert "auto -> codex (gpt-5.4)" in output
    assert "Codex login" in output
    assert "Logged in using ChatGPT" in output


def test_doctor_reports_jobspy_version_and_compatibility_warning(monkeypatch, tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    resume_json = tmp_path / "resume.json"
    resume = tmp_path / "resume.txt"
    searches = tmp_path / "searches.yaml"
    profile.write_text("{}", encoding="utf-8")
    resume.write_text("resume", encoding="utf-8")
    searches.write_text("queries: []\n", encoding="utf-8")

    buffer = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buffer, force_terminal=False, width=220))
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setattr(config, "PROFILE_PATH", profile)
    monkeypatch.setattr(config, "RESUME_JSON_PATH", resume_json)
    monkeypatch.setattr(config, "RESUME_PATH", resume)
    monkeypatch.setattr(config, "RESUME_PDF_PATH", tmp_path / "resume.pdf")
    monkeypatch.setattr(config, "SEARCH_CONFIG_PATH", searches)
    monkeypatch.setattr(config, "get_chrome_path", lambda: "/Applications/Google Chrome.app")
    monkeypatch.setattr(config, "get_auto_apply_agent_setting", lambda environ=None: "auto")
    monkeypatch.setattr(
        config,
        "resolve_auto_apply_agent",
        lambda preferred=None, environ=None: config.AutoApplyAgentSelection(
            requested="auto",
            resolved="codex",
            model="gpt-5.4",
        ),
    )
    monkeypatch.setattr(
        config,
        "get_auto_apply_agent_statuses",
        lambda: {
            "codex": _agent_status(
                "codex", available=True, binary="/opt/homebrew/bin/codex", note="Logged in using ChatGPT"
            ),
            "claude": _agent_status("claude", available=False, note="Install from https://claude.ai/code"),
        },
    )
    monkeypatch.setattr(config, "get_tier", lambda: 3)
    monkeypatch.setattr(llm_provider, "format_llm_provider_status", lambda environ=None: "Gemini (gemini-2.0-flash)")
    monkeypatch.setattr(llm_provider, "llm_config_hint", lambda: "unused")
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/npx" if name == "npx" else None)
    monkeypatch.setattr(
        cli,
        "_jobspy_runtime_capabilities",
        lambda: (
            "1.1.13",
            ["site_name", "search_term", "location", "results_wanted", "is_remote", "proxy", "country_indeed"],
            ["hours_old", "description_format", "linkedin_fetch_description", "proxies"],
        ),
    )

    cli.doctor()
    output = buffer.getvalue()

    assert "python-jobspy" in output
    assert "version 1.1.13" in output
    assert "JobSpy capability mode" in output
    assert "compatibility mode active" in output
    assert "missing args: hours_old" in output
