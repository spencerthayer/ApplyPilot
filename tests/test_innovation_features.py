"""Tests for innovation features — A-F evaluation, story bank, negotiation, framing."""

from applypilot.scoring.evaluation_report import evaluate_level_strategy, evaluate_personalization
from applypilot.scoring.story_bank import generate_stories, _extract_result, BIG_THREE
from applypilot.scoring.negotiation import generate_scripts
from applypilot.services.track_framing import get_framing


class TestLevelStrategy:
    def test_stretch_role(self):
        r = evaluate_level_strategy(2, 4, "4")
        assert r["strategy"] == "stretch"
        assert len(r["sell_plan"]) >= 2
        assert len(r["downlevel_plan"]) >= 1

    def test_level_match(self):
        r = evaluate_level_strategy(3, 3, "5")
        assert r["strategy"] == "level_match"

    def test_overqualified(self):
        r = evaluate_level_strategy(4, 2, "8")
        assert r["strategy"] == "overqualified"


class TestPersonalization:
    def test_generates_cv_changes(self):
        r = evaluate_personalization(["Python", "Docker"], ["React", "GraphQL"], "Backend SDE")
        assert len(r["cv_changes"]) >= 1
        assert len(r["linkedin_changes"]) >= 1

    def test_empty_skills(self):
        r = evaluate_personalization([], [], "SDE")
        assert isinstance(r["cv_changes"], list)


class TestStoryBank:
    def test_generates_stories(self):
        bullets = [
            {"text": "Built DDD system with Flask reducing costs by 75%", "company": "iServeU"},
            {"text": "Deployed on AWS Fargate cutting deploy time by 50%", "company": "iServeU"},
        ]
        stories = generate_stories(bullets, ["Built DDD system with Flask", "Deployed on AWS cloud"])
        assert len(stories) >= 1
        assert stories[0].reflection

    def test_extract_result(self):
        assert "75%" in _extract_result("Built system reducing costs by 75% annually")

    def test_big_three_exists(self):
        assert "tell_me_about_yourself" in BIG_THREE


class TestNegotiation:
    def test_generates_all_scripts(self):
        r = generate_scripts("$120K-$150K", "Backend SDE", "Apple")
        assert "Apple" in r["below_target"]
        assert "120K" in r["salary_expectation"]
        assert len(r) == 5


class TestTrackFraming:
    def test_known_track(self):
        assert (
                "backend" in get_framing("Backend / API Development").lower()
                or "production" in get_framing("Backend / API Development").lower()
        )

    def test_unknown_track(self):
        f = get_framing("Quantum Computing")
        assert "Quantum Computing" in f
