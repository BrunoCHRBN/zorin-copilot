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

    # --- novos recursos (Dynamic Island v2) ---

    def test_muted_visual_class(self):
        """Mutar aplica destructive-action + classe pill-muted no container."""
        self.pill._on_toggle_mute(self.pill.mute_btn)
        self.assertIn("destructive-action", self.pill.mute_btn.get_css_classes())
        self.assertIn("pill-muted", self.pill.container.get_css_classes())

    def test_video_state_rec_dot(self):
        """on_video_state_change(True) torna o REC dot visível; False esconde."""
        self.pill._ui_on_video_state_change(True)
        self.assertTrue(self.pill.rec_dot.get_visible())
        self.pill._ui_on_video_state_change(False)
        self.assertFalse(self.pill.rec_dot.get_visible())

    def test_privacy_state_class(self):
        """on_privacy_state_change aplica classe pill-privacy + tooltip no avatar."""
        self.pill._ui_on_privacy_state_change(True, "Google Chrome")
        self.assertIn("pill-privacy", self.pill.container.get_css_classes())
        self.assertEqual(self.pill.avatar_box.get_tooltip_text(), "Privacidade: Google Chrome")
        # Desativar → remove classe
        self.pill._ui_on_privacy_state_change(False, "")
        self.assertNotIn("pill-privacy", self.pill.container.get_css_classes())

    def test_tool_chip_lifecycle(self):
        """_show_chip troca texto/classe; _restore_status devolve o estado anterior."""
        original_text = self.pill.status_lbl.get_text()
        self.pill._show_chip("✓ Chrome", ok=True)
        self.assertEqual(self.pill.status_lbl.get_text(), "✓ Chrome")
        self.assertIn("pill-chip-ok", self.pill.container.get_css_classes())
        # Simula o callback do timeout
        self.pill._restore_status()
        self.assertEqual(self.pill.status_lbl.get_text(), original_text)
        self.assertNotIn("pill-chip-ok", self.pill.container.get_css_classes())

    def test_idle_fade(self):
        """_set_idle controla opacidade do container."""
        self.pill._set_idle(True)
        self.assertAlmostEqual(self.pill.container.get_opacity(), 0.30, places=2)
        self.pill._set_idle(False)
        self.assertAlmostEqual(self.pill.container.get_opacity(), 1.0, places=2)

    def test_interrupt_calls_client(self):
        """_on_interrupt_action chama live_client.interrupt() se existir."""
        self.pill.live_client.interrupt = MagicMock()
        self.pill._on_interrupt_action()
        self.pill.live_client.interrupt.assert_called_once()

    def test_place_smart_no_crash_headless(self):
        """place_smart() não levanta mesmo com display e cliente mínimos."""
        # Não temos display real aqui; o método deve retornar cedo sem erro
        try:
            self.pill.place_smart()
        except Exception as exc:  # pragma: no cover
            self.fail(f"place_smart não deveria levantar, levantou: {exc}")


if __name__ == "__main__":
    unittest.main()
    unittest.main()
