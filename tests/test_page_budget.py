"""Tests for page budget calculator."""

from applypilot.tailoring.page_budget import calculate, PAGE_LINES


class TestCalculate:
    def test_basic(self):
        r = calculate(experience_count=2, skill_group_count=5)
        assert r["total_bullet_lines"] > 0
        assert len(r["bullets_per_experience"]) == 2

    def test_recency_weighting(self):
        """Most recent role gets more bullets than older roles."""
        r = calculate(experience_count=2, skill_group_count=5)
        assert r["bullets_per_experience"][0] >= r["bullets_per_experience"][1]

    def test_more_skills_less_bullets(self):
        """More skill groups = less space for bullets."""
        r5 = calculate(experience_count=2, skill_group_count=5)
        r14 = calculate(experience_count=2, skill_group_count=14)
        assert r5["total_bullet_lines"] > r14["total_bullet_lines"]

    def test_single_experience(self):
        r = calculate(experience_count=1, skill_group_count=5)
        assert len(r["bullets_per_experience"]) == 1
        assert r["bullets_per_experience"][0] > 0

    def test_no_experience(self):
        r = calculate(experience_count=0, skill_group_count=5)
        assert r["bullets_per_experience"] == []

    def test_overflow_detection(self):
        """Fixed content exceeding page should flag overflow."""
        r = calculate(experience_count=10, skill_group_count=30, page_lines=20)
        assert r["overflow"] is True

    def test_no_summary(self):
        with_s = calculate(experience_count=2, skill_group_count=5, has_summary=True)
        without_s = calculate(experience_count=2, skill_group_count=5, has_summary=False)
        assert without_s["total_bullet_lines"] > with_s["total_bullet_lines"]

    def test_no_education(self):
        with_e = calculate(experience_count=2, skill_group_count=5, has_education=True)
        without_e = calculate(experience_count=2, skill_group_count=5, has_education=False)
        assert without_e["total_bullet_lines"] > with_e["total_bullet_lines"]

    def test_min_one_bullet_per_role(self):
        """Even with tight space, every role gets at least 1 bullet."""
        r = calculate(experience_count=5, skill_group_count=20, page_lines=40)
        for b in r["bullets_per_experience"]:
            assert b >= 1

    def test_three_experiences(self):
        r = calculate(experience_count=3, skill_group_count=5)
        assert len(r["bullets_per_experience"]) == 3
        # Recency: first > second > third
        assert r["bullets_per_experience"][0] >= r["bullets_per_experience"][1]
        assert r["bullets_per_experience"][1] >= r["bullets_per_experience"][2]
