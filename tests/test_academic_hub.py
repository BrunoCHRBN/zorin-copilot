# Decisão de design: testes unitários determinísticos para AcademicHub e apresentação acadêmica no chat.
# Nenhum teste faz requisição de rede real: RAG, WebSearch e LLM entram como dublês controlados.

"""Testes unitários do AcademicHub, fichamento e apresentação acadêmica no chat."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zorin_copilot.core.academic_hub import AcademicDocument, AcademicHub
from zorin_copilot.core.memory import MemoryManager
from zorin_copilot.core.rag import DocumentSearchResult
from zorin_copilot.core.web_search import SearchResult
from zorin_copilot.ai.actions import ActionType
from zorin_copilot.ai.engine import IntentEngine


class FakeRAG:
    """Dublê do motor de RAG local."""

    def __init__(self, results: list[DocumentSearchResult] | None = None):
        self.results = results or []

    def search(self, query: str, limit: int = 4) -> list[DocumentSearchResult]:
        return self.results[:limit]


class FakeWebSearch:
    """Dublê do cliente de busca acadêmica."""

    def __init__(self, results: list[SearchResult] | None = None):
        self.results = results or []

    def academic_search(self, query: str, source: str = "all", max_results: int = 4) -> list[SearchResult]:
        return self.results[:max_results]


def test_academic_document_format_card_local():
    doc = AcademicDocument(
        title="Diagnóstico Comercial e Análise SWOT",
        source="Projeto Integrador (PI)",
        abstract="A empresa apresenta fraquezas na retenção de clientes.",
        file_path_or_url="/home/user/Documentos/Gestao_Comercial/Projetos_Integradores/PI_1.docx",
        is_local=True,
        page_number=3,
        relevance_note="Essencial para o plano de ação.",
    )
    card = doc.format_card(1)
    assert "### 📁 1. Diagnóstico Comercial e Análise SWOT" in card
    assert "**Fonte/Origem:** Projeto Integrador (PI)" in card
    assert "**Página:** 3" in card
    assert "A empresa apresenta fraquezas na retenção de clientes." in card
    assert "**Aplicação Prática:** Essencial para o plano de ação." in card


def test_academic_document_format_card_web():
    doc = AcademicDocument(
        title="Estratégias de Fidelização de Clientes no Varejo",
        source="SciELO",
        year="2024",
        authors="Silva & Santos",
        abstract="Programas de fidelidade impactam diretamente o LTV.",
        abnt_citation="SCIELO BRASIL. Estratégias de Fidelização... 2024.",
        file_path_or_url="https://scielo.br/artigo123",
        is_local=False,
    )
    card = doc.format_card(2)
    assert "### 📄 2. Estratégias de Fidelização de Clientes no Varejo" in card
    assert "**Fonte/Origem:** SciELO" in card
    assert "**Ano:** 2024" in card
    assert "**Autores:** Silva & Santos" in card
    assert "Programas de fidelidade impactam diretamente o LTV." in card
    assert "**Citação (ABNT NBR 6023):**" in card


def test_categorize_local_path():
    hub = AcademicHub(rag=FakeRAG(), web_search=FakeWebSearch())
    cat, _ = hub._categorize_local_path("/docs/Gestao_Comercial/Projetos_Integradores/PI_Vendas.docx")
    assert "Projeto Integrador" in cat

    cat2, _ = hub._categorize_local_path("/docs/Gestao_Comercial/TCC_Artigos/monografia.docx")
    assert "TCC" in cat2

    cat3, _ = hub._categorize_local_path("/docs/Gestao_Comercial/Anotacoes/aula_markup.md")
    assert "Anotação" in cat3

    cat4, _ = hub._categorize_local_path("/docs/Gestao_Comercial/Livros_e_PDFs/livro_crm.pdf")
    assert "Livro" in cat4 or "Material" in cat4


def test_generate_abnt_citation():
    hub = AcademicHub(rag=FakeRAG(), web_search=FakeWebSearch())
    cit = hub._generate_abnt_citation(
        title="Impacto do Churn no Varejo Eletrônico",
        url="https://www.scielo.br/j/rac/a/123",
        source_domain="SciELO",
        year="2025",
    )
    assert "SCIELO BRASIL" in cit
    assert "Impacto do Churn no Varejo Eletrônico" in cit
    assert "2025" in cit
    assert "Disponível em: <https://www.scielo.br/j/rac/a/123>" in cit
    assert "Acesso em:" in cit


def test_search_local_with_results():
    sample_res = [
        DocumentSearchResult(
            file_path="/home/user/Documentos/Gestao_Comercial/Projetos_Integradores/PI_1.docx",
            file_name="PI_1.docx",
            title="Diagnóstico Comercial",
            page_number=2,
            snippet="O público-alvo prioritário é composto por pequenas farmácias.",
        )
    ]
    rag = FakeRAG(sample_res)
    hub = AcademicHub(rag=rag, web_search=FakeWebSearch())

    docs = hub.search_local("público-alvo farmácias", limit=2)
    assert len(docs) == 1
    assert docs[0].title == "Diagnóstico Comercial"
    assert docs[0].is_local is True
    assert docs[0].page_number == 2
    assert "Projeto Integrador" in docs[0].source


def test_search_web_with_results():
    sample_res = [
        SearchResult(
            title="Análise do Comportamento do Consumidor 2026",
            url="https://sebrae.com.br/estudo-consumidor",
            snippet="Estudo do Sebrae demonstra tendências de compras digitais.",
        )
    ]
    web = FakeWebSearch(sample_res)
    hub = AcademicHub(rag=FakeRAG(), web_search=web)

    docs = hub.search_web("tendências consumidor", source="sebrae", limit=2)
    assert len(docs) == 1
    assert "Análise do Comportamento do Consumidor" in docs[0].title
    assert docs[0].is_local is False
    assert docs[0].source == "Sebrae"
    assert "sebrae.com.br" in docs[0].file_path_or_url


def test_search_scope_auto_routing():
    local_res = [
        DocumentSearchResult(
            file_path="/home/user/Documentos/Gestao_Comercial/Projetos_Integradores/PI.docx",
            file_name="PI.docx",
            title="PI",
            page_number=1,
            snippet="Persona do cliente.",
        )
    ]
    web_res = [
        SearchResult(
            title="Artigo Científico SciELO",
            url="https://scielo.br/artigo",
            snippet="Metodologia quantitativa.",
        )
    ]
    rag = FakeRAG(local_res)
    web = FakeWebSearch(web_res)
    hub = AcademicHub(rag=rag, web_search=web)

    # Consulta explícita para PI / local -> deve buscar local
    docs_local = hub.search("o que escrevi no meu pi sobre persona", scope="auto")
    assert len(docs_local) == 1
    assert docs_local[0].is_local is True

    # Consulta explícita para SciELO / artigos -> deve buscar web
    docs_web = hub.search("pesquise no scielo artigos sobre CRM", scope="auto")
    assert len(docs_web) == 1
    assert docs_web[0].is_local is False


def test_format_chat_response():
    hub = AcademicHub(rag=FakeRAG(), web_search=FakeWebSearch())
    docs = [
        AcademicDocument(
            title="Trabalho PI",
            source="Projeto Integrador (PI)",
            abstract="Trecho do PI.",
            file_path_or_url="/caminho/pi.docx",
            is_local=True,
            page_number=4,
        ),
        AcademicDocument(
            title="Artigo SciELO",
            source="SciELO",
            abstract="Resumo do artigo.",
            file_path_or_url="https://scielo.br/123",
            is_local=False,
        ),
    ]

    plan = hub.format_chat_response(docs, "CRM e Fidelização")
    assert plan.thought is not None
    assert "### 📁 1. Trabalho PI" in plan.thought
    assert "### 📄 2. Artigo SciELO" in plan.thought

    assert len(plan.actions) == 2
    # Primeira ação é abrir documento local
    assert plan.actions[0].action_type == ActionType.OPEN_DOCUMENT
    assert plan.actions[0].target == "/caminho/pi.docx"
    assert plan.actions[0].params.get("page_number") == 4
    # Segunda ação é abrir URL do artigo
    assert plan.actions[1].action_type == ActionType.OPEN_URL
    assert plan.actions[1].target == "https://scielo.br/123"


def test_academic_library_crud_in_memory_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.db"
        mem = MemoryManager(db_path=db_path)
        hub = AcademicHub(rag=FakeRAG(), web_search=FakeWebSearch(), memory=mem)

        doc = AcademicDocument(
            title="Pesquisa de Mercado e Precificação",
            authors="Kotler & Armstrong",
            year="2025",
            source="SciELO",
            abstract="Estudo de elasticidade de preço da demanda.",
            abnt_citation="KOTLER, P. Pesquisa de Mercado... 2025.",
            file_path_or_url="https://scielo.br/precificacao",
            discipline="Gestão Comercial",
            tags=["markup", "elasticidade", "uc3"],
        )

        # 1. Salvar na biblioteca
        entry_id = hub.save_to_library(doc)
        assert entry_id > 0

        # 2. Recuperar da biblioteca
        saved = hub.list_saved_library()
        assert len(saved) == 1
        assert saved[0].title == "Pesquisa de Mercado e Precificação"
        assert saved[0].authors == "Kotler & Armstrong"
        assert saved[0].tags == ["markup", "elasticidade", "uc3"]

        # 3. Filtrar por query
        found = hub.list_saved_library(query="elasticidade")
        assert len(found) == 1
        not_found = hub.list_saved_library(query="inexistente")
        assert len(not_found) == 0

        # 4. Deletar
        ok = mem.delete_academic_entry(entry_id)
        assert ok is True
        assert len(hub.list_saved_library()) == 0


def test_engine_parses_academic_query_seamlessly():
    """Testa se o Engine reconhece expressões acadêmicas e aciona o AcademicHub."""
    engine = IntentEngine(inspector=MagicMock())

    # Injeta dublês no academic_hub do engine
    sample_doc = AcademicDocument(
        title="Artigo de Varejo e Fidelização",
        source="SciELO",
        year="2024",
        abstract="Programas de pontos estimulam compras recorrentes.",
        file_path_or_url="https://scielo.br/varejo",
        is_local=False,
    )
    engine.academic_hub.search = MagicMock(return_value=[sample_doc])

    plan = engine.parse("pesquise no scielo artigos sobre fidelização de clientes")
    assert plan is not None
    assert "Artigo de Varejo e Fidelização" in plan.thought
    assert len(plan.actions) == 1
    assert plan.actions[0].action_type == ActionType.OPEN_URL
    assert plan.actions[0].target == "https://scielo.br/varejo"
