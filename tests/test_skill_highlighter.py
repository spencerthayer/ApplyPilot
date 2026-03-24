"""Tests for skill highlighter."""

from applypilot.tailoring.skill_highlighter import highlight, parse_bullet


class TestHighlight:
    def test_basic(self):
        result = highlight("Built SDK using CameraX", ["CameraX"])
        assert "<strong>CameraX</strong>" in result

    def test_multiple_skills(self):
        result = highlight("Used Docker and Kubernetes", ["Docker", "Kubernetes"])
        assert "<strong>Docker</strong>" in result
        assert "<strong>Kubernetes</strong>" in result

    def test_case_insensitive(self):
        result = highlight("built with kotlin", ["Kotlin"])
        assert "<strong>kotlin</strong>" in result

    def test_no_skills(self):
        result = highlight("Built something", [])
        assert result == "Built something"

    def test_empty_text(self):
        assert highlight("", ["Kotlin"]) == ""

    def test_longest_first(self):
        """'Kotlin Coroutines' should match before 'Kotlin'."""
        result = highlight("Used Kotlin Coroutines for async", ["Kotlin", "Kotlin Coroutines"])
        assert "<strong>Kotlin Coroutines</strong>" in result

    def test_word_boundary(self):
        """'Java' should not match inside 'JavaScript'."""
        result = highlight("Used JavaScript and Java", ["Java"])
        # Java should be bolded but not the Java inside JavaScript
        assert result.count("<strong>") == 1

    def test_custom_tag(self):
        result = highlight("Used Python", ["Python"], tag="b")
        assert "<b>Python</b>" in result


class TestParseBullet:
    def test_dict_format(self):
        text, skills = parse_bullet({"text": "Built X", "skills": ["Python"]})
        assert text == "Built X"
        assert skills == ["Python"]

    def test_string_format(self):
        text, skills = parse_bullet("Built X with Python")
        assert text == "Built X with Python"
        assert skills == []

    def test_dict_no_skills(self):
        text, skills = parse_bullet({"text": "Built X"})
        assert text == "Built X"
        assert skills == []

    def test_empty_dict(self):
        text, skills = parse_bullet({})
        assert text == ""
        assert skills == []
