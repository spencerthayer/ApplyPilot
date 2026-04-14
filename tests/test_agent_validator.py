"""Tests for apply/agent_validator.py."""


class TestAgentValidator:
    def test_clean_log(self, tmp_path):
        from applypilot.apply.agent_validator import validate_agent_log

        log = tmp_path / "agent.txt"
        log.write_text(
            '[iter 1] TOOL browser_navigate({"url":"https://example.com"})\n'
            "[iter 2] TOOL browser_snapshot({})\n"
            '[iter 3] TOOL browser_type({"element":"Name","ref":"e42","text":"John"})\n'
            '[iter 4] TOOL browser_click({"element":"Submit","ref":"e19"})\n'
            "[RESULT] RESULT:APPLIED\n"
        )
        result = validate_agent_log(log)
        assert result["passed"] is True
        assert result["iterations"] == 4
        assert result["issues"] == []

    def test_detect_evaluate_write(self, tmp_path):
        from applypilot.apply.agent_validator import validate_agent_log

        log = tmp_path / "agent.txt"
        log.write_text(
            "[iter 1] TOOL browser_evaluate({\"function\":\"document.querySelector('#name').value = 'John'\"})\n"
        )
        result = validate_agent_log(log)
        assert result["passed"] is False
        assert any("evaluate wrote" in i for i in result["issues"])

    def test_detect_html_id_as_ref(self, tmp_path):
        from applypilot.apply.agent_validator import validate_agent_log

        log = tmp_path / "agent.txt"
        log.write_text('[iter 1] TOOL browser_type({"element":"Name","ref":"first_name","text":"John"})\n')
        result = validate_agent_log(log)
        assert result["passed"] is False
        assert any("HTML ids" in i for i in result["issues"])

    def test_detect_wrong_tabs_param(self, tmp_path):
        from applypilot.apply.agent_validator import validate_agent_log

        log = tmp_path / "agent.txt"
        log.write_text('[iter 1] TOOL browser_tabs({"action":"select","tab_index":1})\n')
        result = validate_agent_log(log)
        assert result["passed"] is False
        assert any("tab_index" in i for i in result["issues"])

    def test_detect_blocked_evaluate(self, tmp_path):
        from applypilot.apply.agent_validator import validate_agent_log

        log = tmp_path / "agent.txt"
        log.write_text("[iter 1] BLOCKED evaluate write\n")
        result = validate_agent_log(log)
        assert result["passed"] is False
        assert any("blocked" in i.lower() for i in result["issues"])

    def test_zero_iterations(self, tmp_path):
        from applypilot.apply.agent_validator import validate_agent_log

        log = tmp_path / "agent.txt"
        log.write_text("RESULT:FAILED:no_iterations\n")
        result = validate_agent_log(log)
        assert result["iterations"] == 0
