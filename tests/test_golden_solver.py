"""Golden set determinístico do raciocínio (item #4 / §6.2).

Carrega `tests/golden_solver.json` e confere, sem rede e sem display, a exatidão
dos caminhos que o `math_solver` cobre de forma pura (quadrática, sistema 2x2,
roteamento e a heurística `looks_like_math`).

O roteamento NÃO é inferido por inspeção do grafo: é lido do **rastro de decisão**
(`DecisionTrace`) que o próprio solver grava — o que também prova que o rastro
funciona. Assim, este arquivo mede raciocínio de verdade (e não só mecânica).
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.ai.math_solver import looks_like_math, try_solve  # noqa: E402
from zorin_copilot.core.decision_trace import DecisionTrace  # noqa: E402

_GOLDEN_PATH = Path(__file__).with_name("golden_solver.json")


def _load_golden() -> dict:
    with open(_GOLDEN_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _numbers(text: str) -> list[float]:
    return [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _dispatch(trace: DecisionTrace) -> str:
    steps = trace.steps()
    if "parse_quadratic" in steps:
        return "quadratic"
    if "parse_system2" in steps:
        return "system2"
    if "route_engine" in steps:
        return "engine"
    return "none"


_VALID_ENGINE_PAYLOAD = {
    "problem_statement": "Resolva a derivada de x^3.",
    "givens": "f(x) = x^3",
    "unknown": "f'(x)",
    "answer": "f'(x) = 3x^2",
    "methods": [
        {"id": "m1", "name": "Regra do tombo", "summary": "deriva monômio",
         "steps": [{"id": "s1", "title": "Aplicar regra", "detail": "n·x^(n-1)",
                    "latex": "3x^{2}", "justification": "regra da potência"}]},
    ],
    "verification": {"checks": ["ok"], "passed": True},
}


class _FakeProvider:
    def is_configured(self):
        return True

    def complete(self, prompt, system_prompt=None, json_mode=True):
        return json.dumps(_VALID_ENGINE_PAYLOAD)


class GoldenQuadraticsTest(unittest.TestCase):
    def test_golden_quadratics(self):
        for case in _load_golden()["quadratics"]:
            with self.subTest(input=case["input"]):
                trace = DecisionTrace()
                g = try_solve(case["input"], trace=trace)
                self.assertIsNotNone(g)
                self.assertEqual(g.verification.passed, case["passed"])
                self.assertIn("parse_quadratic", trace.steps())
                if case.get("complex"):
                    self.assertIn("i", g.answer)
                else:
                    got = sorted(_numbers(g.answer))
                    want = sorted(float(r) for r in case["roots"])
                    self.assertEqual(len(got), len(want))
                    for a, b in zip(got, want):
                        self.assertAlmostEqual(a, b, places=6)


class GoldenSystems2Test(unittest.TestCase):
    def test_golden_systems(self):
        for case in _load_golden()["systems2"]:
            with self.subTest(input=case["input"]):
                trace = DecisionTrace()
                g = try_solve(case["input"], trace=trace)
                self.assertIsNotNone(g)
                self.assertIn("parse_system2", trace.steps())
                if case["unique"]:
                    got = _numbers(g.answer)
                    want = [float(v) for v in case["solution"]]
                    self.assertEqual(len(got), len(want))
                    for a, b in zip(got, want):
                        self.assertAlmostEqual(a, b, places=6)
                    self.assertTrue(g.verification.passed)
                else:
                    self.assertIn("det = 0", g.answer)


class GoldenRoutingTest(unittest.TestCase):
    def test_golden_routing(self):
        for case in _load_golden()["routing"]:
            with self.subTest(input=case["input"]):
                engine = _FakeProvider() if case.get("needs_engine") else None
                trace = DecisionTrace()
                try_solve(case["input"], engine=engine, trace=trace)
                self.assertEqual(_dispatch(trace), case["dispatch"])

    def test_trace_is_none_safe(self):
        # Sem trace, o caminho puro continua funcionando (record é no-op).
        g = try_solve("x^2 - 5x + 6 = 0")
        self.assertEqual(g.answer, "S = {2, 3}")


class GoldenLooksLikeMathTest(unittest.TestCase):
    def test_golden_looks_like_math(self):
        for case in _load_golden()["looks_like_math"]:
            with self.subTest(input=case["input"]):
                self.assertEqual(looks_like_math(case["input"]), case["expect"])


if __name__ == "__main__":
    unittest.main()
