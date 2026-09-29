"""Testes do roteamento do atalho de voz ao vivo (`ZorinCopilotApp._handle_voice_shortcut`).

Contrato: o atalho de voz abre **só a pílula** no modo "pill" (independentemente
de a janela principal já estar visível) e o HUD completo no modo "full". Se a
sessão já estiver ativa **e a pílula visível**, o atalho encerra; se a sessão
estiver ativa mas a pílula oculta ("ocultar sem encerrar"), ele reexibe a pílula
em vez de encerrar.

Regressão coberta: o atalho abria a GUI inteira quando a janela principal já
estava visível, por causa do gate `not win.get_visible()`.

Usamos uma `CopilotWindow` mínima (``__init__`` neutralizado) e chamamos o
método estático direto — a decisão sob teste não depende de display/loop GTK.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.live import LiveVoiceState  # noqa: E402
from zorin_copilot.core.config import CopilotConfig  # noqa: E402
from zorin_copilot.ui.app import CopilotWindow, ZorinCopilotApp  # noqa: E402


def make_win(
    *,
    visible: bool = False,
    pill_visible: bool = False,
    live_active: bool = False,
    overlay: str = "pill",
    client_state: LiveVoiceState | None = None,
) -> CopilotWindow:
    """Cria um CopilotWindow 'pelado': só o que o handler consome."""
    with patch.object(CopilotWindow, "__init__", lambda self, *a, **k: None):
        win = CopilotWindow()
    win.config = CopilotConfig()
    win.config.voice_overlay_mode = overlay
    win.get_visible = MagicMock(return_value=visible)
    win.summon_hud = MagicMock()
    win.toggle_live_voice = MagicMock()
    win.stop_live_voice = MagicMock()

    if live_active or client_state is not None:
        win.live_client = MagicMock()
        win.live_client.is_active.return_value = live_active
        if client_state is not None:
            win.live_client.state = client_state
    else:
        win.live_client = None

    if pill_visible:
        win.voice_pill_window = MagicMock()
        win.voice_pill_window.get_visible.return_value = True
    else:
        win.voice_pill_window = None
    return win


class VoiceShortcutRoutingTest(unittest.TestCase):
    """Qual superfície o atalho de voz abre, conforme o modo de overlay."""

    def test_pill_mode_opens_only_pill_when_window_hidden(self):
        win = make_win(visible=False, overlay="pill")
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.toggle_live_voice.assert_called_once_with(as_pill=True)
        win.summon_hud.assert_not_called()

    def test_pill_mode_opens_only_pill_even_when_window_visible(self):
        """Regressão: a GUI não pode ser puxada quando o HUD já está visível."""
        win = make_win(visible=True, overlay="pill")
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.toggle_live_voice.assert_called_once_with(as_pill=True)
        win.summon_hud.assert_not_called()

    def test_full_mode_summon_hud_then_opens_window(self):
        win = make_win(visible=False, overlay="full")
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.summon_hud.assert_called_once()
        win.toggle_live_voice.assert_called_once_with(as_pill=False)

    def test_active_live_session_is_stopped(self):
        win = make_win(live_active=True, overlay="pill")
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.stop_live_voice.assert_called_once()
        win.toggle_live_voice.assert_not_called()

    def test_visible_pill_is_stopped(self):
        win = make_win(pill_visible=True, overlay="pill")
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.stop_live_voice.assert_called_once()
        win.toggle_live_voice.assert_not_called()

    def test_unknown_overlay_mode_falls_back_to_pill(self):
        win = make_win(visible=True, overlay="coisa-que-nao-existe")
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.toggle_live_voice.assert_called_once_with(as_pill=True)
        win.summon_hud.assert_not_called()


class ForcePillShortcutTest(unittest.TestCase):
    """O atalho dedicado (`force_pill`) sempre abre a pílula, ignorando o modo."""

    def test_force_pill_ignores_full_mode(self):
        win = make_win(visible=False, overlay="full")
        ZorinCopilotApp._handle_voice_shortcut(win, force_pill=True)

        win.toggle_live_voice.assert_called_once_with(as_pill=True)
        win.summon_hud.assert_not_called()

    def test_force_pill_when_active_toggles_off(self):
        win = make_win(live_active=True, overlay="full")
        ZorinCopilotApp._handle_voice_shortcut(win, force_pill=True)

        win.stop_live_voice.assert_called_once()
        win.toggle_live_voice.assert_not_called()


class DeadSessionShortcutTest(unittest.TestCase):
    """Sessão em estado terminal conta como inativa: o atalho reinicia limpo."""

    def test_dead_pill_restarts_clean(self):
        win = make_win(
            pill_visible=True,
            overlay="pill",
            client_state=LiveVoiceState.DISCONNECTED,
        )
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.stop_live_voice.assert_called_once()
        win.toggle_live_voice.assert_called_once_with(as_pill=True)

    def test_dead_session_without_pill_starts_fresh(self):
        win = make_win(overlay="pill", client_state=LiveVoiceState.ERROR)
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.toggle_live_voice.assert_called_once_with(as_pill=True)
        win.summon_hud.assert_not_called()

    def test_alive_pill_does_not_restart(self):
        """Pílula viva + atalho = toggle-off; não pode reabrir em cima."""
        win = make_win(pill_visible=True, live_active=True, overlay="pill")
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.stop_live_voice.assert_called_once()
        win.toggle_live_voice.assert_not_called()


class PortalShortcutListTest(unittest.TestCase):
    """O portal só oferece o atalho da pílula quando ele está habilitado."""

    def test_pill_entry_only_when_enabled(self):
        from zorin_copilot.core.shortcuts_portal import portal_shortcut_list

        cfg = CopilotConfig()
        cfg.voice_pill_shortcut_enabled = False
        ids = [entry[0] for entry in portal_shortcut_list(cfg)]
        self.assertIn("voice", ids)
        self.assertNotIn("voice-pill", ids)

        cfg.voice_pill_shortcut_enabled = True
        cfg.voice_pill_shortcut_key = "<Super><Shift>p"
        by_id = {entry[0]: entry for entry in portal_shortcut_list(cfg)}
        self.assertIn("voice-pill", by_id)
        self.assertEqual(by_id["voice-pill"][2], "<Super><Shift>p")


class HiddenLivePillResummonTest(unittest.TestCase):
    """Ocultar sem encerrar não pode ser porta de mão única.

    Com a sessão viva e a pílula oculta, o atalho precisa **trazer a pílula de
    volta**. Antes ele caía no ramo de encerramento: quem ocultasse a pílula
    perdia a conversa e só podia recomeçar.
    """

    def _win_with_hidden_live_pill(self, same_client: bool = True) -> CopilotWindow:
        win = make_win(live_active=True, overlay="pill")
        pill = MagicMock()
        pill.get_visible.return_value = False
        pill.live_client = win.live_client if same_client else MagicMock()
        pill._current_state = LiveVoiceState.LISTENING
        win.voice_pill_window = pill
        return win

    def test_resummon_shows_pill_and_keeps_session(self):
        win = self._win_with_hidden_live_pill()
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.voice_pill_window.present.assert_called_once()
        win.voice_pill_window.place_smart.assert_called_once()
        win.stop_live_voice.assert_not_called()
        win.toggle_live_voice.assert_not_called()

    def test_resummon_reapplies_last_state(self):
        """Oculta, a pílula não recebe frames — o texto ficaria defasado."""
        win = self._win_with_hidden_live_pill()
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.voice_pill_window._ui_on_state_change.assert_called_once_with(
            LiveVoiceState.LISTENING, ""
        )

    def test_pill_bound_to_another_client_still_stops(self):
        """Pílula de sessão anterior não pode ser reapresentada no lugar de encerrar."""
        win = self._win_with_hidden_live_pill(same_client=False)
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.stop_live_voice.assert_called_once()
        win.voice_pill_window.present.assert_not_called()

    def test_no_pill_at_all_still_stops(self):
        win = make_win(live_active=True, overlay="pill")
        ZorinCopilotApp._handle_voice_shortcut(win)

        win.stop_live_voice.assert_called_once()

    def test_show_live_voice_pill_returns_false_without_pill(self):
        win = make_win(live_active=True, overlay="pill")
        self.assertFalse(win.show_live_voice_pill())

    def test_show_live_voice_pill_true_for_matching_client(self):
        win = self._win_with_hidden_live_pill()
        self.assertTrue(win.show_live_voice_pill())


if __name__ == "__main__":
    unittest.main()
