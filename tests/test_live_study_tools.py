# Decisão de design: valida o tool calling do Gemini Live para automação AVA/SCORM
# e ferramentas de estudo (geração de decks, resumos e exportação Anki)
# sem depender de credenciais de rede ou servidor WebSocket ativo.

"""Testes das ferramentas de estudo e AVA no Gemini Live (FASE 8)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.live import (  # noqa: E402
    GeminiLiveClient,
    LIVE_TOOLS_DECLARATION,
)


class TestLiveStudyTools(unittest.TestCase):
    def setUp(self):
        self.client = GeminiLiveClient()

    def test_study_tools_declaradas_no_live(self):
        declarations = {
            f["name"]: f for f in LIVE_TOOLS_DECLARATION[0]["functionDeclarations"]
        }
        self.assertIn("capture_lesson", declarations)
        self.assertIn("study_generate_deck", declarations)
        self.assertIn("study_summarize", declarations)
        self.assertIn("study_export_anki", declarations)

        cap_params = declarations["capture_lesson"]["parameters"]["properties"]
        self.assertIn("multi_page", cap_params)
        self.assertIn("max_pages", cap_params)

        deck_params = declarations["study_generate_deck"]["parameters"]["properties"]
        self.assertIn("source", deck_params)

    @patch("zorin_copilot.core.study_capture.LessonCapture")
    def test_dispatch_capture_lesson_multipage(self, mock_cap_cls):
        mock_cap = MagicMock()
        mock_cap_cls.return_value = mock_cap
        mock_res = MagicMock(ok=True, title="Aula AVA", word_count=450, warnings=[])
        mock_cap.capture_multipage.return_value = mock_res
        mock_cap.save.return_value = "/tmp/estudos/aula-ava.md"

        res = self.client._dispatch_tool("capture_lesson", {"multi_page": True, "max_pages": 4})

        self.assertTrue(res["success"])
        self.assertEqual(res["title"], "Aula AVA")
        self.assertEqual(res["path"], "/tmp/estudos/aula-ava.md")
        self.assertIn("Aula 'Aula AVA' capturada com sucesso", res["message"])
        mock_cap.capture_multipage.assert_called_once_with(app_name=None, max_pages=4)

    @patch("zorin_copilot.ai.agent_tools.ToolRegistry")
    def test_dispatch_study_generate_deck(self, mock_reg_cls):
        mock_reg = MagicMock()
        mock_reg_cls.return_value = mock_reg
        mock_reg.call.return_value = {
            "ok": True,
            "deck_id": "deck-123",
            "title": "Marketing",
            "cards_count": 8,
            "apkg_path": "/tmp/decks/marketing.apkg",
        }

        res = self.client._dispatch_tool("study_generate_deck", {"source": "/tmp/aula.md"})

        self.assertTrue(res["success"])
        self.assertEqual(res["cards_count"], 8)
        self.assertEqual(res["path"], "/tmp/decks/marketing.apkg")
        self.assertIn("Baralho 'Marketing' gerado com 8 flashcards", res["message"])

    @patch("zorin_copilot.ai.agent_tools.ToolRegistry")
    def test_dispatch_study_summarize(self, mock_reg_cls):
        mock_reg = MagicMock()
        mock_reg_cls.return_value = mock_reg
        mock_reg.call.return_value = {
            "ok": True,
            "title": "Gestão",
            "topics_count": 6,
            "glossary_count": 10,
            "path": "/tmp/resumo.md",
        }

        res = self.client._dispatch_tool("study_summarize", {"source": "/tmp/aula.md"})

        self.assertTrue(res["success"])
        self.assertEqual(res["topics_count"], 6)
        self.assertEqual(res["glossary_count"], 10)
        self.assertIn("Resumo de 'Gestão' criado com 6 tópicos e 10 termos", res["message"])

    @patch("zorin_copilot.ai.agent_tools.ToolRegistry")
    def test_dispatch_study_export_anki(self, mock_reg_cls):
        mock_reg = MagicMock()
        mock_reg_cls.return_value = mock_reg
        mock_reg.call.return_value = {
            "ok": True,
            "output_path": "/tmp/export.apkg",
        }

        res = self.client._dispatch_tool("study_export_anki", {"deck_id": "deck-123"})

        self.assertTrue(res["success"])
        self.assertEqual(res["output_path"], "/tmp/export.apkg")
        self.assertIn("Baralho exportado com sucesso para o Anki", res["message"])


if __name__ == "__main__":
    unittest.main()
