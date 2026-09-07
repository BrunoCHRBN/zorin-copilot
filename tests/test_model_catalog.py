"""Catálogo de modelos Gemini: fonte única de verdade e cadeia de fallback.

Estes testes existem porque a lista de modelos já viveu duplicada em
`ai/providers.py`, `ui/preferences.py` e `cli.py`, e as cópias divergiram — o
default do provedor apontava para um modelo e a interface anunciava outro como
recomendado. Se alguém reintroduzir uma lista literal, estes testes quebram.
"""

from __future__ import annotations

import unittest

from zorin_copilot.ai.providers import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_ALIASES,
    GEMINI_FALLBACK_MODELS,
    GEMINI_MODEL_CHOICES,
    GEMINI_PINNED_MODELS,
    GeminiProvider,
    _with_fallbacks,
)
from zorin_copilot.core.config import CopilotConfig


class ModelCatalogTest(unittest.TestCase):
    def test_choices_are_aliases_then_pinned(self):
        """A lista da interface é a concatenação das duas, sem itens soltos."""
        self.assertEqual(GEMINI_MODEL_CHOICES, [*GEMINI_ALIASES, *GEMINI_PINNED_MODELS])

    def test_no_duplicates(self):
        for collection in (GEMINI_ALIASES, GEMINI_PINNED_MODELS, GEMINI_FALLBACK_MODELS, GEMINI_MODEL_CHOICES):
            self.assertEqual(len(collection), len(set(collection)), f"duplicado em {collection}")

    def test_default_is_offered_and_configured(self):
        """O default precisa existir na lista e bater com o default do config."""
        self.assertIn(DEFAULT_GEMINI_MODEL, GEMINI_MODEL_CHOICES)
        self.assertEqual(CopilotConfig().gemini_model, DEFAULT_GEMINI_MODEL)

    def test_retired_models_not_offered(self):
        """Gemini 2.0 foi desligado em 2026 — não pode voltar para a lista."""
        retired = ("gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash", "gemini-1.5-pro")
        for model in retired:
            self.assertNotIn(model, GEMINI_MODEL_CHOICES)
            self.assertNotIn(model, GEMINI_FALLBACK_MODELS)


class FallbackChainTest(unittest.TestCase):
    def test_chosen_model_comes_first(self):
        self.assertEqual(_with_fallbacks("gemini-9.9-ultra")[0], "gemini-9.9-ultra")

    def test_no_duplicates_when_model_is_also_a_fallback(self):
        for model in GEMINI_FALLBACK_MODELS:
            chain = _with_fallbacks(model)
            self.assertEqual(len(chain), len(set(chain)))
            self.assertEqual(chain.count(model), 1)

    def test_ends_with_a_known_good_model(self):
        self.assertIn(_with_fallbacks("modelo-inexistente")[-1], GEMINI_PINNED_MODELS)


class GeminiProviderDefaultsTest(unittest.TestCase):
    def test_uses_shared_default(self):
        self.assertEqual(GeminiProvider(api_key="k").model, DEFAULT_GEMINI_MODEL)

    def test_blank_model_falls_back_to_default(self):
        self.assertEqual(GeminiProvider(api_key="k", model="   ").model, DEFAULT_GEMINI_MODEL)

    def test_explicit_model_is_preserved(self):
        self.assertEqual(GeminiProvider(api_key="k", model=" gemini-2.5-pro ").model, "gemini-2.5-pro")


if __name__ == "__main__":
    unittest.main()
