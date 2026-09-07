# Decisão de design: suíte determinística e sem display (GTK-free) para a contagem de
# tokens. Cobre formatação pt-BR, matemática do acumulador, parsers por provedor e o
# caminho real de captura de `usage` nos três provedores (com a rede mockada).

"""Testes de contagem de tokens e telemetria de uso por provedor."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.providers import (  # noqa: E402
    GeminiProvider,
    OllamaProvider,
    OpenAICompatProvider,
)
from zorin_copilot.core.usage import (  # noqa: E402
    TokenUsage,
    TokenUsageTracker,
    format_tokens,
    usage_from_gemini,
    usage_from_ollama,
    usage_from_openai,
)


class FormatTokensTest(unittest.TestCase):
    def test_small(self):
        self.assertEqual(format_tokens(0), "0")
        self.assertEqual(format_tokens(842), "842")
        self.assertEqual(format_tokens(999), "999")

    def test_thousands(self):
        self.assertEqual(format_tokens(1200), "1,2 mil")
        self.assertEqual(format_tokens(2300), "2,3 mil")
        self.assertEqual(format_tokens(12000), "12,0 mil")

    def test_millions(self):
        self.assertEqual(format_tokens(1_000_000), "1,0 mi")
        self.assertEqual(format_tokens(2_300_000), "2,3 mi")


class TokenUsageTest(unittest.TestCase):
    def test_total(self):
        self.assertEqual(TokenUsage(10, 5).total_tokens, 15)

    def test_add_pure(self):
        a = TokenUsage(10, 5)
        b = TokenUsage(20, 7)
        c = a + b
        self.assertEqual(c.prompt_tokens, 30)
        self.assertEqual(c.completion_tokens, 12)
        self.assertEqual(c.total_tokens, 42)
        # Os operandos originais não são mutados.
        self.assertEqual(a.total_tokens, 15)


class TokenUsageTrackerTest(unittest.TestCase):
    def test_record_accumulates(self):
        t = TokenUsageTracker()
        t.record(TokenUsage(100, 20), provider="gemini", model="m1")
        self.assertEqual(t.session.total_tokens, 120)
        self.assertEqual(t.requests, 1)
        self.assertEqual(t.last_model, "m1")
        t.record(TokenUsage(50, 10), provider="gemini", model="m1")
        self.assertEqual(t.session.total_tokens, 180)
        self.assertEqual(t.requests, 2)

    def test_skip_zero(self):
        t = TokenUsageTracker()
        t.record(TokenUsage(0, 0), provider="gemini", model="m1")
        self.assertEqual(t.requests, 0)
        self.assertEqual(t.session.total_tokens, 0)
        self.assertIsNone(t.last)

    def test_reset(self):
        t = TokenUsageTracker()
        t.record(TokenUsage(100, 20), provider="g", model="m")
        t.reset()
        self.assertEqual(t.session.total_tokens, 0)
        self.assertEqual(t.requests, 0)
        self.assertIsNone(t.last)
        self.assertEqual(t.last_model, "")


class UsageParsersTest(unittest.TestCase):
    def test_gemini(self):
        u = usage_from_gemini(
            {"usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3}}
        )
        self.assertEqual(u.prompt_tokens, 12)
        self.assertEqual(u.completion_tokens, 3)

    def test_gemini_missing(self):
        self.assertIsNone(usage_from_gemini({}))
        self.assertIsNone(usage_from_gemini({"usageMetadata": {}}))

    def test_openai(self):
        u = usage_from_openai({"usage": {"prompt_tokens": 7, "completion_tokens": 2}})
        self.assertEqual(u.prompt_tokens, 7)
        self.assertEqual(u.completion_tokens, 2)

    def test_openai_missing(self):
        self.assertIsNone(usage_from_openai({}))

    def test_ollama(self):
        u = usage_from_ollama({"prompt_eval_count": 9, "eval_count": 4})
        self.assertEqual(u.prompt_tokens, 9)
        self.assertEqual(u.completion_tokens, 4)

    def test_ollama_missing(self):
        self.assertIsNone(usage_from_ollama({}))


class ProviderRecordingTest(unittest.TestCase):
    def test_records_into_tracker(self):
        p = GeminiProvider(api_key="k", model="gemini-1.5-flash")
        tracker = TokenUsageTracker()
        p.usage_tracker = tracker
        p._record_usage(
            usage_from_gemini(
                {"usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3}}
            ),
            provider="gemini",
            model="gemini-1.5-flash",
        )
        self.assertEqual(tracker.session.total_tokens, 15)
        self.assertEqual(tracker.requests, 1)

    def test_skips_when_no_tracker(self):
        p = GeminiProvider(api_key="k", model="m")
        # Tracker nulo (chamada avulsa) não deve quebrar.
        p._record_usage(
            usage_from_gemini({"usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}}),
            provider="gemini",
            model="m",
        )

    def test_skips_none_usage(self):
        p = GeminiProvider(api_key="k", model="m")
        tracker = TokenUsageTracker()
        p.usage_tracker = tracker
        p._record_usage(None, provider="gemini", model="m")
        self.assertEqual(tracker.requests, 0)


class GeminiChatUsageTest(unittest.TestCase):
    def _fake_response(self, usage: dict) -> MagicMock:
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": '{"explanation":"ok","actions":[]}'}]}}
            ],
            **usage,
        }
        return fake

    def test_chat_records_usage(self):
        provider = GeminiProvider(api_key="test-key", model="gemini-1.5-flash")
        tracker = TokenUsageTracker()
        provider.usage_tracker = tracker

        with patch(
            "zorin_copilot.ai.providers.requests.post",
            return_value=self._fake_response(
                {"usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30}}
            ),
        ):
            text, _actions = provider.chat("ola")

        self.assertEqual(tracker.session.total_tokens, 150)
        self.assertEqual(tracker.requests, 1)
        self.assertEqual(tracker.last_model, "gemini-1.5-flash")
        self.assertIn("ok", text)


class OllamaChatUsageTest(unittest.TestCase):
    def test_chat_records_usage(self):
        provider = OllamaProvider(host_url="http://localhost:11434", model="llama3.2")
        tracker = TokenUsageTracker()
        provider.usage_tracker = tracker

        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {
            "message": {"content": '{"explanation":"ok","actions":[]}'},
            "prompt_eval_count": 8,
            "eval_count": 2,
        }
        with patch("zorin_copilot.ai.providers.requests.post", return_value=fake):
            text, _actions = provider.chat("ola")

        self.assertEqual(tracker.session.total_tokens, 10)
        self.assertEqual(tracker.requests, 1)
        self.assertIn("ok", text)


class OpenAIChatUsageTest(unittest.TestCase):
    def test_chat_records_usage(self):
        provider = OpenAICompatProvider(
            api_url="https://api.openai.com/v1/chat/completions",
            api_key="sk-test",
            model="gpt-4o-mini",
        )
        tracker = TokenUsageTracker()
        provider.usage_tracker = tracker

        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {
            "choices": [{"message": {"content": '{"explanation":"ok","actions":[]}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        with patch("zorin_copilot.ai.providers.requests.post", return_value=fake):
            text, _actions = provider.chat("ola")

        self.assertEqual(tracker.session.total_tokens, 8)
        self.assertEqual(tracker.requests, 1)
        self.assertIn("ok", text)


if __name__ == "__main__":
    unittest.main()
