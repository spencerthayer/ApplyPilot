from __future__ import annotations

import io
import shutil
from pathlib import Path

from rich.console import Console

from applypilot import cli, config, llm_provider
from applypilot.apply import agent_backends, launcher
from applypilot.apply.agent_backends import BackendExecution
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

    selection = config.resolve_auto_apply_agent(
        environ={"AUTO_APPLY_AGENT": "auto", "AUTO_APPLY_MODEL": "gpt-5.4"}
    )
    assert selection.requested == "auto"
    assert selection.resolved == "codex"
    assert selection.model == "gpt-5.4"

    monkeypatch.setattr(
        config,
        "get_auto_apply_agent_statuses",
        lambda: {
            "codex": _agent_status("codex", available=False, binary="/opt/homebrew/bin/codex", note="Run `codex login`"),
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


def test_get_auto_apply_agent_priority_falls_back_on_invalid_values() -> None:
    assert config.get_auto_apply_agent_priority(
        {"AUTO_APPLY_AGENT_PRIORITY": "invalid,claude,claude"}
    ) == ("claude", "codex")


def test_auto_apply_model_setting_is_separate_from_llm_model() -> None:
    env = {
        "AUTO_APPLY_MODEL": "gpt-5.4",
        "LLM_MODEL": "gemini-2.0-flash",
    }
    assert config.get_auto_apply_model_setting("codex", env) == "gpt-5.4"
    assert llm_provider.detect_llm_provider(
        {
            "GEMINI_API_KEY": "key",
            "LLM_MODEL": "gemini-2.0-flash",
        }
    ).model == "gemini-2.0-flash"


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
    assert "--full-auto" in cmd
    assert "--ephemeral" in cmd
    assert "--skip-git-repo-check" in cmd
    assert ["-C", str(tmp_path)] == cmd[cmd.index("-C"):cmd.index("-C") + 2]
    assert ["--output-last-message", str(output_file)] == cmd[
        cmd.index("--output-last-message"):cmd.index("--output-last-message") + 2
    ]
    assert ["--model", "gpt-5.4"] == cmd[cmd.index("--model"):cmd.index("--model") + 2]
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
            self.stdout = iter(["progress line\n"])
            self.returncode = 0

        def wait(self, timeout: int | None = None) -> int:
            output_path = Path(self.cmd[self.cmd.index("--output-last-message") + 1])
            output_path.write_text("RESULT:APPLIED\n", encoding="utf-8")
            return self.returncode

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


def test_run_job_returns_deterministic_runtime_failure_without_result(monkeypatch) -> None:
    monkeypatch.setattr(launcher.prompt_mod, "build_prompt", lambda **kwargs: "PROMPT")
    monkeypatch.setattr(launcher, "update_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "add_event", lambda *args, **kwargs: None)

    class FakeBackend:
        def run(self, **kwargs: object) -> BackendExecution:
            return BackendExecution(
                final_output="",
                raw_output="Fatal: network down\n",
                duration_ms=1500,
                returncode=2,
            )

    monkeypatch.setattr(launcher, "get_backend", lambda agent: FakeBackend())

    status, duration_ms = launcher.run_job(
        job={"title": "Engineer", "site": "Example", "url": "https://example.com"},
        port=9222,
        worker_id=0,
        agent="codex",
        model="gpt-5.4",
    )

    assert duration_ms == 1500
    assert status.startswith("failed:codex_runtime_error:")


def test_kill_active_agent_processes_only_kills_running(monkeypatch) -> None:
    killed: list[int] = []

    class Proc:
        def __init__(self, pid: int, running: bool) -> None:
            self.pid = pid
            self._running = running

        def poll(self) -> int | None:
            return None if self._running else 0

    launcher._agent_procs.clear()
    launcher._agent_procs.update({1: Proc(101, True), 2: Proc(102, False)})
    monkeypatch.setattr(launcher, "_kill_process_tree", lambda pid: killed.append(pid))

    launcher._kill_active_agent_processes()

    assert killed == [101]
    launcher._agent_procs.clear()


def test_doctor_reports_auto_apply_agent_layer(monkeypatch, tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    resume = tmp_path / "resume.txt"
    searches = tmp_path / "searches.yaml"
    profile.write_text("{}", encoding="utf-8")
    resume.write_text("resume", encoding="utf-8")
    searches.write_text("queries: []\n", encoding="utf-8")

    buffer = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buffer, force_terminal=False, width=200))
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setattr(config, "PROFILE_PATH", profile)
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
            "codex": _agent_status("codex", available=True, binary="/opt/homebrew/bin/codex", note="Logged in using ChatGPT"),
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


def test_setup_auto_apply_writes_separate_agent_env(monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("GEMINI_API_KEY=test\nLLM_MODEL=gemini-2.0-flash\n", encoding="utf-8")

    answers = iter([True, False])
    prompts = iter(["codex", ""])

    monkeypatch.setattr(wizard_init, "ENV_PATH", env_path)
    monkeypatch.setattr(
        config,
        "get_auto_apply_agent_statuses",
        lambda: {
            "codex": _agent_status("codex", available=True, binary="/opt/homebrew/bin/codex", note="Logged in"),
            "claude": _agent_status("claude", available=False, note="Install from https://claude.ai/code"),
        },
    )
    monkeypatch.setattr(wizard_init.Confirm, "ask", lambda *args, **kwargs: next(answers))
    monkeypatch.setattr(wizard_init.Prompt, "ask", lambda *args, **kwargs: next(prompts))

    wizard_init._setup_auto_apply()

    content = env_path.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=test" in content
    assert "LLM_MODEL=gemini-2.0-flash" in content
    assert "AUTO_APPLY_AGENT=codex" in content
