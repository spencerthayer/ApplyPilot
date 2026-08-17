"""Tests for ``resolve_company_key`` — the function that decides which
company-cap bucket a job belongs to.

Caught in the field: a LinkedIn-discovered job whose Apply button
resolved to ``job-boards.greenhouse.io/temporaltechnologies/...`` was
returning ``None`` from this resolver because:

  * ``company`` was NULL (LinkedIn doesn't extract employer cleanly), and
  * ``strategy`` was ``jobspy``, not in the ``DIRECT_EMPLOYER_STRATEGIES``
    set that triggers the ``site``-fallback.

That left Temporal cap-exempt — the pipeline applied to four Temporal
postings before the user noticed. The fix: also look at the
``application_url`` and extract the ATS-tenant slug from known apply
hosts (Greenhouse, Lever, Ashby, Workday). Those slugs are the
canonical employer identifier regardless of how the row was discovered.
"""

from __future__ import annotations

from applypilot.scoring.tailor import resolve_company_key


# ── Greenhouse ───────────────────────────────────────────────────────────

def test_extracts_greenhouse_slug_for_aggregator_discovered_job():
    job = {
        "company": None,
        "site": "linkedin",
        "strategy": "jobspy",
        "application_url": "https://job-boards.greenhouse.io/temporaltechnologies/jobs/4290103007",
        "url": "https://www.linkedin.com/jobs/view/foo",
    }
    assert resolve_company_key(job) == "temporaltechnologies"


def test_extracts_greenhouse_slug_eu_subdomain():
    job = {
        "company": None,
        "site": "linkedin",
        "strategy": "linkedin",
        "application_url": "https://job-boards.eu.greenhouse.io/overstory/jobs/4600037101",
        "url": "https://www.linkedin.com/jobs/view/x",
    }
    assert resolve_company_key(job) == "overstory"


def test_extracts_greenhouse_legacy_boards_subdomain():
    job = {
        "company": None,
        "site": "linkedin",
        "strategy": "indeed",
        "application_url": "https://boards.greenhouse.io/acme/jobs/123?gh_jid=123",
        "url": "https://www.indeed.com/viewjob?jk=x",
    }
    assert resolve_company_key(job) == "acme"


# ── Other ATSes ──────────────────────────────────────────────────────────

def test_extracts_lever_slug():
    job = {
        "company": None,
        "site": "linkedin",
        "strategy": "linkedin",
        "application_url": "https://jobs.lever.co/stripe/00000000-0000-0000-0000-000000000000",
    }
    assert resolve_company_key(job) == "stripe"


def test_extracts_ashby_slug():
    job = {
        "company": None,
        "site": "linkedin",
        "strategy": "linkedin",
        "application_url": "https://jobs.ashbyhq.com/openai/bd190cad-99ec-4fe7-8f8f-de96b5aa5969/application",
    }
    assert resolve_company_key(job) == "openai"


def test_extracts_workday_tenant():
    job = {
        "company": None,
        "site": "linkedin",
        "strategy": "linkedin",
        "application_url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/Senior-Software-Engineer",
    }
    assert resolve_company_key(job) == "nvidia"


# ── Resolution priority ──────────────────────────────────────────────────

def test_explicit_company_column_wins_over_apply_url():
    job = {
        "company": "AcmeCorp",
        "site": "linkedin",
        "strategy": "linkedin",
        "application_url": "https://job-boards.greenhouse.io/temporaltechnologies/jobs/1",
    }
    # Explicit company beats slug extraction. Lower-cased.
    assert resolve_company_key(job) == "acmecorp"


def test_falls_back_to_site_for_direct_employer_strategies():
    """Pre-existing behavior: greenhouse_api scrapers populate `site` with
    the employer name and may leave `company` NULL."""
    job = {
        "company": None,
        "site": "Anduril Industries",
        "strategy": "greenhouse_api",
        "application_url": "https://example.com/jobs/x",  # not a known ATS host
    }
    assert resolve_company_key(job) == "anduril industries"


def test_returns_none_for_non_ats_aggregator_no_company():
    """Defensive check: aggregator-discovered job that points at a
    non-ATS apply URL still gets None (we don't have a reliable bucket)."""
    job = {
        "company": None,
        "site": "linkedin",
        "strategy": "linkedin",
        "application_url": "https://example.com/careers/apply/123",
        "url": "https://www.linkedin.com/jobs/view/x",
    }
    assert resolve_company_key(job) is None


def test_returns_none_when_application_url_is_empty():
    job = {
        "company": None,
        "site": "linkedin",
        "strategy": "linkedin",
        "application_url": None,
        "url": "https://www.linkedin.com/jobs/view/x",
    }
    assert resolve_company_key(job) is None
