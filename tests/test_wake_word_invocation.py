"""Testes da invocação por palavra de ativação (wake word) na janela principal.

Cobre o contrato de `_handle_wake_on_main_thread`: qual superfície abre
(pílula vs HUD completo), repasse do comando embutido na frase, reaproveitamento
de uma sessão já ativa e cancelamento de um encerramento autônomo pendente.

Usamos uma instância mínima de `CopilotWindow` (``__init__`` neutralizado) porque
construir a janela real exigiria display, loop GTK e config de usuário — nada
disso é relevante para a decisão que está sob teste.
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, GLib  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.config import CopilotConfig  # noqa: E402
from zorin_copilot.ui.app import CopilotWindow  # noqa: E402


def make_window() -> CopilotWindow:
    """Cria um CopilotWindow 'pelado': só os atributos que o handler consome."""
    with patch.object(CopilotWindow, "__init__", lambda self, *a, **k: None):
        win = CopilotWindow()
    win.config = CopilotConfig()
    win.live_client = None
    win.show_toast = MagicMock()
    win.summon_hud = MagicMock()
    win.start_live_voice = MagicMock()
    win._cancel_pending_end_session = MagicMock()
    win._end_session_source_id = 0
    win._pending_end_mode = ""
    return win


class WakeWordInvocationTest(unittest.TestCase):
    """Qual superfície a wake word abre, conforme `voice_overlay_mode`."""

    def setUp(self):
        self.win = make_window()

    def test_pill_mode_opens_pill_without_summoning_hud(self):
        """Modo 'pill': abre só a pílula — a janela inteira não deve ser puxada."""
        self.win.config.voice_overlay_mode = "pill"
        self.win._handle_wake_on_main_thread("ei zorin", "")

        self.win.start_live_voice.assert_called_once_with(initial_command="", as_pill=True)
        self.win.summon_hud.assert_not_called()

    def test_full_mode_summon_hud_then_opens_window(self):
        """Modo 'full': mantém o comportamento antigo (HUD em primeiro plano)."""
        self.win.config.voice_overlay_mode = "full"
        self.win._handle_wake_on_main_thread("ei zorin", "")

        self.win.summon_hud.assert_called_once()
        self.win.start_live_voice.assert_called_once_with(initial_command="", as_pill=False)

    def test_unknown_mode_falls_back_to_pill(self):
        """Modo desconhecido/vazio não pode deixar o usuário sem feedback: cai na pílula."""
        self.win.config.voice_overlay_mode = ""
        self.win._handle_wake_on_main_thread("ei zorin", "")
        self.win.start_live_voice.assert_called_once_with(initial_command="", as_pill=True)

        self.win.start_live_voice.reset_mock()
        self.win.config.voice_overlay_mode = "coisa-que-nao-existe"
        self.win._handle_wake_on_main_thread("ei zorin", "")
        self.win.start_live_voice.assert_called_once_with(initial_command="", as_pill=True)

    def test_missing_attribute_defaults_to_pill(self):
        """Config antiga sem `voice_overlay_mode` → pílula (padrão atual)."""
        if hasattr(self.win.config, "voice_overlay_mode"):
            del self.win.config.voice_overlay_mode
        self.win._handle_wake_on_main_thread("ei zorin", "")
        self.win.start_live_voice.assert_called_once_with(initial_command="", as_pill=True)

    def test_handler_returns_source_remove(self):
        """O handler roda via GLib.idle_add: retornar False o torna one-shot."""
        self.assertEqual(self.win._handle_wake_on_main_thread("ei zorin", ""), GLib.SOURCE_REMOVE)


class WakeWordCommandForwardingTest(unittest.TestCase):
    """O comando embutido na frase ('ei zorin, abra o Chrome') precisa chegar ao Live."""

    def setUp(self):
        self.win = make_window()

    def test_command_is_forwarded(self):
        self.win._handle_wake_on_main_thread("ei zorin", "abra o chrome")
        self.win.start_live_voice.assert_called_once_with(
            initial_command="abra o chrome", as_pill=True
        )

    def test_toast_mentions_command_when_present(self):
        self.win._handle_wake_on_main_thread("ei zorin", "abra o chrome")
        toast = self.win.show_toast.call_args[0][0]
        self.assertIn("abra o chrome", toast)
        self.assertIn("ei zorin", toast)

    def test_toast_without_command_is_an_invitation(self):
        self.win._handle_wake_on_main_thread("ei zorin", "")
        toast = self.win.show_toast.call_args[0][0]
        self.assertIn("ei zorin", toast)
        self.assertIn("Iniciando", toast)


class WakeWordActiveSessionTest(unittest.TestCase):
    """Wake word durante uma sessão ativa não deve abrir outra sessão."""

    def setUp(self):
        self.win = make_window()
        self.client = MagicMock()
        self.client.is_active.return_value = True
        self.win.live_client = self.client

    def test_active_session_with_command_sends_text_turn(self):
        self.win._handle_wake_on_main_thread("ei zorin", "que horas sao")
        self.client.send_text_input.assert_called_once_with("que horas sao")
        self.win.start_live_voice.assert_not_called()
        self.win.summon_hud.assert_not_called()

    def test_active_session_without_command_does_nothing(self):
        """Sem comando, não há o que dizer — e não se interrompe a sessão à toa."""
        self.win._handle_wake_on_main_thread("ei zorin", "")
        self.client.send_text_input.assert_not_called()
        self.win.start_live_voice.assert_not_called()

    def test_inactive_client_starts_new_session(self):
        self.client.is_active.return_value = False
        self.win._handle_wake_on_main_thread("ei zorin", "ola")
        self.client.send_text_input.assert_not_called()
        self.win.start_live_voice.assert_called_once_with(initial_command="ola", as_pill=True)

    def test_no_client_starts_new_session(self):
        self.win.live_client = None
        self.win._handle_wake_on_main_thread("ei zorin", "ola")
        self.win.start_live_voice.assert_called_once_with(initial_command="ola", as_pill=True)


class WakeWordCancelsPendingEndTest(unittest.TestCase):
    """Chamar de novo durante o grace period de um encerramento autônomo cancela."""

    def test_cancel_pending_end_session_is_always_called(self):
        self.win = make_window()
        self.win._handle_wake_on_main_thread("ei zorin", "")
        self.win._cancel_pending_end_session.assert_called_once()

    def test_cancel_happens_before_active_session_shortcut(self):
        """Mesmo com sessão ativa, o encerramento pendente tem que ser cancelado."""
        self.win = make_window()
        client = MagicMock()
        client.is_active.return_value = True
        self.win.live_client = client
        self.win._handle_wake_on_main_thread("ei zorin", "")
        self.win._cancel_pending_end_session.assert_called_once()


class WakeWordErrorToastTest(unittest.TestCase):
    """`_show_wake_word_error` precisa ter rate-limit (microfone indisponível retenta)."""

    def setUp(self):
        self.win = make_window()

    def test_first_error_shows_toast(self):
        self.win._show_wake_word_error("microfone indisponível")
        self.win.show_toast.assert_called_once()
        self.assertIn("microfone indisponível", self.win.show_toast.call_args[0][0])

    def test_second_error_within_a_minute_is_suppressed(self):
        self.win._show_wake_word_error("falha 1")
        self.win.show_toast.reset_mock()
        self.win._show_wake_word_error("falha 2")
        self.win.show_toast.assert_not_called()

    def test_error_after_a_minute_shows_again(self):
        self.win._show_wake_word_error("falha 1")
        self.win.show_toast.reset_mock()
        self.win._last_wake_error_at = time.monotonic() - 61.0
        self.win._show_wake_word_error("falha 2")
        self.win.show_toast.assert_called_once()


if __name__ == "__main__":
    unittest.main()
