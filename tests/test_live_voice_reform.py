"""Testes do §4 da reformulação: o modo de voz ao vivo.

Cobre as cinco mudanças propostas em `docs/PROPOSTA_REFORMULACAO_GUI.md` §4:

1. os cinco botões do dock têm ícones **distintos** — o botão de modo usava
   `video-display-symbolic` no estado "Tela Inteira", que é o glifo exato do
   botão vizinho "Transmitir Tela";
2. a barra lateral recolhe durante a chamada e volta ao estado anterior;
3. o histórico da sessão fica **ao lado** da onda e visível desde o início;
4. o indicador "● Gravando" só acende com o microfone captando de fato;
5. o "Encerrar" fica na ponta direita, atrás de um divisor.

Onde o teste poderia virar verificação de ambiente (existência do ícone no tema
instalado), ele **pula** em vez de falhar — a suíte não pode depender de qual
tema de ícones está instalado na máquina de quem roda.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.live import LiveVoiceState  # noqa: E402
from zorin_copilot.ui.live_view import (  # noqa: E402
    MODE_ICON_FULLSCREEN,
    MODE_ICON_WINDOW,
    LiveVoiceWidget,
)
from tests._gtk_helpers import descendants, icon_names  # noqa: E402

ICON_ROOTS = ("/usr/share/icons", os.path.expanduser("~/.local/share/icons"))


def find_by_css_class(root: Gtk.Widget, css_class: str) -> Gtk.Widget | None:
    for node in descendants(root):
        if node.has_css_class(css_class):
            return node
    return None


def make_client(state=LiveVoiceState.LISTENING, muted=False) -> MagicMock:
    """Cliente de voz falso com o mínimo que a view realmente lê."""
    client = MagicMock()
    client.state = state
    client.is_muted.return_value = muted
    client.monitors = []
    # `live_model_label` faz `.rsplit` no id do modelo: um MagicMock cru
    # estouraria com TypeError ao montar o rótulo.
    client.config.gemini_live_model = "gemini-3.8-live"
    return client


class LiveWidgetTestCase(unittest.TestCase):
    """Base: monta a view numa janela de verdade e desmonta com cuidado."""

    def setUp(self):
        self.client = make_client()
        self.widget = LiveVoiceWidget(live_client=self.client)
        self.window = Gtk.Window()
        self.window.set_child(self.widget)
        self.window.present()

    def tearDown(self):
        # Sem isso, o timer da piscada sobrevive ao teste e o GLib o chama
        # depois, já com a árvore destruída.
        self.widget._stop_record_blink()
        self.widget._stop_timer()
        self.window.set_child(None)
        self.window.destroy()


class DockIconTest(LiveWidgetTestCase):
    """Cada controle do dock precisa de um glifo próprio."""

    def _dock(self) -> Gtk.Widget:
        dock = find_by_css_class(self.widget, "call-control-dock")
        self.assertIsNotNone(dock, "o dock de controles não foi encontrado")
        return dock

    def test_the_five_dock_buttons_use_distinct_icons(self):
        names = icon_names(self._dock())
        self.assertEqual(len(names), 5, f"esperava 5 ícones no dock, achei {names}")
        self.assertEqual(
            len(set(names)),
            5,
            f"dois botões do dock dividem o mesmo glifo: {names}",
        )

    def test_the_mode_button_does_not_repeat_the_video_button(self):
        """Regressão direta: 'Tela Inteira' usava o ícone de 'Transmitir Tela'."""
        video_icon = self.widget.video_icon.get_icon_name()
        self.widget._update_mode_ui("fullscreen")
        self.assertNotEqual(
            self.widget.mode_icon.get_icon_name(),
            video_icon,
            "o botão de modo repete o glifo do botão 'Transmitir Tela'",
        )

    def test_the_two_mode_states_are_distinguishable(self):
        self.widget._update_mode_ui("active_window")
        window_icon = self.widget.mode_icon.get_icon_name()
        self.widget._update_mode_ui("fullscreen")
        fullscreen_icon = self.widget.mode_icon.get_icon_name()
        self.assertNotEqual(window_icon, fullscreen_icon)

    def test_the_mode_labels_are_short_and_stable(self):
        self.widget._update_mode_ui("active_window")
        self.assertEqual(self.widget.mode_lbl.get_text(), "Janela")
        self.widget._update_mode_ui("fullscreen")
        self.assertEqual(self.widget.mode_lbl.get_text(), "Tela")

    def test_the_chosen_icon_names_exist_in_the_theme(self):
        """Um nome inexistente deixa o botão sem ícone — foi o que a proposta
        sugeria (`window-popup-symbolic`, que não existe em nenhum tema)."""
        roots = [r for r in ICON_ROOTS if os.path.isdir(r)]
        if not roots:
            self.skipTest("nenhum diretório de ícones no sistema")
        for name in (MODE_ICON_WINDOW, MODE_ICON_FULLSCREEN):
            found = False
            for root in roots:
                for _dirpath, _dirnames, filenames in os.walk(root):
                    if f"{name}.svg" in filenames or f"{name}.png" in filenames:
                        found = True
                        break
                if found:
                    break
            self.assertTrue(found, f"o ícone {name} não existe no tema instalado")


class SidebarCollapseRuleTest(unittest.TestCase):
    """A regra de recolher/restaurar, sem construir a janela inteira.

    Os métodos só tocam `self.sidebar` e `self._sidebar_revealed_before_live`,
    então exercitá-los num dublê testa a regra de verdade — e sem o custo de
    subir uma `CopilotWindow`.
    """

    class _FakeSidebar:
        def __init__(self, revealed: bool):
            self.revealer = Gtk.Revealer()
            self.revealer.set_reveal_child(revealed)

        @property
        def is_revealed(self) -> bool:
            return self.revealer.get_reveal_child()

    class _FakeWindow:
        def __init__(self, revealed: bool):
            self.sidebar = SidebarCollapseRuleTest._FakeSidebar(revealed)
            self._sidebar_revealed_before_live = None

    def setUp(self):
        from zorin_copilot.ui.app import CopilotWindow
        self.window_cls = CopilotWindow

    def test_collapse_hides_the_sidebar(self):
        win = self._FakeWindow(revealed=True)
        self.window_cls._collapse_sidebar_for_live(win)
        self.assertFalse(win.sidebar.is_revealed)

    def test_restore_brings_it_back_when_it_was_open(self):
        win = self._FakeWindow(revealed=True)
        self.window_cls._collapse_sidebar_for_live(win)
        self.window_cls._restore_sidebar_after_live(win)
        self.assertTrue(win.sidebar.is_revealed)

    def test_a_sidebar_the_user_had_collapsed_stays_collapsed(self):
        """Encerrar a chamada não pode abrir uma barra que o usuário fechou."""
        win = self._FakeWindow(revealed=False)
        self.window_cls._collapse_sidebar_for_live(win)
        self.window_cls._restore_sidebar_after_live(win)
        self.assertFalse(win.sidebar.is_revealed)

    def test_the_remembered_state_is_consumed_once(self):
        win = self._FakeWindow(revealed=True)
        self.window_cls._collapse_sidebar_for_live(win)
        self.assertIsNotNone(win._sidebar_revealed_before_live)
        self.window_cls._restore_sidebar_after_live(win)
        self.assertIsNone(win._sidebar_revealed_before_live)

    def test_a_second_collapse_keeps_the_original_state(self):
        """Se o usuário reabrir a barra durante a chamada, o estado guardado não
        pode ser sobrescrito pelo valor de agora."""
        win = self._FakeWindow(revealed=True)
        self.window_cls._collapse_sidebar_for_live(win)
        win.sidebar.revealer.set_reveal_child(True)  # usuário reabriu na chamada
        self.window_cls._collapse_sidebar_for_live(win)
        self.window_cls._restore_sidebar_after_live(win)
        self.assertTrue(win.sidebar.is_revealed)


class SessionLogPlacementTest(LiveWidgetTestCase):
    """O histórico fica ao lado da onda, não empilhado abaixo dela."""

    def _panel(self) -> Gtk.Widget:
        panel = find_by_css_class(self.widget, "live-log-panel")
        self.assertIsNotNone(panel, "o painel do histórico não foi encontrado")
        return panel

    def test_the_panel_is_visible_from_the_start(self):
        self.assertTrue(self.widget.log_scrolled.get_visible())
        self.assertTrue(self._panel().get_visible())

    def test_the_panel_shares_a_horizontal_row_with_the_wave(self):
        panel = self._panel()
        row = panel.get_parent()
        self.assertIsNotNone(row)
        self.assertIs(
            self.widget.stage_box.get_parent(),
            row,
            "a onda e o histórico não estão na mesma linha",
        )
        self.assertEqual(row.get_orientation(), Gtk.Orientation.HORIZONTAL)

    def test_the_panel_has_a_fixed_width(self):
        """Largura fixa: um painel que cresce com o texto faz a onda ao lado
        redimensionar sozinha no meio da fala."""
        width, _height = self._panel().get_size_request()
        self.assertGreater(width, 0)

    def test_the_empty_hint_explains_the_space(self):
        self.assertTrue(self.widget.log_empty_lbl.get_visible())

    def test_the_first_entry_retires_the_empty_hint(self):
        self.widget._ui_on_transcript("user", "Bom dia")
        self.widget._flush_transcript()
        self.assertFalse(self.widget.log_empty_lbl.get_visible())

    def test_the_panel_does_not_hide_when_entries_arrive(self):
        self.widget._ui_on_transcript("user", "Bom dia")
        self.widget._flush_transcript()
        self.assertTrue(self._panel().get_visible())
        self.assertTrue(self.widget.log_scrolled.get_visible())


class RecordingIndicatorTest(LiveWidgetTestCase):
    """O indicador diz se o microfone está captando AGORA."""

    def _transition(self, state: LiveVoiceState) -> None:
        """Transição como o cliente real a faz: ele atualiza o PRÓPRIO estado e
        só então notifica a view. A view lê `client.state`, então o dublê precisa
        acompanhar — senão o teste mediria uma situação que não acontece."""
        self.client.state = state
        self.widget._ui_on_state_change(state, "")

    def test_it_starts_hidden(self):
        self.assertFalse(self.widget.rec_lbl.get_visible())

    def test_it_lights_up_while_listening(self):
        self._transition(LiveVoiceState.LISTENING)
        self.assertTrue(self.widget.rec_lbl.get_visible())

    def test_it_goes_out_while_the_model_speaks(self):
        self._transition(LiveVoiceState.SPEAKING)
        self.assertFalse(self.widget.rec_lbl.get_visible())

    def test_it_goes_out_while_executing(self):
        self._transition(LiveVoiceState.EXECUTING)
        self.assertFalse(self.widget.rec_lbl.get_visible())

    def test_it_goes_out_while_thinking(self):
        self._transition(LiveVoiceState.THINKING)
        self.assertFalse(self.widget.rec_lbl.get_visible())

    def test_it_goes_out_when_the_microphone_is_muted(self):
        self.client.is_muted.return_value = True
        self._transition(LiveVoiceState.LISTENING)
        self.assertFalse(self.widget.rec_lbl.get_visible())

    def test_it_goes_out_on_disconnect(self):
        self._transition(LiveVoiceState.LISTENING)
        self._transition(LiveVoiceState.DISCONNECTED)
        self.assertFalse(self.widget.rec_lbl.get_visible())

    def test_muting_turns_the_indicator_off_without_a_state_change(self):
        """Mutar não muda o estado da sessão — só o do microfone."""
        self._transition(LiveVoiceState.LISTENING)
        self.assertTrue(self.widget.rec_lbl.get_visible())
        self.client.toggle_mute.return_value = True
        self.client.is_muted.return_value = True
        self.widget._on_toggle_mute(None)
        self.assertFalse(self.widget.rec_lbl.get_visible())

    def test_a_broken_client_does_not_claim_capture(self):
        """Sem resposta do cliente, o seguro é ficar apagado."""
        self.client.is_muted.side_effect = RuntimeError("cliente morto")
        self.widget._sync_recording_indicator()
        self.assertFalse(self.widget.rec_lbl.get_visible())

    def test_it_blinks_with_a_timer_while_capturing(self):
        self._transition(LiveVoiceState.LISTENING)
        self.assertIsNotNone(
            self.widget._rec_blink_id,
            "o indicador deveria ter um timer de piscada",
        )

    def test_the_blink_timer_stops_when_capture_stops(self):
        self._transition(LiveVoiceState.LISTENING)
        self._transition(LiveVoiceState.SPEAKING)
        self.assertIsNone(self.widget._rec_blink_id)

    def test_reduced_motion_leaves_the_indicator_steady(self):
        """O GTK4 não tem `@keyframes`: a piscada é Python, então a preferência
        de movimento reduzido tem de ser consultada AQUI também — senão ela
        valeria só para o que o CSS alcança."""
        with patch(
            "zorin_copilot.ui.live_view.reduced_motion_enabled", return_value=True
        ):
            self._transition(LiveVoiceState.LISTENING)
        self.assertTrue(self.widget.rec_lbl.get_visible())
        self.assertIsNone(
            self.widget._rec_blink_id,
            "com movimento reduzido o indicador não pode piscar",
        )

    def test_the_blink_actually_toggles_the_class(self):
        self._transition(LiveVoiceState.LISTENING)
        self.widget._on_record_blink()
        self.assertTrue(self.widget.rec_lbl.has_css_class("live-recording-dim"))
        self.widget._on_record_blink()
        self.assertFalse(self.widget.rec_lbl.has_css_class("live-recording-dim"))

    def test_the_blink_timer_dies_with_the_widget(self):
        """Fora da árvore o timer tem de se encerrar, senão vaza."""
        self._transition(LiveVoiceState.LISTENING)
        self.window.set_child(None)
        self.assertEqual(self.widget._on_record_blink(), GLib.SOURCE_REMOVE)
        self.assertIsNone(self.widget._rec_blink_id)


class EndButtonPlacementTest(LiveWidgetTestCase):
    """O destrutivo não pode ficar vizinho de um controle de transmissão."""

    def _dock(self) -> Gtk.Widget:
        dock = find_by_css_class(self.widget, "call-control-dock")
        self.assertIsNotNone(dock)
        return dock

    def test_the_end_button_is_the_last_control(self):
        dock = self._dock()
        last = dock.get_last_child()
        self.assertIsNotNone(last)
        # `descendants()` não inclui o próprio widget, então o botão tem de ser
        # conferido como o último filho — não procurado abaixo dele.
        self.assertIsInstance(last, Gtk.Button)
        self.assertTrue(
            last.has_css_class("call-end-btn"),
            "o último controle do dock deveria ser o botão Encerrar",
        )
        self.assertTrue(last.has_css_class("destructive-action"))

    def test_a_divider_separates_it_from_the_transmission_controls(self):
        dock = self._dock()
        last = dock.get_last_child()
        previous = last.get_prev_sibling()
        self.assertIsInstance(
            previous,
            Gtk.Separator,
            "falta o divisor entre os controles de transmissão e o Encerrar",
        )

    def test_the_divider_has_a_style_rule(self):
        from zorin_copilot.ui.style import GLASS_CSS
        self.assertIn(".call-control-divider", GLASS_CSS)


class LiveStyleRuleTest(unittest.TestCase):
    """Classes novas precisam de regra na folha.

    Lição já paga neste projeto: uma classe sem regra é uma mentira silenciosa —
    o widget parece estilizado e não está.
    """

    def setUp(self):
        from zorin_copilot.ui.style import GLASS_CSS
        self.css = GLASS_CSS

    def test_the_log_panel_is_styled_in_both_themes(self):
        self.assertIn(".live-log-panel", self.css)
        self.assertIn("window.light-glass .live-log-panel", self.css)
        self.assertIn("window.dark-glass .live-log-panel", self.css)

    def test_the_recording_indicator_is_styled(self):
        self.assertIn(".live-recording", self.css)
        self.assertIn(".live-recording-dim", self.css)

    def test_the_recording_indicator_has_no_hover_state(self):
        """É estado, não controle: reagir ao ponteiro prometeria um clique."""
        self.assertNotIn(".live-recording:hover", self.css)


if __name__ == "__main__":
    unittest.main()
