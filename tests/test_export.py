"""Testes da exportação da conversa para Markdown (item 4 do backlog da UI).

A parte de formatação é testada sem display (é uma função pura); só a costura
com a janela precisa de GTK.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.core.export import (  # noqa: E402
    exportable_turns,
    render_conversation_markdown,
    slugify,
    suggest_filename,
)
from zorin_copilot.core.session import ChatTurn, TopicSession  # noqa: E402

try:
    Adw.init()
    HAS_DISPLAY = True
except Exception:  # pragma: no cover - só acontece sem servidor gráfico
    HAS_DISPLAY = False

if HAS_DISPLAY:  # importado aqui para não exigir display nos testes puros
    from zorin_copilot.ui.app import CopilotWindow  # noqa: E402

FIXED_NOW = datetime(2025, 3, 12, 14, 30, 0)


def _session(*pairs: tuple[str, str], title: str = "") -> TopicSession:
    session = TopicSession(auto_persist=True)
    for prompt, answer in pairs:
        session.record_turn(prompt, answer)
    if title:
        session.title = title
    return session


class SlugifyTest(unittest.TestCase):
    def test_lowercases_and_dashes(self):
        self.assertEqual(slugify("Nova Conversa"), "nova-conversa")

    def test_strips_accents(self):
        self.assertEqual(slugify("Análise de Vendas"), "analise-de-vendas")

    def test_drops_punctuation(self):
        self.assertEqual(slugify('Como fazer: "oi"? (rápido)'), "como-fazer-oi-rapido")

    def test_collapses_separators(self):
        self.assertEqual(slugify("a   ---   b"), "a-b")

    def test_trims_edges(self):
        self.assertEqual(slugify("---olá---"), "ola")

    def test_empty_text(self):
        self.assertEqual(slugify(""), "")
        self.assertEqual(slugify(None), "")

    def test_respects_limit_without_dangling_dash(self):
        slug = slugify("a " * 80, limit=10)
        self.assertEqual(slug, "a-a-a-a-a")


class SuggestFilenameTest(unittest.TestCase):
    def test_uses_title_and_date(self):
        session = _session(("oi", "olá"), title="Resumo do trimestre")
        self.assertEqual(
            suggest_filename(session, FIXED_NOW), "resumo-do-trimestre-2025-03-12.md"
        )

    def test_falls_back_to_conversa(self):
        self.assertEqual(
            suggest_filename(TopicSession(), FIXED_NOW), "conversa-2025-03-12.md"
        )


class ExportableTurnsTest(unittest.TestCase):
    def test_returns_pinned_turns(self):
        session = _session(("a", "b"), ("c", "d"))
        self.assertEqual([t.prompt for t in exportable_turns(session)], ["a", "c"])

    def test_includes_unpinned_pending_turn(self):
        """O turno ainda não fixado aparece na tela, então deve ir para o arquivo."""
        session = TopicSession(auto_persist=False)
        session.record_turn("oi", "olá")
        self.assertEqual(session.turns, [])
        self.assertEqual([t.prompt for t in exportable_turns(session)], ["oi"])

    def test_does_not_duplicate_pending_turn(self):
        session = TopicSession(auto_persist=False)
        turn = session.record_turn("oi", "olá")
        session.turns.append(turn)  # já foi incorporado por um pin()
        self.assertEqual(len(exportable_turns(session)), 1)

    def test_drops_completely_empty_turns(self):
        session = _session(("oi", "olá"))
        session.turns.append(ChatTurn(prompt="   ", answer=""))
        self.assertEqual([t.prompt for t in exportable_turns(session)], ["oi"])

    def test_keeps_turn_with_only_one_side(self):
        session = _session(("oi", ""))
        self.assertEqual(len(exportable_turns(session)), 1)


class RenderMarkdownTest(unittest.TestCase):
    def test_empty_session_renders_nothing(self):
        """A UI usa o texto vazio para avisar que não há o que salvar."""
        self.assertEqual(render_conversation_markdown(TopicSession()), "")

    def test_frontmatter_comes_first_and_is_closed(self):
        doc = render_conversation_markdown(_session(("oi", "olá")), exported_at=FIXED_NOW)
        self.assertTrue(doc.startswith("---\n"))
        self.assertIn("\n---\n", doc[3:])

    def test_title_heading(self):
        doc = render_conversation_markdown(
            _session(("oi", "olá"), title="Planejamento"), exported_at=FIXED_NOW
        )
        self.assertIn("title: \"Planejamento\"", doc)
        self.assertIn("\n# Planejamento\n", doc)

    def test_untitled_session_gets_default_heading(self):
        session = _session(("oi", "olá"))
        session.title = ""
        doc = render_conversation_markdown(session, exported_at=FIXED_NOW)
        self.assertIn("# Conversa sem título", doc)

    def test_quotes_in_title_are_escaped(self):
        session = _session(("oi", "olá"), title='Ele disse "oi": e saiu')
        doc = render_conversation_markdown(session, exported_at=FIXED_NOW)
        self.assertIn('title: "Ele disse \\"oi\\": e saiu"', doc)

    def test_colon_in_title_does_not_break_yaml(self):
        session = _session(("oi", "olá"), title="Passo a passo: instalar")
        doc = render_conversation_markdown(session, exported_at=FIXED_NOW)
        self.assertIn('title: "Passo a passo: instalar"', doc)

    def test_turn_count_and_timestamp(self):
        doc = render_conversation_markdown(
            _session(("a", "b"), ("c", "d")), exported_at=FIXED_NOW
        )
        self.assertIn("turn_count: 2", doc)
        self.assertIn("exported_at: \"2025-03-12T14:30:00\"", doc)

    def test_turn_timestamp_is_human_readable(self):
        session = _session(("oi", "olá"))
        session.turns[0].timestamp = datetime(2025, 3, 12, 9, 5).timestamp()
        doc = render_conversation_markdown(session, exported_at=FIXED_NOW)
        self.assertIn("## Você · 12/03/2025 09:05", doc)

    def test_answer_is_preserved_verbatim(self):
        """Blocos de código e tabelas precisam chegar intactos ao arquivo."""
        answer = "Use isto:\n\n```bash\n sudo apt update\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |"
        doc = render_conversation_markdown(
            _session(("como?", answer)), exported_at=FIXED_NOW
        )
        self.assertIn("```bash\n sudo apt update\n```", doc)
        self.assertIn("| a | b |", doc)

    def test_turns_are_separated_by_rule(self):
        doc = render_conversation_markdown(
            _session(("a", "b"), ("c", "d")), exported_at=FIXED_NOW
        )
        self.assertIn("\n\n---\n\n## Você", doc)

    def test_answer_content_does_not_corrupt_frontmatter(self):
        """Uma resposta começando com `---` não pode virar um segundo frontmatter."""
        doc = render_conversation_markdown(
            _session(("a", "---\ntitle: falso")), exported_at=FIXED_NOW
        )
        first_block = doc.split("\n---\n", 1)[0]
        self.assertIn('source: "zorin-copilot"', first_block)
        self.assertNotIn("falso", first_block)

    def test_missing_sides_are_marked(self):
        doc = render_conversation_markdown(_session(("oi", "")), exported_at=FIXED_NOW)
        self.assertIn("_(sem resposta)_", doc)

        doc = render_conversation_markdown(_session(("", "olá")), exported_at=FIXED_NOW)
        self.assertIn("_(sem texto)_", doc)

    def test_provider_and_model_are_optional(self):
        bare = render_conversation_markdown(_session(("oi", "olá")), exported_at=FIXED_NOW)
        self.assertNotIn("provider:", bare)
        self.assertNotIn("model:", bare)

        full = render_conversation_markdown(
            _session(("oi", "olá")),
            provider="gemini",
            model="gemini-flash-latest",
            exported_at=FIXED_NOW,
        )
        self.assertIn('provider: "gemini"', full)
        self.assertIn('model: "gemini-flash-latest"', full)

    def test_document_ends_with_newline(self):
        doc = render_conversation_markdown(_session(("oi", "olá")), exported_at=FIXED_NOW)
        self.assertTrue(doc.endswith("\n"))


@unittest.skipUnless(HAS_DISPLAY, "requer servidor gráfico")
class WindowExportTest(unittest.TestCase):
    """Costura: atalho, painel de comandos e gravação do arquivo."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.export")

    def setUp(self):
        self.win = CopilotWindow(self.app)

    def test_shortcut_is_declared(self):
        from zorin_copilot.core.shortcuts import APP_SHORTCUTS

        names = {s.name for s in APP_SHORTCUTS}
        self.assertIn("app.export-conversation", names)

    def test_palette_exposes_export_with_accelerator(self):
        commands = {c.name: c for c in self.win.palette_commands()}
        self.assertIn("app.export-conversation", commands)
        self.assertEqual(commands["app.export-conversation"].accelerator, "<Control>s")

    def test_palette_handler_points_to_export(self):
        handler = self.win._palette_handlers().get("app.export-conversation")
        self.assertIsNotNone(handler)

    def test_build_markdown_is_empty_for_new_session(self):
        self.assertEqual(self.win.build_conversation_markdown(), "")

    def test_build_markdown_includes_turns(self):
        self.win.session.record_turn("oi", "olá")
        doc = self.win.build_conversation_markdown()
        self.assertIn("## Você", doc)
        self.assertIn("olá", doc)

    def test_build_markdown_uses_configured_model(self):
        self.win.session.record_turn("oi", "olá")
        self.win.config.provider = "ollama"
        self.win.config.ollama_model = "llama3.2:latest"
        doc = self.win.build_conversation_markdown()
        self.assertIn('provider: "ollama"', doc)
        self.assertIn('model: "llama3.2:latest"', doc)

    def test_write_path_creates_file(self):
        self.win.session.record_turn("oi", "olá")
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "saida.md")
            self.win._on_export_target_chosen(_FakeDialog(target), None)
            with open(target, encoding="utf-8") as handle:
                content = handle.read()
        self.assertIn("## Você", content)
        self.assertIn("olá", content)

    def test_dismissed_dialog_writes_nothing(self):
        self.win.session.record_turn("oi", "olá")
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "saida.md")
            self.win._on_export_target_chosen(_FakeDialog(target, error=_dismissed()), None)
            self.assertFalse(os.path.exists(target))

    def test_export_with_empty_conversation_does_not_open_dialog(self):
        """Sem turnos, o app avisa em vez de abrir um diálogo inútil."""
        opened = []
        original = Gtk.FileDialog

        class SpyDialog:
            def __init__(self, *args, **kwargs):
                opened.append(self)

            def __getattr__(self, _name):
                raise AssertionError("nenhum método do diálogo deveria ser chamado")

        try:
            Gtk.FileDialog = SpyDialog
            self.win.export_conversation()
        finally:
            Gtk.FileDialog = original
        self.assertEqual(opened, [])


class _FakeDialog:
    """Stand-in de `Gtk.FileDialog` para testar o caminho de gravação."""

    def __init__(self, path: str, error: GLib.Error | None = None):
        self._path = path
        self._error = error

    def save_finish(self, _result):
        if self._error is not None:
            raise self._error
        return Gio.File.new_for_path(self._path)


def _dismissed() -> GLib.Error:
    """Erro equivalente a "usuário cancelou o diálogo"."""
    return GLib.Error.new_literal(
        GLib.quark_from_string("gtk-dialog-error-quark"),
        "dismissed",
        int(Gtk.DialogError.DISMISSED),
    )


if __name__ == "__main__":
    unittest.main()
