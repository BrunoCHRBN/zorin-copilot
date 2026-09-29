"""Testes unitários para a janela flutuante estilo Dynamic Island (VoicePillWindow)."""

import os
import sys
import unittest
from unittest.mock import MagicMock

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.live import LiveVoiceState
from zorin_copilot.ui import voice_pill
from zorin_copilot.ui.voice_pill import VoicePillWindow

import cairo
import math


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
        self.assertIsNotNone(self.pill.details_btn)
        self.assertIsNotNone(self.pill.expand_btn)
        self.assertIsNotNone(self.pill.close_btn)

    def test_details_popover_and_screen_share(self):
        """Verifica a abertura do popover de detalhes e alternância de compartilhamento de tela."""
        self.mock_client.is_video_streaming.return_value = False
        self.mock_client.toggle_video_stream.return_value = True

        self.pill._on_details_clicked()
        self.assertIsNotNone(self.pill._details_popover)
        self.assertIsNotNone(self.pill._details_popover.get_child())

        # Teste toggle video stream
        self.pill._on_toggle_video_stream()
        self.mock_client.toggle_video_stream.assert_called_once()
        self.assertTrue(self.pill._video_streaming)

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

    def test_privacy_tints_the_avatar_too(self):
        """O avatar também recebe `pill-privacy-on`.

        A regra `.voice-pill-avatar.pill-privacy-on` existia na folha desde o
        "Dynamic Island v2" e nada a aplicava — o container era tingido, o avatar
        não. Resultado: "privacidade ativa" e "ouvindo" ficavam a um tint âmbar
        sutil de distância. Este teste trava o fio que faltava.
        """
        self.pill._ui_on_privacy_state_change(True, "Google Chrome")
        self.assertIn("pill-privacy-on", self.pill.avatar_box.get_css_classes())
        # A classe do avatar é independente da do container.
        self.assertNotIn("pill-privacy-on", self.pill.container.get_css_classes())

        self.pill._ui_on_privacy_state_change(False, "")
        self.assertNotIn("pill-privacy-on", self.pill.avatar_box.get_css_classes())

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

    def _walk_widgets(self, widget):
        yield widget
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child:
            yield from self._walk_widgets(child)
            child = child.get_next_sibling() if hasattr(child, "get_next_sibling") else None

    def test_details_popover_shows_voice_and_quick_dropdown(self):
        self.pill._on_details_clicked()
        self.assertIsNotNone(self.pill._details_popover)
        child = self.pill._details_popover.get_child()
        self.assertIsNotNone(child)

        found_dropdown = False
        found_voice_label = False
        for widget in self._walk_widgets(child):
            if isinstance(widget, Gtk.DropDown):
                found_dropdown = True
            if isinstance(widget, Gtk.Label) and "Voz:" in (widget.get_text() or ""):
                found_voice_label = True

        self.assertTrue(found_dropdown)
        self.assertTrue(found_voice_label)

    def test_details_popover_voice_change_calls_live_client(self):
        self.pill._on_details_clicked()
        child = self.pill._details_popover.get_child()
        dropdown = None
        for widget in self._walk_widgets(child):
            if isinstance(widget, Gtk.DropDown):
                dropdown = widget
                break
        self.assertIsNotNone(dropdown)

        # Seleciona outro item no dropdown (índice diferente do atual)
        cur = dropdown.get_selected()
        new_idx = 0 if cur != 0 else 1
        dropdown.set_selected(new_idx)
        self.mock_client.set_voice.assert_called()

    def test_overlay_probes_and_rec_dot_are_untargetable(self):
        """Garante que os elementos de overlay (probes e rec_dot) não interceptam cliques."""
        self.assertFalse(self.pill.rec_dot.get_can_target())
        self.assertFalse(self.pill.rec_dot.get_focusable())
        for state, probe in self.pill._probes.items():
            self.assertFalse(probe.get_can_target(), f"Probe para {state} não deve ser alvo de eventos")
            self.assertFalse(probe.get_focusable(), f"Probe para {state} não deve ser focável")

    def test_close_button_click_triggers_on_close(self):
        """Verifica se o acionamento do close_btn fecha a pílula e chama o callback."""
        self.close_called = False
        self.pill.close_btn.emit("clicked")
        self.assertTrue(self.close_called)
        self.assertFalse(self.pill.get_visible())

    def test_expand_button_click_triggers_on_expand(self):
        """Verifica se o acionamento do expand_btn chama o callback de expansão."""
        self.expand_called = False
        self.pill.expand_btn.emit("clicked")
        self.assertTrue(self.expand_called)
        self.assertFalse(self.pill.get_visible())

    def test_escape_key_closes_pill(self):
        """Verifica se a tecla Escape fecha a pílula."""
        from gi.repository import Gdk
        self.close_called = False
        handled = self.pill._on_key_pressed(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
        self.assertTrue(handled)
        self.assertTrue(self.close_called)

    # --- auto-ocultação em estados terminais (erro/desconexão) ---

    def test_autohide_scheduled_on_terminal_state(self):
        """ERROR/DISCONNECTED agendam auto-hide; um estado vivo cancela."""
        self.pill._ui_on_state_change(LiveVoiceState.ERROR, "")
        self.assertNotEqual(self.pill._autohide_id, 0)

        self.pill._ui_on_state_change(LiveVoiceState.LISTENING, "")
        self.assertEqual(self.pill._autohide_id, 0)

        self.pill._ui_on_state_change(LiveVoiceState.DISCONNECTED, "")
        self.assertNotEqual(self.pill._autohide_id, 0)
        self.pill._cancel_autohide()

    def test_autohide_disabled_when_timeout_zero(self):
        """`pill_error_autohide_sec = 0` desliga o auto-hide."""
        self.pill.config.pill_error_autohide_sec = 0
        self.pill._ui_on_state_change(LiveVoiceState.ERROR, "")
        self.assertEqual(self.pill._autohide_id, 0)

    def test_do_autohide_closes_while_terminal(self):
        """_do_autohide fecha a pílula se o estado terminal persistir."""
        self.pill.present()
        self.pill._current_state = LiveVoiceState.ERROR
        self.pill._autohide_id = 12345  # id falso: não deixa source real pendurado
        self.close_called = False

        result = self.pill._do_autohide()

        self.assertEqual(result, GLib.SOURCE_REMOVE)
        self.assertEqual(self.pill._autohide_id, 0)
        self.assertFalse(self.pill.get_visible())
        self.assertTrue(self.close_called)

    def test_do_autohide_noop_when_recovered(self):
        """Se a sessão voltou a um estado vivo, o auto-hide não fecha nada."""
        self.pill.present()
        self.pill._current_state = LiveVoiceState.LISTENING
        self.close_called = False

        self.pill._do_autohide()

        self.assertTrue(self.pill.get_visible())
        self.assertFalse(self.close_called)

    def test_release_sources_clears_autohide(self):
        """_release_sources() (usado ao esconder) remove o source de auto-hide."""
        self.pill._autohide_id = GLib.timeout_add_seconds(60, lambda: GLib.SOURCE_REMOVE)
        self.pill._release_sources()
        self.assertEqual(self.pill._autohide_id, 0)


class TestWaveformGlow(unittest.TestCase):
    """O visualizador desenha via cairo; validamos sem GTK (surface em memória)."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.voice_pill.glow")

    def setUp(self):
        client = MagicMock()
        client.is_active.return_value = True
        client.toggle_mute.return_value = True
        client.is_muted.return_value = False
        self.pill = VoicePillWindow(
            application=self.app,
            live_client=client,
            on_expand=lambda: None,
            on_close=lambda: None,
        )

    def _ctx(self, w=80, h=26):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        return surf, cairo.Context(surf)

    def test_smooth_curve_emits_bezier(self):
        """A spline Catmull-Rom→Bézier deve produzir segmentos curve_to."""
        _, cr = self._ctx()
        pts = [(float(x), 13.0 + math.sin(x / 5.0) * 4.0) for x in range(0, 80, 4)]
        VoicePillWindow._smooth_curve(cr, pts)
        kinds = [seg[0] for seg in cr.copy_path()]
        self.assertIn(cairo.PATH_CURVE_TO, kinds)
        self.assertIn(cairo.PATH_MOVE_TO, kinds)

    def test_smooth_curve_handles_few_points(self):
        _, cr = self._ctx()
        # 1 ponto ou 0 não devem levantar.
        VoicePillWindow._smooth_curve(cr, [])
        VoicePillWindow._smooth_curve(cr, [(10.0, 13.0)])

    def test_draw_waves_no_crash_silent_and_active(self):
        """Fluxo real de desenho não pode quebrar em silêncio (lvl=0) nem pico (lvl=1)."""
        surf, cr = self._ctx()
        self.pill._draw_waves(cr, 4, 72, 13, 26, 0.0, 1.0, 0.5, 0.4, 0.8)
        surf2, cr2 = self._ctx()
        self.pill._draw_waves(cr2, 4, 72, 13, 26, 1.0, 3.0, 0.5, 0.4, 0.8)

    def test_draw_bars_and_orb_no_crash(self):
        surf, cr = self._ctx()
        self.pill._draw_bars(cr, 4, 72, 13, 26, 0.6, 2.0, 0.5, 0.4, 0.8)
        surf2, cr2 = self._ctx()
        self.pill._draw_orb(cr2, 4, 72, 13, 26, 0.6, 2.0, 0.5, 0.4, 0.8)

    def test_glow_color_cache_avoid_churn(self):
        """Mesma cor não reescreve a propriedade CSS (evita churn a cada frame)."""
        calls = []

        def fake_set(prop, val):
            calls.append((prop, val))

        self.pill.container.set_css_property = fake_set  # type: ignore[assignment]
        self.pill._glow_color_cache = None
        self.pill._set_glow_color((0.5, 0.4, 0.8))
        self.pill._set_glow_color((0.5, 0.4, 0.8))  # repetida → sem novo set
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0] == "--pill-glow-color")
        # cor diferente → reescreve
        self.pill._set_glow_color((0.1, 0.9, 0.2))
        self.assertEqual(len(calls), 2)


class TestPhase3Feedback(unittest.TestCase):
    """Feedback visual (Fase 3): captação, rajada de chips, conexão e fade ocioso.

    O visualizador mostra a *forma* do áudio; nada disso responde "o mic está me
    ouvindo agora?". Estes testes fixam os sinais explícitos que respondem.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.voice_pill.p3")

    def setUp(self):
        self.client = MagicMock()
        self.client.is_active.return_value = True
        self.client.is_muted.return_value = False
        self.pill = VoicePillWindow(
            application=self.app,
            live_client=self.client,
            on_expand=lambda: None,
            on_close=lambda: None,
        )

    def tearDown(self):
        self.pill._release_sources()

    # --- dot de captação de microfone ---

    def test_capture_dot_arms_while_listening(self):
        """Em LISTENING sem mudo o dot fica visível (mic armado)."""
        self.pill._current_state = LiveVoiceState.LISTENING
        self.pill._smooth_audio_level = 0.0
        self.pill._tick_capture_indicator()
        self.assertTrue(self.pill.mic_dot.get_visible())

    def test_capture_dot_hides_when_muted(self):
        """Mutar esconde o dot — quem comunica o mudo é pill-muted + onda vermelha."""
        self.pill._current_state = LiveVoiceState.LISTENING
        self.pill._smooth_audio_level = 0.5
        self.pill._tick_capture_indicator()
        self.assertTrue(self.pill.mic_dot.get_visible())

        self.client.is_muted.return_value = True
        self.pill._tick_capture_indicator()
        self.assertFalse(self.pill.mic_dot.get_visible())

    def test_capture_dot_hidden_outside_listening(self):
        """Falando/pensando/executando: o mic não é o protagonista, dot some."""
        for state in (
            LiveVoiceState.SPEAKING,
            LiveVoiceState.THINKING,
            LiveVoiceState.EXECUTING,
            LiveVoiceState.CONNECTING,
        ):
            self.pill._current_state = state
            self.pill._smooth_audio_level = 0.5
            self.pill._tick_capture_indicator()
            self.assertFalse(
                self.pill.mic_dot.get_visible(), f"dot não deveria aparecer em {state}"
            )

    def test_capture_dot_opacity_tracks_audio_level(self):
        """Silêncio = repouso visível; voz = dot cheio. Nunca apaga de vez armado."""
        self.pill._current_state = LiveVoiceState.LISTENING
        self.pill._smooth_audio_level = 0.0
        self.pill._tick_capture_indicator()
        quiet = self.pill.mic_dot.get_opacity()
        self.assertGreater(quiet, 0.0)

        self.pill._smooth_audio_level = 1.0
        self.pill._tick_capture_indicator()
        loud = self.pill.mic_dot.get_opacity()
        self.assertGreater(loud, quiet)
        self.assertAlmostEqual(loud, 1.0, places=2)

    # --- rajada de chips de ferramenta ---

    def test_chip_burst_prefixes_from_second_chip(self):
        """O primeiro chip mantém o texto cru; a rajada ganha contador legível."""
        self.pill._show_chip("Executando", ok=True)
        self.assertEqual(self.pill.status_lbl.get_text(), "Executando")
        self.pill._show_chip("Executando", ok=True)
        self.assertEqual(self.pill.status_lbl.get_text(), "2× Executando")
        self.pill._show_chip("Executando", ok=True)
        self.assertEqual(self.pill.status_lbl.get_text(), "3× Executando")

    def test_chip_burst_resets_after_restore(self):
        """Passados os 3s, a próxima rajada recomeça sem prefixo."""
        self.pill._show_chip("A", ok=True)
        self.pill._show_chip("B", ok=True)
        self.pill._restore_status()
        self.pill._show_chip("C", ok=True)
        self.assertEqual(self.pill.status_lbl.get_text(), "C")

    # --- feedback de conexão ---

    def test_connecting_flag_toggles_with_state(self):
        """pill-connecting liga em CONNECTING e desliga no primeiro estado real."""
        self.pill._ui_on_state_change(LiveVoiceState.CONNECTING, "")
        self.assertIn("pill-connecting", self.pill.container.get_css_classes())
        self.pill._ui_on_state_change(LiveVoiceState.LISTENING, "")
        self.assertNotIn("pill-connecting", self.pill.container.get_css_classes())

    def test_preparing_sets_connecting_flag(self):
        """show_preparing() já marca CONNECTING antes de o WebSocket subir."""
        # present()/place_smart() exigem display; o alvo aqui é só o flag.
        self.pill.present = lambda *a, **k: None  # type: ignore[assignment]
        self.pill.place_smart = lambda *a, **k: None  # type: ignore[assignment]
        self.pill.show_preparing()
        self.assertIn("pill-connecting", self.pill.container.get_css_classes())

    # --- clareza do fade ocioso ---

    def test_idle_adds_class_and_dims_glow(self):
        """Ocioso apaga o halo, não só o conteúdo — senão a pílula segue 'acesa'."""
        calls: list[tuple[str, str]] = []
        self.pill.container.set_css_property = lambda p, v: calls.append((p, v))  # type: ignore[assignment]

        self.pill._set_glow_color((0.5, 0.4, 0.8))
        active = calls[-1][1]
        self.pill._set_idle(True)
        self.assertIn("pill-idle", self.pill.container.get_css_classes())
        self.pill._set_glow_color((0.5, 0.4, 0.8))
        idle = calls[-1][1]

        self.assertNotEqual(active, idle)
        alpha = lambda s: float(s.rsplit(",", 1)[1].rstrip(")"))  # noqa: E731
        self.assertLess(alpha(idle), alpha(active))

    def test_idle_class_cleared_on_wake(self):
        self.pill._set_idle(True)
        self.pill._bump_activity()
        self.assertNotIn("pill-idle", self.pill.container.get_css_classes())

    def test_idle_releases_keep_above_and_wake_restores(self):
        """Esmaecida, a pílula cede o topo da pilha; acordada, volta a subir."""
        calls: list[bool] = []
        self.pill.set_keep_above = lambda v: calls.append(bool(v))  # type: ignore[assignment]
        self.pill.config.pill_pinned = True

        self.pill._set_idle(True)
        self.assertEqual(calls[-1], False)
        self.pill._set_idle(False)
        self.assertEqual(calls[-1], True)

    def test_idle_never_pins_when_user_unpinned(self):
        """`pill_pinned=False` continua mandando: o fade não reintroduz o pin."""
        calls: list[bool] = []
        self.pill.set_keep_above = lambda v: calls.append(bool(v))  # type: ignore[assignment]
        self.pill.config.pill_pinned = False

        self.pill._set_idle(True)
        self.pill._set_idle(False)
        self.assertTrue(all(c is False for c in calls))


class TestPhase4Discovery(unittest.TestCase):
    """Descoberta (Fase 4): dicas de atalho transitórias.

    Os atalhos de teclado só existiam para quem abrisse o menu de clique direito.
    Aqui fixamos o contrato: aparecem uma vez por execução, ocupam o lugar do
    status (a janela é não-redimensionável — crescer em altura quebraria o
    footprint) e saem de cena quando o texto passa a ser a única fonte de estado.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.voice_pill.p4")

    def setUp(self):
        self.client = MagicMock()
        self.client.is_active.return_value = True
        self.client.is_muted.return_value = False
        self.pill = VoicePillWindow(
            application=self.app,
            live_client=self.client,
            on_expand=lambda: None,
            on_close=lambda: None,
        )

    def tearDown(self):
        self.pill._release_sources()

    def test_hints_take_the_place_of_status(self):
        self.pill._start_hints()
        self.assertTrue(self.pill.hint_lbl.get_visible())
        self.assertFalse(self.pill.status_lbl.get_visible())
        self.assertEqual(self.pill.hint_lbl.get_text(), voice_pill._HINT_TIPS[0])

    def test_hints_show_only_once_per_run(self):
        """O ciclo é único por execução: repetir a cada sessão vira ruído."""
        self.pill._start_hints()
        self.pill._end_hints()
        self.pill._start_hints()  # segunda invocação na mesma execução
        self.assertFalse(self.pill.hint_lbl.get_visible())
        self.assertTrue(self.pill.status_lbl.get_visible())

    def test_hints_respect_config_switch(self):
        self.pill.config.pill_hint_tips = False
        self.pill._start_hints()
        self.assertFalse(self.pill.hint_lbl.get_visible())
        self.assertTrue(self.pill.status_lbl.get_visible())

    def test_hints_respect_zero_duration(self):
        self.pill.config.pill_hint_sec = 0
        self.pill._start_hints()
        self.assertFalse(self.pill.hint_lbl.get_visible())

    def test_hints_advance_through_tips(self):
        self.pill._start_hints()
        self.pill._hint_ticks_left = 5
        self.pill._advance_hint()
        self.assertEqual(self.pill.hint_lbl.get_text(), voice_pill._HINT_TIPS[1])
        self.assertFalse(self.pill._hint_id == 0)  # segue agendado

    def test_hints_end_after_last_tick(self):
        self.pill._start_hints()
        self.pill._hint_ticks_left = 1
        result = self.pill._advance_hint()
        self.assertEqual(result, GLib.SOURCE_REMOVE)
        self.assertFalse(self.pill.hint_lbl.get_visible())
        self.assertTrue(self.pill.status_lbl.get_visible())

    def test_chip_preempts_hints(self):
        """Um chip é informação provocada pelo usuário — não pode ficar oculto."""
        self.pill._start_hints()
        self.pill._show_chip("Executando", ok=True)
        self.assertFalse(self.pill.hint_lbl.get_visible())
        self.assertTrue(self.pill.status_lbl.get_visible())
        self.assertEqual(self.pill.status_lbl.get_text(), "Executando")

    def test_hints_survive_connecting_and_listening(self):
        """Nestes dois estados os visuais já contam o estado; a dica pode ficar."""
        self.pill._start_hints()
        self.pill._ui_on_state_change(LiveVoiceState.CONNECTING, "")
        self.assertTrue(self.pill.hint_lbl.get_visible())
        self.pill._ui_on_state_change(LiveVoiceState.LISTENING, "")
        self.assertTrue(self.pill.hint_lbl.get_visible())

    def test_hints_yield_when_text_is_the_only_signal(self):
        """Falando/pensando/executando/erro: o texto é a única fonte. Dica sai."""
        for state in (
            LiveVoiceState.SPEAKING,
            LiveVoiceState.THINKING,
            LiveVoiceState.EXECUTING,
            LiveVoiceState.ERROR,
        ):
            self.pill._hints_shown = False
            self.pill._end_hints()
            self.pill._start_hints()
            self.assertTrue(self.pill.hint_lbl.get_visible(), f"dica sumiu antes de {state}")
            self.pill._ui_on_state_change(state, "")
            self.assertFalse(
                self.pill.hint_lbl.get_visible(), f"dica não saiu em {state}"
            )
            self.assertTrue(self.pill.status_lbl.get_visible())

    def test_release_sources_restores_status_visibility(self):
        """Esconder a pílula no meio das dicas não pode deixar o status oculto."""
        self.pill._start_hints()
        self.pill._release_sources()
        self.assertEqual(self.pill._hint_id, 0)
        self.assertFalse(self.pill.hint_lbl.get_visible())
        self.assertTrue(self.pill.status_lbl.get_visible())


class TestPhase4ExpandAffordance(unittest.TestCase):
    """O botão de expansão precisa dizer o que faz.

    `view-fullscreen-symbolic` + "Expandir para janela completa" sugeria que a
    *pílula* viraria tela cheia. O que o botão faz é outro: fecha a pílula e abre
    a conversa na janela principal do Copilot.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.voice_pill.p4b")

    def setUp(self):
        self.pill = VoicePillWindow(
            application=self.app,
            live_client=MagicMock(),
            on_expand=lambda: None,
            on_close=lambda: None,
        )

    def tearDown(self):
        self.pill._release_sources()

    @staticmethod
    def _icon_name(btn: Gtk.Button) -> str:
        return btn.get_icon_name() or ""

    def test_expand_icon_names_the_copilot_window(self):
        self.assertEqual(self._icon_name(self.pill.expand_btn), "window-new-symbolic")
        self.assertNotEqual(
            self._icon_name(self.pill.expand_btn), "view-fullscreen-symbolic"
        )

    def test_expand_tooltip_is_unambiguous(self):
        tip = self.pill.expand_btn.get_tooltip_text() or ""
        self.assertIn("janela do Copilot", tip)

    def test_expand_closes_pill_and_opens_window(self):
        """O comportamento não mudou — só o rótulo: esconde a pílula e delega."""
        opened = []
        self.pill.on_expand_cb = lambda: opened.append(True)
        self.pill.set_visible(True)
        self.pill._on_expand_clicked(self.pill.expand_btn)
        self.assertEqual(opened, [True])
        self.assertFalse(self.pill.get_visible())

    def test_icon_only_buttons_are_named_for_screen_readers(self):
        """Botão só-ícone sem nome acessível é invisível para leitor de tela.

        `Gtk.Widget` não expõe getter do rótulo acessível, então verificamos a
        ligação: os quatro botões da pílula precisam passar pelo helper.
        """
        labels: list[str] = []
        original = VoicePillWindow._set_accessible_label
        VoicePillWindow._set_accessible_label = staticmethod(
            lambda _w, label: labels.append(label)
        )
        pill = None
        try:
            pill = VoicePillWindow(
                application=self.app,
                live_client=MagicMock(),
                on_expand=lambda: None,
                on_close=lambda: None,
            )
        finally:
            # `original` é a FUNÇÃO nua: no Python ≥ 3.10 o descritor
            # `staticmethod` some ao acessá-lo pela classe. Devolvê-lo sem
            # re-embrulhar transforma o método em método de instância e a pílula
            # passa a explodir com "takes 2 positional arguments but 3 were
            # given" em TODO teste seguinte — foi o que aconteceu.
            VoicePillWindow._set_accessible_label = staticmethod(original)
        try:
            self.assertEqual(len(labels), 4, f"esperado 4 botões nomeados: {labels}")
            self.assertTrue(any("janela do Copilot" in s for s in labels))
            self.assertTrue(all(s.strip() for s in labels))
        finally:
            if pill is not None:
                pill._release_sources()


class TestPhase4HideWithoutEnding(unittest.TestCase):
    """Ocultar sem encerrar: liberar a tela sem perder a conversa.

    Antes, a única forma de esconder a pílula era fechá-la — e fechar encerrava a
    sessão de voz. As duas intenções agora são ações distintas.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.voice_pill.p4c")

    def setUp(self):
        self.client = MagicMock()
        self.client.is_active.return_value = True
        self.client.is_muted.return_value = False
        self.hidden: list[bool] = []
        self.closed: list[bool] = []
        self.expanded: list[bool] = []
        self.pill = VoicePillWindow(
            application=self.app,
            live_client=self.client,
            on_expand=lambda: self.expanded.append(True),
            on_close=lambda: self.closed.append(True),
            on_hide=lambda: self.hidden.append(True),
        )
        self.pill.set_visible(True)

    def tearDown(self):
        self.pill._release_sources()

    # --- ação ---

    def test_hide_only_hides_and_reports(self):
        self.pill._on_hide_only()
        self.assertFalse(self.pill.get_visible())
        self.assertEqual(self.hidden, [True])
        self.assertEqual(self.closed, [], "ocultar não pode encerrar a sessão")

    def test_hide_only_keeps_client_running(self):
        """A sessão segue viva: o cliente não pode ser parado nem fechado."""
        self.pill._on_hide_only()
        self.client.stop.assert_not_called()
        self.assertTrue(self.client.is_active())

    def test_close_still_ends(self):
        """Contraprova: fechar continua encerrando."""
        self.pill._on_close_clicked()
        self.assertEqual(self.closed, [True])
        self.assertEqual(self.hidden, [])

    # --- gatilhos ---

    def test_h_key_hides_only(self):
        handled = self.pill._on_key_pressed(
            None, Gdk.KEY_h, 0, Gdk.ModifierType(0)
        )
        self.assertTrue(handled)
        self.assertEqual(self.hidden, [True])
        self.assertEqual(self.closed, [])

    def test_escape_still_ends(self):
        self.pill._on_key_pressed(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
        self.assertEqual(self.closed, [True])
        self.assertEqual(self.hidden, [])

    def test_ctrl_h_is_not_the_hide_shortcut(self):
        """Ctrl+H não pode ocultar por engano (reservado a outros usos)."""
        self.pill._on_key_pressed(
            None, Gdk.KEY_h, 0, Gdk.ModifierType.CONTROL_MASK
        )
        self.assertEqual(self.hidden, [])

    def test_avatar_double_click_hides_only(self):
        self.pill._on_avatar_pressed(None, 2, 0.0, 0.0)
        self.assertEqual(self.hidden, [True])
        self.assertFalse(self.pill.get_visible())

    def test_avatar_single_click_does_nothing(self):
        self.pill._on_avatar_pressed(None, 1, 0.0, 0.0)
        self.assertEqual(self.hidden, [])
        self.assertTrue(self.pill.get_visible())

    def test_menu_exposes_hide_without_ending(self):
        popover = self.pill._build_popover_menu()
        model = popover.get_menu_model()
        self.assertIn("Ocultar sem encerrar", " ".join(_menu_labels(model)))
        # o gesto de ocultar precisa estar registrado no grupo de ações
        self.assertTrue(popover.get_menu_model().get_n_items() > 0)


def _menu_items(model, depth: int = 0) -> list[tuple]:
    """(label, action, target) de um `Gio.MenuModel`, descendo nos submenus."""
    out: list[tuple] = []
    if depth > 3:
        return out
    for i in range(model.get_n_items()):
        label = model.get_item_attribute_value(i, "label", None)
        action = model.get_item_attribute_value(i, Gio.MENU_ATTRIBUTE_ACTION, None)
        target = model.get_item_attribute_value(i, Gio.MENU_ATTRIBUTE_TARGET, None)
        out.append(
            (
                label.get_string() if label is not None else None,
                action.get_string() if action is not None else None,
                target.get_string() if target is not None else None,
            )
        )
        submenu = model.get_item_link(i, "submenu")
        if submenu is not None:
            out.extend(_menu_items(submenu, depth + 1))
    return out


def _menu_labels(model) -> list[str]:
    """Rótulos de um `Gio.MenuModel` (inclui submenus), sem os nulos."""
    return [label for label, _action, _target in _menu_items(model) if label]


class TestPhase4CornerMenu(unittest.TestCase):
    """Reposicionar pelo menu, não só por arraste.

    Arrastar depende de o compositor repassar o movimento da janela — em vários
    Wayland isso falha. O menu é o caminho confiável, então precisa ser legível
    ("Topo · Centro") e mostrar onde a pílula está.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.voice_pill.p4d")

    def setUp(self):
        self.pill = VoicePillWindow(
            application=self.app,
            live_client=MagicMock(),
            on_expand=lambda: None,
            on_close=lambda: None,
        )

    def tearDown(self):
        self.pill._release_sources()

    def _corner_items(self) -> list[tuple]:
        model = self.pill._build_popover_menu().get_menu_model()
        return [item for item in _menu_items(model) if item[1] == "pill.set-corner"]

    def test_corner_menu_has_human_labels_for_every_corner(self):
        items = self._corner_items()
        self.assertEqual(len(items), len(voice_pill._CORNER_OFFSETS))
        labels = [item[0] for item in items]
        for label in labels:
            self.assertNotIn("-", label, f"código cru vazou para o menu: {label}")
        self.assertIn("Topo · Centro", labels)
        self.assertIn("Base · Centro", labels)

    def test_corner_items_target_the_real_codes(self):
        targets = {item[2] for item in self._corner_items()}
        self.assertEqual(targets, set(voice_pill._CORNER_OFFSETS))

    def test_widget_activate_action_reaches_the_handler(self):
        """Ponta a ponta: o grupo de ações está instalado com o nome certo.

        `activate_action` percorre o muxer da janela até o grupo "pill" — é o
        mesmo caminho que o PopoverMenu usa ao clicar no item. O grupo só existe
        depois que o menu é construído (é ele quem o instala).
        """
        self.pill.place_smart = MagicMock()  # type: ignore[assignment]
        self.pill.config.save = MagicMock()  # type: ignore[assignment]
        self.pill._build_popover_menu()

        self.pill.activate_action(
            "pill.set-corner", GLib.Variant.new_string("bottom-center")
        )

        self.assertEqual(self.pill.config.pill_corner, "bottom-center")
        self.pill.place_smart.assert_called_once()

    def test_activating_corner_updates_state_and_config(self):
        self.pill.place_smart = MagicMock()  # type: ignore[assignment]
        action = Gio.SimpleAction.new_stateful(
            "set-corner",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string("top-center"),
        )
        self.pill._on_set_corner_action(action, GLib.Variant.new_string("top-left"))

        self.assertEqual(action.get_state().get_string(), "top-left")
        self.assertEqual(self.pill.config.pill_corner, "top-left")
        self.pill.place_smart.assert_called_once()

    def test_activating_unknown_corner_is_ignored(self):
        self.pill.place_smart = MagicMock()  # type: ignore[assignment]
        self.pill.config.pill_corner = "top-center"
        action = Gio.SimpleAction.new_stateful(
            "set-corner",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string("top-center"),
        )
        self.pill._on_set_corner_action(action, GLib.Variant.new_string("canto-inexistente"))

        self.assertEqual(action.get_state().get_string(), "top-center")
        self.assertEqual(self.pill.config.pill_corner, "top-center")
        self.pill.place_smart.assert_not_called()


def _menu_items(model, depth: int = 0) -> list[tuple]:
    """(label, action, target) de um `Gio.MenuModel`, descendo nos submenus."""
    out: list[tuple] = []
    if depth > 3:
        return out
    for i in range(model.get_n_items()):
        label = model.get_item_attribute_value(i, "label", None)
        action = model.get_item_attribute_value(i, Gio.MENU_ATTRIBUTE_ACTION, None)
        target = model.get_item_attribute_value(i, Gio.MENU_ATTRIBUTE_TARGET, None)
        out.append(
            (
                label.get_string() if label is not None else None,
                action.get_string() if action is not None else None,
                target.get_string() if target is not None else None,
            )
        )
        submenu = model.get_item_link(i, "submenu")
        if submenu is not None:
            out.extend(_menu_items(submenu, depth + 1))
    return out


def _menu_labels(model) -> list[str]:
    """Rótulos de um `Gio.MenuModel` (inclui submenus), sem os nulos."""
    return [label for label, _action, _target in _menu_items(model) if label]


def _tree_labels(widget) -> list[str]:
    """Textos de todos os `Gtk.Label` da árvore — para afirmar sobre a UI realmente montada."""
    out: list[str] = []
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_text())
    child = widget.get_first_child()
    while child is not None:
        out.extend(_tree_labels(child))
        child = child.get_next_sibling()
    return out


def _fake_live_client(mode: str, supports_video: bool, model: str = "gemini-2.5-flash-live"):
    """Dublê que **declara** o modo, como os clientes reais passaram a fazer (P1-5)."""
    c = MagicMock()
    c.is_active.return_value = True
    c.is_muted.return_value = False
    c.is_video_streaming.return_value = False
    c.toggle_video_stream.return_value = False
    c.video_mode = "active_window"
    c.fence = None
    c.model = model
    c.mode = mode
    c.supports_video = supports_video
    return c


class TestModeHelpers(unittest.TestCase):
    """Entrega C / P1-5: os helpers que decidem modo/capacidade a partir do cliente."""

    def test_client_mode_defaults_to_cloud_when_undeclared(self):
        # Clientes antigos e dublês não declaram `mode` → comportamento histórico (nuvem).
        self.assertEqual(voice_pill._client_mode(MagicMock()), "cloud")
        self.assertEqual(voice_pill._client_mode(object()), "cloud")

    def test_client_mode_reads_declared_local(self):
        class _Local:
            mode = "local"

        self.assertEqual(voice_pill._client_mode(_Local()), "local")

    def test_supports_video_honours_declared_false(self):
        class _Local:
            mode = "local"
            supports_video = False

        self.assertFalse(voice_pill._supports_video(_Local()))

    def test_supports_video_ignores_truthy_non_bool(self):
        """`MagicMock` devolve um mock (truthy): aceitá-lo faria o local parecer capaz de vídeo."""

        class _LocalComMock:
            mode = "local"
            supports_video = MagicMock()

        self.assertFalse(voice_pill._supports_video(_LocalComMock()))


class TestModeIndicator(unittest.TestCase):
    """Entrega C / P1-5: a pílula declara o modo e não afirma capacidade inexistente."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.mode")

    def _pill(self, client):
        return VoicePillWindow(application=self.app, live_client=client)

    def _popover_labels(self, pill) -> list[str]:
        pill._on_details_clicked()
        return _tree_labels(pill._details_popover.get_child())

    def test_chip_says_nuvem_for_cloud_client(self):
        pill = self._pill(_fake_live_client("cloud", True))
        self.assertEqual(pill.mode_chip.get_text(), "Nuvem")
        self.assertTrue(pill.mode_chip.has_css_class("pill-mode-cloud"))
        self.assertFalse(pill.mode_chip.has_css_class("pill-mode-local"))

    def test_chip_says_local_for_local_client(self):
        pill = self._pill(_fake_live_client("local", False))
        self.assertEqual(pill.mode_chip.get_text(), "Local")
        self.assertTrue(pill.mode_chip.has_css_class("pill-mode-local"))
        self.assertFalse(pill.mode_chip.has_css_class("pill-mode-cloud"))

    def test_local_mode_does_not_offer_screen_controls(self):
        """O defeito: os stubs de vídeo do cliente local faziam o botão existir e não fazer nada."""
        pill = self._pill(_fake_live_client("local", False))
        labels = self._popover_labels(pill)
        self.assertNotIn("Transmitir Tela", labels)
        self.assertNotIn("Pausar Tela", labels)
        self.assertIn("Indisponível no modo local", labels)

    def test_cloud_mode_still_offers_screen_controls(self):
        pill = self._pill(_fake_live_client("cloud", True))
        labels = self._popover_labels(pill)
        self.assertIn("Transmitir Tela", labels)
        self.assertNotIn("Indisponível no modo local", labels)

    def test_local_mode_model_row_names_local_model(self):
        """O defeito: em modo local a linha anunciava o modelo da nuvem."""
        pill = self._pill(_fake_live_client("local", False))
        self.assertIn(f"Modelo: {pill.config.ollama_model}", self._popover_labels(pill))

    def test_cloud_mode_model_row_names_cloud_model(self):
        pill = self._pill(_fake_live_client("cloud", True, model="gemini-3.8-live"))
        self.assertIn("Modelo: Gemini 3.8 Live", self._popover_labels(pill))

    def test_local_mode_does_not_claim_spatial_fence_active(self):
        """O defeito: o texto "Cerca Espacial & Cursor Fantasma ativos" era fixo."""
        pill = self._pill(_fake_live_client("local", False))
        labels = self._popover_labels(pill)
        self.assertNotIn("Cerca Espacial & Cursor Fantasma ativos", labels)
        self.assertIn("Cerca Espacial indisponível no modo local", labels)

    def test_cloud_mode_without_fence_says_inactive_not_unavailable(self):
        """A causa importa: na nuvem a cerca **existe** e está inativa; no local não existe."""
        pill = self._pill(_fake_live_client("cloud", True))
        labels = self._popover_labels(pill)
        self.assertIn("Cerca Espacial inativa", labels)
        self.assertNotIn("Cerca Espacial indisponível no modo local", labels)

    def test_local_mode_toggle_reports_unavailable_not_paused(self):
        """O defeito: o clique respondia "Transmissão de tela pausada" (recurso existente, parado)."""
        pill = self._pill(_fake_live_client("local", False))
        pill._on_toggle_video_stream()
        texto = pill.status_lbl.get_text()
        self.assertIn("indisponível no modo local", texto)
        self.assertNotIn("pausada", texto)

    def test_legacy_double_without_declaration_behaves_like_cloud(self):
        """Sem `mode`/`supports_video` declarados, nada regride: vale o comportamento antigo."""
        c = MagicMock()
        c.is_active.return_value = True
        c.is_muted.return_value = False
        c.is_video_streaming.return_value = False
        c.fence = None
        c.model = "gemini-2.5-flash-live"
        pill = self._pill(c)
        self.assertEqual(pill.mode_chip.get_text(), "Nuvem")
        self.assertIn("Transmitir Tela", self._popover_labels(pill))


if __name__ == "__main__":
    unittest.main()
