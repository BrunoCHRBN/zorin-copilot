"""Persistência do SolutionGraph por turno (botão "Ver no Quadro Negro").

O botão do turno de chat só existe se o `ChatTurn` carregar o grafo de solução.
Como o grafo mora no turno (e não no estado da janela), ele precisa sobreviver
à serialização da sessão — igual ao plano de ações (ver `test_turn_plan.py`).
Estes testes são determinísticos: não tocam GTK, rede nem LLM.
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.ai.math_solver import (  # noqa: E402
    SolutionGraph,
    build_quadratic_sample,
    solution_graph_from_dict,
    try_solve,
)
from zorin_copilot.core.session import ChatTurn, TopicSession  # noqa: E402


class TurnSolutionStorageTest(unittest.TestCase):
    def test_turn_defaults_to_no_solution(self):
        self.assertIsNone(ChatTurn("p", "r").solution)

    def test_turn_keeps_solution_object(self):
        graph = build_quadratic_sample()
        turn = ChatTurn("p", "r", solution=graph)
        self.assertIs(turn.solution, graph)

    def test_turn_equality_ignores_solution(self):
        """Igualdade por texto continua valendo mesmo com grafos diferentes.

        `TopicSession.pin()` usa `turn not in self.turns`; o grafo não deve
        interferir nessa comparação.
        """
        turn_id = "mesmo-id"
        with_solution = ChatTurn("p", "r", timestamp=1.0, id=turn_id, solution=build_quadratic_sample())
        without = ChatTurn("p", "r", timestamp=1.0, id=turn_id)
        self.assertEqual(without, with_solution)


class TurnSolutionRoundtripTest(unittest.TestCase):
    def test_roundtrip_preserves_graph(self):
        graph = try_solve("x + y = 10; x - y = 2")
        self.assertIsInstance(graph, SolutionGraph)
        turn = ChatTurn("resolva o sistema", "S = {6, 4}", solution=graph)
        restored = ChatTurn.from_dict(turn.to_dict())
        self.assertIsNotNone(restored.solution)
        self.assertEqual(restored.solution.answer, "S = {6, 4}")
        # Os passos voltam como objetos (não dicts) — exigência do Quadro Negro.
        for m in restored.solution.methods:
            for s in m.steps:
                self.assertTrue(s.id)

    def test_turn_without_solution_has_no_key(self):
        self.assertNotIn("solution", ChatTurn("p", "r").to_dict())

    def test_solution_problem_defaults_empty(self):
        self.assertEqual(ChatTurn("p", "r").solution_problem, "")

    def test_roundtrip_preserves_solution_problem(self):
        turn = ChatTurn("derivada", "3x^2", solution_problem="calcule a derivada de x^3")
        restored = ChatTurn.from_dict(turn.to_dict())
        self.assertEqual(restored.solution_problem, "calcule a derivada de x^3")

    def test_turn_without_solution_problem_has_no_key(self):
        self.assertNotIn("solution_problem", ChatTurn("p", "r").to_dict())

    def test_missing_solution_key_is_tolerated(self):
        """Turnos salvos antes deste campo precisam continuar abrindo."""
        self.assertIsNone(ChatTurn.from_dict({"prompt": "p", "answer": "r"}).solution)

    def test_corrupted_solution_shape_is_tolerated(self):
        restored = ChatTurn.from_dict({"prompt": "p", "answer": "r", "solution": "lixo"})
        self.assertIsNone(restored.solution)

    def test_solution_roundtrips_within_session(self):
        session = TopicSession(title="Estudos", auto_persist=True)
        session.record_turn("resolva x^2 - 5x + 6 = 0", "raízes 2 e 3", solution=try_solve("x^2 - 5x + 6 = 0"))
        restored = TopicSession()
        restored.load_from_dict(session.to_dict())
        self.assertEqual(restored.turns[0].solution.answer, "S = {2, 3}")


class SessionRecordTurnSolutionTest(unittest.TestCase):
    def test_record_turn_accepts_solution(self):
        session = TopicSession(auto_persist=True)
        graph = solution_graph_from_dict(build_quadratic_sample().to_dict())
        turn = session.record_turn("p", "r", solution=graph)
        self.assertIs(turn.solution, graph)


if __name__ == "__main__":
    unittest.main()
