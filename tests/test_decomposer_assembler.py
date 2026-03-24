"""Tests for resume decomposer and assembler."""

import pytest

from applypilot.tailoring.decomposer import decompose
from applypilot.tailoring.assembler import assemble


SAMPLE_RESUME = {
    "basics": {
        "name": "Test User",
        "label": "Software Engineer",
        "email": "test@example.com",
        "phone": "+1 555 0100",
        "summary": "Engineer with 3 years experience.",
    },
    "work": [
        {
            "name": "BigCorp",
            "position": "SDE",
            "startDate": "2023-01",
            "endDate": "",
            "highlights": ["Built API serving 1M requests/day", "Reduced latency by 40%"],
        },
        {
            "name": "StartupCo",
            "position": "Developer",
            "startDate": "2021-06",
            "endDate": "2022-12",
            "highlights": ["Shipped mobile app to 50K users"],
        },
    ],
    "skills": [
        {"name": "Languages", "keywords": ["Python", "Java"]},
        {"name": "Cloud", "keywords": ["AWS", "Docker"]},
    ],
    "education": [
        {"institution": "MIT", "studyType": "B.S.", "area": "CS", "startDate": "2017", "endDate": "2021"},
    ],
    "projects": [
        {"name": "Side Project", "description": "A cool thing", "keywords": ["Python"]},
    ],
}


class TestDecompose:
    def test_returns_segments(self):
        segs = decompose(SAMPLE_RESUME)
        assert len(segs) > 0

    def test_has_root(self):
        segs = decompose(SAMPLE_RESUME)
        roots = [s for s in segs if s.type == "root"]
        assert len(roots) == 1
        assert roots[0].content == "Test User"

    def test_experience_count(self):
        segs = decompose(SAMPLE_RESUME)
        exps = [s for s in segs if s.type == "experience"]
        assert len(exps) == 2

    def test_bullet_count(self):
        segs = decompose(SAMPLE_RESUME)
        bullets = [s for s in segs if s.type == "bullet"]
        assert len(bullets) == 3  # 2 from BigCorp + 1 from StartupCo

    def test_bullets_have_parent(self):
        segs = decompose(SAMPLE_RESUME)
        exp_ids = {s.id for s in segs if s.type == "experience"}
        for b in segs:
            if b.type == "bullet":
                assert b.parent_id in exp_ids

    def test_skill_groups(self):
        segs = decompose(SAMPLE_RESUME)
        skills = [s for s in segs if s.type == "skill_group"]
        assert len(skills) == 2

    def test_education(self):
        segs = decompose(SAMPLE_RESUME)
        edu = [s for s in segs if s.type == "education"]
        assert len(edu) == 1
        assert "MIT" in edu[0].content

    def test_project(self):
        segs = decompose(SAMPLE_RESUME)
        proj = [s for s in segs if s.type == "project"]
        assert len(proj) == 1

    def test_empty_resume(self):
        segs = decompose({"basics": {}})
        assert len(segs) == 1  # just root

    def test_no_projects(self):
        r = {**SAMPLE_RESUME, "projects": []}
        segs = decompose(r)
        assert not any(s.type == "project" for s in segs)


class TestAssemble:
    def test_round_trip(self):
        segs = decompose(SAMPLE_RESUME)
        text = assemble(segs, SAMPLE_RESUME)
        assert "Test User" in text
        assert "SUMMARY" in text
        assert "EXPERIENCE" in text
        assert "Built API serving 1M requests/day" in text

    def test_contains_all_sections(self):
        segs = decompose(SAMPLE_RESUME)
        text = assemble(segs, SAMPLE_RESUME)
        assert "TECHNICAL SKILLS" in text
        assert "EDUCATION" in text
        assert "MIT" in text

    def test_contact_info(self):
        segs = decompose(SAMPLE_RESUME)
        text = assemble(segs, SAMPLE_RESUME)
        assert "test@example.com" in text

    def test_empty_segments(self):
        text = assemble([], {"basics": {"name": "X"}})
        assert "X" in text
