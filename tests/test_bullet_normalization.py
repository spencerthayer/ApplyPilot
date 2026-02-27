"""Tests for bullet normalization to prevent JSON metadata leaks.

@file test_bullet_normalization.py
@description Tests that bullets with embedded JSON are properly cleaned.
"""

import pytest
from applypilot.scoring.tailor import _normalize_bullet, assemble_resume_text


class TestNormalizeBullet:
    """Test the _normalize_bullet function handles various input types."""

    def test_plain_string_bullet(self):
        """Plain string bullets should pass through unchanged."""
        bullet = "Built API handling 1M requests/day"
        result = _normalize_bullet(bullet)
        assert result == "Built API handling 1M requests/day"

    def test_bullet_with_trailing_json(self):
        """Bullets with trailing JSON metadata should have JSON stripped."""
        bullet = 'Built API handling 1M requests/day {"variants": {"car": "Test", "who": "Test"}, "tags": ["test"], "skills": ["python"]}'
        result = _normalize_bullet(bullet)
        assert result == "Built API handling 1M requests/day"
        assert "variants" not in result
        assert "tags" not in result

    def test_dict_bullet_with_text_field(self):
        """Dict bullets should extract the text field."""
        bullet = {"text": "Built API handling 1M requests/day", "variants": {"car": "Test"}, "tags": ["test"]}
        result = _normalize_bullet(bullet)
        assert result == "Built API handling 1M requests/day"

    def test_pure_json_bullet(self):
        """Bullets that are pure JSON should extract text if present."""
        bullet = '{"text": "Built API handling 1M requests/day", "variants": {}}'
        result = _normalize_bullet(bullet)
        assert result == "Built API handling 1M requests/day"

    def test_bullet_with_variants_keyword(self):
        """Bullets containing 'variants' in JSON should be cleaned."""
        bullet = 'Led team of 5 engineers {"variants": {"technical": "Test"}, "role_families": ["ai_engineer"]}'
        result = _normalize_bullet(bullet)
        assert result == "Led team of 5 engineers"
        assert "variants" not in result

    def test_bullet_with_tags_keyword(self):
        """Bullets containing 'tags' in JSON should be cleaned."""
        bullet = 'Designed system architecture {"tags": ["python", "aws"], "domains": ["ai"]}'
        result = _normalize_bullet(bullet)
        assert result == "Designed system architecture"
        assert "tags" not in result

    def test_numeric_bullet(self):
        """Numeric bullets should be converted to string."""
        bullet = 12345
        result = _normalize_bullet(bullet)
        assert result == "12345"

    def test_empty_bullet(self):
        """Empty bullets should be handled gracefully."""
        bullet = ""
        result = _normalize_bullet(bullet)
        assert result == ""


class TestAssembleResumeTextWithJsonBullets:
    """Test that assemble_resume_text properly handles JSON in bullets."""

    @pytest.fixture
    def sample_profile(self):
        return {"personal": {"full_name": "Test User", "email": "test@example.com"}}

    def test_experience_with_json_bullets(self, sample_profile):
        """Experience bullets with JSON should be cleaned in final output."""
        data = {
            "title": "Software Engineer",
            "summary": "Test summary",
            "skills": {"Languages": "Python"},
            "experience": [
                {
                    "header": "Engineer | TestCorp | 2020-2023",
                    "subtitle": "Backend | 2020-2023",
                    "bullets": [
                        "Built API handling 1M requests/day",
                        'Led team {"variants": {"car": "Test"}, "tags": ["test"]}',
                        'Designed system {"variants": {}, "skills": ["python"]}',
                    ],
                }
            ],
            "projects": [],
            "education": "BS Computer Science",
        }

        result = assemble_resume_text(data, sample_profile)

        # Check that JSON is not in output
        assert "variants" not in result
        assert '"car"' not in result
        assert '"tags"' not in result

        # Check that bullet text IS in output
        assert "Built API handling 1M requests/day" in result
        assert "Led team" in result
        assert "Designed system" in result

    def test_projects_with_json_bullets(self, sample_profile):
        """Project bullets with JSON should be cleaned in final output."""
        data = {
            "title": "Software Engineer",
            "summary": "Test summary",
            "skills": {"Languages": "Python"},
            "experience": [],
            "projects": [
                {
                    "header": "Project X - AI Platform",
                    "subtitle": "Python, AI | 2023",
                    "bullets": ['Built ML pipeline {"variants": {"technical": "Test"}, "role_families": ["ai"]}'],
                }
            ],
            "education": "BS Computer Science",
        }

        result = assemble_resume_text(data, sample_profile)

        # Check that JSON is not in output
        assert "variants" not in result
        assert "role_families" not in result

        # Check that bullet text IS in output
        assert "Built ML pipeline" in result

    def test_no_regression_with_clean_bullets(self, sample_profile):
        """Clean bullets should still work normally."""
        data = {
            "title": "Software Engineer",
            "summary": "Test summary",
            "skills": {"Languages": "Python"},
            "experience": [
                {
                    "header": "Engineer | TestCorp | 2020-2023",
                    "subtitle": "Backend | 2020-2023",
                    "bullets": [
                        "Built API handling 1M requests/day",
                        "Led team of 5 engineers",
                        "Reduced costs by 40%",
                    ],
                }
            ],
            "projects": [],
            "education": "BS Computer Science",
        }

        result = assemble_resume_text(data, sample_profile)

        # All bullets should appear
        assert "Built API handling 1M requests/day" in result
        assert "Led team of 5 engineers" in result
        assert "Reduced costs by 40%" in result

        # Should have proper formatting
        assert "- Built API" in result
        assert "- Led team" in result
        assert "- Reduced costs" in result
