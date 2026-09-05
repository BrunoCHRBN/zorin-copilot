# Decisão de design: Testes unitários para o ClipboardService e a integração semântica
# do motor de intenções com o clipboard (código, tradução, formalização de e-mails, imagens).

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.actions import ActionPlan, ActionType
from zorin_copilot.ai.engine import IntentEngine
from zorin_copilot.core.clipboard import ClipboardService
from zorin_copilot.core.config import CopilotConfig


class ClipboardServiceTest(unittest.TestCase):
    """Testes unitários para o ClipboardService."""

    @patch("shutil.which")
    def test_is_available_true(self, mock_which):
        mock_which.side_effect = lambda cmd: "/usr/bin/" + cmd if cmd == "wl-paste" else None
        self.assertTrue(ClipboardService.is_available())

    @patch("shutil.which", return_value=None)
    def test_is_available_false(self, mock_which):
        self.assertFalse(ClipboardService.is_available())

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_get_content_text(self, mock_run, mock_which):
        mock_which.side_effect = lambda cmd: "/usr/bin/" + cmd if cmd == "wl-paste" else None

        # 1a chamada: wl-paste --list-types
        res_types = MagicMock(returncode=0, stdout="text/plain;charset=utf-8\nUTF8_STRING\n")
        # 2a chamada: wl-paste --type text/plain
        res_text = MagicMock(returncode=0, stdout="def hello(): pass")

        mock_run.side_effect = [res_types, res_text]

        kind, data = ClipboardService.get_content()
        self.assertEqual(kind, "text")
        self.assertEqual(data, "def hello(): pass")

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_get_content_empty(self, mock_run, mock_which):
        mock_which.side_effect = lambda cmd: "/usr/bin/" + cmd if cmd == "wl-paste" else None
        res_types = MagicMock(returncode=1, stdout="")
        mock_run.return_value = res_types

        kind, data = ClipboardService.get_content()
        self.assertEqual(kind, "empty")
        self.assertIsNone(data)

    def test_get_preview_text(self):
        with patch.object(ClipboardService, "get_content", return_value=("text", "Linha 1 \n  Linha 2  com bastante texto para teste")):
            preview = ClipboardService.get_preview(max_len=20)
            self.assertTrue(preview.endswith("..."))
            self.assertLessEqual(len(preview), 23)

    def test_get_preview_image(self):
        with patch.object(ClipboardService, "get_content", return_value=("image", b"fakebytes")):
            preview = ClipboardService.get_preview()
            self.assertIn("Imagem", preview)

    def test_get_preview_empty(self):
        with patch.object(ClipboardService, "get_content", return_value=("empty", None)):
            preview = ClipboardService.get_preview()
            self.assertIn("vazia", preview)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_set_text_wl_copy(self, mock_run, mock_which):
        mock_which.side_effect = lambda cmd: "/usr/bin/" + cmd if cmd == "wl-copy" else None
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(ClipboardService.set_text("Texto para teste"))


class ClipboardIntentEngineTest(unittest.TestCase):
    """Testes de interpretação inteligente de comandos de clipboard no IntentEngine."""

    def setUp(self):
        self.config = CopilotConfig(gemini_api_key="test-key", provider="gemini")
        self.engine = IntentEngine(config=self.config)

    @patch.object(ClipboardService, "get_content", return_value=("empty", None))
    def test_empty_clipboard_guidance(self, _mock_clip):
        plan = self.engine.parse("explique o código que acabei de copiar")
        self.assertIn("vazia no momento", plan.thought)
        self.assertIn("Ctrl+C", plan.thought)

    @patch.object(ClipboardService, "get_content", return_value=("text", "def somar(a, b):\n    return a + b"))
    def test_explain_code_clipboard(self, _mock_clip):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.chat.return_value = ("Esta função realiza a soma de dois valores.", [])
        self.engine.llm_provider = mock_provider

        plan = self.engine.parse("explique o código que acabei de copiar")
        self.assertEqual(plan.thought, "Esta função realiza a soma de dois valores.")
        mock_provider.chat.assert_called_once()
        prompt_sent = mock_provider.chat.call_args[0][0]
        self.assertIn("def somar(a, b):", prompt_sent)
        self.assertIn("Finalidade", prompt_sent)

    @patch.object(ClipboardService, "get_content", return_value=("text", "Bom dia a todos"))
    def test_translate_clipboard(self, _mock_clip):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.chat.return_value = ("Good morning everyone", [])
        self.engine.llm_provider = mock_provider

        plan = self.engine.parse("traduza o texto selecionado para o inglês")
        self.assertEqual(plan.thought, "Good morning everyone")
        prompt_sent = mock_provider.chat.call_args[0][0]
        self.assertIn("Bom dia a todos", prompt_sent)
        self.assertIn("inglês", prompt_sent.lower())

    @patch.object(ClipboardService, "get_content", return_value=("text", "oi chefe segue o relatorio abs"))
    def test_formalize_email_clipboard(self, _mock_clip):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.chat.return_value = ("Prezado,\n\nSegue em anexo o relatório solicitado.", [])
        self.engine.llm_provider = mock_provider

        plan = self.engine.parse("corrija a gramática e formalize este e-mail")
        self.assertIn("Prezado", plan.thought)
        prompt_sent = mock_provider.chat.call_args[0][0]
        self.assertIn("oi chefe segue o relatorio abs", prompt_sent)
        self.assertIn("Versão Formal", prompt_sent)

    @patch.object(ClipboardService, "get_content", return_value=("text", "Item 1. Item 2. Item 3."))
    def test_summarize_clipboard(self, _mock_clip):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.chat.return_value = ("• Resumo dos 3 itens.", [])
        self.engine.llm_provider = mock_provider

        plan = self.engine.parse("resuma o que acabei de copiar")
        self.assertEqual(plan.thought, "• Resumo dos 3 itens.")
        prompt_sent = mock_provider.chat.call_args[0][0]
        self.assertIn("resumo conciso", prompt_sent)

    @patch.object(ClipboardService, "get_content", return_value=("image", b"fake-png-bytes"))
    def test_image_clipboard_multimodal(self, _mock_clip):
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.chat.return_value = ("Análise da imagem copiada: trata-se de um gráfico.", [])
        self.engine.llm_provider = mock_provider

        plan = self.engine.parse("analisar copiado")
        self.assertIn("gráfico", plan.thought)
        mock_provider.chat.assert_called_once()
        self.assertEqual(mock_provider.chat.call_args[1]["image_bytes"], b"fake-png-bytes")

    @patch.object(ClipboardService, "get_content", return_value=("text", "print('teste')"))
    def test_unconfigured_ai_shows_preview(self, _mock_clip):
        unconfigured_engine = IntentEngine(config=CopilotConfig(provider="gemini", gemini_api_key=""))
        plan = unconfigured_engine.parse("analisar copiado")
        self.assertIn("Conteúdo detectado na área de transferência", plan.thought)
        self.assertIn("print('teste')", plan.thought)
        self.assertIn("⚙️", plan.thought)


if __name__ == "__main__":
    unittest.main()
