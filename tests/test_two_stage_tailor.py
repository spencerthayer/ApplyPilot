"""Tests for two-stage tailoring pipeline."""

import json
import pytest


class TestTwoStagePipeline:
    def test_planner_prompt_has_required_fields(self):
        from applypilot.scoring.tailor.two_stage_prompts import PLANNER_PROMPT

        prompt = PLANNER_PROMPT.format(
            yoe="4",
            current_role="SDE",
            current_company="Amazon",
            resume_text="test resume",
            jd_title="Backend Engineer",
            jd_company="Apple",
            jd_text="test JD",
        )
        assert "requirements" in prompt
        assert "gap" in prompt
        assert "bullets_to_keep" in prompt

    def test_generator_prompt_has_required_fields(self):
        from applypilot.scoring.tailor.two_stage_prompts import GENERATOR_PROMPT

        prompt = GENERATOR_PROMPT.format(
            plan_json="{}",
            resume_text="test",
            name="Test",
            email="t@t.com",
            phone="123",
            location="India",
            profiles="",
            banned_words="worked on",
        )
        assert "STAR" in prompt
        assert "CROSS-ROLE" in prompt
        assert "reverse chronological" in prompt.lower() or "Current role FIRST" in prompt

    def test_fallback_on_planner_failure(self, monkeypatch):
        from applypilot.scoring.tailor import orchestrator

        # Mock two-stage to fail
        monkeypatch.setattr(
            "applypilot.scoring.tailor.two_stage_pipeline.run_two_stage_tailor",
            lambda *a, **kw: (None, {"status": "planner_failed"}),
        )
        # Mock single-stage fallback
        monkeypatch.setattr(
            "applypilot.scoring.tailor.orchestrator.tailor_resume",
            lambda *a, **kw: ("fallback resume", {"status": "approved"}),
        )

        tailored, report = orchestrator._two_stage_with_fallback("resume", {}, {}, "normal")
        assert tailored == "fallback resume"
        assert report["pipeline"] == "single_stage_fallback"

    def test_two_stage_returns_assembled_text(self, monkeypatch):
        from applypilot.scoring.tailor import orchestrator

        fake_json = json.dumps(
            {
                "title": "SDE",
                "summary": "Test summary.",
                "skills": {"Languages": "Python"},
                "experience": [
                    {"header": "SDE | Co | 2024 - Present", "bullets": [{"text": "Built stuff", "skills": ["Python"]}]}
                ],
                "education": "MIT | BS | CS | 2022",
            }
        )
        monkeypatch.setattr(
            "applypilot.scoring.tailor.two_stage_pipeline.run_two_stage_tailor",
            lambda *a, **kw: (
                fake_json,
                {"status": "approved", "pipeline": "two_stage", "plan_requirements": 5, "plan_gaps": 0},
            ),
        )

        tailored, report = orchestrator._two_stage_with_fallback("resume", {}, {}, "normal")
        assert report["status"] == "approved"
        assert report["pipeline"] == "two_stage"
        assert "Built stuff" in tailored
