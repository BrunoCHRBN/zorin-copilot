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

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
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

    def _rows(self):
        rows = []
        child = self.widget.log_box.get_first_child()
        while child:
            rows.append(child)
            child = child.get_next_sibling()
        return rows

    def test_log_starts_hidden(self):
        self.assertFalse(self.widget.log_scrolled.get_visible())
        self.assertEqual(len(self._rows()), 0)

    def test_transcript_accumulates_instead_of_replacing(self):
        """Regressão: antes cada fala sobrescrevia a anterior."""
        self.widget._ui_on_transcript("user", "Qual é o meu IP?")
        self.widget._ui_on_transcript("model", "Use o comando ip a.")
        self.widget._ui_on_transcript("user", "E as portas abertas?")

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
        self.assertEqual(self.widget.timer_lbl.get_text(), "00:03")
        self.widget._stop_timer()

    def test_timer_formats_minutes(self):
        self.widget._start_timer()
        self.widget._elapsed_sec = 125
        self.widget._on_timer_tick()
        self.assertEqual(self.widget.timer_lbl.get_text(), "02:06")
        self.widget._stop_timer()

    def test_timer_stops_when_widget_is_detached(self):
        """O timer não deve continuar rodando se o widget sair da árvore."""
        self.widget._start_timer()
        self.window.set_child(None)
        self.assertIsNone(self.widget.get_root())
        self.assertEqual(self.widget._on_timer_tick(), GLib.SOURCE_REMOVE)
        self.assertIsNone(self.widget._timer_id)


if __name__ == "__main__":
    unittest.main()
