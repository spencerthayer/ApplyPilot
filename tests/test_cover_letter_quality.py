"""Tests for the 2026-06-10 cover-letter quality fixes.

Covers:
- display_company: site (aggregator) is never presented as the employer
- validate_cover_letter: stem-based banned patterns (error tier) and the
  4-paragraph structure check
- generate_cover_letter: returns (letter, validation), retries with explicit
  word-count expansion feedback, and the prompt names the real company
- _cover_one_job: validation failures return path=None (→ cover_failed)
  instead of shipping the rejected letter
"""

import pytest

from applypilot.scoring.tailor import display_company
from applypilot.scoring.validator import validate_cover_letter


# ── Fixtures ──────────────────────────────────────────────────────────────

# 23 words, no banned patterns / leak phrases.
_SENT = (
    "At Uber I ran the Cadence platform for fifty engineering teams, "
    "moving billions of operations across regions every single day with measured uptime. "
)


def _para(n: int) -> str:
    return (_SENT * n).strip()


def _good_letter() -> str:
    # 4 substantial paragraphs, ~370 words, starts with Dear, ends with name.
    return (
        "Dear Hiring Manager,\n\n"
        + _para(4) + "\n\n"
        + _para(6) + "\n\n"
        + _para(4) + "\n\n"
        + _para(2) + "\n\n"
        + "Jordan"
    )


PROFILE = {
    "personal": {"full_name": "Jordan Lee", "preferred_name": "Jordan"},
    "skills_boundary": {"languages": ["Python", "Kotlin"]},
    "resume_facts": {},
}


class StubClient:
    """LLM client stub returning canned replies; records every chat call."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0]


# ── display_company ───────────────────────────────────────────────────────

def test_display_company_prefers_company_column():
    job = {"company": "Acme Corp", "site": "linkedin"}
    assert display_company(job) == "Acme Corp"


def test_display_company_never_returns_aggregator_site():
    job = {"company": "", "site": "linkedin", "strategy": "jobspy"}
    assert display_company(job) == ""


def test_display_company_extracts_greenhouse_slug():
    job = {
        "company": None,
        "site": "linkedin",
        "application_url": "https://job-boards.greenhouse.io/databricks/jobs/123",
    }
    assert display_company(job) == "databricks"


def test_display_company_direct_employer_site_fallback():
    job = {"company": "", "site": "Temporal", "strategy": "greenhouse_api"}
    assert display_company(job) == "temporal"


# ── validate_cover_letter: banned patterns (error tier) ──────────────────

GOOD = _good_letter()


def test_good_letter_passes():
    result = validate_cover_letter(GOOD)
    assert result["passed"], result["errors"]


@pytest.mark.parametrize("phrase,label", [
    ("This role aligns with my background in distributed systems work.", "align with"),
    ("My background aligned with the posting requirements from day one.", "align with"),
    ("These experiences demonstrate my ability to lead reliable teams.", "demonstrate"),
    ("I demonstrated a forty percent uptime improvement last quarter alone.", "demonstrate"),
    ("Happy to walk through the migration details on a call.", "happy to walk through"),
    ("Happy to walk you through the on-call setup sometime soon.", "happy to walk through"),
    ("The mission really resonates with the work I have done.", "resonate"),
])
def test_banned_patterns_are_errors(phrase, label):
    letter = GOOD.replace("Jordan", phrase + "\n\nJordan")
    result = validate_cover_letter(letter)
    assert not result["passed"]
    assert any(label in e for e in result["errors"]), result["errors"]


def test_plain_walkthrough_word_not_banned():
    # "walkthrough" / "walked through the code" should not trip the
    # "happy to walk through" stock-closer pattern.
    letter = GOOD.replace("Jordan", "I walked through the codebase with new hires weekly.\n\nJordan")
    result = validate_cover_letter(letter)
    assert result["passed"], result["errors"]


# ── validate_cover_letter: structure ──────────────────────────────────────

def test_two_paragraph_letter_fails_structure():
    letter = "Dear Hiring Manager,\n\n" + _para(5) + "\n\n" + _para(5) + "\n\nJordan"
    result = validate_cover_letter(letter)
    assert not result["passed"]
    assert any("body paragraph" in e for e in result["errors"]), result["errors"]


def test_three_paragraph_letter_passes_with_warning():
    letter = (
        "Dear Hiring Manager,\n\n"
        + _para(5) + "\n\n" + _para(5) + "\n\n" + _para(4) + "\n\nJordan"
    )
    result = validate_cover_letter(letter)
    assert result["passed"], result["errors"]
    assert any("3 body paragraphs" in w for w in result["warnings"])


# ── generate_cover_letter ────────────────────────────────────────────────

JOB = {
    "url": "https://linkedin.com/jobs/view/1",
    "title": "Senior Engineer",
    "site": "linkedin",
    "company": "Acme Corp",
    "location": "Seattle, WA",
    "full_description": "Acme Corp builds infrastructure software.",
}


def test_generate_returns_letter_and_validation(monkeypatch):
    from applypilot.scoring import cover_letter as cl
    stub = StubClient([GOOD])
    monkeypatch.setattr(cl, "get_client", lambda quality=False: stub)

    letter, validation = cl.generate_cover_letter("RESUME", JOB, PROFILE)
    assert validation["passed"]
    assert letter.startswith("Dear")


def test_prompt_names_real_company_not_site(monkeypatch):
    from applypilot.scoring import cover_letter as cl
    stub = StubClient([GOOD])
    monkeypatch.setattr(cl, "get_client", lambda quality=False: stub)

    cl.generate_cover_letter("RESUME", JOB, PROFILE)
    user_msg = stub.calls[0][1]["content"]
    assert "COMPANY: Acme Corp" in user_msg
    assert "COMPANY: linkedin" not in user_msg


def test_unknown_company_marked_unknown_not_aggregator(monkeypatch):
    from applypilot.scoring import cover_letter as cl
    stub = StubClient([GOOD])
    monkeypatch.setattr(cl, "get_client", lambda quality=False: stub)

    job = dict(JOB, company=None)
    cl.generate_cover_letter("RESUME", job, PROFILE)
    user_msg = stub.calls[0][1]["content"]
    assert "COMPANY: unknown" in user_msg
    assert "COMPANY: linkedin" not in user_msg


def test_retry_gets_word_count_expansion_feedback(monkeypatch):
    from applypilot.scoring import cover_letter as cl
    short_letter = "Dear Hiring Manager,\n\n" + _para(2) + "\n\nJordan"  # <180 words
    stub = StubClient([short_letter, GOOD])
    monkeypatch.setattr(cl, "get_client", lambda quality=False: stub)

    letter, validation = cl.generate_cover_letter("RESUME", JOB, PROFILE)
    assert validation["passed"]
    assert len(stub.calls) == 2
    retry_system = stub.calls[1][0]["content"]
    assert "AVOID THESE ISSUES" in retry_system
    assert "Expand the hook" in retry_system


def test_exhausted_retries_returns_failed_validation(monkeypatch):
    from applypilot.scoring import cover_letter as cl
    short_letter = "Dear Hiring Manager,\n\n" + _para(2) + "\n\nJordan"
    stub = StubClient([short_letter])
    monkeypatch.setattr(cl, "get_client", lambda quality=False: stub)

    letter, validation = cl.generate_cover_letter("RESUME", JOB, PROFILE, max_retries=1)
    assert not validation["passed"]
    assert len(stub.calls) == 2  # initial + 1 retry


# ── _cover_one_job: rejection path ────────────────────────────────────────

def test_cover_one_job_rejection_returns_no_path(monkeypatch, tmp_path):
    from applypilot.scoring import cover_letter as cl
    bad = "Dear Hiring Manager,\n\nToo short.\n\nJordan"
    monkeypatch.setattr(cl, "COVER_LETTER_DIR", tmp_path)
    monkeypatch.setattr(
        cl, "generate_cover_letter",
        lambda *a, **kw: (bad, {"passed": False, "errors": ["Too short (5 words)"], "warnings": []}),
    )

    result = cl._cover_one_job(JOB, "RESUME", PROFILE)
    assert result["path"] is None
    assert result["pdf_path"] is None
    assert result["error"].startswith("validation failed")
    rejected = list(tmp_path.glob("*_CL_rejected.txt"))
    assert len(rejected) == 1
    assert rejected[0].read_text() == bad


def test_cover_one_job_success_writes_letter(monkeypatch, tmp_path):
    from applypilot.scoring import cover_letter as cl
    monkeypatch.setattr(cl, "COVER_LETTER_DIR", tmp_path)
    monkeypatch.setattr(
        cl, "generate_cover_letter",
        lambda *a, **kw: (GOOD, {"passed": True, "errors": [], "warnings": []}),
    )

    result = cl._cover_one_job(JOB, "RESUME", PROFILE)
    assert result["path"] is not None
    assert result["path"].endswith("_CL.txt")
    assert not list(tmp_path.glob("*_CL_rejected.txt"))
