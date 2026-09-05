"""Testes unitários para o motor de RAG Local (LocalDocumentRAG) e FTS5."""

import os
import tempfile
import unittest
from pathlib import Path

from zorin_copilot.core.rag import LocalDocumentRAG


class LocalDocumentRAGTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.docs_dir = Path(self.temp_dir.name) / "Documentos"
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(self.temp_dir.name) / "test_rag.db"

        self.rag = LocalDocumentRAG(db_path=self.db_path, watched_dirs=[self.docs_dir])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_index_and_search_text_document(self):
        """Indexa arquivo .txt e busca termo exato."""
        doc_file = self.docs_dir / "anotacoes.txt"
        doc_file.write_text(
            "Reunião de alinhamento estratégico sobre o Zorin Copilot e novas tecnologias.",
            encoding="utf-8",
        )

        indexed = self.rag.index_file(doc_file)
        self.assertTrue(indexed)

        results = self.rag.search("alinhamento estratégico")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].file_name, "anotacoes.txt")
        self.assertIn("alinhamento", results[0].snippet.lower())

    def test_search_with_unicode_diacritics_resilience(self):
        """Verifica se busca sem acento encontra palavras acentuadas (ex: rescisao -> rescisão)."""
        contract = self.docs_dir / "contrato_servicos.md"
        contract.write_text(
            "# Contrato de Prestação de Serviços\n\n"
            "Cláusula 8ª: A rescisão unilateral imotivada acarretará multa indenizatória de 15%.\n"
            "Parágrafo único: O aviso prévio deve ser de trinta dias úteis.",
            encoding="utf-8",
        )

        self.rag.index_file(contract)

        # Busca sem acentos: "rescisao unilateral"
        results = self.rag.search("rescisao unilateral")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].file_name, "contrato_servicos.md")
        self.assertIn("rescisão", results[0].snippet.lower())

    def test_index_and_search_csv(self):
        """Indexa planilha .csv e busca por valor."""
        csv_file = self.docs_dir / "orcamento_2026.csv"
        csv_file.write_text(
            "Item,Categoria,Valor\n"
            "Notebook Dell,Equipamentos,5200.00\n"
            "Monitor AOC 27,Perifericos,1400.00\n"
            "Licenca Software,Sistemas,850.00\n",
            encoding="utf-8",
        )

        self.rag.index_file(csv_file)

        results = self.rag.search("Monitor AOC")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].file_name, "orcamento_2026.csv")
        self.assertIn("1400", results[0].snippet)

    def test_incremental_indexing_skips_unchanged_files(self):
        """Garante que arquivos não modificados são pulados sem reindexar."""
        sample = self.docs_dir / "arquivo_teste.txt"
        sample.write_text("Conteúdo de teste para cache incremental.", encoding="utf-8")

        # 1ª indexação: novo
        self.assertTrue(self.rag.index_file(sample))

        # 2ª indexação imediata: não modificado -> False
        self.assertFalse(self.rag.index_file(sample))

    def test_index_directories_and_stats(self):
        """Varre diretório com múltiplos arquivos e reporta estatísticas."""
        (self.docs_dir / "doc1.txt").write_text("Primeiro documento de teste", encoding="utf-8")
        (self.docs_dir / "doc2.md").write_text("Segundo documento em markdown", encoding="utf-8")

        stats = self.rag.index_directories()
        self.assertGreaterEqual(stats["indexed"], 2)

        rag_stats = self.rag.get_stats()
        self.assertGreaterEqual(rag_stats["total_documents"], 2)
        self.assertGreaterEqual(rag_stats["total_chunks"], 2)

    def test_read_document_page(self):
        """Lê conteúdo da página de um documento indexado."""
        doc = self.docs_dir / "manual.txt"
        doc.write_text("Conteúdo da página inicial do manual.", encoding="utf-8")
        self.rag.index_file(doc)

        content = self.rag.read_document_page(str(doc), page_number=1)
        self.assertIn("Conteúdo da página inicial", content)

    def test_search_empty_returns_empty_list(self):
        """Busca com termo vazio ou espaços retorna lista vazia sem erro."""
        self.assertEqual(self.rag.search(""), [])
        self.assertEqual(self.rag.search("   "), [])

    def test_intent_engine_document_search(self):
        """Testa se o IntentEngine reconhece pedidos de busca de documentos e propõe abertura."""
        from zorin_copilot.ai.actions import ActionType
        from zorin_copilot.ai.engine import IntentEngine

        doc = self.docs_dir / "relatorio_anual.txt"
        doc.write_text("Metas financeiras e planejamento estratégico anual consolidado.", encoding="utf-8")
        self.rag.index_file(doc)

        engine = IntentEngine(rag=self.rag)
        plan = engine.parse("busque nos meus documentos planejamento estratégico")

        self.assertIn("relatorio_anual.txt", plan.thought)
        self.assertGreaterEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].action_type, ActionType.OPEN_DOCUMENT)
        self.assertEqual(plan.actions[0].target, str(doc))

    def test_executor_open_document(self):
        """Testa se o ActionExecutor executa ação de abrir documento."""
        from zorin_copilot.ai.actions import ActionType, DesktopAction
        from zorin_copilot.shell.executor import ActionExecutor

        doc = self.docs_dir / "nota.txt"
        doc.write_text("Nota de teste", encoding="utf-8")

        executor = ActionExecutor()
        executor.rag = self.rag

        action = DesktopAction(ActionType.OPEN_DOCUMENT, str(doc), {"page_number": 1})
        report = executor._execute_single(action)
        self.assertTrue(report.success)
        self.assertIn("aberto", report.message.lower())


if __name__ == "__main__":
    unittest.main()
