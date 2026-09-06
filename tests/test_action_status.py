"""Testes para o rastro de execução das ações propostas (item 2.8 do plano de UI).

Cobrem o registro de resultados, a identidade estável dos turnos e — principalmente —
o fato de que uma falha passa a ficar visível na linha da ação, em vez de desaparecer
em um toast que some.
"""

import os
import sys
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction  # noqa: E402
from zorin_copilot.core.session import ChatTurn  # noqa: E402
from zorin_copilot.shell.action_status import ActionOutcome, ActionOutcomeRegistry  # noqa: E402
from zorin_copilot.shell.executor import ExecutionReport  # noqa: E402
from zorin_copilot.ui.app import CopilotWindow  # noqa: E402


class FakeExecutor:
    """Executor determinístico: consome os resultados pré-programados como uma fila.

    Comportamento de fila é o que permite simular uma nova tentativa devolvendo um
    resultado diferente do primeiro.
    """

    def __init__(self, results):
        self.queue = list(results)
        self.plans = []

    def execute_plan(self, plan, dry_run=False):
        self.plans.append(plan)
        reports = []
        for action in plan.actions:
            ok, message = self.queue.pop(0) if self.queue else (False, "sem resultado")
            reports.append(ExecutionReport(action=action, success=ok, message=message))
        return reports


class FakeMemory:
    """Substituto do MemoryManager que só registra as chamadas de log_action."""

    def __init__(self):
        self.logged = []

    def log_action(self, **kwargs):
        self.logged.append(kwargs)


class ActionOutcomeRegistryTest(unittest.TestCase):
    def test_key_is_deterministic(self):
        """A mesma (turno, índice, ação) sempre gera a mesma chave."""
        action = DesktopAction(ActionType.LAUNCH_APP, "nautilus")
        k1 = ActionOutcomeRegistry.make_key("abc", 0, action)
        k2 = ActionOutcomeRegistry.make_key("abc", 0, action)
        self.assertEqual(k1, k2)

    def test_key_distinguishes_turn_and_position(self):
        action = DesktopAction(ActionType.LAUNCH_APP, "nautilus")
        self.assertNotEqual(
            ActionOutcomeRegistry.make_key("turn-a", 0, action),
            ActionOutcomeRegistry.make_key("turn-b", 0, action),
        )
        self.assertNotEqual(
            ActionOutcomeRegistry.make_key("turn-a", 0, action),
            ActionOutcomeRegistry.make_key("turn-a", 1, action),
        )
        other = DesktopAction(ActionType.OPEN_URL, "https://example.com")
        self.assertNotEqual(
            ActionOutcomeRegistry.make_key("turn-a", 0, action),
            ActionOutcomeRegistry.make_key("turn-a", 0, other),
        )

    def test_record_and_read_back(self):
        registry = ActionOutcomeRegistry()
        outcome = ActionOutcome(success=False, message="boom")
        registry.record("k", outcome)
        self.assertEqual(len(registry), 1)
        self.assertEqual(registry.get("k"), outcome)
        self.assertFalse(registry.get("k").success)

    def test_unknown_key_returns_none(self):
        self.assertIsNone(ActionOutcomeRegistry().get("nao-existe"))

    def test_clear(self):
        registry = ActionOutcomeRegistry()
        registry.record("k", ActionOutcome(success=True, message="ok"))
        registry.clear()
        self.assertEqual(len(registry), 0)

    def test_from_report_copies_success_and_message(self):
        action = DesktopAction(ActionType.OPEN_URL, "https://example.com")
        report = ExecutionReport(action=action, success=False, message="sem rede")
        outcome = ActionOutcome.from_report(report)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.message, "sem rede")


class ChatTurnIdentityTest(unittest.TestCase):
    def test_turns_have_distinct_ids(self):
        self.assertNotEqual(ChatTurn("a", "b").id, ChatTurn("a", "b").id)

    def test_id_survives_roundtrip(self):
        turn = ChatTurn(prompt="p", answer="a")
        restored = ChatTurn.from_dict(turn.to_dict())
        self.assertEqual(restored.id, turn.id)

    def test_legacy_payload_without_id_gets_one(self):
        restored = ChatTurn.from_dict({"prompt": "p", "answer": "a", "timestamp": 1.0})
        self.assertTrue(restored.id)


class ActionFeedbackTest(unittest.TestCase):
    """A linha da ação precisa mostrar o que aconteceu — inclusive quando falha."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.action_status")

    def setUp(self):
        self.win = CopilotWindow(self.app)
        self.memory = FakeMemory()
        self.win.engine.memory = self.memory

    def _plan(self, *targets):
        return ActionPlan(
            thought="Plano de teste",
            actions=[DesktopAction(ActionType.LAUNCH_APP, t) for t in targets],
        )

    def _handles(self, plan, turn):
        return [
            self.win.chat_stream._create_action_handle(a, turn, i)
            for i, a in enumerate(plan.actions)
        ]

    # -- execução individual -------------------------------------------------

    def test_success_updates_button_and_subtitle(self):
        turn = ChatTurn(prompt="abrir", answer="ok")
        plan = self._plan("nautilus")
        self.win.executor = FakeExecutor([(True, "Aplicativo iniciado.")])

        handle = self._handles(plan, turn)[0]
        handle.button.emit("clicked")

        self.assertEqual(handle.button.get_label(), "Executado ✓")
        self.assertIn("Aplicativo iniciado.", handle.row.get_subtitle())
        self.assertTrue(handle.row.has_css_class("action-done"))
        self.assertFalse(handle.row.has_css_class("action-failed"))

    def test_failure_shows_message_and_offers_retry(self):
        """O ponto central do item 2.8: a falha não pode sumir do histórico."""
        turn = ChatTurn(prompt="abrir", answer="ok")
        plan = self._plan("steam")
        self.win.executor = FakeExecutor([(False, "Aplicativo 'steam' não encontrado.")])

        handle = self._handles(plan, turn)[0]
        handle.button.emit("clicked")

        self.assertEqual(handle.button.get_label(), "Tentar novamente")
        # Continua clicável para nova tentativa.
        self.assertTrue(handle.button.get_sensitive())
        self.assertIn("não encontrado", handle.row.get_subtitle())
        self.assertTrue(handle.row.has_css_class("action-failed"))
        self.assertTrue(handle.button.has_css_class("destructive-action"))

    def test_failure_is_logged_to_memory(self):
        turn = ChatTurn(prompt="abrir steam", answer="ok")
        plan = self._plan("steam")
        self.win.executor = FakeExecutor([(False, "falhou")])

        self._handles(plan, turn)[0].button.emit("clicked")

        self.assertEqual(len(self.memory.logged), 1)
        self.assertFalse(self.memory.logged[0]["success"])
        self.assertEqual(self.memory.logged[0]["prompt"], "abrir steam")

    def test_retry_does_not_stack_subtitle_lines(self):
        turn = ChatTurn(prompt="abrir", answer="ok")
        plan = self._plan("steam")
        self.win.executor = FakeExecutor([(False, "falha 1"), (False, "falha 2")])

        handle = self._handles(plan, turn)[0]
        handle.button.emit("clicked")
        handle.button.emit("clicked")

        subtitle = handle.row.get_subtitle()
        self.assertIn("falha 2", subtitle)
        self.assertNotIn("falha 1", subtitle)

    def test_executor_without_report_is_treated_as_failure(self):
        turn = ChatTurn(prompt="abrir", answer="ok")
        plan = self._plan("steam")
        self.win.executor = FakeExecutor([])  # nenhum relatório

        handle = self._handles(plan, turn)[0]
        handle.button.emit("clicked")

        self.assertEqual(handle.button.get_label(), "Tentar novamente")
        self.assertTrue(handle.row.has_css_class("action-failed"))

    # -- executar todas ------------------------------------------------------

    def test_execute_all_reports_partial_failure(self):
        """Antes o botão dizia 'Todas Executadas ✓' mesmo com falhas."""
        turn = ChatTurn(prompt="varios", answer="ok")
        plan = self._plan("nautilus", "steam", "gedit")
        self.win.executor = FakeExecutor(
            [(True, "ok"), (False, "não encontrado"), (True, "ok")]
        )
        handles = self._handles(plan, turn)
        exec_all = self.win.chat_stream._build_execute_all_button(plan, turn, handles)

        exec_all.emit("clicked")

        label = exec_all.get_label()
        self.assertIn("2 ok", label)
        self.assertIn("1 falharam", label)
        self.assertNotIn("Todas", label)
        self.assertTrue(exec_all.has_css_class("destructive-action"))

    def test_execute_all_success_still_confirms(self):
        turn = ChatTurn(prompt="varios", answer="ok")
        plan = self._plan("nautilus", "gedit")
        self.win.executor = FakeExecutor([(True, "ok"), (True, "ok")])
        handles = self._handles(plan, turn)
        exec_all = self.win.chat_stream._build_execute_all_button(plan, turn, handles)

        exec_all.emit("clicked")

        self.assertIn("Todas executadas", exec_all.get_label())
        self.assertFalse(exec_all.has_css_class("destructive-action"))

    def test_execute_all_updates_every_row(self):
        turn = ChatTurn(prompt="varios", answer="ok")
        plan = self._plan("nautilus", "steam")
        self.win.executor = FakeExecutor([(True, "ok"), (False, "falhou")])
        handles = self._handles(plan, turn)
        exec_all = self.win.chat_stream._build_execute_all_button(plan, turn, handles)

        exec_all.emit("clicked")

        self.assertEqual(handles[0].button.get_label(), "Executado ✓")
        self.assertEqual(handles[1].button.get_label(), "Tentar novamente")
        self.assertIn("falhou", handles[1].row.get_subtitle())

    # -- persistência entre reconstruções ------------------------------------

    def test_outcome_survives_stream_rebuild(self):
        """Trocar de tópico e voltar não pode apagar o rastro da execução."""
        turn = ChatTurn(prompt="abrir", answer="ok")
        plan = self._plan("steam")
        self.win.executor = FakeExecutor([(False, "não encontrado")])

        handle = self._handles(plan, turn)[0]
        handle.button.emit("clicked")

        # Reconstrói o fluxo a partir do zero, como uma troca de tópico faria.
        self.win.session.turns.clear()
        self.win.session.turns.append(turn)
        self.win.current_plan = plan
        self.win._rebuild_chat_stream()

        rebuilt = self._find_action_rows()
        self.assertEqual(len(rebuilt), 1)
        self.assertTrue(rebuilt[0].has_css_class("action-failed"))
        self.assertIn("não encontrado", rebuilt[0].get_subtitle())

    def _find_action_rows(self):
        found = []

        def walk(widget):
            while widget is not None:
                if isinstance(widget, Adw.ActionRow):
                    found.append(widget)
                if isinstance(widget, Gtk.Box):
                    walk(widget.get_first_child())
                widget = widget.get_next_sibling()

        walk(self.win.chat_stream_box.get_first_child())
        return found


if __name__ == "__main__":
    unittest.main()
