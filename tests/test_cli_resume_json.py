from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

import applypilot.cli as cli
import applypilot.cli.commands.apply_cmd as _apply_cmd
import applypilot.config as config
import applypilot.llm_provider as llm_provider
import applypilot.resume_render as resume_render
import applypilot.wizard.init as wizard_init


def _sample_resume_json() -> dict:
    return {
        "basics": {
            "name": "Alex Example",
            "label": "Software Engineer",
            "email": "alex@example.com",
            "phone": "5551234567",
            "summary": "Built reliable systems.",
            "location": {"city": "Seattle", "region": "WA", "countryCode": "US"},
            "profiles": [
                {"network": "GitHub", "url": "https://github.com/alex"},
                {"network": "LinkedIn", "url": "https://linkedin.com/in/alex"},
            ],
        },
        "work": [
            {
                "name": "Example Co",
                "position": "Senior Engineer",
                "startDate": "2020-01",
                "summary": "Led backend delivery.",
                "highlights": ["Shipped APIs"],
                "x-applypilot": {"key_metrics": ["35% faster processing"]},
            }
        ],
        "education": [],
        "skills": [{"name": "Programming Languages", "keywords": ["Python"]}],
        "projects": [],
        "meta": {
            "applypilot": {
                "target_role": "Staff Engineer",
                "work_authorization": {
                    "legally_authorized_to_work": "Yes",
                    "require_sponsorship": "No",
                },
                "compensation": {
                    "salary_expectation": "170000",
                    "salary_currency": "USD",
                    "salary_range_min": "160000",
                    "salary_range_max": "190000",
                },
                "availability": {"earliest_start_date": "Immediately"},
                "personal": {
                    "github_url": "https://github.com/alex",
                    "linkedin_url": "https://linkedin.com/in/alex",
                },
                "target_role": "Staff Engineer",
                "years_of_experience_total": "10",
                "target_locations": {"locations": ["US"], "mode": "any"},
                "tailoring_config": {
                    "default_role_type": "software_engineer",
                    "validation": {
                        "enabled": True,
                        "max_retries": 3,
                        "min_bullets_per_role": 2,
                        "max_bullets_per_role": 5,
                        "min_metrics_ratio": 0.7,
                    },
                    "role_types": {},
                },
            }
        },
    }


def test_analyze_accepts_json_resume_override(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    runner = CliRunner()
    job_file = tmp_path / "job.txt"
    resume_file = tmp_path / "resume.json"
    job_file.write_text("Need Python and APIs", encoding="utf-8")
    resume_file.write_text(json.dumps(_sample_resume_json()), encoding="utf-8")

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)

    class FakeParser:
        def parse(self, job: dict) -> SimpleNamespace:
            return SimpleNamespace(
                title=job["title"],
                company=job["company"],
                seniority=SimpleNamespace(value="senior"),
                requirements=[],
                skills=[],
                key_responsibilities=[],
                red_flags=[],
                company_context="",
            )

    class FakeMatcher:
        def analyze(self, resume_text: str, job_intel: SimpleNamespace) -> SimpleNamespace:
            assert "TECHNICAL SKILLS" in resume_text
            assert "Alex Example" in resume_text
            return SimpleNamespace(
                overall_score=9.0,
                strengths=["Python"],
                gaps=[],
                recommendations=["Lean into APIs"],
                bullet_priorities={"Shipped APIs": 10},
            )

    monkeypatch.setattr("applypilot.intelligence.jd_parser.JobDescriptionParser", FakeParser)
    monkeypatch.setattr("applypilot.intelligence.resume_matcher.ResumeMatcher", FakeMatcher)

    result = runner.invoke(
        cli.app,
        ["analyze", "--text-file", str(job_file), "--resume-file", str(resume_file)],
    )

    assert result.exit_code == 0
    assert '"overall_score": 9.0' in result.stdout


def test_resume_render_command_uses_helper(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    canonical = tmp_path / "resume.json"
    output = tmp_path / "resume.html"
    canonical.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr(cli, "RESUME_JSON_PATH", canonical)
    monkeypatch.setattr(
        resume_render,
        "render_resume_html",
        lambda resume_path=None, theme=None, output_path=None: (
            output_path or output,
            theme or "jsonresume-theme-even",
        ),
    )

    result = runner.invoke(
        cli.app,
        ["resume", "render", "--format", "html", "--theme", "jsonresume-theme-even", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert "Rendered HTML" in result.stdout
    assert "jsonresume-theme-even" in result.stdout


def test_doctor_reports_canonical_mode(monkeypatch, tmp_path: Path) -> None:
    canonical = tmp_path / "resume.json"
    profile = tmp_path / "profile.json"
    resume_txt = tmp_path / "resume.txt"
    searches = tmp_path / "searches.yaml"
    canonical.write_text(json.dumps(_sample_resume_json()), encoding="utf-8")
    profile.write_text("{}", encoding="utf-8")
    resume_txt.write_text("legacy", encoding="utf-8")
    searches.write_text("queries: []\n", encoding="utf-8")

    buffer = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buffer, force_terminal=False, width=200))
    monkeypatch.setattr(config, "load_env", lambda: None)
    monkeypatch.setattr(config, "PROFILE_PATH", profile)
    monkeypatch.setattr(config, "RESUME_JSON_PATH", canonical)
    monkeypatch.setattr(config, "RESUME_PATH", resume_txt)
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
            "codex": config.AutoApplyAgentStatus(
                key="codex",
                label="Codex CLI",
                binary_path="/opt/homebrew/bin/codex",
                available=True,
                note="Logged in",
                auth_ok=True,
            ),
            "claude": config.AutoApplyAgentStatus(
                key="claude",
                label="Claude Code CLI",
                binary_path=None,
                available=False,
                note="Install from https://claude.ai/code",
            ),
        },
    )
    monkeypatch.setattr(llm_provider, "format_llm_provider_status", lambda environ=None: "Gemini (gemini-2.0-flash)")
    monkeypatch.setattr(llm_provider, "llm_config_hint", lambda: "unused")
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/npx" if name == "npx" else None)
    monkeypatch.setattr(resume_render, "LOCAL_RESUMED", tmp_path / "node_modules" / ".bin" / "resumed")
    resume_render.LOCAL_RESUMED.parent.mkdir(parents=True, exist_ok=True)
    resume_render.LOCAL_RESUMED.write_text("", encoding="utf-8")

    cli.doctor()
    output = buffer.getvalue()

    assert "profile.json" in output
    assert "resume.json" in output
    assert "resume.txt" in output
    assert "canonical resume.json" not in output
    assert "Profile source" not in output


def test_load_profile_backfills_from_resume_json_once(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    canonical = tmp_path / "resume.json"
    profile = tmp_path / "profile.json"
    canonical.write_text(json.dumps(_sample_resume_json()), encoding="utf-8")

    monkeypatch.setattr(config, "PROFILE_PATH", profile)
    monkeypatch.setattr(config, "RESUME_JSON_PATH", canonical)

    first_profile = config.load_profile()

    assert profile.exists()
    assert first_profile["personal"]["full_name"] == "Alex Example"
    written_profile = json.loads(profile.read_text(encoding="utf-8"))
    assert "personal" not in written_profile
    assert written_profile["work_authorization"]["legally_authorized_to_work"] == "Yes"

    canonical.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(config.ResumeJsonError, match="Invalid resume.json"):
        config.load_profile()


def test_apply_backfills_profile_from_resume_json(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    runner = CliRunner()
    canonical = tmp_path / "resume.json"
    profile = tmp_path / "profile.json"
    prompt_file = tmp_path / "prompt.txt"
    canonical.write_text(json.dumps(_sample_resume_json()), encoding="utf-8")
    prompt_file.write_text("PROMPT", encoding="utf-8")

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr(config, "check_tier", lambda *args, **kwargs: None)
    monkeypatch.setattr(config, "PROFILE_PATH", profile)
    monkeypatch.setattr(config, "RESUME_JSON_PATH", canonical)
    monkeypatch.setattr(_apply_cmd, "_resolve_backend_option", lambda *args, **kwargs: ("codex", "gpt-5.4"))
    monkeypatch.setattr("applypilot.apply.launcher.gen_prompt", lambda *args, **kwargs: prompt_file)
    monkeypatch.setattr("applypilot.apply.backends.build_manual_command", lambda *args, **kwargs: "codex exec")

    result = runner.invoke(
        cli.app,
        ["apply", "--gen", "--url", "https://example.com/jobs/1"],
    )

    assert result.exit_code == 0
    assert "Wrote prompt to:" in result.stdout
    assert profile.exists()
    written_profile = json.loads(profile.read_text(encoding="utf-8"))
    assert "personal" not in written_profile
    assert written_profile["compensation"]["salary_expectation"] == "170000"


def test_load_profile_migrates_missing_resume_content_from_legacy_profile(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    canonical = tmp_path / "resume.json"
    profile = tmp_path / "profile.json"
    resume_data = _sample_resume_json()
    resume_data["basics"]["email"] = ""
    resume_data["basics"]["phone"] = ""
    resume_data["basics"]["profiles"] = []
    resume_data["work"] = []
    resume_data["education"] = []
    resume_data["skills"] = []
    resume_data["projects"] = []
    resume_data["meta"]["applypilot"]["personal"] = {}
    resume_data["meta"]["applypilot"].pop("target_role", None)
    canonical.write_text(json.dumps(resume_data), encoding="utf-8")
    profile.write_text(
        json.dumps(
            {
                "personal": {
                    "full_name": "Alex Example",
                    "email": "legacy@example.com",
                    "phone": "5550001111",
                    "linkedin_url": "https://linkedin.com/in/legacy-alex",
                    "github_url": "https://github.com/legacy-alex",
                },
                "experience": {
                    "target_role": "Principal Engineer",
                    "years_of_experience_total": "9",
                    "current_title": "Senior Engineer",
                },
                "work_authorization": {
                    "legally_authorized_to_work": "Yes",
                    "require_sponsorship": "No",
                },
                "work_history": [
                    {
                        "company": "Legacy Co",
                        "position": "Senior Engineer",
                        "start_date": "2018-01-01",
                        "end_date": "2020-12-31",
                        "highlights": ["Built API platform"],
                        "key_metrics": ["2x throughput"],
                    }
                ],
                "education": [
                    {
                        "institution": "Legacy University",
                        "studyType": "B.S.",
                        "area": "Computer Science",
                        "endDate": "2017",
                    }
                ],
                "skills_boundary": {
                    "programming_languages": ["Python"],
                    "frameworks": ["FastAPI"],
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(config, "PROFILE_PATH", profile)
    monkeypatch.setattr(config, "RESUME_JSON_PATH", canonical)

    loaded = config.load_profile()
    saved_resume = json.loads(canonical.read_text(encoding="utf-8"))
    saved_profile = json.loads(profile.read_text(encoding="utf-8"))

    assert loaded["personal"]["email"] == "legacy@example.com"
    assert loaded["work"][0]["company"] == "Legacy Co"
    assert saved_resume["basics"]["email"] == "legacy@example.com"
    assert saved_resume["work"][0]["name"] == "Legacy Co"
    assert saved_resume["education"][0]["institution"] == "Legacy University"
    assert saved_resume["skills"][0]["keywords"] == ["Python"]
    assert saved_resume["meta"]["applypilot"]["target_role"] == "Principal Engineer"
    assert "personal" not in saved_profile
    assert saved_profile["work_authorization"]["legally_authorized_to_work"] == "Yes"
