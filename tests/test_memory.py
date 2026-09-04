# Decisão de design: testes unitários isolados da base de memória usando banco SQLite em memória temporária.

"""Testes unitários da Base de Conhecimento e Memória do Zorin Copilot."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.memory import MemoryManager


class MemoryManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_memory.db"
        self.mem = MemoryManager(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_log_action_and_stats(self):
        self.mem.log_action("abrir steam", "launch_app", "Steam", {}, True, "Iniciado")
        self.mem.log_action("teste falho", "command", "invalid", {}, False, "Falha")

        stats = self.mem.get_action_stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["successful"], 1)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["success_rate"], 50.0)

        history = self.mem.get_recent_actions(limit=5)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["target"], "invalid")
        self.assertEqual(history[1]["target"], "Steam")

    def test_save_and_retrieve_facts(self):
        self.mem.save_fact("navegador", "Prefere Brave", category="preferencia", source="usuario")
        self.mem.save_fact("editor", "Usa VS Code", category="ferramenta", source="usuario")

        facts = self.mem.get_all_facts()
        self.assertEqual(len(facts), 2)

        # Teste de atualização com mesma chave
        self.mem.save_fact("navegador", "Prefere Firefox agora", category="preferencia", source="usuario")
        facts_updated = self.mem.get_all_facts()
        self.assertEqual(len(facts_updated), 2)
        nav_fact = next(f for f in facts_updated if f["key"] == "navegador")
        self.assertEqual(nav_fact["content"], "Prefere Firefox agora")

    def test_delete_fact(self):
        self.mem.save_fact("chave1", "Conteudo 1")
        self.mem.save_fact("chave2", "Conteudo 2")

        facts = self.mem.get_all_facts()
        fid = facts[0]["id"]
        deleted = self.mem.delete_fact(fid)
        self.assertTrue(deleted)
        self.assertEqual(len(self.mem.get_all_facts()), 1)

    def test_context_summary(self):
        self.mem.save_fact("repo", "O projeto fica em ~/Projetos/zorin")
        self.mem.log_action("abrir terminal", "launch_app", "Terminal", {}, True, "OK")

        summary = self.mem.get_context_summary()
        self.assertIn("Base de Conhecimento", summary)
        self.assertIn("~/Projetos/zorin", summary)
        self.assertIn("Terminal", summary)

    def test_clear_all(self):
        self.mem.save_fact("f1", "Fato 1")
        self.mem.log_action("a1", "launch_app", "App", {}, True, "OK")
        self.mem.clear_all()

        self.assertEqual(len(self.mem.get_all_facts()), 0)
        self.assertEqual(len(self.mem.get_recent_actions()), 0)


if __name__ == "__main__":
    unittest.main()
