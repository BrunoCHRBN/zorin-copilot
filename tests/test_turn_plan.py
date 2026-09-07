"""Persistência do plano de ações por turno (item B do Sprint 4).

O bug: `chat_stream.rebuild()` passava `ctx.current_plan` apenas para o último
turno, então os botões de ação das respostas anteriores desapareciam sempre que
o fluxo era reconstruído (troca de tópico, desfazer ação, reabrir conversa).
O plano agora mora no turno e é serializado junto com a sessão.
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction  # noqa: E402
from zorin_copilot.core.session import ChatTurn, TopicSession  # noqa: E402


def _plan(*actions: DesktopAction) -> ActionPlan:
    return ActionPlan(thought="pensei aqui", actions=list(actions))


def _launch(app: str) -> DesktopAction:
    return DesktopAction(action_type=ActionType.LAUNCH_APP, target=app)


class TurnPlanStorageTest(unittest.TestCase):
    def test_record_turn_stores_plan(self):
        session = TopicSession(auto_persist=True)
        plan = _plan(_launch("Firefox"))
        turn = session.record_turn("abra o firefox", "certo", plan=plan)
        self.assertIs(turn.plan, plan)

    def test_record_turn_without_plan_leaves_none(self):
        session = TopicSession(auto_persist=True)
        self.assertIsNone(session.record_turn("oi", "olá").plan)

    def test_plan_travels_with_unpinned_turn(self):
        """O turno temporário (ainda não fixado) também precisa do plano."""
        session = TopicSession()
        plan = _plan(_launch("Calculator"))
        turn = session.record_turn("abra a calculadora", "ok", plan=plan)
        self.assertIs(session._last_unpinned_turn, turn)
        self.assertIs(turn.plan, plan)

    def test_turn_equality_ignores_plan(self):
        """Dois turnos iguais no texto continuam iguais mesmo com planos distintos.

        Isso importa porque `TopicSession.pin()` usa `turn not in self.turns`
        (igualdade do dataclass) para decidir se incorpora o turno pendente.
        """
        turn_id = "mesmo-id"
        with_plan = ChatTurn("p", "r", timestamp=1.0, id=turn_id, plan=_plan(_launch("Firefox")))
        without_plan = ChatTurn("p", "r", timestamp=1.0, id=turn_id)
        self.assertEqual(without_plan, with_plan)
        self.assertNotEqual(without_plan, ChatTurn("p", "outra", timestamp=1.0, id=turn_id, plan=None))


class TurnPlanRoundtripTest(unittest.TestCase):
    def test_roundtrip_preserves_actions(self):
        plan = _plan(_launch("Firefox"), DesktopAction(action_type=ActionType.ANSWER, target="ok"))
        turn = ChatTurn("p", "r", plan=plan)
        restored = ChatTurn.from_dict(turn.to_dict())
        self.assertEqual([a.target for a in restored.plan.actions], ["Firefox", "ok"])
        self.assertEqual(restored.plan.thought, "pensei aqui")

    def test_roundtrip_preserves_params_and_confirmation(self):
        action = DesktopAction(
            action_type=ActionType.WRITE_FILE,
            target="nota.md",
            params={"filename": "nota.md", "content": "olá"},
            requires_confirmation=True,
        )
        restored = ChatTurn.from_dict(ChatTurn("p", "r", plan=_plan(action)).to_dict())
        restored_action = restored.plan.actions[0]
        self.assertEqual(restored_action.params, {"filename": "nota.md", "content": "olá"})
        self.assertTrue(restored_action.requires_confirmation)

    def test_empty_plan_is_not_persisted(self):
        self.assertIsNone(ChatTurn("p", "r", plan=ActionPlan(thought="")).to_dict()["plan"])

    def test_missing_plan_key_is_tolerated(self):
        """Sessões salvas antes deste campo precisam continuar abrindo."""
        self.assertIsNone(ChatTurn.from_dict({"prompt": "p", "answer": "r"}).plan)

    def test_unknown_action_type_is_skipped_not_fatal(self):
        data = {"prompt": "p", "answer": "r", "plan": {"thought": "x", "actions": [{"action_type": "voar"}]}}
        restored = ChatTurn.from_dict(data)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.plan.actions, [])

    def test_corrupted_plan_shape_is_tolerated(self):
        self.assertIsNone(ChatTurn.from_dict({"prompt": "p", "answer": "r", "plan": "lixo"}).plan)


class SessionPersistenceTest(unittest.TestCase):
    def _roundtrip(self, session: TopicSession) -> TopicSession:
        restored = TopicSession()
        restored.load_from_dict(session.to_dict())
        return restored

    def test_plans_survive_reload_for_every_turn(self):
        session = TopicSession(title="Tópico", auto_persist=True)
        session.record_turn("primeira", "r1", plan=_plan(_launch("Firefox")))
        session.record_turn("segunda", "r2", plan=_plan(_launch("Terminal")))

        turns = self._roundtrip(session).turns
        self.assertEqual([t.plan.actions[0].target for t in turns], ["Firefox", "Terminal"])

    def test_action_counting_after_reload(self):
        """O card de ações mostra a contagem; ela tem de bater após recarregar."""
        session = TopicSession(auto_persist=True)
        session.record_turn("p", "r", plan=_plan(_launch("A"), _launch("B"), _launch("C")))
        restored_turn = self._roundtrip(session).turns[0]
        self.assertEqual(len([a for a in restored_turn.plan.actions if a.action_type != ActionType.ANSWER]), 3)


if __name__ == "__main__":
    unittest.main()
