"""Ferramenta `study_show_board` do ToolRegistry (comando de voz/agente).

O registro é agnóstico de GTK: o abridor do Quadro Negro é injetado via
`registry.board_opener` com o contrato `(problema, grafo, foco)`. Estes testes
usam um abridor falso e verificam que a resolução determinística acontece aqui e
que a abertura é delegada — sem tocar em display.
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.ai.agent_tools import ToolRegistry  # noqa: E402
from zorin_copilot.core.board_outcome import (  # noqa: E402
    BOARD_FAILED,
    BOARD_OK,
    BOARD_PENDING,
    BOARD_UNAVAILABLE,
    BOARD_UNKNOWN,
    BoardOutcome,
    message_for,
)


def _outcome(status: str, message: str = "") -> dict:
    return BoardOutcome(status, message or message_for(status)).to_dict()


class StudyShowBoardTest(unittest.TestCase):
    def test_tool_is_registered(self):
        self.assertIn("study_show_board", ToolRegistry().names())

    def test_solves_and_delegates_to_opener(self):
        reg = ToolRegistry()
        calls: list[tuple] = []

        def opener(problem, graph, focus):
            calls.append((problem, graph, focus))
            return _outcome(BOARD_OK, "abri!")

        reg.board_opener = opener
        res = reg.call("study_show_board", {"problem": "x + y = 10; x - y = 2", "focus": "passo 2"})

        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("opened"))
        self.assertTrue(res.get("has_graph"))
        self.assertEqual(res.get("message"), "abri!")
        self.assertEqual(len(calls), 1)
        problem, graph, focus = calls[0]
        self.assertEqual(problem, "x + y = 10; x - y = 2")
        self.assertEqual(graph.answer, "S = {6, 4}")
        self.assertEqual(focus, "passo 2")

    def test_non_math_problem_forwards_problem_with_none_graph(self):
        reg = ToolRegistry()
        seen: dict = {}

        def opener(problem, graph, focus):
            seen.update(problem=problem, graph=graph, focus=focus)
            return _outcome(BOARD_PENDING)

        reg.board_opener = opener
        res = reg.call("study_show_board", {"problem": "qual a capital da França?"})

        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("pending"))
        self.assertFalse(res.get("opened"), "o quadro ainda não existe: o modelo está resolvendo")
        self.assertFalse(res.get("has_graph"))
        self.assertIsNone(seen["graph"])
        # O problema cru é repassado — é o que permite a UI escalar para o modelo.
        self.assertEqual(seen["problem"], "qual a capital da França?")

    def test_no_problem_reuses_last_solution_via_opener(self):
        # Sem `problem`, o registro não resolve nada: passa None e o app decide
        # (usar a última solução da conversa).
        reg = ToolRegistry()
        seen: dict = {}
        reg.board_opener = (
            lambda problem, graph, focus: seen.update(graph=graph) or _outcome(BOARD_OK)
        )
        res = reg.call("study_show_board", {})
        self.assertTrue(res.get("ok"))
        self.assertIsNone(seen["graph"])

    def test_unavailable_hook_is_not_reported_to_the_model_as_success(self):
        """O defeito original: sem provedor de IA nada abria, e a ferramenta dizia
        `ok: True` ao modelo — que então afirmava ao usuário que o quadro abriu."""
        reg = ToolRegistry()
        reg.board_opener = lambda *a: _outcome(BOARD_UNAVAILABLE)

        res = reg.call("study_show_board", {"problem": "qual a capital da França?"})

        self.assertFalse(res.get("ok"))
        self.assertFalse(res.get("opened"))
        self.assertEqual(res.get("status"), BOARD_UNAVAILABLE)
        self.assertIn("provedor", res.get("error", ""))

    def test_hook_without_an_outcome_is_not_reported_as_success(self):
        """Gancho que devolve `{}` não autoriza afirmar nada."""
        reg = ToolRegistry()
        reg.board_opener = lambda *a: {}

        res = reg.call("study_show_board", {"problem": "qual a capital da França?"})

        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("status"), BOARD_UNKNOWN)

    def test_failed_hook_surfaces_the_reason(self):
        reg = ToolRegistry()
        reg.board_opener = lambda *a: BoardOutcome(
            BOARD_FAILED, message_for(BOARD_FAILED), detail="modelo fora do ar"
        ).to_dict()

        res = reg.call("study_show_board", {"problem": "qual a capital da França?"})

        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("status"), BOARD_FAILED)
        self.assertIn("modelo fora do ar", res.get("error", ""))

    def test_method_alias_feeds_focus(self):
        reg = ToolRegistry()
        seen: dict = {}
        reg.board_opener = lambda problem, graph, focus: (
            seen.update(focus=focus) or _outcome(BOARD_OK)
        )
        reg.call("study_show_board", {"problem": "x^2 - 5x + 6 = 0", "method": "Fatoração"})
        self.assertEqual(seen["focus"], "Fatoração")

    def test_missing_opener_is_graceful(self):
        res = ToolRegistry().call("study_show_board", {"problem": "x^2 - 5x + 6 = 0"})
        self.assertFalse(res.get("ok"))
        self.assertIn("Quadro Negro", res.get("error", ""))

    def test_opener_exception_is_captured(self):
        reg = ToolRegistry()

        def boom(problem, graph, focus):
            raise RuntimeError("sem display")

        reg.board_opener = boom
        res = reg.call("study_show_board", {"problem": "x^2 - 5x + 6 = 0"})
        self.assertFalse(res.get("ok"))
        self.assertIn("Falha ao abrir o Quadro Negro", res.get("error", ""))


if __name__ == "__main__":
    unittest.main()
