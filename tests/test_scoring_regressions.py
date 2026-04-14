"""Regression tests for scoring pipeline bugs found during applypilot run.

Bug 1: compute_deterministic_baseline() called with wrong args
  - Orchestrator passed (resume_text, job, scoring_profile) — 3 args
  - Function signature is (scoring_profile, job) — 2 args
  - Fix: removed resume_text arg from call

Bug 2: apply_score_calibration() called with wrong args
  - Orchestrator passed (baseline, parsed, job) — 3 args
  - Function signature needs 7 positional args
  - Fix: unpack parsed dict into individual args

Bug 3: Qwen3/DeepSeek reasoning models wrap JSON in <think>...</think> tags
  - extract_json_object() couldn't find JSON because <think> block contained { }
  - Fix: strip <think>...</think> tags before JSON extraction
"""

import pytest
from applypilot.scoring.llm.calibrator import extract_json_object, ScoreResponseParseError


class TestThinkTagStripping:
    """Qwen3 and DeepSeek R1 output <think>reasoning</think> before JSON."""

    def test_clean_json(self):
        assert extract_json_object('{"score": 7}')["score"] == 7

    def test_think_tags_stripped(self):
        raw = '<think>Let me analyze the job requirements carefully...</think>{"score": 8, "reasoning": "good fit"}'
        assert extract_json_object(raw)["score"] == 8

    def test_think_with_braces_inside(self):
        raw = '<think>The {requirements} section mentions {Python} and {Docker}</think>{"score": 6, "confidence": 0.8}'
        result = extract_json_object(raw)
        assert result["score"] == 6
        assert result["confidence"] == 0.8

    def test_multiline_think(self):
        raw = """<think>
This is a senior role.
The candidate has 3 years experience.
Score should be moderate.
</think>
{"score": 5, "matched_skills": ["python", "docker"], "missing_requirements": ["java"]}"""
        result = extract_json_object(raw)
        assert result["score"] == 5
        assert "python" in result["matched_skills"]

    def test_markdown_fenced_json(self):
        assert extract_json_object('```json\n{"score": 9}\n```')["score"] == 9

    def test_think_plus_markdown(self):
        raw = '<think>analyzing...</think>\n```json\n{"score": 4}\n```'
        assert extract_json_object(raw)["score"] == 4

    def test_empty_think(self):
        raw = '<think></think>{"score": 7}'
        assert extract_json_object(raw)["score"] == 7

    def test_no_json_raises(self):
        with pytest.raises(ScoreResponseParseError):
            extract_json_object("<think>just thinking, no json</think>")

    def test_empty_response_raises(self):
        with pytest.raises(ScoreResponseParseError):
            extract_json_object("")


class TestBaselineScorerSignature:
    """compute_deterministic_baseline takes (scoring_profile, job) — not 3 args."""

    def test_accepts_two_args(self):
        from applypilot.scoring.deterministic.baseline_scorer import compute_deterministic_baseline

        profile = {"skills": [], "target_titles": [], "work": []}
        job = {"title": "Software Engineer", "full_description": "Python developer needed"}
        result = compute_deterministic_baseline(profile, job)
        assert "score" in result
        assert isinstance(result["score"], (int, float))

    def test_rejects_three_args(self):
        from applypilot.scoring.deterministic.baseline_scorer import compute_deterministic_baseline

        with pytest.raises(TypeError):
            compute_deterministic_baseline("resume text", {}, {})


class TestCalibrationSignature:
    """apply_score_calibration takes 7 positional args."""

    def test_accepts_correct_args(self):
        from applypilot.scoring.llm.calibrator import apply_score_calibration

        score, delta = apply_score_calibration(
            {"score": 6, "skill_overlap": 0.5, "title_similarity": 0.7},
            7,
            0.85,
            ["python", "docker"],
            ["java"],
            "We need a Python developer with Docker experience",
        )
        assert 1 <= score <= 10
        assert isinstance(delta, int)
