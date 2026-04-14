"""Tests for llm/call_logger.py."""


class TestLLMCallLogger:
    def test_hash_prompt(self):
        from applypilot.llm.call_logger import hash_prompt

        h1 = hash_prompt([{"role": "user", "content": "hello"}])
        h2 = hash_prompt([{"role": "user", "content": "hello"}])
        h3 = hash_prompt([{"role": "user", "content": "world"}])
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 16

    def test_llm_call_record(self):
        from applypilot.llm.call_logger import LLMCallRecord

        r = LLMCallRecord(provider="gemini", model="flash", tokens_in=100, tokens_out=50)
        assert r.success is True
        assert r.cost_usd == 0.0

    def test_log_call_no_error(self):
        from applypilot.llm.call_logger import LLMCallRecord, log_call

        r = LLMCallRecord(provider="test", model="test", tokens_in=10, tokens_out=5)
        log_call(r)
