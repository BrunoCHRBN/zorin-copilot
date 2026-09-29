"""Testes da barra de prompt — hierarquia dos controles e atalhos (§1.4).

Cobrem a reformulação §1.4 do plano (`docs/PROPOSTA_REFORMULACAO_GUI.md`):

- a barra abria com **três botões-fantasma circulares idênticos** (câmera,
  clipboard e o modo agente). Dois são ações de entrada; o terceiro é um modo
  que muda o significado do Enter. Dividir a mesma silhueta apagava a hierarquia
  (§0.1). Agora o modo é um chip rotulado, e o corredor de ícones tem dois;
- o botão de enviar não dizia o atalho. O tooltip passou a citar os dois que
  enviam de verdade — e `Ctrl+Enter` virou ligação real, para o tooltip não
  prometer o que não existe.
"""

import os
import sys
import unittest
from unittest.mock import patch

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

try:  # descoberta como pacote (`-t .`) vs. descoberta simples
    from tests._gtk_helpers import first_controller
except ImportError:  # pragma: no cover - depende do runner
    from _gtk_helpers import first_controller

from zorin_copilot.ui.app import CopilotWindow  # noqa: E402


def _siblings(box: Gtk.Box) -> list[Gtk.Widget]:
    """Filhos diretos de `box`, na ordem visual."""
    out: list[Gtk.Widget] = []
    child = box.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def _index_of(widgets: list[Gtk.Widget], target: Gtk.Widget) -> int:
    for index, widget in enumerate(widgets):
        if widget is target:
            return index
    raise AssertionError("widget não está na lista")


class PromptBarHierarchyTest(unittest.TestCase):
    """Comando e estado não podem dividir a mesma forma (§0.1 / §1.4)."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.promptbar")

    def setUp(self):
        self.win = CopilotWindow(self.app)

    def tearDown(self):
        self.win.destroy()

    def _leading_controls(self) -> list[Gtk.Widget]:
        """Os controles antes do campo de texto — o antigo 'corredor de ícones'."""
        leading: list[Gtk.Widget] = []
        for child in _siblings(self.win.prompt_bar_box):
            if child is self.win.entry:
                break
            leading.append(child)
        return leading

    # -- ordem -------------------------------------------------------------
    def test_camera_comes_first(self):
        """A leitura de tela é a capacidade mais distintiva do app: vem primeiro."""
        widgets = _siblings(self.win.prompt_bar_box)
        self.assertIs(widgets[0], self.win.vision_btn)

    def test_clipboard_comes_second(self):
        widgets = _siblings(self.win.prompt_bar_box)
        self.assertIs(widgets[1], self.win.clipboard_btn)

    def test_entry_comes_after_both_input_actions(self):
        widgets = _siblings(self.win.prompt_bar_box)
        entry_index = _index_of(widgets, self.win.entry)
        self.assertLess(_index_of(widgets, self.win.vision_btn), entry_index)
        self.assertLess(_index_of(widgets, self.win.clipboard_btn), entry_index)

    def test_submit_is_last(self):
        """O envio fecha a barra — nada depois dele compete por atenção."""
        widgets = _siblings(self.win.prompt_bar_box)
        self.assertIs(widgets[-1], self.win.submit_btn)

    # -- a hierarquia propriamente dita ------------------------------------
    def test_only_two_ghost_icon_buttons_before_the_entry(self):
        """O ponto do §1.4: o corredor de ícones tinha três, todos iguais.

        `glass-icon-btn` é a classe do botão-fantasma circular. Contar pela
        classe, e não pelo atributo, é o que captura um quarto ícone que alguém
        venha a acrescentar no futuro.
        """
        ghost_icons = [
            widget
            for widget in self._leading_controls()
            if widget.has_css_class("glass-icon-btn")
        ]
        self.assertEqual(
            ghost_icons,
            [self.win.vision_btn, self.win.clipboard_btn],
            "o corredor de ícones deve ter exatamente câmera e clipboard",
        )

    def test_agent_control_is_not_a_third_ghost_icon(self):
        """O modo agente saiu da forma dos botões de ação."""
        agent_btn = self.win.prompt_bar.agent_btn
        self.assertFalse(agent_btn.has_css_class("glass-icon-btn"))
        self.assertFalse(agent_btn.has_css_class("circular"))

    def test_agent_control_is_labelled(self):
        """Um terceiro glifo indistinguível não dizia o que o controle é.

        O rótulo diz. E o ícone saiu de propósito: era exatamente o que fazia os
        três controles se confundirem.
        """
        agent_btn = self.win.prompt_bar.agent_btn
        self.assertEqual(agent_btn.get_label(), "Agente")
        self.assertIsNone(agent_btn.get_icon_name())

    def test_agent_control_is_still_a_toggle(self):
        """O modo continua alternável — só mudou de forma, não de função."""
        agent_btn = self.win.prompt_bar.agent_btn
        self.assertIsInstance(agent_btn, Gtk.ToggleButton)
        self.assertTrue(agent_btn.has_css_class("mode-chip"))

    # -- estado do modo ----------------------------------------------------
    def test_active_mode_is_visible_on_the_chip(self):
        agent_btn = self.win.prompt_bar.agent_btn

        agent_btn.set_active(True)
        self.assertTrue(agent_btn.has_css_class("mode-chip-active"))

        agent_btn.set_active(False)
        self.assertFalse(agent_btn.has_css_class("mode-chip-active"))

    def test_mode_is_reflected_in_the_placeholder(self):
        """Feedback no lugar onde o modo age: o convite do campo muda."""
        agent_btn = self.win.prompt_bar.agent_btn
        default = self.win.entry.get_placeholder_text()

        agent_btn.set_active(True)
        self.assertIn("Agente", self.win.entry.get_placeholder_text())
        self.assertNotEqual(self.win.entry.get_placeholder_text(), default)

        agent_btn.set_active(False)
        self.assertEqual(self.win.entry.get_placeholder_text(), default)

    def test_mode_reaches_the_context_flag(self):
        self.win.prompt_bar.agent_btn.set_active(True)

        self.assertTrue(self.win.agent_mode_active)

    def test_mode_stays_reachable_from_the_command_palette(self):
        """Regressão: o chip não é o único caminho para o modo.

        `Ctrl+K` mantém a entrada "Alternar Modo Agente", e o handler precisa
        continuar ligado — senão mexer no chip quebraria a descoberta do modo.
        """
        handlers = self.win._palette_handlers()
        self.assertIn("app.toggle-agent", handlers)

        self.assertFalse(self.win.prompt_bar.agent_btn.get_active())
        handlers["app.toggle-agent"]()
        self.assertTrue(self.win.prompt_bar.agent_btn.get_active())

    def test_agent_chip_is_disabled_while_busy(self):
        self.win.prompt_bar.set_busy(True)
        self.assertFalse(self.win.prompt_bar.agent_btn.get_sensitive())

        self.win.prompt_bar.set_busy(False)
        self.assertTrue(self.win.prompt_bar.agent_btn.get_sensitive())


class SubmitShortcutTest(unittest.TestCase):
    """O tooltip só pode citar atalhos que funcionam."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.submit")

    def setUp(self):
        self.win = CopilotWindow(self.app)
        self.prompt_bar = self.win.prompt_bar
        self.controller = first_controller(self.win.entry, Gtk.EventControllerKey)
        self.assertIsNotNone(self.controller, "o campo precisa do controlador de teclas")

    def tearDown(self):
        self.win.destroy()

    def _press(self, keyval: int, state: int = 0) -> bool:
        """Emite `key-pressed` e devolve se o evento foi consumido."""
        return bool(self.controller.emit("key-pressed", keyval, 0, state))

    # -- tooltip -----------------------------------------------------------
    def test_tooltip_names_both_working_shortcuts(self):
        tooltip = self.prompt_bar.submit_btn.get_tooltip_text()
        self.assertIn("Enter", tooltip)
        self.assertIn("Ctrl+Enter", tooltip)

    # -- Ctrl+Enter --------------------------------------------------------
    def test_ctrl_enter_sends_and_is_consumed(self):
        with patch.object(type(self.prompt_bar), "submit") as submit:
            consumed = self._press(Gdk.KEY_Return, Gdk.ModifierType.CONTROL_MASK)

        self.assertTrue(consumed, "o controlador deve consumir o evento")
        submit.assert_called_once()

    def test_ctrl_enter_on_the_keypad_sends_too(self):
        """Quem usa o teclado numérico tem o mesmo atalho."""
        with patch.object(type(self.prompt_bar), "submit") as submit:
            consumed = self._press(Gdk.KEY_KP_Enter, Gdk.ModifierType.CONTROL_MASK)

        self.assertTrue(consumed)
        submit.assert_called_once()

    def test_plain_enter_is_left_to_the_entry(self):
        """Enter simples continua sendo tratado pelo `activate` do `Gtk.Entry`.

        O controlador não pode consumi-lo: se consumisse, o envio por Enter —
        que já existia — deixaria de funcionar.
        """
        with patch.object(type(self.prompt_bar), "submit") as submit:
            consumed = self._press(Gdk.KEY_Return)

        self.assertFalse(consumed)
        submit.assert_not_called()

    def test_ctrl_with_another_key_does_nothing(self):
        with patch.object(type(self.prompt_bar), "submit") as submit:
            consumed = self._press(Gdk.KEY_a, Gdk.ModifierType.CONTROL_MASK)

        self.assertFalse(consumed)
        submit.assert_not_called()

    def test_controller_runs_in_capture_phase(self):
        """Guardado de propósito: em fase BUBBLE o mesmo Return poderia virar dois envios.

        Na fase padrão o tratamento interno do `Gtk.Entry` roda antes do
        controlador, e o retorno `True` já não impediria o `activate` de
        disparar — o `submit` rodaria duas vezes. Em CAPTURE o evento é
        consumido antes de chegar ao campo.
        """
        self.assertEqual(
            self.controller.get_propagation_phase(), Gtk.PropagationPhase.CAPTURE
        )


if __name__ == "__main__":
    unittest.main()
