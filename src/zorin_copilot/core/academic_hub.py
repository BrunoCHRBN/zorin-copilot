# Decisão de design: Centralizador e orquestrador de conhecimento acadêmico (Local + Web + Biblioteca).
# Conecta o RAG local aos materiais da graduação (Gestão Comercial / Senac EAD), à pesquisa em bases
# científicas oficiais (SciELO, IBGE, Sebrae, Scholar) e ao acervo local no SQLite (memory.db).
# Formata resultados em "fichas acadêmicas" padronizadas com citação NBR 6023 e ações de abertura direta.

"""Módulo central de pesquisa, fichamento e apresentação acadêmica para o Zorin Copilot."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .memory import MemoryManager
from .rag import LocalDocumentRAG, DocumentSearchResult
from .web_search import WebSearchClient, SearchResult
from ..ai.actions import ActionPlan, ActionType, DesktopAction

logger = logging.getLogger(__name__)

MONTH_ABBR = {
    1: "jan.", 2: "fev.", 3: "mar.", 4: "abr.", 5: "maio", 6: "jun.",
    7: "jul.", 8: "ago.", 9: "set.", 10: "out.", 11: "nov.", 12: "dez."
}


@dataclass
class AcademicDocument:
    """Representação unificada de um material acadêmico (artigo, trabalho, PI ou anotação)."""

    title: str
    authors: str = ""
    year: str = ""
    source: str = ""  # Ex: "SciELO", "Projeto Integrador", "Anotação de Aula", "IBGE", "Sebrae"
    abstract: str = ""
    abnt_citation: str = ""
    file_path_or_url: str = ""
    is_local: bool = False
    page_number: int = 1
    discipline: str = "Gestão Comercial"
    relevance_note: str = ""
    tags: list[str] = field(default_factory=list)

    def format_card(self, index: int = 1) -> str:
        """Gera um card em Markdown limpo para exibição no chat."""
        icon = "📁" if self.is_local else "📄"
        category = f"[{self.source}]" if self.source else ""
        header = f"### {icon} {index}. {self.title}"

        lines = [header]
        meta_parts = []
        if self.source:
            meta_parts.append(f"**Fonte/Origem:** {self.source}")
        if self.year:
            meta_parts.append(f"**Ano:** {self.year}")
        if self.authors:
            meta_parts.append(f"**Autores:** {self.authors}")
        if self.is_local and self.page_number > 0:
            meta_parts.append(f"**Página:** {self.page_number}")

        if meta_parts:
            lines.append("* " + " | ".join(meta_parts))

        if self.abstract:
            clean_snip = re.sub(r"</?[a-zA-Z0-9]+[^>]*>", "", self.abstract).strip()
            # Se for citação ou trecho local
            if self.is_local:
                lines.append(f"* **Trecho Localizado:**\n> \"{clean_snip}\"")
            else:
                lines.append(f"* **Resumo Breve:** {clean_snip}")

        if self.relevance_note:
            lines.append(f"* **Aplicação Prática:** {self.relevance_note}")

        if self.abnt_citation:
            lines.append(f"* **Citação (ABNT NBR 6023):**\n  `{self.abnt_citation}`")

        return "\n".join(lines)


class AcademicHub:
    """Coordena busca acadêmica híbrida (arquivos do aluno + artigos externos) e biblioteca local."""

    def __init__(
        self,
        rag: LocalDocumentRAG | None = None,
        web_search: WebSearchClient | None = None,
        memory: MemoryManager | None = None,
        study_dir: str | Path | None = None,
    ):
        self.rag = rag or LocalDocumentRAG()
        self.web_search = web_search or WebSearchClient()
        self.memory = memory or MemoryManager()
        self.study_dir = Path(
            os.path.expanduser(str(study_dir or "~/Documentos/Gestao_Comercial"))
        )

    # =========================================================================
    # Busca em Trabalhos e Arquivos Locais
    # =========================================================================

    def _categorize_local_path(self, path_str: str) -> tuple[str, str]:
        """Categoriza o arquivo com base na estrutura de pastas da graduação."""
        p_low = path_str.lower()
        if "projeto" in p_low or "pi_" in p_low or "pi " in p_low:
            return "Projeto Integrador (PI)", "Relevante para elaboração de diagnóstico e plano de ação comercial."
        elif "tcc" in p_low or "artigo" in p_low or "monografia" in p_low:
            return "TCC / Artigo Científico", "Fundamentação teórica ou metodologia de pesquisa acadêmica."
        elif "anotac" in p_low or "aula" in p_low or "uc_" in p_low or "uc " in p_low:
            return "Anotação de Aula / UC", "Conceito abordado na grade curricular do Senac."
        elif "livro" in p_low or "apostila" in p_low or "pdf" in p_low:
            return "Material Didático / Livro", "Conteúdo programático e bibliografia básica da disciplina."
        return "Documento Acadêmico", ""

    def search_local(self, query: str, limit: int = 4) -> list[AcademicDocument]:
        """Busca em arquivos de trabalhos, PIs e anotações locais usando o motor RAG."""
        if not self.rag:
            return []

        clean_q = query.strip()
        if not clean_q:
            return []

        rag_results = self.rag.search(clean_q, limit=limit * 2)
        if not rag_results:
            return []

        docs: list[AcademicDocument] = []
        seen_files: set[str] = set()

        for r in rag_results:
            # Prioriza materiais que estão na árvore de estudos
            cat_name, note = self._categorize_local_path(r.file_path)
            f_name = r.file_name or os.path.basename(r.file_path)
            title = r.title if r.title and len(r.title) > 3 else f_name

            key = f"{r.file_path}#{r.page_number}"
            if key in seen_files:
                continue
            seen_files.add(key)

            docs.append(
                AcademicDocument(
                    title=title,
                    source=cat_name,
                    abstract=r.snippet,
                    file_path_or_url=r.file_path,
                    is_local=True,
                    page_number=r.page_number,
                    discipline="Gestão Comercial",
                    relevance_note=note,
                )
            )
            if len(docs) >= limit:
                break

        return docs

    # =========================================================================
    # Busca em Artigos Científicos e Bases Oficiais (Web)
    # =========================================================================

    def _generate_abnt_citation(
        self, title: str, url: str, source_domain: str = "", year: str = ""
    ) -> str:
        """Gera citação preliminar em formato ABNT NBR 6023 para artigos/estudos online."""
        now = datetime.now()
        month_str = MONTH_ABBR.get(now.month, "set.")
        access_str = f"Acesso em: {now.day:02d} {month_str} {now.year}."
        year_str = year or str(now.year)

        # Trata fontes institucionais comuns
        u_low = (url or "").lower()
        if "scielo" in u_low:
            inst = "SCIELO BRASIL"
        elif "ibge" in u_low or "sidra" in u_low:
            inst = "IBGE - Instituto Brasileiro de Geografia e Estatística"
        elif "sebrae" in u_low:
            inst = "SEBRAE - Serviço Brasileiro de Apoio às Micro e Pequenas Empresas"
        elif "ipea" in u_low:
            inst = "IPEA - Instituto de Pesquisa Econômica Aplicada"
        else:
            inst = source_domain.upper() if source_domain else "PORTAL ACADÊMICO"

        clean_title = re.sub(r"\s+", " ", title.strip().rstrip("."))
        return f"{inst}. {clean_title}. {year_str}. Disponível em: <{url}>. {access_str}"

    def search_web(
        self, query: str, source: str = "all", limit: int = 4
    ) -> list[AcademicDocument]:
        """Busca em bases acadêmicas (SciELO, IBGE, Sebrae, IPEA, Scholar)."""
        if not self.web_search:
            return []

        results = self.web_search.academic_search(query, source=source, max_results=limit)
        docs: list[AcademicDocument] = []

        for r in results:
            # Extrai domínio ou fonte
            url_low = (r.url or "").lower()
            if "scielo" in url_low:
                src_label = "SciELO"
            elif "ibge" in url_low or "sidra" in url_low:
                src_label = "IBGE / SIDRA"
            elif "sebrae" in url_low:
                src_label = "Sebrae"
            elif "ipea" in url_low:
                src_label = "IPEA"
            elif "scholar" in url_low:
                src_label = "Google Acadêmico"
            else:
                src_label = "Base Científica"

            # Tenta inferir ano a partir do snippet ou título
            year_match = re.search(r"\b(201[5-9]|202[0-6])\b", f"{r.title} {r.snippet}")
            year_val = year_match.group(1) if year_match else ""

            abnt = self._generate_abnt_citation(r.title, r.url, source_domain=src_label, year=year_val)

            docs.append(
                AcademicDocument(
                    title=r.title,
                    source=src_label,
                    year=year_val,
                    abstract=r.snippet,
                    abnt_citation=abnt,
                    file_path_or_url=r.url,
                    is_local=False,
                    discipline="Gestão Comercial",
                )
            )

        return docs

    # =========================================================================
    # Busca Híbrida e Roteamento Inteligente
    # =========================================================================

    def search(
        self,
        query: str,
        scope: str = "auto",
        source: str = "all",
        limit: int = 4,
    ) -> list[AcademicDocument]:
        """Realiza busca direcionada ou híbrida com base no escopo e na intenção."""
        q_low = query.lower()

        # Determina escopo se for 'auto'
        if scope == "auto":
            is_explicit_local = any(
                k in q_low
                for k in [
                    "meu pi", "meus pis", "projeto integrador", "meu trabalho", "meus trabalhos",
                    "minha tarefa", "minhas tarefas", "minha anotação", "minhas anotações",
                    "meu tcc", "meus arquivos", "minha aula", "no meu arquivo", "no meu documento"
                ]
            )
            is_explicit_web = any(
                k in q_low
                for k in [
                    "scielo", "sebrae", "ibge", "ipea", "scholar", "pesquisa científica",
                    "artigo científico", "artigos científicos", "literatura", "autores",
                    "revista", "periódicos", "pesquise artigos", "buscar artigos"
                ]
            )

            if is_explicit_local and not is_explicit_web:
                scope = "local"
            elif is_explicit_web and not is_explicit_local:
                scope = "web"
            else:
                scope = "hybrid"

        if scope == "local":
            return self.search_local(query, limit=limit)
        elif scope == "web":
            return self.search_web(query, source=source, limit=limit)
        else:
            # Híbrido: tenta local primeiro; completa com web
            local_docs = self.search_local(query, limit=limit)
            needed = limit - len(local_docs)
            if needed > 0:
                web_docs = self.search_web(query, source=source, limit=needed)
                return local_docs + web_docs
            return local_docs

    # =========================================================================
    # Formatação de Resposta Estruturada para o Chat (ActionPlan)
    # =========================================================================

    def format_chat_response(
        self,
        documents: list[AcademicDocument],
        query: str,
        intro: str = "",
    ) -> ActionPlan:
        """Monta a resposta em Markdown com cards e ações executáveis correspondentes."""
        if not documents:
            thought = (
                f"Nenhum material acadêmico ou artigo encontrado para o termo **'{query}'**.\n\n"
                f"💡 **Dica:** Tente especificar termos mais amplos (ex.: *'comportamento do consumidor'*, *'fidelização de clientes'*, *'markup e preço'*)."
            )
            return ActionPlan(thought=thought, actions=[])

        total = len(documents)
        local_count = sum(1 for d in documents if d.is_local)
        web_count = total - local_count

        if not intro:
            if local_count > 0 and web_count > 0:
                intro = f"Localizei **{total} materiais acadêmicos** para **'{query}'** (sendo {local_count} nos seus arquivos e {web_count} em fontes científicas externas):"
            elif local_count > 0:
                intro = f"Localizei **{local_count} trecho(s) nos seus trabalhos e anotações** para **'{query}'**:"
            else:
                intro = f"Encontrei **{web_count} artigos e pesquisas acadêmicas** sobre **'{query}'**:"

        cards = [d.format_card(idx + 1) for idx, d in enumerate(documents)]
        thought_body = f"{intro}\n\n" + "\n\n---\n\n".join(cards)

        # Monta as ações correspondentes para os botões interativos do chat
        actions: list[DesktopAction] = []
        for idx, doc in enumerate(documents):
            if doc.is_local:
                f_name = os.path.basename(doc.file_path_or_url)
                p_text = f" (Pág. {doc.page_number})" if doc.page_number > 0 else ""
                actions.append(
                    DesktopAction(
                        action_type=ActionType.OPEN_DOCUMENT,
                        target=doc.file_path_or_url,
                        params={"page_number": doc.page_number},
                        description=f"Abrir {f_name}{p_text}",
                    )
                )
            else:
                clean_title = (doc.title[:38] + "...") if len(doc.title) > 40 else doc.title
                actions.append(
                    DesktopAction(
                        action_type=ActionType.OPEN_URL,
                        target=doc.file_path_or_url,
                        description=f"Acessar no {doc.source}: {clean_title}",
                    )
                )

        return ActionPlan(thought=thought_body, actions=actions)

    # =========================================================================
    # Operações de Biblioteca e Fichamento (SQLite memory.db)
    # =========================================================================

    def save_to_library(self, doc: AcademicDocument) -> int:
        """Salva um documento acadêmico na tabela academic_library do SQLite."""
        return self.memory.save_academic_entry(
            title=doc.title,
            authors=doc.authors,
            year=doc.year,
            source=doc.source,
            abstract=doc.abstract,
            abnt_citation=doc.abnt_citation,
            file_path_or_url=doc.file_path_or_url,
            discipline=doc.discipline,
            tags=doc.tags,
        )

    def list_saved_library(self, query: str | None = None, limit: int = 15) -> list[AcademicDocument]:
        """Recupera itens salvos no fichamento acadêmico."""
        rows = self.memory.list_academic_entries(query=query, limit=limit)
        docs: list[AcademicDocument] = []
        for r in rows:
            is_local = not r["file_path_or_url"].startswith(("http://", "https://"))
            docs.append(
                AcademicDocument(
                    title=r["title"],
                    authors=r["authors"],
                    year=r["year"],
                    source=r["source"] or ("Arquivo Local" if is_local else "Web"),
                    abstract=r["abstract"],
                    abnt_citation=r["abnt_citation"],
                    file_path_or_url=r["file_path_or_url"],
                    is_local=is_local,
                    discipline=r["discipline"],
                    tags=r["tags"],
                )
            )
        return docs
