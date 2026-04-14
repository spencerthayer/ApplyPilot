"""Tests for resume validation submodule."""

import pytest
from applypilot.resume.validation import (
    ResumeJsonError,
    looks_like_resume_json,
    _format_path,
    _find_forbidden_keys,
)


class TestLooksLikeResumeJson:
    def test_with_basics(self):
        assert looks_like_resume_json({"basics": {"name": "Test"}}) is True

    def test_with_work(self):
        assert looks_like_resume_json({"work": []}) is True

    def test_with_meta(self):
        assert looks_like_resume_json({"meta": {"applypilot": {}}}) is True

    def test_empty_dict(self):
        assert looks_like_resume_json({}) is False

    def test_not_dict(self):
        assert looks_like_resume_json("string") is False
        assert looks_like_resume_json(None) is False


class TestFormatPath:
    def test_empty(self):
        assert _format_path([]) == "$"

    def test_nested(self):
        assert _format_path(["work", 0, "highlights"]) == "$.work[0].highlights"


class TestFindForbiddenKeys:
    def test_finds_secrets(self):
        data = {"api_key": "abc123", "name": "Test"}
        findings = _find_forbidden_keys(data)
        assert len(findings) == 1
        assert "api_key" in findings[0]

    def test_nested_secrets(self):
        data = {"config": {"password": "secret"}}
        findings = _find_forbidden_keys(data)
        assert len(findings) == 1

    def test_clean(self):
        assert _find_forbidden_keys({"name": "Test", "skills": []}) == []

    def test_list_traversal(self):
        data = [{"token": "abc"}]
        findings = _find_forbidden_keys(data)
        assert len(findings) == 1


class TestResumeJsonError:
    def test_is_value_error(self):
        with pytest.raises(ValueError):
            raise ResumeJsonError("bad")
