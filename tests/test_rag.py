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

    def test_index_and_search_docx(self):
        """Testa extração e indexação de contrato em formato Word (.docx)."""
        import zipfile
        docx_file = self.docs_dir / "contrato_locacao.docx"
        with zipfile.ZipFile(docx_file, "w") as z:
            doc_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>'
                '<w:p><w:r><w:t>Contrato de Locação Residencial Urbana</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>Cláusula 5: O valor do aluguel mensal é R$ 2.850,00 com vencimento no dia 10.</w:t></w:r></w:p>'
                '</w:body></w:document>'
            )
            z.writestr("word/document.xml", doc_xml)

        self.assertTrue(self.rag.index_file(docx_file))
        results = self.rag.search("aluguel mensal")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].file_name, "contrato_locacao.docx")
        self.assertIn("2.850", results[0].snippet)

    def test_index_and_search_xlsx(self):
        """Testa extração e indexação de planilha Excel (.xlsx)."""
        import zipfile
        xlsx_file = self.docs_dir / "demonstrativo_vendas.xlsx"
        with zipfile.ZipFile(xlsx_file, "w") as z:
            shared_strings = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<si><t>Produto</t></si>'
                '<si><t>Receita Total</t></si>'
                '<si><t>Zorin Copilot Pro</t></si>'
                '</sst>'
            )
            sheet_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData>'
                '<row r="1">'
                '<c r="A1" t="s"><v>0</v></c>'
                '<c r="B1" t="s"><v>1</v></c>'
                '</row>'
                '<row r="2">'
                '<c r="A2" t="s"><v>2</v></c>'
                '<c r="B2"><v>98500</v></c>'
                '</row>'
                '</sheetData></worksheet>'
            )
            z.writestr("xl/sharedStrings.xml", shared_strings)
            z.writestr("xl/worksheets/sheet1.xml", sheet_xml)

        self.assertTrue(self.rag.index_file(xlsx_file))
        results = self.rag.search("Zorin Copilot Pro 98500")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].file_name, "demonstrativo_vendas.xlsx")
        self.assertIn("98500", results[0].snippet)

    def test_index_and_search_odt_and_tsv(self):
        """Testa suporte a LibreOffice ODT e arquivos TSV."""
        import zipfile
        odt_file = self.docs_dir / "acordo.odt"
        with zipfile.ZipFile(odt_file, "w") as z:
            content_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                '<office:body><office:text>'
                '<text:p>Termo de Confidencialidade e Não Divulgação (NDA) Zorin OS</text:p>'
                '</office:text></office:body></office:document-content>'
            )
            z.writestr("content.xml", content_xml)

        self.assertTrue(self.rag.index_file(odt_file))
        results = self.rag.search("Confidencialidade NDA")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].file_name, "acordo.odt")

        tsv_file = self.docs_dir / "inventario.tsv"
        tsv_file.write_text("Codigo\tNome\tEstoque\nCPU-01\tProcessador AMD\t42\n", encoding="utf-8")
        self.assertTrue(self.rag.index_file(tsv_file))
        res_tsv = self.rag.search("Processador AMD")
        self.assertGreaterEqual(len(res_tsv), 1)
        self.assertEqual(res_tsv[0].file_name, "inventario.tsv")

    def test_rag_ask_questions_about_documents(self):
        """Testa método RAG ask para responder a dúvidas com citações e resposta direta."""
        contract = self.docs_dir / "contrato_ti.txt"
        contract.write_text(
            "Contrato de Suporte Técnico.\n"
            "Cláusula 12: O prazo máximo para atendimento de chamados críticos (SLA) é de 2 horas úteis.\n",
            encoding="utf-8",
        )
        self.rag.index_file(contract)

        ans = self.rag.ask("qual o prazo de atendimento no contrato?")
        self.assertTrue(ans["found"])
        self.assertIn("contrato_ti.txt", ans["answer"])
        self.assertIn("2 horas", ans["answer"])
        self.assertEqual(len(ans["citations"]), 1)

        # Pergunta sobre assunto inexistente
        ans_miss = self.rag.ask("qual o tempo de voo para marte?")
        self.assertFalse(ans_miss["found"])
        self.assertIn("Não encontrei", ans_miss["answer"])

    def test_get_stats_by_type(self):
        """Testa se get_stats inclui contagem detalhada por tipo de arquivo."""
        (self.docs_dir / "d1.txt").write_text("doc 1", encoding="utf-8")
        (self.docs_dir / "d2.csv").write_text("a,b\n1,2", encoding="utf-8")
        self.rag.index_file(self.docs_dir / "d1.txt")
        self.rag.index_file(self.docs_dir / "d2.csv")

        stats = self.rag.get_stats()
        self.assertIn("by_type", stats)
        self.assertIn(".txt", stats["by_type"])
        self.assertIn(".csv", stats["by_type"])


if __name__ == "__main__":
    unittest.main()

