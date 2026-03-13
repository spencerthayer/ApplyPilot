from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import applypilot.config as config_module
import applypilot.llm as llm_module
from applypilot.apply import prompt as prompt_module
from applypilot.scoring import scorer, tailor
from applypilot.view import generate_dashboard


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        if isinstance(self._rows, list):
            return self._rows[0]
        return self._rows

    def fetchall(self):
        if isinstance(self._rows, list):
            return self._rows
        return [self._rows]


class _FakeConnection:
    def __init__(self, jobs: list[dict]) -> None:
        self._jobs = jobs

    def execute(self, query: str) -> _FakeCursor:
        compact = " ".join(query.split())
        if compact == "SELECT COUNT(*) FROM jobs":
            return _FakeCursor((1,))
        if "WHERE full_description IS NOT NULL AND application_url IS NOT NULL" in compact:
            return _FakeCursor((1,))
        if "WHERE fit_score IS NOT NULL" in compact and "GROUP BY" not in compact:
            return _FakeCursor((1,))
        if "WHERE fit_score >= 7" in compact:
            return _FakeCursor((1,))
        if "GROUP BY fit_score" in compact:
            return _FakeCursor([(8, 1)])
        if "FROM jobs GROUP BY site" in compact:
            return _FakeCursor(
                [
                    {
                        "site": self._jobs[0]["site"],
                        "total": 1,
                        "high_fit": 1,
                        "mid_fit": 0,
                        "low_fit": 0,
                        "unscored": 0,
                        "avg_score": 8.0,
                    }
                ]
            )
        if "WHERE fit_score >= 5" in compact:
            return _FakeCursor(self._jobs)
        if "WHERE apply_status = 'applied'" in compact:
            return _FakeCursor([])
        if "WHERE apply_status IS NOT NULL AND apply_status != 'applied'" in compact:
            return _FakeCursor([])
        raise AssertionError(f"Unexpected query: {compact}")


def test_build_prompt_does_not_embed_secret_values(monkeypatch, tmp_path: Path) -> None:
    resume_txt = tmp_path / "tailored_resume.txt"
    resume_pdf = tmp_path / "tailored_resume.pdf"
    resume_txt.write_text("Tailored resume", encoding="utf-8")
    resume_pdf.write_text("pdf-bytes", encoding="utf-8")

    monkeypatch.setenv("CAPSOLVER_API_KEY", "cap-secret-key")
    monkeypatch.setattr(prompt_module.config, "load_env", lambda: None)
    monkeypatch.setattr(prompt_module.config, "APPLY_WORKER_DIR", tmp_path / "apply-workers")
    monkeypatch.setattr(
        prompt_module.config,
        "load_profile",
        lambda: {
            "personal": {
                "full_name": "Alex Example",
                "preferred_name": "Alex",
                "email": "alex@example.com",
                "phone": "5551234567",
                "city": "Seattle",
                "password": "plaintext-password",
            }
        },
    )
    monkeypatch.setattr(prompt_module.config, "load_search_config", lambda: {"locations": []})
    monkeypatch.setattr(config_module, "load_blocked_sso", lambda: [])
    monkeypatch.setattr(config_module, "load_no_signup_domains", lambda: [])
    monkeypatch.setattr(prompt_module, "_build_profile_summary", lambda profile: "PROFILE")
    monkeypatch.setattr(prompt_module, "_build_location_check", lambda profile, cfg: "LOCATION")
    monkeypatch.setattr(prompt_module, "_build_salary_section", lambda profile: "SALARY")
    monkeypatch.setattr(prompt_module, "_build_screening_section", lambda profile: "SCREENING")
    monkeypatch.setattr(prompt_module, "_build_hard_rules", lambda profile: "HARD RULES")

    prompt = prompt_module.build_prompt(
        job={
            "url": "https://example.com/job",
            "title": "Engineer",
            "site": "Example",
            "application_url": "https://example.com/apply",
            "fit_score": 8,
            "tailored_resume_path": str(resume_txt),
        },
        tailored_resume="Tailored resume",
    )

    assert "cap-secret-key" not in prompt
    assert "plaintext-password" not in prompt
    assert "$CAPSOLVER_API_KEY" in prompt
    assert "APPLYPILOT_LOGIN_EXAMPLE_COM_PASSWORD" in prompt
    assert "APPLYPILOT_SITE_PASSWORD" in prompt


def test_build_prompt_marks_indeed_as_no_signup_domain(monkeypatch, tmp_path: Path) -> None:
    resume_txt = tmp_path / "tailored_resume.txt"
    resume_pdf = tmp_path / "tailored_resume.pdf"
    resume_txt.write_text("Tailored resume", encoding="utf-8")
    resume_pdf.write_text("pdf-bytes", encoding="utf-8")

    monkeypatch.setenv("CAPSOLVER_API_KEY", "cap-secret-key")
    monkeypatch.setattr(prompt_module.config, "load_env", lambda: None)
    monkeypatch.setattr(prompt_module.config, "APPLY_WORKER_DIR", tmp_path / "apply-workers")
    monkeypatch.setattr(
        prompt_module.config,
        "load_profile",
        lambda: {
            "personal": {
                "full_name": "Alex Example",
                "preferred_name": "Alex",
                "email": "alex@example.com",
                "phone": "5551234567",
                "city": "Seattle",
            }
        },
    )
    monkeypatch.setattr(prompt_module.config, "load_search_config", lambda: {"locations": []})
    monkeypatch.setattr(config_module, "load_blocked_sso", lambda: [])
    monkeypatch.setattr(config_module, "load_no_signup_domains", lambda: ["indeed.com", "ziprecruiter.com"])
    monkeypatch.setattr(prompt_module, "_build_profile_summary", lambda profile: "PROFILE")
    monkeypatch.setattr(prompt_module, "_build_location_check", lambda profile, cfg: "LOCATION")
    monkeypatch.setattr(prompt_module, "_build_salary_section", lambda profile: "SALARY")
    monkeypatch.setattr(prompt_module, "_build_screening_section", lambda profile: "SCREENING")
    monkeypatch.setattr(prompt_module, "_build_hard_rules", lambda profile: "HARD RULES")

    prompt = prompt_module.build_prompt(
        job={
            "url": "https://www.indeed.com/viewjob?jk=abc123",
            "title": "Engineer",
            "site": "Indeed",
            "application_url": "https://www.indeed.com/viewjob?jk=abc123",
            "fit_score": 8,
            "tailored_resume_path": str(resume_txt),
        },
        tailored_resume="Tailored resume",
    )

    assert "APPLYPILOT_LOGIN_INDEED_COM_EMAIL" in prompt
    assert "APPLYPILOT_LOGIN_INDEED_COM_PASSWORD" in prompt
    assert "Signup policy: NO SIGNUP for this domain." in prompt


def test_generate_dashboard_escapes_attribute_bound_values(monkeypatch, tmp_path: Path) -> None:
    job = {
        "url": 'https://example.com/job?q="quoted"',
        "title": "Engineer",
        "salary": "$100k",
        "description": "desc",
        "location": 'Remote" onclick="alert(1)',
        "site": "Example",
        "strategy": None,
        "full_description": "full description",
        "application_url": "https://example.com/apply",
        "detail_error": None,
        "fit_score": 8,
        "score_reasoning": "python\nstrong fit",
        "applied_at": None,
        "apply_status": None,
        "apply_error": None,
        "last_attempted_at": None,
    }

    monkeypatch.setattr("applypilot.view.get_connection", lambda: _FakeConnection([job]))
    monkeypatch.setattr("applypilot.view.APP_DIR", tmp_path)

    output_path = Path(generate_dashboard(str(tmp_path / "dashboard.html")))
    html = output_path.read_text(encoding="utf-8")

    assert 'data-location="remote&quot; onclick=&quot;alert(1)"' in html
    assert 'data-location="remote" onclick=' not in html
    assert 'data-cmd="applypilot apply --url https://example.com/job?q=&quot;quoted&quot;"' in html
    assert 'data-cmd="applypilot apply --url https://example.com/job?q="quoted""' not in html


def test_run_scoring_returns_safe_summary_when_resume_is_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(scorer, "load_resume_text", lambda: (_ for _ in ()).throw(FileNotFoundError()))

    result = scorer.run_scoring()

    assert result == {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": [], "excluded": 0, "auto_healed": 0}


def test_run_tailoring_returns_safe_summary_when_resume_is_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tailor, "load_resume_text", lambda: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(tailor, "load_profile", lambda: {"personal": {}})

    result = tailor.run_tailoring()

    assert result == {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}


def test_run_tailoring_defaults_to_unbounded_limit(monkeypatch) -> None:
    captured: dict[str, int] = {}

    monkeypatch.setattr(tailor, "load_profile", lambda: {"personal": {}, "skills": [], "work": [], "education": []})
    monkeypatch.setattr(tailor, "load_resume_text", lambda: "base resume")
    monkeypatch.setattr(tailor, "get_connection", lambda: object())

    def _fake_get_jobs_by_stage(**kwargs):  # noqa: ANN003
        captured["limit"] = kwargs["limit"]
        return []

    monkeypatch.setattr(tailor, "get_jobs_by_stage", _fake_get_jobs_by_stage)

    result = tailor.run_tailoring()

    assert captured["limit"] == 0
    assert result == {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}


def test_get_client_initializes_singleton_once_under_concurrency(monkeypatch) -> None:
    created: list[object] = []
    registered: list[object] = []

    class FakeClient:
        def __init__(self, config) -> None:
            time.sleep(0.02)
            created.append(config)

        def close(self) -> None:
            return None

    monkeypatch.setattr(config_module, "load_env", lambda: None)
    monkeypatch.setattr(
        llm_module,
        "resolve_llm_config",
        lambda: llm_module.LLMConfig(
            provider="openai",
            api_base=None,
            model="openai/gpt-4o-mini",
            api_key="test-key",
        ),
    )
    monkeypatch.setattr(llm_module, "LLMClient", FakeClient)
    monkeypatch.setattr(llm_module.atexit, "register", lambda fn: registered.append(fn))
    monkeypatch.setattr(llm_module, "_instance", None)
    monkeypatch.setattr(llm_module, "_instance_lock", threading.Lock())

    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: llm_module.get_client(), range(8)))

    assert len(created) == 1
    assert len(registered) == 1
    assert len({id(client) for client in clients}) == 1
