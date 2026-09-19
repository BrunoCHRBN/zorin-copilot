"""Testes unitários para o banco de vetores episódicos embarcado (EpisodicVectorStore)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zorin_copilot.core.vector_store import EpisodicVectorStore, cosine_similarity


class TestEpisodicVectorStore(unittest.TestCase):
    def test_cosine_similarity(self):
        # Vetores idênticos: score = 1.0
        v1 = [1.0, 0.0, 0.0]
        v2 = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(v1, v2), 1.0, places=4)

        # Vetores ortogonais: score = 0.0
        v3 = [0.0, 1.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(v1, v3), 0.0, places=4)

        # Vetores opostos: score = -1.0
        v4 = [-1.0, 0.0, 0.0]
        self.assertAlmostEqual(cosine_similarity(v1, v4), -1.0, places=4)

    def test_store_and_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_memory.db"
            store = EpisodicVectorStore(db_path=db_path)

            # Inserções de teste
            store.add(
                doc_id="doc_hyprland",
                text="Configuração de atalhos e janelas no Hyprland",
                vector=[0.9, 0.1, 0.2],
                category="linux",
                metadata={"file": "hyprland.conf"},
            )
            store.add(
                doc_id="doc_anki",
                text="Baralhos e flashcards de Gestão Comercial com SM-2",
                vector=[0.1, 0.8, 0.9],
                category="study",
                metadata={"deck": "gestao"},
            )
            store.add(
                doc_id="doc_fastapi",
                text="Endpoints e rotas assíncronas com FastAPI e Python",
                vector=[0.8, 0.2, 0.1],
                category="code",
            )

            self.assertEqual(store.count(), 3)
            self.assertEqual(store.count(category="study"), 1)

            # Busca por termo relacionado a Linux/Hyprland
            results = store.search(query_vector=[0.85, 0.15, 0.25], top_k=2)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].id, "doc_hyprland")
            self.assertGreater(results[0].score, 0.95)

            # Busca filtrando categoria 'study'
            study_results = store.search(
                query_vector=[0.1, 0.8, 0.85],
                category="study",
                top_k=2,
            )
            self.assertEqual(len(study_results), 1)
            self.assertEqual(study_results[0].id, "doc_anki")

            # Remoção
            self.assertTrue(store.delete("doc_hyprland"))
            self.assertEqual(store.count(), 2)


if __name__ == "__main__":
    unittest.main()
