"""Tests for the LLM client cascade and exhaustion state machine.

Covers:
- Exhausted models are skipped within the cooldown window
- Exhaustion clears after the cooldown period
- All models exhausted: client clears state and retries
- Failed _try_entry marks model exhausted and falls through to next
- _build_fallback_chain returns quality vs fast model sets correctly
- _build_fallback_chain raises RuntimeError when no keys are configured
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_client(n_models: int = 2):
    """Instantiate an LLMClient with fake models injected directly into the chain."""
    from applypilot.llm import LLMClient, ModelEntry

    # Patch _build_fallback_chain to return fake models so no real API keys needed
    fake_chain = [
        ModelEntry(f"fake-model-{i}", "openai_compat", "https://fake.api/v1", "fake-key")
        for i in range(n_models)
    ]
    with patch("applypilot.llm._build_fallback_chain", return_value=fake_chain):
        client = LLMClient(
            base_url="https://fake.api/v1",
            model="fake-model-0",
            api_key="fake-key",
        )
    return client


class TestModelExhaustion(unittest.TestCase):
    """Exhausted models are skipped; exhaustion clears after cooldown."""

    def test_exhausted_model_skipped(self):
        """A model marked exhausted is not tried again within the cooldown window."""
        client = _make_client(2)
        first_name = client._fallback_chain[0].name
        second_name = client._fallback_chain[1].name

        client._exhausted[first_name] = time.time()

        tried = []

        def fake_try(entry, messages, temperature, max_tokens, is_last):
            tried.append(entry.name)
            return "ok"

        with patch.object(client, "_try_entry", side_effect=fake_try):
            client.chat([{"role": "user", "content": "hello"}])

        self.assertNotIn(first_name, tried, "Exhausted model should not have been tried")
        self.assertIn(second_name, tried, "Non-exhausted model should have been tried")

    def test_exhausted_model_retried_after_cooldown(self):
        """A model marked exhausted 6+ minutes ago is eligible again (cooldown = 5 min)."""
        client = _make_client(2)
        first_name = client._fallback_chain[0].name

        # Mark exhausted 6 minutes ago
        client._exhausted[first_name] = time.time() - 361

        tried = []

        def fake_try(entry, messages, temperature, max_tokens, is_last):
            tried.append(entry.name)
            return "ok"

        with patch.object(client, "_try_entry", side_effect=fake_try):
            client.chat([{"role": "user", "content": "hello"}])

        self.assertIn(first_name, tried, "Model should be retried after cooldown expires")

    def test_exhaustion_clears_when_all_exhausted(self):
        """When every model is exhausted, the client clears state and retries all."""
        client = _make_client(2)
        now = time.time()
        for m in client._fallback_chain:
            client._exhausted[m.name] = now

        call_count = {"n": 0}

        def fake_try(entry, messages, temperature, max_tokens, is_last):
            call_count["n"] += 1
            return "ok"

        with patch.object(client, "_try_entry", side_effect=fake_try):
            result = client.chat([{"role": "user", "content": "hello"}])

        self.assertGreater(call_count["n"], 0, "At least one model should have been tried")
        self.assertEqual(result, "ok")

    def test_failed_try_falls_through_to_next(self):
        """Returning None from _try_entry causes the cascade to try the next model."""
        client = _make_client(2)
        first_name = client._fallback_chain[0].name
        second_name = client._fallback_chain[1].name
        call_order = []

        def fake_try(entry, messages, temperature, max_tokens, is_last):
            call_order.append(entry.name)
            if entry.name == first_name:
                return None  # simulate failure
            return "success from fallback"

        with patch.object(client, "_try_entry", side_effect=fake_try):
            result = client.chat([{"role": "user", "content": "test"}])

        self.assertIn(first_name, call_order)
        self.assertIn(second_name, call_order)
        self.assertEqual(result, "success from fallback")


class TestBuildFallbackChain(unittest.TestCase):
    """_build_fallback_chain returns different model sets for quality vs fast."""

    def _build(self, quality: bool) -> list[str]:
        with patch.dict("os.environ", {
            "GEMINI_API_KEY": "fake-gemini",
            "OPENAI_API_KEY": "fake-openai",
        }):
            from applypilot.llm import _build_fallback_chain
            primary = "gemini-2.5-pro" if quality else "gemini-2.5-flash"
            return [m.name for m in _build_fallback_chain(primary, quality=quality)]

    def test_fast_chain_includes_flash_or_mini(self):
        names = self._build(quality=False)
        self.assertTrue(
            any("flash" in n or "nano" in n or "mini" in n for n in names),
            f"Fast chain should include a flash/nano/mini model. Got: {names}",
        )

    def test_quality_chain_includes_pro_or_sonnet(self):
        names = self._build(quality=True)
        self.assertTrue(
            any("pro" in n or "sonnet" in n for n in names),
            f"Quality chain should include a pro/sonnet model. Got: {names}",
        )

    def test_fast_and_quality_chains_differ(self):
        fast = self._build(quality=False)
        quality = self._build(quality=True)
        self.assertNotEqual(fast, quality, "Fast and quality chains should differ")

    def test_chain_nonempty_with_keys(self):
        names = self._build(quality=False)
        self.assertGreater(len(names), 0, "Should have at least one model when keys are set")

    def test_raises_without_api_keys(self):
        """RuntimeError is raised when no API keys are configured."""
        env_without_keys = {
            k: v for k, v in __import__("os").environ.items()
            if k not in ("GEMINI_API_KEY", "OPENAI_API_KEY",
                         "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "LLM_URL")
        }
        with patch.dict("os.environ", env_without_keys, clear=True):
            from applypilot.llm import _build_fallback_chain
            with self.assertRaises(RuntimeError):
                _build_fallback_chain("gemini-2.5-flash", quality=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
