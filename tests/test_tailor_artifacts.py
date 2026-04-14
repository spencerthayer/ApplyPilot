from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from applypilot.scoring.tailor import orchestrator as tailor


def _mock_app(monkeypatch, jobs: list[dict] | None = None):
    """Create a mock app with a fake job_repo."""
    repo = MagicMock()
    repo.get_jobs_by_stage_dict.return_value = jobs or []
    repo.update_tailoring.return_value = None
    repo.increment_attempts.return_value = None
    repo.find_by_url_fuzzy.return_value = None
    app = SimpleNamespace(container=SimpleNamespace(job_repo=repo), config=SimpleNamespace())
    monkeypatch.setattr("applypilot.bootstrap.get_app", lambda: app)
    return repo


def _make_job(url: str = "https://example.com/job/1") -> dict:
    return {
        "url": url,
        "title": "Staff Software Engineer - AI II",
        "site": "Thomson Reuters",
        "location": "Remote",
        "fit_score": 9,
        "full_description": "Build production AI systems.",
    }


def _approved_report() -> dict:
    return {
        "attempts": 1,
        "validator": {"passed": True, "errors": [], "warnings": []},
        "judge": {"passed": True, "verdict": "PASS", "issues": "none"},
        "status": "approved",
    }


def test_build_tailored_prefix_is_deterministic_and_unique_per_url() -> None:
    base = _make_job("https://example.com/job/1")
    same = _make_job("https://example.com/job/1")
    other = _make_job("https://example.com/job/2")

    first = tailor._build_tailored_prefix(base)
    second = tailor._build_tailored_prefix(same)
    third = tailor._build_tailored_prefix(other)

    assert first == second
    assert first != third
    assert first.startswith("Thomson_Reuters_Staff_Software_Engineer_-_AI_II_")


def test_run_tailoring_requires_pdf_for_submission(monkeypatch, tmp_path: Path) -> None:
    job = _make_job()

    monkeypatch.setattr(tailor, "TAILORED_DIR", tmp_path)
    monkeypatch.setattr(tailor, "load_profile", lambda: {"personal": {}})
    monkeypatch.setattr(tailor, "load_resume_text", lambda: "base resume")
    repo = _mock_app(monkeypatch, jobs=[job])
    monkeypatch.setattr(tailor, "tailor_resume", lambda *args, **kwargs: ("tailored resume", _approved_report()))

    def _fake_convert_to_pdf(text_path: Path) -> Path:
        out = Path(text_path).with_suffix(".pdf")
        out.write_bytes(b"%PDF-1.4 fake\n")
        return out

    monkeypatch.setattr("applypilot.scoring.pdf.convert_to_pdf", _fake_convert_to_pdf)

    result = tailor.run_tailoring(min_score=7, limit=1, validation_mode="normal")

    assert result["approved"] == 1
    assert result["errors"] == 0
    txts = list(tmp_path.glob("*.txt"))
    pdfs = list(tmp_path.glob("*.pdf"))
    assert any(not p.name.endswith("_JOB.txt") for p in txts)
    assert len(pdfs) == 1
    repo.update_tailoring.assert_called_once()
    repo.increment_attempts.assert_called()


def test_run_tailoring_does_not_persist_when_pdf_generation_fails(monkeypatch, tmp_path: Path) -> None:
    job = _make_job()

    monkeypatch.setattr(tailor, "TAILORED_DIR", tmp_path)
    monkeypatch.setattr(tailor, "load_profile", lambda: {"personal": {}})
    monkeypatch.setattr(tailor, "load_resume_text", lambda: "base resume")
    repo = _mock_app(monkeypatch, jobs=[job])
    monkeypatch.setattr(tailor, "tailor_resume", lambda *args, **kwargs: ("tailored resume", _approved_report()))
    monkeypatch.setattr("applypilot.scoring.pdf.convert_to_pdf", lambda _: (_ for _ in ()).throw(RuntimeError("boom")))

    result = tailor.run_tailoring(min_score=7, limit=1, validation_mode="normal")

    assert result["approved"] == 0
    assert result["errors"] == 1
    assert any(not p.name.endswith("_JOB.txt") for p in tmp_path.glob("*.txt"))
    # Verify repo was called for attempts but NOT for tailoring path
    repo.update_tailoring.assert_not_called()
    repo.increment_attempts.assert_called()
