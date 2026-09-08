# Decisão de design: suíte do Sprint 6 cobre o indicador de digitação animado (item #6)
# e o diálogo de comparação side-by-side (item #5). Roda sob xvfb como o resto da UI.

"""Testes do indicador de digitação e do diálogo de comparação de respostas."""

from __future__ import annotations

import os
import sys
import unittest

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw  # noqa: E402

Adw.init()

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction  # noqa: E402
from zorin_copilot.core.session import ChatTurn  # noqa: E402
from zorin_copilot.ui.app import CopilotWindow  # noqa: E402
from zorin_copilot.ui.widgets.chat_stream import TypingIndicator  # noqa: E402
from zorin_copilot.ui.widgets.compare_dialog import CompareResponsesDialog  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def walk(widget):
    """Gera todos os widgets na árvore a partir de `widget` (inclusive)."""
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


def find_by_type(root, cls):
    return [w for w in walk(root) if isinstance(w, cls)]


def find_button_with_label(root, label):
    from gi.repository import Gtk

    return [w for w in walk(root) if isinstance(w, Gtk.Button) and w.get_label() == label]


def _answered_turn(prompt, answer, with_plan=False):
    plan = None
    if with_plan:
        plan = ActionPlan(
            thought=answer, actions=[DesktopAction(ActionType.ANSWER, answer)]
        )
    return ChatTurn(prompt=prompt, answer=answer, plan=plan)


class TypingIndicatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.typing")

    def setUp(self):
        self.win = CopilotWindow(self.app)

    def test_pending_turn_shows_typing_indicator(self):
        from zorin_copilot.core.session import ChatTurn

        turn = ChatTurn(prompt="Qual o clima?", answer="")
        widget = self.win.chat_stream.create_turn_widget(turn, is_pending=True)
        indicators = find_by_type(widget, TypingIndicator)
        self.assertEqual(len(indicators), 1)
        self.assertEqual(len(indicators[0].dots), 3)
        # Para o timer do indicador (o widget de teste não é realizado).
        indicators[0]._on_unrealize()
        widget.unparent()

    def test_typing_indicator_stops_on_unrealize(self):
        ind = TypingIndicator()
        self.assertIsNotNone(ind._timer)
        # O sinal "unrealize" só emite num widget realizado; testamos o handler direto.
        ind._on_unrealize()
        self.assertIsNone(ind._timer)


class CompareButtonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.cmpbtn")

    def setUp(self):
        self.win = CopilotWindow(self.app)

    def test_compare_button_hidden_with_one_answer(self):
        self.win.session.turns = [_answered_turn("Pergunta única", "Resposta única")]
        turn = _answered_turn("Outra", "Outra resposta")
        widget = self.win.chat_stream.create_turn_widget(turn)
        self.assertEqual(find_button_with_label(widget, "Comparar"), [])

    def test_compare_button_shown_with_two_answers(self):
        self.win.session.turns = [
            _answered_turn("Primeira pergunta", "Primeira resposta"),
            _answered_turn("Segunda pergunta", "Segunda resposta"),
        ]
        turn = _answered_turn("Terceira", "Terceira resposta", with_plan=True)
        widget = self.win.chat_stream.create_turn_widget(turn)
        btns = find_button_with_label(widget, "Comparar")
        self.assertEqual(len(btns), 1)
        widget.unparent()


class CompareDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.cmpdlg")

    def setUp(self):
        self.win = CopilotWindow(self.app)

    def _seed_two(self):
        self.win.session.turns = [
            _answered_turn("O que é foo?", "Foo é bar.", with_plan=True),
            _answered_turn("Explique baz", "Baz é qux."),
        ]

    def test_empty_state_without_enough_answers(self):
        dlg = CompareResponsesDialog(self.win)
        self.assertEqual(len(dlg.candidates), 0)

    def test_dialog_lists_candidates_and_renders_panes(self):
        self._seed_two()
        anchor = self.win.session.turns[1]
        dlg = CompareResponsesDialog(self.win, anchor)

        self.assertEqual(len(dlg.candidates), 2)
        # Painel A pré-seleciona o turno âncora.
        self.assertEqual(dlg.candidates[dlg.drop_a.get_selected()], anchor)

        # Cada painel deve ter conteúdo renderizado (prompt + separador + resposta).
        self.assertIsNotNone(dlg.pane_a._content_box.get_first_child())
        self.assertIsNotNone(dlg.pane_b._content_box.get_first_child())

    def test_switching_selection_rerenders_panes(self):
        self._seed_two()
        dlg = CompareResponsesDialog(self.win)
        # Trocar a seleção dos dropdowns não deve quebrar e deve repovoar os painéis.
        dlg.drop_a.set_selected(1)
        dlg.drop_b.set_selected(0)
        self.assertIsNotNone(dlg.pane_a._content_box.get_first_child())
        self.assertIsNotNone(dlg.pane_b._content_box.get_first_child())


if __name__ == "__main__":
    unittest.main()
