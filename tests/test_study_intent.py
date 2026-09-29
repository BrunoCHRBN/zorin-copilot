"""Detecção do comando de voz do Quadro Negro (`core/study_intent`).

Puro e determinístico: sem GTK, sem áudio, sem modelo. O que estes testes
protegem é o **equilíbrio** do detector — um falso positivo aqui desvia um
pedido legítimo do IntentEngine (o usuário pede uma coisa e o app abre um
quadro), então os negativos valem tanto quanto os positivos.
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.core.board_nav import find_node  # noqa: E402
from zorin_copilot.core.study_intent import BoardCommand, parse_board_command  # noqa: E402


class DetectionTest(unittest.TestCase):
    """Quando o detector casa e quando não casa."""

    def test_strong_term_alone_is_enough(self):
        # "quadro negro" e "lousa" são inequívocos: dispensam verbo.
        self.assertIsNotNone(parse_board_command("abre o quadro negro"))
        self.assertIsNotNone(parse_board_command("lousa"))
        self.assertIsNotNone(parse_board_command("blackboard"))

    def test_strong_term_at_start_is_a_vocative(self):
        """Chamar o quadro pelo nome no começo da frase basta, mesmo sem verbo."""
        self.assertIsNotNone(parse_board_command("quadro negro, resolve isso"))

    def test_weak_term_requires_a_show_verb(self):
        self.assertIsNotNone(parse_board_command("abre o quadro"))
        self.assertIsNotNone(parse_board_command("mostra no quadro"))
        # Sem verbo de exibição, "quadro" é ambíguo demais.
        self.assertIsNone(parse_board_command("o quadro está lento"))
        self.assertIsNone(parse_board_command("o quadro de funcionários da empresa"))

    def test_homonyms_are_rejected(self):
        """Outros "quadros" do mundo real não são o nosso."""
        for frase in (
            "abre o quadro elétrico",
            "quadro de avisos do condomínio",
            "abre o quadro de horários",
            "quadro de distribuição",
        ):
            self.assertIsNone(parse_board_command(frase), frase)

    def test_unrelated_speech_is_rejected(self):
        for frase in (
            "abre o firefox",
            "qual a capital da França?",
            "me mostra as notícias de hoje",
            "toca uma música",
            "",
            "   ",
        ):
            self.assertIsNone(parse_board_command(frase), frase)

    def test_general_statement_about_blackboards_is_rejected(self):
        """Afirmação sobre o objeto, não pedido para abrir o recurso."""
        self.assertIsNone(
            parse_board_command("o quadro negro é usado em salas de aula")
        )


class FocusExtractionTest(unittest.TestCase):
    """O resíduo que vira alvo de destaque."""

    def test_focus_is_the_leftover(self):
        self.assertEqual(parse_board_command("abre o quadro e mostra a fatoração").focus, "fatoracao")
        self.assertEqual(parse_board_command("me mostra no quadro o passo 2").focus, "passo 2")

    def test_focus_keeps_inner_connectives(self):
        """Tirar o "de" do meio quebraria o casamento com o rótulo do nó."""
        cmd = parse_board_command("destaca a fórmula de bhaskara no quadro")
        self.assertEqual(cmd.focus, "formula de bhaskara")

    def test_focus_is_empty_when_only_opening(self):
        self.assertEqual(parse_board_command("abre o quadro negro").focus, "")
        self.assertEqual(parse_board_command("quero ver o quadro").focus, "")


class ProblemExtractionTest(unittest.TestCase):
    """Quando a frase carrega um problema novo, e não só um alvo."""

    def test_equation_becomes_the_problem_verbatim(self):
        frase = "abre o quadro e resolve x² - 5x + 6 = 0"
        cmd = parse_board_command(frase)
        # O texto cru vai inteiro: o `try_solve` extrai a equação do meio da prosa.
        self.assertEqual(cmd.problem, frase)
        self.assertEqual(cmd.focus, "", "o resíduo é a equação, não um alvo")

    def test_math_word_without_equation_is_not_a_problem(self):
        """`looks_like_math` aceitaria "fatoração"; aqui isso abriria um quadro inventado."""
        cmd = parse_board_command("mostra a fatoração no quadro")
        self.assertEqual(cmd.problem, "")

    def test_problem_is_empty_when_only_opening(self):
        self.assertEqual(parse_board_command("abre o quadro").problem, "")


class ResolvesToNodeTest(unittest.TestCase):
    """Integração: o `focus` extraído tem de casar com um nó de verdade."""

    @classmethod
    def setUpClass(cls):
        from zorin_copilot.ai.math_solver import build_quadratic_sample

        g = build_quadratic_sample()
        cls.nodes = [{"id": "root", "type": "root", "label": "Enunciado",
                      "sub": g.problem_statement}]
        for b in g.branches_for_layout():
            cls.nodes.append({"id": b["id"], "type": "branch", "kind": b["kind"],
                              "label": b["name"], "sub": b.get("summary", "")})
            for s in b.get("steps", []):
                cls.nodes.append({"id": s["id"], "type": "step",
                                  "label": s["title"], "sub": s.get("detail", "")})

    def _target(self, frase: str) -> str:
        cmd = parse_board_command(frase)
        self.assertIsNotNone(cmd, frase)
        node = find_node(self.nodes, cmd.focus) if cmd.focus else self.nodes[0]
        return node["label"] if node else ""

    def test_frases_resolve_to_the_expected_node(self):
        casos = {
            "abre o quadro e mostra a fatoração": "Fatoração",
            "destaca a fórmula de bhaskara no quadro": "Fórmula de Bhaskara",
            "me mostra no quadro o passo 2": "Encontrar os fatores",
            # O usuário diz o alvo com palavras a mais: casa pela palavra do rótulo.
            "abre o quadro e mostra o ponto de vista geométrico": "Geométrico (数形结合)",
        }
        for frase, esperado in casos.items():
            self.assertEqual(self._target(frase), esperado, frase)

    def test_opening_without_focus_lands_on_the_enunciado(self):
        self.assertEqual(self._target("abre o quadro negro"), "Enunciado")


class BoardCommandShapeTest(unittest.TestCase):
    def test_defaults_are_empty(self):
        cmd = BoardCommand()
        self.assertEqual(cmd.problem, "")
        self.assertEqual(cmd.focus, "")


if __name__ == "__main__":
    unittest.main()
