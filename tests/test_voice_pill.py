"""Testes unitários para a janela flutuante estilo Dynamic Island (VoicePillWindow)."""

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

    # --- ciclo de vida / teardown (sem vazamento de frame clock) ---

    def test_tick_paused_while_hidden(self):
        """Esconder a pílula remove o tick; reexibir religa. `_alive` continua True.

        A pílula é reaproveitada entre sessões, então pausar não pode matá-la.
        """
        self.pill.present()
        self.assertTrue(self.pill.get_visible())

        self.pill.set_visible(False)
        self.assertEqual(self.pill._tick_id, 0)
        self.assertTrue(self.pill._alive, "esconder não deve matar a pílula")

        self.pill.set_visible(True)
        self.assertNotEqual(self.pill._tick_id, 0)

    def test_start_tick_is_idempotent(self):
        """_start_tick() repetido não empilha callbacks."""
        self.pill.present()
        first = self.pill._tick_id
        self.pill._start_tick()
        self.pill._start_tick()
        self.assertEqual(self.pill._tick_id, first)

    def test_close_request_hides_without_destroying(self):
        """do_close_request apenas esconde (retorna True) e avisa o callback.

        Destruir aqui faria a próxima invocação recriar a janela do zero — e
        perder o posicionamento/estado já resolvidos.
        """
        self.pill.present()
        handled = self.pill.do_close_request()
        self.assertTrue(handled)
        self.assertFalse(self.pill.get_visible())
        self.assertTrue(self.pill._alive, "fechar não deve destruir a pílula")
        self.assertTrue(self.close_called)

    def test_teardown_zeroes_source_ids(self):
        """_teardown() mata tick + os dois timers e marca _alive=False."""
        self.pill.present()
        # Cria timers reais para provar que são removidos (e não só esquecidos).
        self.pill._session_timer_id = GLib.timeout_add_seconds(60, lambda: GLib.SOURCE_REMOVE)
        self.pill._chip_restore_id = GLib.timeout_add_seconds(60, lambda: GLib.SOURCE_REMOVE)
        self.assertNotEqual(self.pill._tick_id, 0)

        self.pill._teardown()

        self.assertFalse(self.pill._alive)
        self.assertEqual(self.pill._tick_id, 0)
        self.assertEqual(self.pill._session_timer_id, 0)
        self.assertEqual(self.pill._chip_restore_id, 0)

    def test_teardown_is_idempotent(self):
        """Chamar _teardown() duas vezes não levanta."""
        self.pill._teardown()
        self.pill._teardown()
        self.assertFalse(self.pill._alive)

    def test_destroy_runs_teardown(self):
        """destroy() dispara do_unrealize → teardown.

        No GTK4 não existe sinal "destroy" em widget; `unrealize` é o único
        ponto seguro. Sem ele cada pílula criada deixava um tick callback
        rodando para sempre.
        """
        self.pill.present()
        self.assertTrue(self.pill.get_realized())
        self.pill.destroy()
        self.assertFalse(self.pill._alive)
        self.assertEqual(self.pill._tick_id, 0)

    # --- "Preparando..." (feedback imediato na invocação) ---

    def test_show_preparing_then_listening(self):
        """show_preparing() mostra a pílula na hora; o estado real substitui o texto."""
        self.pill.show_preparing()
        self.assertEqual(self.pill.status_lbl.get_text(), "Preparando...")
        self.assertTrue(self.pill._preparing)

        # CONNECTING é o estado "ainda preparando" — não deve limpar a flag.
        self.pill._ui_on_state_change(LiveVoiceState.CONNECTING, "")
        self.assertTrue(self.pill._preparing)

        # Qualquer estado real encerra o provisório.
        self.pill._ui_on_state_change(LiveVoiceState.LISTENING, "")
        self.assertEqual(self.pill.status_lbl.get_text(), "Ouvindo você...")
        self.assertFalse(self.pill._preparing)

    def test_show_preparing_presents_window(self):
        """show_preparing() deve tornar a pílula visível imediatamente."""
        self.assertFalse(self.pill.get_visible())
        self.pill.show_preparing()
        self.assertTrue(self.pill.get_visible())

    # --- paleta (re-resolução na troca de tema) ---

    def test_palette_invalidated_on_theme_change(self):
        """Trocar de tema invalida o cache; refresh_theme_colors() resolve de novo."""
        self.pill._color_for(LiveVoiceState.LISTENING)
        self.assertTrue(self.pill._palette_resolved)
        self.assertTrue(self.pill._palette)

        self.pill._on_theme_changed()
        self.assertFalse(self.pill._palette_resolved)
        self.assertEqual(self.pill._palette, {})

        self.pill.refresh_theme_colors()
        self.assertTrue(self.pill._palette_resolved)
        self.assertTrue(self.pill._palette)

    def test_palette_reresolve_replaces_instead_of_merging(self):
        """Re-resolver não pode mesclar lixo da resolução anterior."""
        self.pill.refresh_theme_colors()
        first = dict(self.pill._palette)
        self.pill.refresh_theme_colors()
        self.assertEqual(first, self.pill._palette)


if __name__ == "__main__":
    unittest.main()
