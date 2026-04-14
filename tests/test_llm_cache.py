"""Tests for llm/cache.py."""


class TestLLMCache:
    def test_hash_key_deterministic(self):
        from applypilot.llm.cache import _hash_key

        msgs = [{"role": "user", "content": "test"}]
        h1 = _hash_key(msgs, "model-a", 0.7)
        h2 = _hash_key(msgs, "model-a", 0.7)
        assert h1 == h2

    def test_hash_key_varies_by_model(self):
        from applypilot.llm.cache import _hash_key

        msgs = [{"role": "user", "content": "test"}]
        h1 = _hash_key(msgs, "model-a", 0.7)
        h2 = _hash_key(msgs, "model-b", 0.7)
        assert h1 != h2

    def test_hash_key_varies_by_temperature(self):
        from applypilot.llm.cache import _hash_key

        msgs = [{"role": "user", "content": "test"}]
        h1 = _hash_key(msgs, "model-a", 0.0)
        h2 = _hash_key(msgs, "model-a", 1.0)
        assert h1 != h2
