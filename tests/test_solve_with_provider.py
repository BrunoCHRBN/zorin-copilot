"""Solver model-backed (`solve_with_provider`) — problemas além de quadráticas/2x2.

Determinístico: usa um provedor FAKE (sem rede). Verifica o contrato JSON, o
parse tolerante a cercas ```json, a degradação graciosa e a escalada do dispatch.
Cobre também o item #3: verificação numérica independente dos checks do modelo e
o contrato `generate_with_tool` (antes caminho morto, hoje vivo via BaseLLMProvider).
"""

from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.ai.math_solver import (  # noqa: E402
    SolutionGraph,
    Step,
    looks_like_math,
    request_solution_graph,
    solve_with_provider,
    try_solve,
    SOLUTION_GRAPH_SCHEMA,
)
from zorin_copilot.ai.providers import BaseLLMProvider  # noqa: E402

_VALID = {
    "problem_statement": "Resolva a derivada de x^3.",
    "givens": "f(x) = x^3",
    "unknown": "f'(x)",
    "answer": "f'(x) = 3x^2",
    "methods": [
        {
            "id": "m1", "name": "Regra do tombo", "summary": "deriva monômio",
            "steps": [
                {"id": "s1", "title": "Aplicar regra", "detail": "n·x^(n-1)",
                 "latex": "3x^{2}", "justification": "regra da potência"},
            ],
        },
    ],
    "viewpoints": [{"id": "v1", "name": "Geométrico", "description": "inclinação", "latex": ""}],
    "alternatives": [{"id": "a1", "name": "Definição por limite", "description": "via limite"}],
    "verification": {"checks": ["ok"], "passed": True},
    "scoring": {"total": 5, "items": [{"step": "regra", "points": 2}]},
}


class FakeProvider:
    def __init__(self, payload, configured=True):
        self._payload = payload
        self._configured = configured
        self.calls: list[dict] = []

    def is_configured(self):
        return self._configured

    def complete(self, prompt, system_prompt=None, json_mode=True):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt, "json_mode": json_mode})
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload)


class SolveWithProviderTest(unittest.TestCase):
    def test_builds_graph_from_json(self):
        provider = FakeProvider(_VALID)
        g = solve_with_provider("derivada de x^3", provider)
        self.assertIsInstance(g, SolutionGraph)
        self.assertEqual(g.answer, "f'(x) = 3x^2")
        # Passos viram objetos (exigência do Quadro Negro).
        self.assertIsInstance(g.methods[0].steps[0], Step)
        self.assertEqual(g.branches_for_layout()[0]["name"], "Regra do tombo")

    def test_passes_system_prompt_and_json_mode(self):
        provider = FakeProvider(_VALID)
        solve_with_provider("derivada de x^3", provider)
        call = provider.calls[0]
        self.assertTrue(call["json_mode"])
        self.assertIn("JSON", call["system_prompt"])
        self.assertIn("derivada de x^3", call["prompt"])

    def test_tolerates_code_fences(self):
        fenced = "```json\n" + json.dumps(_VALID) + "\n```"
        g = solve_with_provider("derivada", FakeProvider(fenced))
        self.assertIsNotNone(g)
        self.assertEqual(g.answer, "f'(x) = 3x^2")

    def test_garbage_returns_none(self):
        self.assertIsNone(solve_with_provider("x", FakeProvider("desculpe, não sei")))

    def test_unconfigured_returns_none(self):
        self.assertIsNone(solve_with_provider("x", FakeProvider(_VALID, configured=False)))

    def test_provider_without_complete_returns_none(self):
        class Bare:
            pass

        self.assertIsNone(solve_with_provider("x", Bare()))

    def test_json_without_answer_or_methods_returns_none(self):
        self.assertIsNone(solve_with_provider("x", FakeProvider({"problem_statement": "x"})))


class SolveWithProviderVerificationTest(unittest.TestCase):
    def test_numeric_mismatch_flips_passed_false(self):
        payload = dict(_VALID)
        payload["verification"] = {"checks": ["2 + 2 = 5"], "passed": True}
        g = solve_with_provider("conta", FakeProvider(payload))
        self.assertIsNotNone(g)
        self.assertFalse(g.verification.passed)
        self.assertTrue(any("Verificação independente" in c for c in g.verification.checks))

    def test_matching_check_keeps_passed_true(self):
        payload = dict(_VALID)
        payload["verification"] = {"checks": ["2 + 2 = 4"], "passed": True}
        g = solve_with_provider("conta", FakeProvider(payload))
        self.assertIsNotNone(g)
        self.assertTrue(g.verification.passed)

    def test_prose_check_keeps_passed_true(self):
        # Check em prosa ("ok") não é numericamente parseável: veredito preservado.
        g = solve_with_provider("conta", FakeProvider(_VALID))
        self.assertIsNotNone(g)
        self.assertTrue(g.verification.passed)

    def test_model_false_is_preserved(self):
        # Se o modelo já marcou FALHOU, não flipamos para True.
        payload = dict(_VALID)
        payload["verification"] = {"checks": ["2 + 2 = 4"], "passed": False}
        g = solve_with_provider("conta", FakeProvider(payload))
        self.assertIsNotNone(g)
        self.assertFalse(g.verification.passed)


class GenerateWithToolTest(unittest.TestCase):
    class _FakeToolProvider(BaseLLMProvider):
        def __init__(self, payload):
            self._payload = payload

        def is_configured(self):
            return True

        def test_connection(self):
            return (True, "ok")

        def chat(self, prompt, app_list=None, context_summary=None, history=None,
                 image_bytes=None, image_mime="image/jpeg"):
            return json.dumps(self._payload), []

    def test_returns_dict_from_json(self):
        provider = self._FakeToolProvider(_VALID)
        data = provider.generate_with_tool("derivada", SOLUTION_GRAPH_SCHEMA)
        self.assertIsInstance(data, dict)
        self.assertEqual(data["answer"], "f'(x) = 3x^2")

    def test_empty_response_raises(self):
        provider = self._FakeToolProvider(_VALID)
        provider.complete = lambda *a, **k: ""  # type: ignore[assignment]
        with self.assertRaises(ValueError):
            provider.generate_with_tool("derivada", SOLUTION_GRAPH_SCHEMA)

    def test_request_solution_graph_live_path(self):
        # O caminho antes 'morto' (engine.generate_with_tool) agora produz grafo.
        class FakeEngine:
            def generate_with_tool(self, prompt, schema):
                return dict(_VALID)

        g = try_solve("derivada de x^3", engine=FakeEngine())
        self.assertIsNotNone(g)
        self.assertIsInstance(g, SolutionGraph)
        self.assertEqual(g.answer, "f'(x) = 3x^2")

    def test_request_solution_graph_missing_contract_raises(self):
        class Bare:
            pass

        with self.assertRaises(NotImplementedError):
            request_solution_graph("derivada", Bare())


class TrySolveEscalationTest(unittest.TestCase):
    def test_deterministic_wins_over_provider(self):
        provider = FakeProvider(_VALID)
        g = try_solve("x^2 - 5x + 6 = 0", engine=provider)
        self.assertEqual(g.answer, "S = {2, 3}")
        self.assertEqual(provider.calls, [])  # não chamou o modelo

    def test_escalates_to_provider_for_other_problems(self):
        provider = FakeProvider(_VALID)
        g = try_solve("derivada de x^3", engine=provider)
        self.assertIsNotNone(g)
        self.assertEqual(g.answer, "f'(x) = 3x^2")
        self.assertEqual(len(provider.calls), 1)

    def test_provider_failure_is_swallowed(self):
        class Boom(FakeProvider):
            def complete(self, prompt, system_prompt=None, json_mode=True):
                raise RuntimeError("rede fora")

        self.assertIsNone(try_solve("derivada de x^3", engine=Boom(_VALID)))


class LooksLikeMathTest(unittest.TestCase):
    def test_equation_with_variable_and_digit(self):
        self.assertTrue(looks_like_math("resolva 2x^2 - 4x - 6 = 0"))

    def test_system(self):
        self.assertTrue(looks_like_math("x + y = 10; x - y = 2"))

    def test_keyword(self):
        self.assertTrue(looks_like_math("calcule a derivada de sen(x)"))

    def test_plain_text_is_not_math(self):
        self.assertFalse(looks_like_math("qual é a capital da França?"))
        self.assertFalse(looks_like_math("abrir o firefox"))
        self.assertFalse(looks_like_math(""))


if __name__ == "__main__":
    unittest.main()
