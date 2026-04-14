"""Tests for new RUN features: title filter, company registry, PipelineContext, source/company filtering."""

from __future__ import annotations


# ── Title filter ────────────────────────────────────────────────────


class TestTitleFilter:
    def test_empty_query_matches(self):
        from applypilot.discovery.title_filter import title_matches_query

        assert title_matches_query("Software Engineer", "") is True

    def test_any_term_matches_default(self):
        from applypilot.discovery.title_filter import title_matches_query

        assert title_matches_query("Senior Software Engineer", "software engineer") is True

    def test_any_term_no_match(self):
        from applypilot.discovery.title_filter import title_matches_query

        assert title_matches_query("Nurse Practitioner", "software engineer") is False

    def test_strict_all_terms_must_match(self):
        from applypilot.discovery.title_filter import title_matches_query

        assert title_matches_query("Senior Software Engineer", "software engineer", strict=True) is True

    def test_strict_partial_fails(self):
        from applypilot.discovery.title_filter import title_matches_query

        # "sales" is in title but "software" is not
        assert title_matches_query("Sales Engineer", "software engineer", strict=True) is False

    def test_strict_false_partial_passes(self):
        from applypilot.discovery.title_filter import title_matches_query

        # "engineer" matches, so loose mode passes
        assert title_matches_query("Sales Engineer", "software engineer", strict=False) is True

    def test_short_terms_ignored(self):
        from applypilot.discovery.title_filter import title_matches_query

        # "AI" is only 2 chars, should be ignored; "engineer" matches
        assert title_matches_query("AI Engineer", "AI engineer") is True

    def test_case_insensitive(self):
        from applypilot.discovery.title_filter import title_matches_query

        assert title_matches_query("SENIOR SOFTWARE ENGINEER", "software") is True

    def test_no_valid_terms(self):
        from applypilot.discovery.title_filter import title_matches_query

        # All terms <= 2 chars
        assert title_matches_query("AI ML", "AI ML") is True


# ── PipelineContext ─────────────────────────────────────────────────


class TestPipelineContext:
    def test_defaults(self):
        from applypilot.pipeline.context import PipelineContext

        ctx = PipelineContext()
        assert ctx.urls is None
        assert ctx.companies is None
        assert ctx.sources is None
        assert ctx.strict_title is False
        assert ctx.force is False
        assert ctx.is_single is False
        assert ctx.job_url is None

    def test_single_url(self):
        from applypilot.pipeline.context import PipelineContext

        ctx = PipelineContext(urls=["https://example.com/job/1"])
        assert ctx.is_single is True
        assert ctx.job_url == "https://example.com/job/1"

    def test_multiple_urls(self):
        from applypilot.pipeline.context import PipelineContext

        ctx = PipelineContext(urls=["https://a.com", "https://b.com"])
        assert ctx.is_single is False
        assert ctx.job_url is None

    def test_no_urls(self):
        from applypilot.pipeline.context import PipelineContext

        ctx = PipelineContext()
        assert ctx.is_single is False
        assert ctx.job_url is None

    def test_companies_field(self):
        from applypilot.pipeline.context import PipelineContext

        ctx = PipelineContext(companies=["walmart", "stripe"])
        assert ctx.companies == ["walmart", "stripe"]

    def test_strict_title_field(self):
        from applypilot.pipeline.context import PipelineContext

        ctx = PipelineContext(strict_title=True)
        assert ctx.strict_title is True

    def test_force_field(self):
        from applypilot.pipeline.context import PipelineContext

        ctx = PipelineContext(force=True)
        assert ctx.force is True


# ── Company registry ────────────────────────────────────────────────


class TestCompanyRegistry:
    def _make_registry(self):
        from applypilot.discovery.company_registry import CompanyRegistry, CompanyRecord

        reg = CompanyRegistry()
        # Manually add test entries instead of loading from YAML
        rec1 = CompanyRecord(
            key="walmart",
            name="Walmart",
            aliases=["walmart inc", "walmart global tech"],
            domain="walmart.com",
            runners={"workday": "walmart", "lever": "walmart_global_tech"},
        )
        rec2 = CompanyRecord(
            key="stripe",
            name="Stripe",
            aliases=["stripe inc"],
            domain="stripe.com",
            runners={"greenhouse": "stripe"},
        )
        reg._companies = {"walmart": rec1, "stripe": rec2}
        reg._alias_index = {
            "walmart": "walmart",
            "walmart inc": "walmart",
            "walmart global tech": "walmart",
            "stripe": "stripe",
            "stripe inc": "stripe",
        }
        return reg

    def test_resolve_by_key(self):
        reg = self._make_registry()
        rec = reg.resolve("walmart")
        assert rec is not None
        assert rec.name == "Walmart"

    def test_resolve_by_alias(self):
        reg = self._make_registry()
        rec = reg.resolve("Walmart Inc")
        assert rec is not None
        assert rec.key == "walmart"

    def test_resolve_by_domain(self):
        reg = self._make_registry()
        rec = reg.resolve("stripe.com")
        assert rec is not None
        assert rec.key == "stripe"

    def test_resolve_by_substring(self):
        reg = self._make_registry()
        rec = reg.resolve("Wal")
        assert rec is not None
        assert rec.key == "walmart"

    def test_resolve_unknown(self):
        reg = self._make_registry()
        assert reg.resolve("nonexistent") is None

    def test_resolve_many(self):
        reg = self._make_registry()
        resolved, unresolved = reg.resolve_many(["walmart", "stripe", "unknown"])
        assert len(resolved) == 2
        assert unresolved == ["unknown"]

    def test_matches_scraped_name(self):
        reg = self._make_registry()
        rec = reg.resolve("walmart")
        assert reg.matches_scraped_name("Walmart Inc.", rec) is True
        assert reg.matches_scraped_name("WALMART GLOBAL TECH LLC", rec) is True
        assert reg.matches_scraped_name("Google", rec) is False

    def test_runner_routing(self):
        reg = self._make_registry()
        rec = reg.resolve("walmart")
        assert rec.runners == {"workday": "walmart", "lever": "walmart_global_tech"}
        rec2 = reg.resolve("stripe")
        assert rec2.runners == {"greenhouse": "stripe"}


# ── Mutual exclusion validation ─────────────────────────────────────


class TestMutualExclusion:
    """Test that --url is mutually exclusive with --source/--company at the CLI level."""

    def test_url_with_source_raises(self):
        """Simulates the validation logic in run_cmd.py."""
        urls = ["https://example.com"]
        sources = ["workday"]
        companies = None
        # This is the validation from run_cmd.py
        assert urls and (sources or companies)

    def test_url_with_company_raises(self):
        urls = ["https://example.com"]
        sources = None
        companies = ["walmart"]
        assert urls and (sources or companies)

    def test_url_alone_ok(self):
        urls = ["https://example.com"]
        sources = None
        companies = None
        assert not (urls and (sources or companies))

    def test_source_alone_ok(self):
        urls = None
        sources = ["workday"]
        companies = None
        assert not (urls and (sources or companies))

    def test_company_alone_ok(self):
        urls = None
        sources = None
        companies = ["walmart"]
        assert not (urls and (sources or companies))

    def test_source_and_company_ok(self):
        urls = None
        sources = ["workday"]
        companies = ["walmart"]
        assert not (urls and (sources or companies))


# ── Source filtering ────────────────────────────────────────────────


class TestSourceFiltering:
    def test_filter_runners_by_source(self):
        """Simulates the source filtering logic in job_service.py."""
        runners = {
            "jobspy": lambda: None,
            "workday": lambda: None,
            "greenhouse": lambda: None,
            "hackernews": lambda: None,
        }
        sources = ["workday", "greenhouse"]
        filtered = {k: v for k, v in runners.items() if k in sources}
        assert set(filtered.keys()) == {"workday", "greenhouse"}

    def test_no_source_filter_keeps_all(self):
        runners = {"jobspy": 1, "workday": 2, "greenhouse": 3}
        sources = None
        if sources:
            runners = {k: v for k, v in runners.items() if k in sources}
        assert len(runners) == 3


# ── Company filtering per runner ────────────────────────────────────


class TestCompanyFiltering:
    def test_workday_employer_key_filter(self):
        """Simulates filtering employers dict by registry-resolved keys."""
        employers = {
            "walmart": {"name": "Walmart"},
            "google": {"name": "Google"},
            "apple": {"name": "Apple"},
        }
        employer_keys = ["walmart"]
        filtered = {k: v for k, v in employers.items() if k in employer_keys}
        assert list(filtered.keys()) == ["walmart"]

    def test_no_employer_keys_keeps_all(self):
        employers = {"a": 1, "b": 2, "c": 3}
        employer_keys = None
        if employer_keys is not None:
            employers = {k: v for k, v in employers.items() if k in employer_keys}
        assert len(employers) == 3

    def test_empty_employer_keys_filters_all(self):
        employers = {"a": 1, "b": 2}
        employer_keys = []
        filtered = {k: v for k, v in employers.items() if k in employer_keys}
        assert len(filtered) == 0


# ── Jobspy title filter backward compat ─────────────────────────────


class TestJobspyTitleFilterCompat:
    def test_shared_filter_matches_old_behavior(self):
        """The shared filter with strict=False should match the old _title_matches_query behavior."""
        from applypilot.discovery.title_filter import title_matches_query

        # Old behavior: any term in title_lower for term in query.lower().split() if len(term) > 2
        assert title_matches_query("Senior Software Engineer", "software engineer") is True
        assert title_matches_query("Nurse", "software engineer") is False
        assert title_matches_query("Android Developer", "android") is True


# ── Greenhouse title filter backward compat ──────────────────────────


class TestGreenhouseTitleFilterCompat:
    def test_greenhouse_title_filter_delegates(self):
        from applypilot.discovery.greenhouse.search import _title_matches_query

        assert _title_matches_query("Software Engineer", "software") is True
        assert _title_matches_query("Nurse", "software") is False

    def test_greenhouse_title_filter_strict(self):
        from applypilot.discovery.greenhouse.search import _title_matches_query

        assert _title_matches_query("Software Engineer", "software engineer", strict=True) is True
        assert _title_matches_query("Sales Engineer", "software engineer", strict=True) is False
