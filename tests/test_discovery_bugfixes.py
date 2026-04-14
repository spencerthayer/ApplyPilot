"""Regression tests for discovery/enrichment bug fixes."""


def test_jobspy_title_matches_query():
    from applypilot.discovery.jobspy.storage import _title_matches_query

    assert _title_matches_query("Backend Software Engineer", "backend engineer")
    assert _title_matches_query("Senior Python Developer", "python developer")
    assert not _title_matches_query("UI/UX Designer", "serverless developer")
    assert not _title_matches_query("Marketing Manager", "backend engineer")
    assert _title_matches_query("Anything", "")  # empty query = accept all


def test_linkedin_url_normalization():
    from applypilot.cli.commands.single_cmd import _normalize_linkedin_url

    # Session URL → public
    assert (
            _normalize_linkedin_url("https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4346005653")
            == "https://www.linkedin.com/jobs/view/4346005653"
    )

    # Already public → unchanged
    assert (
            _normalize_linkedin_url("https://www.linkedin.com/jobs/view/4346005653")
            == "https://www.linkedin.com/jobs/view/4346005653"
    )

    # Non-LinkedIn → unchanged
    assert (
            _normalize_linkedin_url("https://careers.dexcom.com/careers/job/39595611")
            == "https://careers.dexcom.com/careers/job/39595611"
    )


def test_login_page_titles_detected():
    from applypilot.enrichment.scraper import _LOGIN_PAGE_TITLES

    assert any(p in "linkedin login, sign in".lower() for p in _LOGIN_PAGE_TITLES)
    assert any(p in "Create Account - Workday".lower() for p in _LOGIN_PAGE_TITLES)
    assert not any(p in "Backend Engineer at Apple".lower() for p in _LOGIN_PAGE_TITLES)
