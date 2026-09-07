"""Testes unitários para a janela flutuante estilo Dynamic Island (VoicePillWindow)."""

import os
import sys
import unittest
from unittest.mock import MagicMock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.live import LiveVoiceState
from zorin_copilot.ui.voice_pill import VoicePillWindow


class TestVoicePillWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.voice_pill")

    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_client.is_active.return_value = True
        self.mock_client.toggle_mute.return_value = True
        self.mock_client.is_muted.return_value = False

        self.expand_called = False
        self.close_called = False

        def on_expand():
            self.expand_called = True

        def on_close():
            self.close_called = True

        self.pill = VoicePillWindow(
            application=self.app,
            live_client=self.mock_client,
            on_expand=on_expand,
            on_close=on_close,
        )

    def test_window_geometry_and_decorations(self):
        """Valida se a janela da pílula não possui decorações de título e tem tamanho compacto."""
        self.assertEqual(self.pill.get_title(), "Zorin Copilot Live")
        self.assertFalse(self.pill.get_decorated())
        self.assertFalse(self.pill.get_resizable())
        width, height = self.pill.get_default_size()
        self.assertLessEqual(width, 400)
        self.assertLessEqual(height, 80)

    def test_ui_components_exist(self):
        """Verifica se os elementos centrais (avatar, status, visualizador e botões) estão presentes."""
        self.assertIsNotNone(self.pill.avatar_box)
        self.assertIsNotNone(self.pill.status_lbl)
        self.assertIsNotNone(self.pill.drawing_area)
        self.assertIsNotNone(self.pill.mute_btn)
        self.assertIsNotNone(self.pill.expand_btn)
        self.assertIsNotNone(self.pill.close_btn)

    def test_state_changes(self):
        """Verifica se a mudança de estado da chamada atualiza o rótulo da UI."""
        self.pill._ui_on_state_change(LiveVoiceState.LISTENING, "")
        self.assertEqual(self.pill.status_lbl.get_text(), "Ouvindo você...")

        self.pill._ui_on_state_change(LiveVoiceState.SPEAKING, "")
        self.assertEqual(self.pill.status_lbl.get_text(), "Falando...")

        self.pill._ui_on_state_change(LiveVoiceState.THINKING, "")
        self.assertEqual(self.pill.status_lbl.get_text(), "Pensando...")

    def test_mute_action(self):
        """Verifica se o clique no botão mudo aciona o cliente de voz."""
        self.pill._on_toggle_mute(self.pill.mute_btn)
        self.mock_client.toggle_mute.assert_called_once()
        self.assertEqual(self.pill.status_lbl.get_text(), "Microfone mudo")

    def test_expand_and_close_callbacks(self):
        """Verifica se as ações de expandir e fechar chamam seus respectivos callbacks."""
        self.pill._on_expand_clicked(self.pill.expand_btn)
        self.assertTrue(self.expand_called)
        self.assertFalse(self.pill.get_visible())

        self.pill._on_close_clicked(self.pill.close_btn)
        self.assertTrue(self.close_called)


if __name__ == "__main__":
    unittest.main()
