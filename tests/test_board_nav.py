"""Seleção de nós do Quadro Negro por consulta textual (comando de voz/agente).

`core/board_nav.py` é puro (sem GTK), então estes testes rodam headless e
cobrem a decisão de qual nó destacar — a parte que a voz precisa acertar.
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.core.board_nav import find_node, fold  # noqa: E402

NODES = [
    {"id": "root", "type": "root", "label": "Enunciado", "sub": "Resolva x² - 5x + 6 = 0."},
    {"id": "m_bhas", "type": "branch", "kind": "method",
     "label": "Fórmula de Bhaskara", "sub": "Aplica a fórmula geral."},
    {"id": "b1", "type": "step", "label": "Coeficientes", "sub": "a=1, b=-5, c=6"},
    {"id": "b2", "type": "step", "label": "Discriminante", "sub": "Δ = b² - 4ac = 1"},
    {"id": "m_fator", "type": "branch", "kind": "method",
     "label": "Fatoração", "sub": "Produto de dois binômios."},
    {"id": "f1", "type": "step", "label": "Soma e produto", "sub": "r1+r2 = 5"},
]


class FoldTest(unittest.TestCase):
    def test_removes_accents_and_case(self):
        self.assertEqual(fold("Fatoração"), "fatoracao")
        self.assertEqual(fold("BHASKARA"), "bhaskara")

    def test_preserves_punctuation(self):
        # Ao contrário de `normalize_text` (wake word), a pontuação fica:
        # é o que torna "x² - 5x + 6" inencontrável se for removida.
        self.assertIn("-", fold("x² - 5x + 6"))
        self.assertIn("5x", fold("x² - 5x + 6"))

    def test_none_is_empty(self):
        self.assertEqual(fold(None), "")


class FindNodeTest(unittest.TestCase):
    def test_empty_query_returns_root(self):
        self.assertIs(find_node(NODES, ""), NODES[0])
        self.assertIs(find_node(NODES, "   "), NODES[0])

    def test_method_by_accent_insensitive_name(self):
        self.assertIs(find_node(NODES, "fatoracao"), NODES[4])
        self.assertIs(find_node(NODES, "Bhaskara"), NODES[1])

    def test_step_by_number(self):
        # "passo 2" -> o 2º nó de passo (Discriminante), não o 2º nó da lista.
        self.assertIs(find_node(NODES, "passo 2"), NODES[3])
        self.assertIs(find_node(NODES, "etapa 1"), NODES[2])

    def test_enunciado_returns_root(self):
        self.assertIs(find_node(NODES, "enunciado"), NODES[0])

    def test_substring_in_subtitle(self):
        self.assertIs(find_node(NODES, "binômios"), NODES[4])

    def test_unknown_returns_none(self):
        self.assertIsNone(find_node(NODES, "raiz quadrada de banana"))

    def test_out_of_range_step_falls_through_to_none(self):
        self.assertIsNone(find_node(NODES, "passo 99"))

    def test_empty_nodes(self):
        self.assertIsNone(find_node([], "fatoracao"))


if __name__ == "__main__":
    unittest.main()
