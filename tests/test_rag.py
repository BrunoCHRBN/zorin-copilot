"""Testes unitários para o motor de RAG Local (LocalDocumentRAG) e FTS5."""

import contextlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from zorin_copilot.core.rag import LocalDocumentRAG

# ---------------------------------------------------------------------------
# O `conftest.py` do pytest substitui `start_background_indexing` por um no-op
# (fixture de sessão, para a suíte não varrer o disco do usuário). Dois testes de
# cancelamento precisam exercitar a implementação **real** — é exatamente o
# contrato de "limpar o cancelamento antes de subir a thread" que está sob teste,
# e um no-op não tem thread para juntar.
#
# A referência é capturada no import do módulo, que acontece na coleta — antes de
# o pytest montar as fixtures de sessão. Assim os testes valem nos dois runners.
# ---------------------------------------------------------------------------
_REAL_START_BACKGROUND_INDEXING = LocalDocumentRAG.start_background_indexing


@contextlib.contextmanager
def _real_background_indexing():
    """Devolve a `start_background_indexing` real durante o bloco."""
    with patch.object(
        LocalDocumentRAG, "start_background_indexing", _REAL_START_BACKGROUND_INDEXING
    ):
        yield


class _FakeProvider:
    """Provedor mínimo: devolve `reply` como resposta, sem levantar exceção.

    É deliberado que ele NÃO levante: o comportamento real dos provedores deste
    projeto é devolver a frase de erro no canal de texto, e é exatamente isso que
    passa por fora de um `except Exception`.
    """

    def __init__(self, reply: str = ""):
        self.reply = reply
        self.prompts: list[str] = []

    def is_configured(self) -> bool:
        return True

    def chat(self, prompt, *args, **kwargs):
        self.prompts.append(prompt)
        return self.reply, []


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

    def test_erro_do_provedor_nao_vira_resposta_do_rag(self):
        """A frase de erro do provedor não pode ser a resposta sobre os documentos.

        Os provedores devolvem a mensagem de erro NO LUGAR da resposta em vez de
        levantar (`ai/provider_failure.py`), então o `except` de `ask()` nunca dispara
        neste caso. Sem a checagem, a consulta aos documentos era respondida com
        "o Gemini está indisponível/sem cota…", com `found: True`, e o fallback
        determinístico logo abaixo nunca rodava.
        """
        manual = self.docs_dir / "manual_equipamento.txt"
        manual.write_text(
            "Manual do equipamento.\n"
            "A garantia cobre 24 meses a partir da data de compra.\n",
            encoding="utf-8",
        )
        self.rag.index_file(manual)

        prov = _FakeProvider(
            reply=(
                "Não foi possível processar sua solicitação: o Gemini está indisponível/sem cota "
                "e o modelo local Ollama não pôde ser alcançado em http://localhost:11434."
            )
        )
        ans = self.rag.ask("qual o prazo de garantia do equipamento?", llm_provider=prov)

        self.assertNotIn("Não foi possível processar", ans["answer"])
        self.assertNotIn("sem cota", ans["answer"])
        # O fallback determinístico tem de ter rodado.
        self.assertIn("Encontrei os seguintes trechos relevantes", ans["answer"])
        self.assertIn("manual_equipamento.txt", ans["answer"])
        self.assertTrue(ans["found"])

    def test_sintese_real_do_provedor_e_usada(self):
        """Contraprova: com resposta legítima, o RAG usa a síntese do modelo."""
        manual = self.docs_dir / "manual_garantia.txt"
        manual.write_text("Manual.\nA garantia cobre 24 meses.\n", encoding="utf-8")
        self.rag.index_file(manual)

        prov = _FakeProvider(reply="A garantia cobre 24 meses a partir da compra.")
        ans = self.rag.ask("qual o prazo de garantia?", llm_provider=prov)

        self.assertEqual(ans["answer"], "A garantia cobre 24 meses a partir da compra.")
        self.assertEqual(len(prov.prompts), 1)

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


class IndexingCancellationTest(unittest.TestCase):
    """A varredura de disco precisa ser interrompível.

    Regressão que isto cobre: cada `CopilotWindow` dispara
    `start_background_indexing()`, que percorre ~/Documentos e ~/Downloads. A
    thread `RAGIndexer` não tinha freio — sobrevivia à janela que a criou e
    continuava batendo no disco. Numa suíte que cria dezenas de janelas, isso
    mantinha dezenas de varreduras simultâneas até o OOM killer agir.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.docs_dir = Path(self.temp_dir.name) / "Documentos"
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.rag = LocalDocumentRAG(
            db_path=Path(self.temp_dir.name) / "cancel.db",
            watched_dirs=[self.docs_dir],
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cancel_before_start_aborts_scan(self):
        """Cancelar antes de varrer faz a varredura sair imediatamente."""
        for i in range(20):
            (self.docs_dir / f"doc{i}.txt").write_text(f"conteúdo {i}", encoding="utf-8")

        self.rag.cancel_indexing()
        stats = self.rag.index_directories()

        self.assertTrue(stats["cancelled"], "a varredura deveria ter sido cancelada")
        self.assertEqual(stats["indexed"], 0, "nada deveria ter sido indexado após o cancelamento")

    def test_cancel_is_idempotent(self):
        """Chamar o cancelamento várias vezes não levanta exceção."""
        self.rag.cancel_indexing()
        self.rag.cancel_indexing()
        self.assertTrue(self.rag._index_cancel.is_set())

    def test_start_background_indexing_clears_previous_cancel(self):
        """Uma nova varredura em segundo plano limpa o cancelamento anterior.

        O reset precisa acontecer *antes* de a thread subir. Se ficasse dentro
        de `index_directories`, um `cancel_indexing()` chamado logo após o
        `start()` seria apagado pela própria thread ao começar a rodar — que é
        exatamente o padrão de "criar janela e destruir em seguida".
        """
        self.rag.cancel_indexing()
        self.assertTrue(self.rag._index_cancel.is_set())

        with _real_background_indexing():
            thread = self.rag.start_background_indexing()
        thread.join(timeout=10)
        self.assertFalse(self.rag._index_cancel.is_set())

    def test_cancel_after_start_is_not_lost(self):
        """Cancelar logo depois do start não pode ser descartado pela thread."""
        for i in range(200):
            (self.docs_dir / f"bulk{i}.txt").write_text(f"texto {i}" * 40, encoding="utf-8")

        with _real_background_indexing():
            self.rag.start_background_indexing()
        self.rag.cancel_indexing()

        # A varredura cooperativa deve terminar rápido, mesmo com 200 arquivos.
        for _ in range(100):
            if not self.rag._is_indexing:
                break
            time.sleep(0.05)
        self.assertFalse(self.rag._is_indexing, "a varredura não parou após o cancelamento")

    def test_cancel_does_not_break_subsequent_direct_scan(self):
        """Após um cancelamento, uma varredura direta ainda pode ser feita.

        Quem limpa o sinal é `start_background_indexing`; uma chamada direta a
        `index_directories` respeita o cancelamento pendente. Este teste fixa
        esse contrato para que ninguém "conserte" movendo o `clear()` para
        dentro do laço e reintroduza a corrida.
        """
        (self.docs_dir / "unico.txt").write_text("documento único", encoding="utf-8")

        self.rag.cancel_indexing()
        self.assertTrue(self.rag.index_directories()["cancelled"])

        # Uma nova execução em segundo plano reabre o caminho normal.
        with _real_background_indexing():
            thread = self.rag.start_background_indexing()
        thread.join(timeout=10)
        self.assertGreaterEqual(self.rag.index_directories()["indexed"], 0)


if __name__ == "__main__":
    unittest.main()

