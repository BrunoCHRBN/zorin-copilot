# Decisão de design: o math_solver é testado SEM GTK, SEM rede e SEM LLM. A lógica
# que importa é determinística e pura: a estrutura do SolutionGraph (métodos, pontos de
# vista, alternativas, verificação e pontuação) e o cálculo de verificação numérica
# (Horner). O que sobra para o teste é exatamente o contrato que o chat, a voz e o
# quadro negro consomem — nada de texto solto, tudo validável.

"""Testes determinísticos do gerador de SolutionGraph (modo de estudos)."""

from __future__ import annotations

import unittest

from zorin_copilot.ai.math_solver import (
    Method,
    SolutionGraph,
    Step,
    Verification,
    _evaluate_polynomial,
    build_quadratic_sample,
    solve,
    verify_roots,
)


class TestEvaluatePolynomial(unittest.TestCase):
    """Horner deve avaliar o polinômio corretamente (caso base de verify_roots)."""

    def test_horner_linear(self):
        self.assertAlmostEqual(_evaluate_polynomial([2.0, 3.0], 4.0), 11.0)  # 2x + 3

    def test_horner_quadratic(self):
        # x^2 - 5x + 6  ->  em ordem [1, -5, 6]
        self.assertAlmostEqual(_evaluate_polynomial([1.0, -5.0, 6.0], 2.0), 0.0)
        self.assertAlmostEqual(_evaluate_polynomial([1.0, -5.0, 6.0], 3.0), 0.0)
        self.assertAlmostEqual(_evaluate_polynomial([1.0, -5.0, 6.0], 0.0), 6.0)


class TestVerifyRoots(unittest.TestCase):
    def test_known_roots_pass(self):
        v = verify_roots([1.0, -5.0, 6.0], [2.0, 3.0])
        self.assertTrue(v.passed)
        self.assertEqual(len(v.checks), 2)

    def test_wrong_root_fails(self):
        v = verify_roots([1.0, -5.0, 6.0], [2.0, 99.0])
        self.assertFalse(v.passed)
        self.assertIn("FALHOU", v.checks[1])


class TestQuadraticSample(unittest.TestCase):
    """O exemplo canônico deve satisfazer o contrato do SolutionGraph."""

    def setUp(self):
        self.g = build_quadratic_sample()
        self.d = self.g.to_dict()

    def test_answer_is_correct(self):
        self.assertEqual(self.d["answer"], "S = {2, 3}")

    def test_at_least_two_methods(self):
        self.assertGreaterEqual(len(self.d["methods"]), 2)
        names = {m["name"] for m in self.d["methods"]}
        self.assertIn("Fatoração", names)
        self.assertIn("Fórmula de Bhaskara", names)

    def test_every_method_has_justified_steps(self):
        for m in self.d["methods"]:
            self.assertGreaterEqual(len(m["steps"]), 1)
            for s in m["steps"]:
                self.assertTrue(s["justification"])

    def test_has_geometric_viewpoint(self):
        geo = [v for v in self.d["viewpoints"] if "Geom" in v["name"]]
        self.assertEqual(len(geo), 1)
        self.assertTrue(geo[0]["latex"])

    def test_has_alternatives(self):
        self.assertGreaterEqual(len(self.d["alternatives"]), 1)

    def test_verification_passed(self):
        self.assertTrue(self.d["verification"]["passed"])

    def test_scoring_positive(self):
        self.assertGreater(self.d["scoring"]["total"], 0)
        self.assertGreaterEqual(len(self.d["scoring"]["items"]), 1)

    def test_rigor_fields_present(self):
        # 已知/求/解/答 devem existir para o modo rigor chinês
        self.assertTrue(self.d["givens"])
        self.assertTrue(self.d["unknown"])
        self.assertTrue(self.d["answer"])

    def test_roundtrip_to_dict(self):
        self.assertIsInstance(self.d, dict)
        # reconstrói a partir das instâncias originais (o contrato consumido pelos
        # renderizadores é o próprio dataclass, não o dict)
        rebuilt = SolutionGraph(
            problem_statement=self.g.problem_statement,
            givens=self.g.givens,
            unknown=self.g.unknown,
            answer=self.g.answer,
            methods=self.g.methods,
            viewpoints=self.g.viewpoints,
            alternatives=self.g.alternatives,
            verification=self.g.verification,
            scoring=self.g.scoring,
        )
        self.assertEqual(rebuilt.to_dict()["answer"], "S = {2, 3}")


class TestSolveDispatch(unittest.TestCase):
    def test_solve_returns_graph(self):
        g = solve("resolva x^2 - 5x + 6 = 0")
        self.assertIsInstance(g, SolutionGraph)
        self.assertEqual(g.to_dict()["answer"], "S = {2, 3}")


class TestParseQuadratic(unittest.TestCase):
    def test_parse_canonical(self):
        from zorin_copilot.ai.math_solver import parse_quadratic
        self.assertEqual(parse_quadratic("x^2 - 5x + 6 = 0"), (1.0, -5.0, 6.0))

    def test_parse_with_leading_a(self):
        from zorin_copilot.ai.math_solver import parse_quadratic
        self.assertEqual(parse_quadratic("2x^2 - 4x - 6 = 0"), (2.0, -4.0, -6.0))

    def test_parse_rejects_system(self):
        from zorin_copilot.ai.math_solver import parse_quadratic
        self.assertIsNone(parse_quadratic("x + y = 10; x - y = 2"))


class TestParseSystem2(unittest.TestCase):
    def test_parse_two_equations(self):
        from zorin_copilot.ai.math_solver import parse_system2
        self.assertEqual(
            parse_system2("x + y = 10; x - y = 2"),
            (1.0, 1.0, 10.0, 1.0, -1.0, 2.0),
        )


class TestSolveQuadraticReal(unittest.TestCase):
    def test_perfect_square_factors(self):
        from zorin_copilot.ai.math_solver import solve_quadratic
        g = solve_quadratic(1, -5, 6)
        self.assertEqual(g.to_dict()["answer"], "S = {2, 3}")
        self.assertTrue(g.verification.passed)
        names = {m.name for m in g.methods}
        self.assertIn("Fatoração", names)
        self.assertIn("Fórmula de Bhaskara", names)

    def test_irrational_uses_completing_square(self):
        from zorin_copilot.ai.math_solver import solve_quadratic
        g = solve_quadratic(1, 0, -2)  # raízes ±√2
        self.assertTrue(g.verification.passed)
        names = {m.name for m in g.methods}
        self.assertNotIn("Fatoração", names)
        self.assertIn("Completar o quadrado", names)


class TestSolveSystem2Real(unittest.TestCase):
    def test_unique_solution(self):
        from zorin_copilot.ai.math_solver import solve_system2
        g = solve_system2(1, 1, 10, 1, -1, 2)
        self.assertEqual(g.to_dict()["answer"], "S = {6, 4}")
        self.assertTrue(g.verification.passed)

    def test_dispatch_system(self):
        g = solve("x + y = 10; x - y = 2")
        self.assertIsInstance(g, SolutionGraph)
        self.assertEqual(g.to_dict()["answer"], "S = {6, 4}")

    def test_dispatch_quadratic_non_canonical(self):
        g = solve("2x^2 - 4x - 6 = 0")
        self.assertEqual(g.to_dict()["answer"], "S = {-1, 3}")


class TestRenderTerminal(unittest.TestCase):
    def test_terminal_has_rigor_sections(self):
        from zorin_copilot.ai.math_solver import render_terminal
        out = render_terminal(solve("x^2 - 5x + 6 = 0"))
        self.assertIn("已知", out)
        self.assertIn("求", out)
        self.assertIn("答", out)
        self.assertIn("过程性评价", out)
        self.assertIn("数形结合", out)


class TestTrySolve(unittest.TestCase):
    """`try_solve` distingue matemática real de texto comum (sem fallback)."""

    def test_quadratic_matches(self):
        from zorin_copilot.ai.math_solver import try_solve
        g = try_solve("x^2 - 5x + 6 = 0")
        self.assertIsNotNone(g)
        self.assertEqual(g.answer, "S = {2, 3}")

    def test_system_matches(self):
        from zorin_copilot.ai.math_solver import try_solve
        g = try_solve("x + y = 10; x - y = 2")
        self.assertIsNotNone(g)
        self.assertEqual(g.answer, "S = {6, 4}")

    def test_plain_text_returns_none(self):
        from zorin_copilot.ai.math_solver import try_solve
        self.assertIsNone(try_solve("qual é a capital da França?"))
        self.assertIsNone(try_solve(""))
        self.assertIsNone(try_solve("abrir o firefox"))


class TestSolutionGraphFromDict(unittest.TestCase):
    """Roundtrip to_dict -> solution_graph_from_dict reconstrói objetos de verdade."""

    def test_roundtrip_rebuilds_step_objects(self):
        from zorin_copilot.ai.math_solver import (
            Step,
            build_quadratic_sample,
            solution_graph_from_dict,
        )
        g = build_quadratic_sample()
        restored = solution_graph_from_dict(g.to_dict())
        self.assertEqual(restored.answer, g.answer)
        # Passos precisam virar Step (o Quadro Negro acessa s.id / s.title).
        for m in restored.methods:
            for s in m.steps:
                self.assertIsInstance(s, Step)
                self.assertTrue(s.id)
        # A união de métodos+pontos de vista (consumida pelo layout) não pode quebrar.
        branches = restored.branches_for_layout()
        self.assertGreaterEqual(len(branches), 2)

    def test_roundtrip_of_real_solver(self):
        from zorin_copilot.ai.math_solver import solve, solution_graph_from_dict
        g = solve("x + y = 10; x - y = 2")
        restored = solution_graph_from_dict(g.to_dict())
        self.assertEqual(restored.answer, "S = {6, 4}")
        self.assertTrue(restored.verification.passed)
        names = {m.name for m in restored.methods}
        self.assertIn("Substituição", names)

    def test_empty_dict_is_tolerated(self):
        from zorin_copilot.ai.math_solver import solution_graph_from_dict
        g = solution_graph_from_dict({})
        self.assertEqual(g.answer, "")
        self.assertEqual(g.methods, [])


if __name__ == "__main__":
    unittest.main()
