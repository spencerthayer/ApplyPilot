from __future__ import annotations

from applypilot.url_safety import extract_company, host_matches, is_algolia_queries_url, parse_hostname


def test_host_matches_accepts_exact_hosts_and_true_subdomains() -> None:
    assert host_matches("job-boards.greenhouse.io", "greenhouse.io") is True
    assert host_matches("foo-dsn.algolia.net", "algolia.net") is True


def test_host_matches_rejects_query_string_and_path_bypasses() -> None:
    assert host_matches(parse_hostname("https://evil.example/?next=algolia.net"), "algolia.net") is False
    assert host_matches(parse_hostname("https://evil.example/greenhouse.io/jobs"), "greenhouse.io") is False


def test_is_algolia_queries_url_accepts_real_algolia_queries_endpoint() -> None:
    assert is_algolia_queries_url("https://foo-dsn.algolia.net/1/indexes/*/queries") is True


def test_is_algolia_queries_url_rejects_non_algolia_bypass() -> None:
    assert is_algolia_queries_url("https://evil.example/1/indexes/main/queries?next=algolia.net") is False


def test_extract_company_handles_precise_ats_patterns() -> None:
    assert extract_company("https://workiva.wd503.myworkdayjobs.com/en-US/recruiting/job/123") == "workiva"
    assert extract_company("https://job-boards.greenhouse.io/embed/job_app?for=coinbase") == "coinbase"
    assert extract_company("https://careers-mercuryinsurance.icims.com/jobs/123") == "mercuryinsurance"
    assert extract_company("https://apply.workable.com/acme/j/123") == "acme"
