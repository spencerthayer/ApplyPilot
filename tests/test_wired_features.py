"""Tests for newly wired innovation features + rendering fixes."""


# ── story_bank ────────────────────────────────────────────────────────────


class TestStoryBank:
    def test_generate_stories_basic(self):
        from applypilot.scoring.story_bank import generate_stories

        bullets = [
            {"text": "Designed and built a DDD microservice architecture reducing latency by 40%", "company": "Amazon"},
            {"text": "Migrated legacy monolith to cloud Kubernetes saving 75% infra cost", "company": "iServeU"},
        ]
        reqs = ["microservice architecture design", "cloud migration Kubernetes"]
        stories = generate_stories(bullets, reqs)
        assert len(stories) >= 1
        assert stories[0].requirement
        assert stories[0].action
        assert stories[0].reflection

    def test_generate_stories_empty_reqs(self):
        from applypilot.scoring.story_bank import generate_stories

        assert generate_stories([{"text": "Built API", "company": "X"}], []) == []

    def test_generate_stories_no_match(self):
        from applypilot.scoring.story_bank import generate_stories

        stories = generate_stories(
            [{"text": "short", "company": "X"}],
            ["quantum computing expertise"],
        )
        assert stories == []

    def test_extract_result_with_metric(self):
        from applypilot.scoring.story_bank import _extract_result

        assert "40%" in _extract_result("Reduced latency by 40% across all services")

    def test_extract_result_no_metric(self):
        from applypilot.scoring.story_bank import _extract_result

        assert _extract_result("Built a thing") == "Delivered successfully"

    def test_generate_reflection_design(self):
        from applypilot.scoring.story_bank import _generate_reflection

        r = _generate_reflection("Independently designed the payment system")
        assert "end-to-end" in r.lower()

    def test_generate_reflection_migration(self):
        from applypilot.scoring.story_bank import _generate_reflection

        r = _generate_reflection("Migrated legacy system to cloud")
        assert "migration" in r.lower()

    def test_big_three_exists(self):
        from applypilot.scoring.story_bank import BIG_THREE

        assert "tell_me_about_yourself" in BIG_THREE
        assert "most_impactful_project" in BIG_THREE
        assert "conflict_resolution" in BIG_THREE


# ── negotiation ───────────────────────────────────────────────────────────


class TestNegotiation:
    def test_generate_scripts_all_keys(self):
        from applypilot.scoring.negotiation import generate_scripts

        scripts = generate_scripts("$150K", "Backend Engineer", "Google")
        assert set(scripts.keys()) == {
            "salary_expectation",
            "geographic_pushback",
            "below_target",
            "competing_offer",
            "downlevel_counter",
        }

    def test_scripts_contain_salary(self):
        from applypilot.scoring.negotiation import generate_scripts

        scripts = generate_scripts("$150K", "SDE", "Amazon")
        assert "$150K" in scripts["salary_expectation"]

    def test_scripts_contain_company(self):
        from applypilot.scoring.negotiation import generate_scripts

        scripts = generate_scripts("$100K", "SDE", "Apple")
        assert "Apple" in scripts["below_target"]
        assert "Apple" in scripts["competing_offer"]


# ── star_validator ────────────────────────────────────────────────────────


class TestStarValidator:
    def test_valid_bullet(self):
        from applypilot.scoring.star_validator import validate_star

        r = validate_star("Designed and built a DDD microservice reducing latency by 40%")
        assert r["valid"] is True
        assert r["issues"] == []

    def test_missing_action_verb(self):
        from applypilot.scoring.star_validator import validate_star

        r = validate_star("the system was built using Python")
        assert r["valid"] is False
        assert any("action verb" in i.lower() for i in r["issues"])

    def test_missing_result(self):
        from applypilot.scoring.star_validator import validate_star

        r = validate_star("Built a backend service using Python and Flask for the team")
        assert any("result" in i.lower() or "metric" in i.lower() for i in r["issues"])

    def test_too_short(self):
        from applypilot.scoring.star_validator import validate_star

        r = validate_star("Built API")
        assert any("short" in i.lower() for i in r["issues"])

    def test_validate_all_bullets(self):
        from applypilot.scoring.star_validator import validate_all_bullets

        resume = {
            "work": [
                {
                    "name": "Amazon",
                    "highlights": [
                        "Built API",
                        "Designed and shipped a DDD microservice reducing latency by 40%",
                    ],
                }
            ]
        }
        issues = validate_all_bullets(resume)
        assert len(issues) >= 1
        assert issues[0]["company"] == "Amazon"

    def test_empty_resume(self):
        from applypilot.scoring.star_validator import validate_all_bullets

        assert validate_all_bullets({}) == []
        assert validate_all_bullets({"work": []}) == []


# ── track_framing ─────────────────────────────────────────────────────────


class TestTrackFraming:
    def test_exact_match(self):
        from applypilot.services.track_framing import get_framing

        f = get_framing("backend")
        assert "backend" in f.lower()

    def test_fuzzy_match(self):
        from applypilot.services.track_framing import get_framing

        f = get_framing("Android Development")
        assert "android" in f.lower() or "Android" in f

    def test_unknown_track_fallback(self):
        from applypilot.services.track_framing import get_framing

        f = get_framing("Quantum Computing")
        assert "Quantum Computing" in f

    def test_all_framings_non_empty(self):
        from applypilot.services.track_framing import TRACK_FRAMINGS

        for key, val in TRACK_FRAMINGS.items():
            assert val, f"Empty framing for {key}"


# ── resume_rendering ──────────────────────────────────────────────────────


class TestResumeRendering:
    def test_default_config(self):
        from applypilot.config.resume_rendering import ResumeConfig

        rc = ResumeConfig()
        assert rc.max_pages == "auto"
        assert rc.bullet_format == "star"
        assert "experience" in rc.section_order

    def test_academic_preset(self):
        from applypilot.config.resume_rendering import PRESETS

        ac = PRESETS["academic"]
        assert ac.max_pages == "unlimited"
        assert ac.section_order[0] == "education"

    def test_startup_preset(self):
        from applypilot.config.resume_rendering import PRESETS

        sp = PRESETS["startup"]
        assert sp.max_pages == "1"

    def test_resolve_academic_job(self):
        from applypilot.config.resume_rendering import resolve_config

        rc = resolve_config(job={"title": "Postdoc Researcher"})
        assert rc.max_pages == "unlimited"

    def test_resolve_startup_job(self):
        from applypilot.config.resume_rendering import resolve_config

        rc = resolve_config(job={"title": "SDE", "company": "Series A startup"})
        assert rc.max_pages == "1"

    def test_resolve_default(self):
        from applypilot.config.resume_rendering import resolve_config

        rc = resolve_config(job={"title": "SDE", "company": "Google"})
        assert rc.max_pages == "auto"

    def test_override_wins(self):
        from applypilot.config.resume_rendering import ResumeConfig, resolve_config

        override = ResumeConfig(max_pages="3")
        rc = resolve_config(job={"title": "Postdoc"}, override=override)
        assert rc.max_pages == "3"


# ── evaluation_report wiring ──────────────────────────────────────────────


class TestEvaluationReportWiring:
    def test_report_includes_stories(self):
        from applypilot.scoring.evaluation_report import generate_evaluation_report

        profile = {
            "work": [
                {
                    "name": "Amazon",
                    "highlights": [
                        "Designed and built a DDD microservice architecture reducing latency by 40%",
                        "Migrated legacy monolith to cloud Kubernetes saving 75% infra cost",
                    ],
                }
            ],
            "meta": {"applypilot": {"years_of_experience_total": 4}},
        }
        score = {
            "score": 7,
            "title": "Backend Engineer",
            "missing_requirements": ["microservice architecture design", "cloud migration Kubernetes"],
            "matched_skills": ["Python", "Kubernetes"],
        }
        report = generate_evaluation_report(score, profile)
        assert "interview_stories" in report
        assert len(report["interview_stories"]) >= 1

    def test_report_includes_negotiation_when_salary_set(self):
        from applypilot.scoring.evaluation_report import generate_evaluation_report

        profile = {
            "work": [],
            "meta": {"applypilot": {"compensation": {"salary_expectation": "$150K"}}},
        }
        score = {"score": 7, "title": "SDE", "company": "Google", "matched_skills": [], "missing_requirements": []}
        report = generate_evaluation_report(score, profile)
        assert "negotiation" in report
        assert "$150K" in report["negotiation"]["salary_expectation"]

    def test_report_no_negotiation_without_salary(self):
        from applypilot.scoring.evaluation_report import generate_evaluation_report

        profile = {"work": [], "meta": {"applypilot": {}}}
        score = {"score": 5, "title": "SDE", "matched_skills": [], "missing_requirements": []}
        report = generate_evaluation_report(score, profile)
        assert "negotiation" not in report


# ── ResumeBuilder.render_html ─────────────────────────────────────────────


class TestResumeBuilderHTML:
    def test_render_html_basic(self):
        from applypilot.resume_builder import ResumeBuilder

        b = ResumeBuilder()
        b.set_header("John Doe", "Software Engineer", "NYC", "john@example.com | github.com/john")
        b.add_section("SUMMARY", "Experienced engineer with 5 years.")
        b.add_section("TECHNICAL SKILLS", "Languages: Python, Java\nFrameworks: Flask, Spring")
        html = b.render_html()
        assert "John Doe" in html
        assert "Software Engineer" in html
        assert "Python" in html
        assert "<!DOCTYPE html>" in html

    def test_render_html_empty_sections_skipped(self):
        from applypilot.resume_builder import ResumeBuilder

        b = ResumeBuilder()
        b.set_header("Jane")
        b.add_section("SUMMARY", "")
        b.add_section("EXPERIENCE", "SDE at Amazon\n- Built stuff")
        html = b.render_html()
        assert "Experience" in html
        assert "Summary" not in html

    def test_render_html_from_json_resume(self):
        from applypilot.resume_builder import from_json_resume

        data = {
            "basics": {"name": "Test User", "label": "SDE", "email": "test@test.com"},
            "work": [
                {"name": "Co", "position": "SDE", "startDate": "2022", "highlights": ["Built API reducing latency 30%"]}
            ],
            "skills": [{"name": "Languages", "keywords": ["Python"]}],
        }
        builder = from_json_resume(data)
        html = builder.render_html()
        assert "Test User" in html
        assert "Python" in html
        assert "Built API" in html


# ── HTML renderer shared path ─────────────────────────────────────────────


class TestHTMLRendererShared:
    def test_build_html_from_builder(self):
        from applypilot.resume_builder import ResumeBuilder
        from applypilot.scoring.pdf.html_renderer import build_html_from_builder

        b = ResumeBuilder()
        b.set_header("Alice", "Engineer")
        b.add_section("EDUCATION", "MIT | CS | 2020")
        html = build_html_from_builder(b)
        assert "Alice" in html
        assert "Education" in html
        assert "MIT" in html

    def test_build_html_legacy_compat(self):
        from applypilot.scoring.pdf.html_renderer import build_html

        resume = {
            "name": "Bob",
            "title": "SDE",
            "location": "NYC",
            "contact": "bob@test.com | github.com/bob",
            "sections": {"SUMMARY": "Experienced dev.", "EDUCATION": "Stanford | CS"},
        }
        html = build_html(resume)
        assert "Bob" in html
        assert "Stanford" in html
