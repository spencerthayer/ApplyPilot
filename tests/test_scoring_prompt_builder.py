"""Tests for scoring/llm/prompt_builder.py."""


class TestScoringPromptBuilder:
    def test_score_prompt_has_schema(self):
        from applypilot.scoring.llm.prompt_builder import SCORE_PROMPT

        assert "score" in SCORE_PROMPT
        assert "confidence" in SCORE_PROMPT
        assert "matched_skills" in SCORE_PROMPT
        assert "JSON" in SCORE_PROMPT

    def test_format_scoring_profile(self):
        from applypilot.scoring.llm.prompt_builder import format_scoring_profile_for_prompt

        profile = {
            "target_role": "Backend Engineer",
            "years_total": 4,
            "current_titles": ["SDE-1"],
            "known_skills": ["Python", "Java", "Kotlin"],
        }
        text = format_scoring_profile_for_prompt(profile)
        assert "Backend Engineer" in text
        assert "4" in text
        assert "Python" in text

    def test_format_empty_profile(self):
        from applypilot.scoring.llm.prompt_builder import format_scoring_profile_for_prompt

        text = format_scoring_profile_for_prompt({})
        assert "N/A" in text

    def test_response_format(self):
        from applypilot.scoring.llm.prompt_builder import SCORING_RESPONSE_FORMAT

        assert SCORING_RESPONSE_FORMAT == {"type": "json_object"}
