"""Testes unitários para o Grafo de Conhecimento Semântico (KnowledgeGraph)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zorin_copilot.core.knowledge_graph import KnowledgeGraph, GraphNode, GraphEdge


class TestKnowledgeGraph(unittest.TestCase):
    def test_graph_nodes_and_edges(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_graph.db"
            graph = KnowledgeGraph(db_path=db_path)

            # 1. Cria nós
            u = graph.add_node("user:bruno", "User", {"name": "Bruno"})
            c = graph.add_node("course:gestao", "Course", {"name": "Gestão Comercial"})
            p = graph.add_node("proj:tcc", "Project", {"title": "TCC de Gestão", "deadline": "2026-11"})
            d = graph.add_node("device:avant", "Device", {"type": "light", "room": "quarto"})

            self.assertIsNotNone(graph.get_node("user:bruno"))
            self.assertEqual(len(graph.find_nodes("Course")), 1)

            # 2. Conecta nós via arestas direcionadas
            graph.add_edge("user:bruno", "course:gestao", "STUDIES")
            graph.add_edge("course:gestao", "proj:tcc", "HAS_PROJECT")
            graph.add_edge("user:bruno", "device:avant", "CONTROLS")

            # 3. Consulta de vizinhos (relações de saída)
            user_out = graph.get_neighbors("user:bruno", direction="out")
            self.assertEqual(len(user_out), 2)
            rel_types = {edge.relation for edge, _ in user_out}
            self.assertEqual(rel_types, {"STUDIES", "CONTROLS"})

            # 4. Consulta de vizinhos com filtro de relação
            controls_neighbors = graph.get_neighbors("user:bruno", direction="out", relation="CONTROLS")
            self.assertEqual(len(controls_neighbors), 1)
            _, target_node = controls_neighbors[0]
            self.assertEqual(target_node.id, "device:avant")
            self.assertEqual(target_node.properties.get("room"), "quarto")

            # 5. Consulta de entrada (quem tem o projeto TCC?)
            tcc_in = graph.get_neighbors("proj:tcc", direction="in")
            self.assertEqual(len(tcc_in), 1)
            self.assertEqual(tcc_in[0][1].id, "course:gestao")

            # 6. Remoção de nó em cascata
            self.assertTrue(graph.delete_node("device:avant"))
            self.assertIsNone(graph.get_node("device:avant"))
            user_out_after = graph.get_neighbors("user:bruno", direction="out")
            self.assertEqual(len(user_out_after), 1)


if __name__ == "__main__":
    unittest.main()
