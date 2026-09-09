"""Testes dos subcomandos da CLI do Zorin Copilot."""

from __future__ import annotations

import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.cli import main


class CLISubcommandsTest(unittest.TestCase):
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_cli_help(self, mock_stdout):
        with self.assertRaises(SystemExit) as cm:
            main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        out = mock_stdout.getvalue()
        self.assertIn("rag", out)
        self.assertIn("setup", out)
        self.assertIn("web", out)

    @patch("zorin_copilot.core.shortcuts.ShortcutManager.is_registered", return_value=True)
    @patch("zorin_copilot.core.shortcuts.ShortcutManager.is_crop_registered", return_value=True)
    @patch("zorin_copilot.core.shortcuts.AutostartManager.is_enabled", return_value=True)
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_setup_status(self, mock_stdout, *mocks):
        code = main(["setup", "--status"])
        self.assertEqual(code, 0)
        out = mock_stdout.getvalue()
        self.assertIn("Super+C", out)
        self.assertIn("Super+Shift+S", out)
        self.assertIn("Autostart", out)

    # Os três slots precisam ser mockados: sem o de voz, o setup cai no backend
    # real, não encontra um e responde "com avisos" em vez de "com sucesso".
    @patch("zorin_copilot.core.shortcuts.ShortcutManager.register", return_value=True)
    @patch("zorin_copilot.core.shortcuts.ShortcutManager.register_crop", return_value=True)
    @patch("zorin_copilot.core.shortcuts.ShortcutManager.register_voice", return_value=True)
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_setup_shortcut(self, mock_stdout, mock_voice, mock_crop, mock_reg):
        code = main(["setup", "--shortcut"])
        self.assertEqual(code, 0)
        out = mock_stdout.getvalue()
        # A mensagem cita o backend real; "GNOME" cravado seria mentira em
        # Hyprland/Sway/KDE — e era exatamente o que a saída antiga dizia.
        self.assertIn("Atalhos registrados", out)

    @patch("zorin_copilot.core.shortcuts.AutostartManager.enable", return_value=True)
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_setup_autostart(self, mock_stdout, mock_enable):
        code = main(["setup", "--autostart"])
        self.assertEqual(code, 0)
        out = mock_stdout.getvalue()
        self.assertIn("ativada", out)

    @patch("zorin_copilot.core.rag.LocalDocumentRAG.get_stats")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_rag_stats(self, mock_stdout, mock_stats):
        mock_stats.return_value = {
            "total_documents": 5,
            "total_chunks": 12,
            "by_type": {".pdf": 3, ".xlsx": 2},
            "watched_directories": ["/home/user/Documentos"],
        }
        code = main(["rag", "stats"])
        self.assertEqual(code, 0)
        out = mock_stdout.getvalue()
        self.assertIn("Total de documentos: 5", out)
        self.assertIn(".pdf: 3", out)

    @patch("zorin_copilot.core.rag.LocalDocumentRAG.search")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_rag_search(self, mock_stdout, mock_search):
        mock_search.return_value = [
            {
                "file_name": "contrato_locacao.docx",
                "file_path": "/home/user/contrato_locacao.docx",
                "page_number": 1,
                "chunk_text": "O prazo de locação é de 30 meses a partir do aceite.",
            }
        ]
        code = main(["rag", "search", "prazo locação"])
        self.assertEqual(code, 0)
        out = mock_stdout.getvalue()
        self.assertIn("contrato_locacao.docx", out)
        self.assertIn("prazo de locação", out)

    @patch("zorin_copilot.core.browser.WebPageReader.fetch_and_clean")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_web_read_url(self, mock_stdout, mock_fetch):
        mock_fetch.return_value = {
            "success": True,
            "url": "https://zorin.com",
            "title": "Zorin OS - Fast, Secure, Easy",
            "length": 150,
            "text": "Zorin OS is designed to be easy, so you don't need to learn anything to get started.",
        }
        code = main(["web", "read", "--url", "https://zorin.com"])
        self.assertEqual(code, 0)
        out = mock_stdout.getvalue()
        self.assertIn("Zorin OS - Fast, Secure, Easy", out)
        self.assertIn("Caracteres: 150", out)

    @patch("zorin_copilot.core.web_search.DeepWebResearcher.deep_search")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_web_deep_search(self, mock_stdout, mock_deep):
        mock_deep.return_value = {
            "success": True,
            "report": "### Síntese da Pesquisa\nInformações detalhadas sobre Wayland no Zorin OS.",
            "sources": [{"title": "Wayland Guide", "url": "https://zorin.com/wayland"}],
        }
        code = main(["web", "deep-search", "wayland zorin", "--sources", "2"])
        self.assertEqual(code, 0)
        out = mock_stdout.getvalue()
        self.assertIn("Síntese da Pesquisa", out)
        self.assertIn("Wayland Guide", out)

    @patch("zorin_copilot.core.trust.DocumentTrustManager.evaluate_file")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_rag_trust_check_cli(self, mock_stdout, mock_eval):
        from zorin_copilot.core.trust import TrustLevel

        mock_eval.return_value = (TrustLevel.TRUSTED, "Diretório seguro")
        code = main(["rag", "trust-check", "~/Documentos/contrato.docx"])
        self.assertEqual(code, 0)
        out = mock_stdout.getvalue()
        self.assertIn("Confiável", out)
        self.assertIn("Elegível para Leitura RAG: Sim", out)

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_rag_pii_test_cli(self, mock_stdout):
        code = main(["rag", "pii-test", "CPF 123.456.789-00 do titular"])
        self.assertEqual(code, 0)
        out = mock_stdout.getvalue()
        self.assertIn("CPF [CPF_PROTEGIDO] do titular", out)


if __name__ == "__main__":
    unittest.main()
