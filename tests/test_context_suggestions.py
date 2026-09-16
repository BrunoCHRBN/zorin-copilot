# Decisão de design: Testes unitários e de integração para a Fase 3 (Consciência de Contexto
# e Sugestões Dinâmicas Proativas).

"""Testes para o motor de contexto do desktop e sugestões adaptativas."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.desktop_context import (
    AppCategory,
    ClipboardCategory,
    DesktopContext,
    DesktopContextDetector,
)
from zorin_copilot.core.context_suggestions import (
    ContextSuggestion,
    ContextSuggestionEngine,
)


class DesktopContextDetectorTest(unittest.TestCase):
    def setUp(self):
        self.mock_inspector = MagicMock()
        self.detector = DesktopContextDetector(inspector=self.mock_inspector)

    def test_classify_code_editor_vscode(self):
        self.mock_inspector.get_active_window_info.return_value = (
            "Code",
            "dictation.py - zorin-copilot - Visual Studio Code",
            (0, 0, 1920, 1080),
        )
        with patch("zorin_copilot.core.desktop_context.ClipboardService.get_content", return_value=("empty", None)):
            ctx = self.detector.detect_context()

        self.assertTrue(ctx.has_active_window)
        self.assertEqual(ctx.category, AppCategory.CODE_EDITOR)
        self.assertEqual(ctx.extracted_target, "dictation.py")
        self.assertEqual(ctx.app_name, "Code")

    def test_classify_code_editor_neovim(self):
        self.mock_inspector.get_active_window_info.return_value = (
            "nvim",
            "main.rs (src) - NVIM",
            (100, 100, 800, 600),
        )
        with patch("zorin_copilot.core.desktop_context.ClipboardService.get_content", return_value=("empty", None)):
            ctx = self.detector.detect_context()

        self.assertEqual(ctx.category, AppCategory.CODE_EDITOR)
        self.assertEqual(ctx.extracted_target, "main.rs")

    def test_classify_browser_chrome(self):
        self.mock_inspector.get_active_window_info.return_value = (
            "Google-chrome",
            "Documentação Oficial do Python 3.14 - Google Chrome",
            (0, 0, 1280, 800),
        )
        with patch("zorin_copilot.core.desktop_context.ClipboardService.get_content", return_value=("empty", None)):
            ctx = self.detector.detect_context()

        self.assertEqual(ctx.category, AppCategory.BROWSER)
        self.assertEqual(ctx.extracted_target, "Documentação Oficial do Python 3.14")

    def test_classify_terminal(self):
        self.mock_inspector.get_active_window_info.return_value = (
            "ptyxis",
            "bruno@endeavouros: ~/zorin-copilot",
            (200, 200, 900, 600),
        )
        with patch("zorin_copilot.core.desktop_context.ClipboardService.get_content", return_value=("empty", None)):
            ctx = self.detector.detect_context()

        self.assertEqual(ctx.category, AppCategory.TERMINAL)

    def test_classify_document_writer(self):
        self.mock_inspector.get_active_window_info.return_value = (
            "soffice.bin",
            "Relatorio_Executivo_2026.odt - LibreOffice Writer",
            (50, 50, 1000, 800),
        )
        with patch("zorin_copilot.core.desktop_context.ClipboardService.get_content", return_value=("empty", None)):
            ctx = self.detector.detect_context()

        self.assertEqual(ctx.category, AppCategory.DOCUMENT)
        self.assertEqual(ctx.extracted_target, "Relatorio_Executivo_2026.odt")

    def test_classify_file_manager_nautilus(self):
        self.mock_inspector.get_active_window_info.return_value = (
            "org.gnome.Nautilus",
            "Downloads - Arquivos",
            (100, 100, 800, 600),
        )
        with patch("zorin_copilot.core.desktop_context.ClipboardService.get_content", return_value=("empty", None)):
            ctx = self.detector.detect_context()

        self.assertEqual(ctx.category, AppCategory.FILE_MANAGER)
        self.assertEqual(ctx.extracted_target, "Downloads")

    def test_clipboard_error_traceback(self):
        self.mock_inspector.get_active_window_info.return_value = ("", "", None)
        error_text = (
            "Traceback (most recent call last):\n"
            "  File 'test.py', line 10, in <module>\n"
            "ValueError: not enough values to unpack (expected 2, got 0)"
        )
        with patch("zorin_copilot.core.desktop_context.ClipboardService.get_content", return_value=("text", error_text)):
            ctx = self.detector.detect_context()

        self.assertEqual(ctx.clipboard_category, ClipboardCategory.ERROR_TRACEBACK)
        self.assertIn("ValueError", ctx.clipboard_text)

    def test_clipboard_url(self):
        self.mock_inspector.get_active_window_info.return_value = ("", "", None)
        with patch("zorin_copilot.core.desktop_context.ClipboardService.get_content", return_value=("text", "https://github.com/zorin/zorin-desktop")):
            ctx = self.detector.detect_context()

        self.assertEqual(ctx.clipboard_category, ClipboardCategory.URL)
        self.assertEqual(ctx.clipboard_text, "https://github.com/zorin/zorin-desktop")

    def test_ignores_copilot_window_and_uses_cache(self):
        # 1. Primeira amostragem: janela externa
        self.mock_inspector.get_active_window_info.return_value = (
            "Code",
            "dictation.py - zorin-copilot",
            (0, 0, 1000, 700),
        )
        with patch("zorin_copilot.core.desktop_context.ClipboardService.get_content", return_value=("empty", None)):
            ctx1 = self.detector.detect_context()
        self.assertEqual(ctx1.app_name, "Code")

        # 2. Segunda amostragem: Zorin Copilot assumiu o foco
        self.mock_inspector.get_active_window_info.return_value = (
            "zorin-copilot",
            "Zorin Copilot",
            (100, 100, 880, 620),
        )
        with patch("zorin_copilot.core.desktop_context.ClipboardService.get_content", return_value=("empty", None)):
            ctx2 = self.detector.detect_context()

        # O detector recupera a última aplicação de trabalho externa
        self.assertEqual(ctx2.app_name, "Code")
        self.assertEqual(ctx2.extracted_target, "dictation.py")


class ContextSuggestionEngineTest(unittest.TestCase):
    def test_error_traceback_takes_highest_priority(self):
        ctx = DesktopContext(
            app_name="ptyxis",
            window_title="terminal",
            category=AppCategory.TERMINAL,
            clipboard_category=ClipboardCategory.ERROR_TRACEBACK,
            clipboard_text="TypeError: unsupported operand type(s)",
            has_active_window=True,
        )
        suggestions = ContextSuggestionEngine.get_suggestions(ctx, limit=4)
        self.assertGreaterEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].id, "diagnose_error")
        self.assertIn("TypeError", suggestions[0].prompt_template)

    def test_code_editor_suggestions(self):
        ctx = DesktopContext(
            app_name="Code",
            window_title="dictation.py - zorin-copilot",
            category=AppCategory.CODE_EDITOR,
            extracted_target="dictation.py",
            has_active_window=True,
        )
        suggestions = ContextSuggestionEngine.get_suggestions(ctx, limit=4)
        self.assertEqual(len(suggestions), 4)

        ids = [s.id for s in suggestions]
        self.assertIn("explain_code_file", ids)
        self.assertIn("generate_unit_tests", ids)
        self.assertIn("refactor_code", ids)

        explain_sug = next(s for s in suggestions if s.id == "explain_code_file")
        self.assertIn("dictation.py", explain_sug.title)
        self.assertIn("dictation.py", explain_sug.prompt_template)

    def test_browser_suggestions(self):
        ctx = DesktopContext(
            app_name="Firefox",
            window_title="Guia de Boas Práticas Python",
            category=AppCategory.BROWSER,
            extracted_target="Guia de Boas Práticas Python",
            has_active_window=True,
        )
        suggestions = ContextSuggestionEngine.get_suggestions(ctx, limit=4)
        ids = [s.id for s in suggestions]
        self.assertIn("summarize_page", ids)
        self.assertIn("research_topic", ids)

    def test_fallback_when_context_is_empty(self):
        suggestions = ContextSuggestionEngine.get_suggestions(None, limit=4)
        self.assertEqual(len(suggestions), 4)
        ids = [s.id for s in suggestions]
        self.assertIn("global_live_voice", ids)
        self.assertIn("global_crop_screen", ids)
        self.assertIn("global_analyze_clipboard", ids)
        self.assertIn("global_toggle_dark", ids)


class ChatStreamContextIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from zorin_copilot.ui.gi_versions import require_gtk4
        require_gtk4()
        from gi.repository import Gtk
        Gtk.init()

    def test_update_context_modifies_banner_and_grid(self):
        from zorin_copilot.ui.widgets.chat_stream import ChatStreamView

        mock_ctx = MagicMock()
        mock_ctx.config.context_awareness_enabled = True
        mock_ctx.config.context_suggestions_limit = 4

        widget = ChatStreamView(mock_ctx)
        self.assertFalse(widget.context_banner_box.get_visible())

        # Dispara contexto de código
        desktop_ctx = DesktopContext(
            app_name="Code",
            window_title="engine.py - project",
            category=AppCategory.CODE_EDITOR,
            extracted_target="engine.py",
            has_active_window=True,
        )
        widget.update_context(desktop_ctx)

        self.assertTrue(widget.context_banner_box.get_visible())
        self.assertIn("engine.py", widget.context_banner_label.get_text())
        self.assertEqual(len(widget.current_suggestions), 4)
        self.assertEqual(widget.current_suggestions[0].id, "explain_code_file")

    def test_palette_context_command(self):
        from zorin_copilot.ui.app import CopilotWindow

        mock_app = MagicMock()
        with patch.object(CopilotWindow, "__init__", return_value=None):
            win = CopilotWindow.__new__(CopilotWindow)
            win.context_detector = MagicMock()
            win.chat_stream = MagicMock()
            win.show_toast = MagicMock()
            win.prompt_bar = MagicMock()
            win.context_detector.detect_context.return_value = DesktopContext(
                app_name="Firefox",
                has_active_window=True,
            )

            win._refresh_context_command()
            win.chat_stream.update_context.assert_called_once()
            win.prompt_bar.set_active_context.assert_called_once()
            win.show_toast.assert_called_with("Contexto: Firefox")

    def test_prompt_bar_context_pill(self):
        from gi.repository import Gtk
        from zorin_copilot.ui.widgets.prompt_bar import PromptBar

        mock_win = MagicMock()
        mock_win.attachment_bar.box = Gtk.Box()
        mock_win.vision.preview_box = Gtk.Box()
        mock_win.entry = Gtk.Entry()

        pbar = PromptBar(mock_win)
        self.assertFalse(pbar.context_pill_revealer.get_reveal_child())

        # Ativa contexto com Git e arquivo
        ctx = DesktopContext(
            app_name="Code",
            extracted_target="main.py",
            git_repo="zorin-copilot",
            git_branch="feature",
            git_has_diff=True,
            has_active_window=True,
        )
        pbar.set_active_context(ctx)
        self.assertTrue(pbar.context_pill_revealer.get_reveal_child())
        self.assertIn("zorin-copilot", pbar.context_pill_label.get_text())
        self.assertIn("main.py", pbar.context_pill_label.get_text())
        self.assertEqual(pbar.get_active_context(), ctx)

        # Descarta o contexto através do botão
        pbar.clear_active_context()
        self.assertFalse(pbar.context_pill_revealer.get_reveal_child())
        self.assertFalse(pbar.get_active_context().has_active_window)

    def test_trigger_suggestion_file_auto_grounding(self):
        from zorin_copilot.ui.app import CopilotWindow
        from zorin_copilot.core.attachments import Attachment, AttachmentKind

        with patch.object(CopilotWindow, "__init__", return_value=None):
            win = CopilotWindow.__new__(CopilotWindow)
            win.entry = MagicMock()
            win.prompt_bar = MagicMock()
            win.attachment_bar = MagicMock()
            win.attachments = []
            win.current_context = DesktopContext(
                app_name="Code",
                extracted_target="pyproject.toml",
                git_repo=os.path.abspath("."),
                has_active_window=True,
            )

            dummy_att = Attachment(
                path=os.path.abspath("pyproject.toml"),
                name="pyproject.toml",
                kind=AttachmentKind.TEXT,
                text="[project]",
            )
            with patch("zorin_copilot.ui.app.load_attachment", return_value=dummy_att):
                sug = ContextSuggestion(
                    id="explain_code_file",
                    icon="system-search-symbolic",
                    title="Explicar pyproject.toml",
                    description="",
                    prompt_template="Explique pyproject.toml",
                )
                win._trigger_suggestion(sug)

            self.assertEqual(len(win.attachments), 1)
            self.assertEqual(win.attachments[0].name, "pyproject.toml")
            win.entry.set_text.assert_called_with("Explique pyproject.toml")
            win.prompt_bar.submit.assert_called_once()


class SituationalContextEngineTest(unittest.TestCase):
    def test_situational_context_injection(self):
        from zorin_copilot.ai.engine import IntentEngine
        from zorin_copilot.core.config import CopilotConfig

        mock_inspector = MagicMock()
        config = CopilotConfig()
        config.context_awareness_enabled = True

        engine = IntentEngine(mock_inspector, config)

        test_ctx = DesktopContext(
            app_name="Code",
            extracted_target="engine.py",
            git_repo="zorin-copilot",
            git_branch="refactor/fase-3",
            git_has_diff=True,
            clipboard_category=ClipboardCategory.ERROR_TRACEBACK,
            clipboard_text="ZeroDivisionError: division by zero",
            has_active_window=True,
        )

        situational = engine._get_situational_context(desktop_context=test_ctx)
        self.assertIn("Janela em foco: Code (Arquivo/Alvo: 'engine.py')", situational)
        self.assertIn("Repositório Git: zorin-copilot (branch: 'refactor/fase-3', com alterações não salvas)", situational)
        self.assertIn("ZeroDivisionError: division by zero", situational)


class GitContextDetectionTest(unittest.TestCase):
    def test_git_repo_detection(self):
        mock_inspector = MagicMock()
        detector = DesktopContextDetector(mock_inspector)

        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = "zorin-copilot\n"

        mock_branch = MagicMock()
        mock_branch.returncode = 0
        mock_branch.stdout = "main\n"

        mock_diff = MagicMock()
        mock_diff.returncode = 0
        mock_diff.stdout = " M src/main.py\n"

        def subprocess_side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "rev-parse --show-toplevel" in cmd_str:
                return mock_completed
            if "branch --show-current" in cmd_str:
                return mock_branch
            if "status --porcelain" in cmd_str:
                return mock_diff
            return MagicMock(returncode=1, stdout="")

        with patch("subprocess.run", side_effect=subprocess_side_effect):
            repo, branch, has_diff = detector._detect_git_info()

        self.assertEqual(repo, "zorin-copilot")
        self.assertEqual(branch, "main")
        self.assertTrue(has_diff)

    def test_git_diff_commit_suggestion(self):
        ctx = DesktopContext(
            app_name="ptyxis",
            category=AppCategory.TERMINAL,
            git_repo="zorin-copilot",
            git_branch="main",
            git_has_diff=True,
            has_active_window=True,
        )
        suggestions = ContextSuggestionEngine.get_suggestions(ctx, limit=4)
        ids = [s.id for s in suggestions]
        self.assertIn("git_diff_commit", ids)
        git_sug = next(s for s in suggestions if s.id == "git_diff_commit")
        self.assertEqual(git_sug.priority, 12)
        self.assertIn("Conventional Commits", git_sug.prompt_template)


if __name__ == "__main__":
    unittest.main()

