"""Testes do Sprint 2: seleção de provedor por abas e registro da sessão de voz.

Cobrem:
  - Controle segmentado de provedores no PreferencesDialog (substitui o ComboRow).
  - Registro rolável de ações/transcrição e cronômetro no LiveVoiceWidget.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.live import LiveVoiceState  # noqa: E402
from zorin_copilot.ui.live_view import LiveVoiceWidget  # noqa: E402
from zorin_copilot.ui.preferences import VALID_PROVIDERS, PreferencesDialog  # noqa: E402


class ProviderSelectorTest(unittest.TestCase):
    """O provedor deve ser escolhido por um controle segmentado, não por dropdown."""

    def setUp(self):
        self.dialog = PreferencesDialog(None)

    def test_has_one_button_per_provider(self):
        self.assertEqual(set(self.dialog.provider_buttons), set(VALID_PROVIDERS))

    def test_only_active_group_is_visible(self):
        self.dialog.set_active_provider("ollama")
        self.assertTrue(self.dialog.ollama_group.get_visible())
        self.assertFalse(self.dialog.gemini_group.get_visible())
        self.assertFalse(self.dialog.openai_group.get_visible())

    def test_selection_is_mutually_exclusive(self):
        self.dialog.set_active_provider("gemini")
        self.dialog.provider_buttons["openai"].set_active(True)
        self.assertFalse(self.dialog.provider_buttons["gemini"].get_active())
        self.assertFalse(self.dialog.provider_buttons["ollama"].get_active())
        self.assertTrue(self.dialog.provider_buttons["openai"].get_active())

    def test_active_provider_drives_collected_config(self):
        for provider in VALID_PROVIDERS:
            self.dialog.set_active_provider(provider)
            self.assertEqual(self.dialog.active_provider(), provider)
            self.assertEqual(self.dialog._collect_current_config().provider, provider)

    def test_invalid_provider_falls_back_to_gemini(self):
        self.dialog.set_active_provider("openai")
        self.dialog.set_active_provider("inexistente")
        self.assertEqual(self.dialog.active_provider(), "gemini")

    def test_fields_survive_provider_switch(self):
        """Valores digitados não podem ser perdidos ao trocar de provedor e voltar."""
        self.dialog.gemini_key_row.set_text("CHAVE-TESTE")
        self.dialog.set_active_provider("ollama")
        self.dialog.set_active_provider("gemini")
        self.assertEqual(self.dialog.gemini_key_row.get_text(), "CHAVE-TESTE")
        self.assertEqual(self.dialog._collect_current_config().gemini_api_key, "CHAVE-TESTE")

    def test_gemini_live_voice_row_and_preview_button(self):
        """Verifica a presença da linha de voz do Gemini Live e do botão de preview."""
        self.assertIsNotNone(self.dialog.gemini_live_voice_row)
        self.assertIsNotNone(self.dialog.gemini_live_voice_prev_btn)
        self.assertEqual(len(self.dialog.gemini_live_voices_list), 8)

        with unittest.mock.patch("zorin_copilot.ai.voice_preview.VoicePreviewService.play_voice") as mock_play:
            self.dialog._on_preview_live_voice_clicked(self.dialog.gemini_live_voice_prev_btn)
            mock_play.assert_called_once()


class LiveSessionLogTest(unittest.TestCase):
    """A sessão de voz deve acumular ações e transcrição em lista rolável."""

    def setUp(self):
        self.client = MagicMock()
        self.client.state = LiveVoiceState.LISTENING
        self.client.is_muted.return_value = False
        self.client.monitors = []

        self.widget = LiveVoiceWidget(live_client=self.client)
        self.window = Gtk.Window()
        self.window.set_child(self.widget)
        self.window.present()

    def tearDown(self):
        self.widget._on_end_call()
        self.window.set_child(None)
        self.window.destroy()

    def _rows(self):
        rows = []
        child = self.widget.log_box.get_first_child()
        while child:
            rows.append(child)
            child = child.get_next_sibling()
        return rows

    def test_log_panel_is_present_from_the_start(self):
        """O painel nasce visível.

        Antes começava oculto e só surgia na primeira linha: além de esconder o
        histórico — o problema relatado —, o painel aparecendo no meio de uma
        frase fazia a onda ao lado encolher sozinha. Agora ele está no lugar
        desde o início, com uma linha dizendo o que vai aparecer ali.
        """
        self.assertTrue(self.widget.log_scrolled.get_visible())
        self.assertEqual(len(self._rows()), 0)
        self.assertTrue(self.widget.log_empty_lbl.get_visible())

    def test_first_entry_retires_the_empty_hint(self):
        self.widget._ui_on_transcript("user", "Bom dia")
        self.widget._flush_transcript()
        self.assertFalse(self.widget.log_empty_lbl.get_visible())
        self.assertEqual(len(self._rows()), 1)

    def test_transcript_accumulates_instead_of_replacing(self):
        """Regressão: antes cada fala sobrescrevia a anterior.

        Com o batching, cada troca de papel encerra o turno anterior (1 linha por
        fala); o flush final efetiva o turno corrente.
        """
        self.widget._ui_on_transcript("user", "Qual é o meu IP?")
        self.widget._ui_on_transcript("model", "Use o comando ip a.")
        self.widget._ui_on_transcript("user", "E as portas abertas?")
        self.widget._flush_transcript()

        self.assertEqual(len(self._rows()), 3)
        self.assertTrue(self.widget.log_scrolled.get_visible())

    def test_transcript_ignores_blank_text(self):
        self.widget._ui_on_transcript("user", "   ")
        self.assertEqual(len(self._rows()), 0)

    def test_tool_execution_is_logged(self):
        self.widget._ui_on_tool_executed("launch_app", "Abriu o Firefox", True)
        self.assertEqual(len(self._rows()), 1)

    def test_failed_tool_is_logged_without_raising(self):
        self.widget._ui_on_tool_executed("open_url", "Falha ao abrir", False)
        self.assertEqual(len(self._rows()), 1)

    def test_error_is_logged(self):
        self.widget._ui_on_error("Conexão encerrada")
        self.assertEqual(len(self._rows()), 1)

    def test_timer_starts_on_connecting_and_stops_on_disconnected(self):
        self.widget._ui_on_state_change(LiveVoiceState.CONNECTING, "")
        self.assertIsNotNone(self.widget._timer_id)
        self.assertTrue(self.widget.timer_lbl.get_visible())

        self.widget._ui_on_state_change(LiveVoiceState.DISCONNECTED, "")
        self.assertIsNone(self.widget._timer_id)
        self.assertFalse(self.widget.timer_lbl.get_visible())

    def test_timer_counts_seconds(self):
        self.widget._start_timer()
        self.assertEqual(self.widget.elapsed_seconds, 0)
        self.widget._on_timer_tick()
        self.widget._on_timer_tick()
        self.widget._on_timer_tick()
        self.assertEqual(self.widget.elapsed_seconds, 3)
        # O redesign do live voice (commit 0db1302) prefixou o rótulo com
        # "Ao Vivo · " para dar contexto ao cronômetro no dock de chamada.
        # A asserção ficou para trás; alinhada ao formato real.
        self.assertEqual(self.widget.timer_lbl.get_text(), "Ao Vivo · 00:03")
        self.widget._stop_timer()

    def test_timer_formats_minutes(self):
        self.widget._start_timer()
        self.widget._elapsed_sec = 125
        self.widget._on_timer_tick()
        self.assertEqual(self.widget.timer_lbl.get_text(), "Ao Vivo · 02:06")
        self.widget._stop_timer()

    def test_timer_stops_when_widget_is_detached(self):
        """O timer não deve continuar rodando se o widget sair da árvore."""
        self.widget._start_timer()
        self.window.set_child(None)
        self.assertIsNone(self.widget.get_root())
        self.assertEqual(self.widget._on_timer_tick(), GLib.SOURCE_REMOVE)
        self.assertIsNone(self.widget._timer_id)


class PillHintPreferenceTest(unittest.TestCase):
    """Preferência das dicas de atalho da pílula.

    O coletor parte de um `CopilotConfig()` novo e reatribui campo por campo —
    sem a reatribuição explícita o switch voltaria ao default a cada save.
    """

    def setUp(self):
        self.dialog = PreferencesDialog(None)

    def test_switch_reflects_config(self):
        self.dialog.config.pill_hint_tips = False
        self.dialog._load_values()
        self.assertFalse(self.dialog.pill_hint_switch_row.get_active())

    def test_collected_config_follows_switch(self):
        self.dialog.pill_hint_switch_row.set_active(False)
        self.assertFalse(self.dialog._collect_current_config().pill_hint_tips)
        self.dialog.pill_hint_switch_row.set_active(True)
        self.assertTrue(self.dialog._collect_current_config().pill_hint_tips)

    def test_hint_duration_survives_collection(self):
        """`pill_hint_sec` não tem controle na tela: precisa ser copiado."""
        self.dialog.config.pill_hint_sec = 11
        self.assertEqual(self.dialog._collect_current_config().pill_hint_sec, 11)


class AdwMarkupEscapingGuardTest(unittest.TestCase):
    """Onde o `&` precisa ser escapado — medido, não presumido (Adw 1.9.4).

    Um `&` cru faz o parse de Pango markup falhar, emite `Gtk-WARNING` e deixa o
    rótulo **vazio**: sem exceção e sem sintoma além do texto sumido.

    | Propriedade                          | Tratamento     | "A & B"      |
    |--------------------------------------|----------------|--------------|
    | `Adw.PreferencesPage.title`          | texto puro     | `"A & B"`    |
    | `Adw.ActionRow.title` / `.subtitle`  | **markup**     | `"A &amp; B"`|
    | `Adw.PreferencesGroup.title` / `.description` | **markup** | `"A &amp; B"`|

    As duas metades importam: escapar o que é texto puro faz aparecer um `&amp;`
    literal na tela. A crença anterior ("ActionRow é texto puro") estava
    invertida e teria apagado rótulos em silêncio.
    """

    @staticmethod
    def _labels(widget: Gtk.Widget) -> list[str]:
        out: list[str] = []

        def visit(w: Gtk.Widget) -> None:
            if isinstance(w, Gtk.Label):
                out.append(w.get_text())
            child = w.get_first_child()
            while child is not None:
                visit(child)
                child = child.get_next_sibling()

        visit(widget)
        return out

    def test_row_title_and_subtitle_are_markup(self):
        row = Adw.ActionRow(title="ROW &amp; OK", subtitle="SUB &amp; OK")
        win = Gtk.Window()
        win.set_child(row)
        win.realize()
        try:
            rendered = self._labels(row)
        finally:
            win.destroy()
        self.assertIn("ROW & OK", rendered)
        self.assertIn("SUB & OK", rendered)

    def test_group_title_and_description_are_markup(self):
        group = Adw.PreferencesGroup(title="GRUPO &amp; OK", description="DESC &amp; OK")
        win = Gtk.Window()
        win.set_child(group)
        win.realize()
        try:
            rendered = self._labels(group)
        finally:
            win.destroy()
        self.assertIn("GRUPO & OK", rendered)
        self.assertIn("DESC & OK", rendered)

    def test_page_title_is_plain_text(self):
        """O contrário também vale: aqui `&amp;` apareceria literal na tela."""
        dlg = Adw.PreferencesDialog()
        dlg.add(Adw.PreferencesPage(title="IA & Modelos"))
        dlg.present()
        try:
            self.assertIn("IA & Modelos", self._labels(dlg))
        finally:
            dlg.unrealize()

    def test_preferences_source_escapes_markup_ampersands(self):
        """Guarda de fonte: `&` cru em title/subtitle/description de row/grupo."""
        import ast
        import re
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src" / "zorin_copilot" / "ui" / "preferences.py"
        )
        tree = ast.parse(src.read_text(encoding="utf-8"))
        entity = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")
        attrs = ("title", "subtitle", "description")
        offenders: list[tuple[str, str]] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
                continue
            if func.value.id != "Adw":
                continue
            cls = func.attr
            # `PreferencesPage.title` é texto puro: não entra na guarda.
            if not (cls.endswith("Row") or cls == "PreferencesGroup"):
                continue
            for kw in node.keywords:
                if (
                    kw.arg in attrs
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    value = kw.value.value
                    if "&" in entity.sub("", value):
                        offenders.append((f"Adw.{cls}.{kw.arg}", value))

        self.assertEqual(
            offenders, [], f"`&` cru em markup do Adw (rótulo fica vazio): {offenders}"
        )


if __name__ == "__main__":
    unittest.main()
