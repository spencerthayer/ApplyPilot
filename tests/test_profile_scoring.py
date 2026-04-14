"""Tests for INIT-05, INIT-08, INIT-21."""

from applypilot.scoring.profile_completeness import compute_completeness
from applypilot.scoring.resume_quality import compute_quality
from applypilot.scoring.star_validator import validate_star, validate_all_bullets


def _sample():
    return {
        "basics": {
            "name": "Test",
            "email": "t@t.com",
            "phone": "123",
            "summary": "Engineer with 4 years building production systems.",
        },
        "work": [
            {
                "name": "Amazon",
                "position": "SDE",
                "highlights": [
                    "Built DDD system with Flask reducing costs by 75%",
                    "Deployed on AWS Fargate cutting deploy time by 50%",
                    "Helped with testing",
                ],
            }
        ],
        "skills": [{"name": "Languages", "keywords": ["Python", "Java", "Kotlin"]}],
        "education": [{"institution": "MIT"}],
        "projects": [],
    }


class TestCompleteness:
    def test_score_range(self):
        r = compute_completeness(_sample())
        assert 0 <= r["score"] <= 10

    def test_empty_resume(self):
        r = compute_completeness({"basics": {}})
        assert r["score"] < 3
        assert len(r["tips"]) > 0

    def test_full_resume_scores_higher(self):
        full = _sample()
        full["projects"] = [{"name": "App", "highlights": ["Built it"]}]
        empty = {"basics": {}}
        assert compute_completeness(full)["score"] > compute_completeness(empty)["score"]


class TestQuality:
    def test_score_range(self):
        r = compute_quality(_sample())
        assert 0 <= r["score"] <= 10

    def test_weak_verb_flagged(self):
        r = compute_quality(_sample())
        weak = [f for f in r["feedback"] if "Weak verb" in f.get("issue", "")]
        assert len(weak) >= 1  # "Helped with testing"

    def test_no_bullets(self):
        r = compute_quality({"work": []})
        assert r["score"] == 0


class TestStarValidator:
    def test_good_bullet(self):
        r = validate_star("Built DDD system with Flask reducing costs by 75%")
        assert r["valid"]

    def test_weak_verb(self):
        r = validate_star("helped with testing stuff")
        assert not r["valid"]
        assert any("action verb" in i.lower() for i in r["issues"])

    def test_no_metrics(self):
        r = validate_star("Designed a new architecture for the backend system")
        assert any("measurable" in i.lower() for i in r["issues"])

    def test_validate_all(self):
        results = validate_all_bullets(_sample())
        assert len(results) >= 1  # "Helped with testing" should fail
