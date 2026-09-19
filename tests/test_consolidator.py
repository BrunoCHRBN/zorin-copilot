"""Testes unitários para o consolidador de memória (MemoryConsolidator)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zorin_copilot.core.consolidator import MemoryConsolidator
from zorin_copilot.core.memory import MemoryManager


class TestMemoryConsolidator(unittest.TestCase):
    def test_consolidation_and_pruning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_memory.db"
            mem = MemoryManager(db_path=db_path)

            # Insere ações recentes
            now_iso = datetime.now().isoformat()
            old_iso = (datetime.now() - timedelta(days=30)).isoformat()

            with mem._get_connection() as conn:
                # Log recente
                conn.execute(
                    """
                    INSERT INTO action_logs (timestamp, prompt, action_type, target, params_json, success, message)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (now_iso, "abrir TCC", "open_document", "TCC_Gestao.docx", "{}", 1, "ok"),
                )
                # Log antigo para ser podado
                conn.execute(
                    """
                    INSERT INTO action_logs (timestamp, prompt, action_type, target, params_json, success, message)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (old_iso, "tocar musica", "media_control", "Queen", "{}", 1, "ok"),
                )

            consolidator = MemoryConsolidator(db_path=db_path)
            report = consolidator.run_consolidation(days_to_keep=14)

            self.assertEqual(report.logs_pruned, 1)
            self.assertGreaterEqual(report.nodes_updated, 1)

            # Verifica que o documento frequente foi promovido para o Grafo de Conhecimento
            node = consolidator.graph.get_node("file:TCC_Gestao.docx")
            self.assertIsNotNone(node)
            self.assertEqual(node.label, "FrequentDocument")


if __name__ == "__main__":
    unittest.main()
