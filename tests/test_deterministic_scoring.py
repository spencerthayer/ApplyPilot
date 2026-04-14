"""Tests for deterministic scoring: title matcher, skill overlap, job context, seniority."""

import pytest
from applypilot.scoring.deterministic.title_matcher import (
    tokenize,
    tokenize_set,
    title_key,
    infer_role_family,
    seniority_from_text,
    jaccard_similarity,
)
from applypilot.scoring.deterministic.skill_overlap import (
    contains_phrase,
    extract_known_skills,
)
from applypilot.scoring.deterministic.job_context_extractor import (
    extract_requirement_focused_text,
)


class TestTokenize:
    def test_basic(self):
        assert tokenize("Senior Software Engineer") == ["senior", "software", "engineer"]

    def test_special_chars(self):
        assert tokenize("C++ / C#") == ["c", "c"]

    def test_empty(self):
        assert tokenize("") == []


class TestTokenizeSet:
    def test_removes_stopwords(self):
        tokens = tokenize_set("Senior Software Engineer for the Team")
        assert "for" not in tokens
        assert "the" not in tokens
        assert "software" in tokens


class TestTitleKey:
    def test_basic(self):
        assert title_key("Senior Software Engineer") == "software engineer"

    def test_empty(self):
        assert title_key("") == "untitled"


class TestInferRoleFamily:
    def test_software(self):
        assert infer_role_family("Senior Software Engineer") == "software_engineering"

    def test_data(self):
        assert infer_role_family("Data Scientist ML") == "data_ai"

    def test_marketing(self):
        assert infer_role_family("Marketing Manager") == "marketing"

    def test_unknown(self):
        assert infer_role_family("Chief Happiness Officer") == "unknown"


class TestSeniority:
    def test_intern(self):
        assert seniority_from_text("Intern") == 0

    def test_senior(self):
        assert seniority_from_text("Senior Backend Engineer") == 3

    def test_staff(self):
        assert seniority_from_text("Staff Engineer") == 4

    def test_director(self):
        assert seniority_from_text("Director of Engineering") == 5

    def test_default(self):
        assert seniority_from_text("") == 2


class TestJaccard:
    def test_identical(self):
        assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self):
        assert jaccard_similarity({"a"}, {"b"}) == 0.0

    def test_partial(self):
        assert jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"}) == 0.5

    def test_empty(self):
        assert jaccard_similarity(set(), {"a"}) == 0.0


class TestContainsPhrase:
    def test_simple(self):
        assert contains_phrase("we need python experience", "python") is True

    def test_not_found(self):
        assert contains_phrase("we need java experience", "python") is False

    def test_special_chars(self):
        assert contains_phrase("experience with c++ required", "c++") is True

    def test_empty(self):
        assert contains_phrase("anything", "") is False


class TestExtractKnownSkills:
    def test_multiple(self):
        skills = extract_known_skills("We need Python, Docker, and Kubernetes experience")
        assert "python" in skills
        assert "docker" in skills
        assert "kubernetes" in skills

    def test_none(self):
        assert extract_known_skills("No technical skills mentioned") == set()

    def test_aliases(self):
        skills = extract_known_skills("Experience with k8s and Node.js")
        assert "kubernetes" in skills
        assert "javascript" in skills


class TestExtractRequirementFocusedText:
    def test_short_text_unchanged(self):
        text = "Short job description"
        assert extract_requirement_focused_text(text) == text

    def test_prioritizes_requirements(self):
        blocks = "\n\n".join(
            [
                "About the company\nWe are great.",
                "Requirements\n- Python\n- Docker\n- 5 years experience",
                "Benefits\nFree lunch and gym.",
            ]
        )
        result = extract_requirement_focused_text(blocks, max_chars=200)
        assert "Python" in result
        assert "Requirements" in result

    def test_empty(self):
        assert extract_requirement_focused_text("") == ""
