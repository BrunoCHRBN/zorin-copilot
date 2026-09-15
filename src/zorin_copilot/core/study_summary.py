# Decisão de design: resumo e glossário saem do MESMO material dos flashcards, mas
# atendem a outra necessidade — reler rápido antes da prova em vez de ser testado.
# Por isso este módulo reaproveita a infraestrutura de `study_cards` (leitura da fonte,
# StudyAI, recorte de contexto) e muda só o prompt e a validação:
#
#   • tópico de resumo precisa ser uma AFIRMAÇÃO completa (quem leu sabe o fato),
#     não um título de seção ("Introdução", "Conceitos gerais");
#   • termo de glossário precisa ser termo TÉCNICO da disciplina, com definição em
#     palavras próprias — definição copiada do material não ensina nada;
#   • o JSON vazio é resposta válida: material sem conteúdo não vira resumo inventado.
#
# O agendamento de revisão continua em study_cards; aqui nada passa por SM-2.

"""Resumo em tópicos e glossário a partir de material capturado."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from .study_cards import (
    MAX_SOURCE_CHARS,
    MIN_SOURCE_WORDS,
    BUREAUCRATIC_PATTERNS,
    StudyAI,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "1"

#: Tópico de resumo abaixo disso é rótulo de seção, não conteúdo.
MIN_TOPIC_WORDS = 4
#: Acima disso virou parágrafo colado do material.
MAX_TOPIC_WORDS = 60

#: Definição de glossário: muito curta não define nada.
MIN_DEFINITION_WORDS = 4
MAX_DEFINITION_WORDS = 60

SUMMARY_SYSTEM_PROMPT = """Você produz material de revisão para um aluno de ensino superior (EAD).

Tarefa: dado o MATERIAL, devolver um resumo em tópicos e um glossário.

Regras obrigatórias:
1. Cada tópico é uma AFIRMAÇÃO COMPLETA, que faz sentido sozinha. Nada de rótulo
   de seção como "Introdução", "Conceitos", "Considerações finais".
2. Nada de metacomentário: não escreva "o autor explica", "neste material veremos",
   "o texto aborda". Escreva o fato.
3. O glossário traz só TERMOS TÉCNICOS que aparecem no material, com a definição
   em palavras próprias (não copie a frase do texto).
4. NUNCA inclua dado burocrático ou de navegação: código de curso/disciplina/turma,
   carga horária, nome de professor, prazos, menus, links, avisos, sumário.
5. Se o material não tiver conteúdo de estudo, devolva listas vazias:
   {"topics": [], "glossary": []}. Inventar é pior que devolver vazio.
6. Escreva em português do Brasil.

Responda SOMENTE com JSON válido, sem markdown e sem texto fora do JSON:
{"topics": ["afirmacao"], "glossary": [{"term": "termo", "definition": "definicao"}]}
"""

USER_TEMPLATE = """Disciplina: {discipline}
Material: {title}

Produza no máximo {max_topics} tópicos de resumo e no máximo {max_terms} termos de glossário.

MATERIAL:
\"\"\"
{text}
\"\"\"
"""


@dataclass
class GlossaryTerm:
    term: str
    definition: str

    def to_dict(self) -> dict[str, Any]:
        return {"term": self.term, "definition": self.definition}


@dataclass
class Summary:
    """Resumo + glossário de um material. Serializável (CLI --json)."""

    title: str
    source: str = ""
    discipline: str = "Gestão Comercial"
    topics: list[str] = field(default_factory=list)
    glossary: list[GlossaryTerm] = field(default_factory=list)
    created_at: str = ""
    provider: str = ""
    prompt_version: str = PROMPT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "discipline": self.discipline,
            "topics": list(self.topics),
            "glossary": [term.to_dict() for term in self.glossary],
            "created_at": self.created_at,
            "provider": self.provider,
            "prompt_version": self.prompt_version,
        }


def parse_summary(raw: str) -> dict[str, Any]:
    """Extrai topics/glossary da resposta do modelo. Nunca levanta."""
    from ..ai.agent_router import extract_json_object

    payload = extract_json_object(raw or "")
    if not isinstance(payload, dict):
        return {}
    return payload


def _is_useful_topic(text: str) -> bool:
    """Rótulo de seção e metacomentário não servem como tópico de revisão."""
    topic = " ".join((text or "").split())
    words = len(topic.split())
    if words < MIN_TOPIC_WORDS or words > MAX_TOPIC_WORDS:
        return False
    if not re.search(r"[^\W\d_]", topic, flags=re.UNICODE):
        return False
    lowered = topic.lower()
    if any(re.search(pattern, lowered) for pattern in BUREAUCRATIC_PATTERNS):
        return False
    # Metacomentário: o texto fala sobre si mesmo em vez de dizer o fato.
    if re.match(r"^(o|a|os|as)?\s*(texto|material|autor|cap[íi]tulo|aula|artigo)\b", lowered):
        return False
    if re.match(r"^(neste|nesta|nesse|nessa)\b", lowered):
        return False
    return True


def _is_useful_term(term: str, definition: str) -> bool:
    clean_term = " ".join((term or "").split())
    clean_def = " ".join((definition or "").split())
    if not clean_term or len(clean_term.split()) > 5:
        return False
    words = len(clean_def.split())
    if words < MIN_DEFINITION_WORDS or words > MAX_DEFINITION_WORDS:
        return False
    if not re.search(r"[^\W\d_]", clean_def, flags=re.UNICODE):
        return False
    if clean_term.lower() in clean_def.lower() and words < 8:
        return False  # "X é X" — não define nada
    lowered = f"{clean_term} {clean_def}".lower()
    if any(re.search(pattern, lowered) for pattern in BUREAUCRATIC_PATTERNS):
        return False
    return True


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = re.sub(r"\s+", " ", value.strip().lower())
        if key and key not in seen:
            seen.add(key)
            ordered.append(value.strip())
    return ordered


def generate_summary(
    text: str,
    ai: Any,
    *,
    title: str = "",
    discipline: str = "Gestão Comercial",
    max_topics: int = 8,
    max_terms: int = 12,
) -> tuple[Summary, list[str]]:
    """Gera resumo e glossário. Devolve (resumo, avisos)."""
    warnings: list[str] = []
    words = len((text or "").split())
    if words < MIN_SOURCE_WORDS:
        return Summary(title=title, discipline=discipline), [
            f"Material curto demais para resumir ({words} palavras, mínimo {MIN_SOURCE_WORDS})."
        ]

    excerpt = (text or "").strip()
    if len(excerpt) > MAX_SOURCE_CHARS:
        excerpt = excerpt[:MAX_SOURCE_CHARS]
        warnings.append(f"Material cortado em {MAX_SOURCE_CHARS} caracteres para geração.")

    prompt = USER_TEMPLATE.format(
        discipline=discipline,
        title=title or "(sem título)",
        max_topics=max_topics,
        max_terms=max_terms,
        text=excerpt,
    )
    try:
        raw = ai.complete(prompt, system_prompt=SUMMARY_SYSTEM_PROMPT, json_mode=True)
    except Exception as exc:
        return Summary(title=title, discipline=discipline), [f"Falha ao consultar o modelo: {exc}"]

    payload = parse_summary(raw)
    if not payload:
        return Summary(title=title, discipline=discipline), [
            "O modelo não devolveu conteúdo utilizável. Tente recapturar o material ou usar a nuvem."
        ]

    raw_topics = payload.get("topics")
    raw_terms = payload.get("glossary")

    topics = [t for t in (_dedupe([str(x) for x in raw_topics or []]) if isinstance(raw_topics, list) else [])]
    topics = [t for t in topics if _is_useful_topic(t)][:max_topics]
    if isinstance(raw_topics, list) and raw_topics and not topics:
        warnings.append("Nenhum tópico passou no filtro de utilidade (rótulos de seção são descartados).")

    glossary: list[GlossaryTerm] = []
    seen_terms: set[str] = set()
    if isinstance(raw_terms, list):
        for entry in raw_terms:
            if not isinstance(entry, dict):
                continue
            term = " ".join(str(entry.get("term") or "").split())
            definition = " ".join(str(entry.get("definition") or "").split())
            key = term.lower()
            if key in seen_terms:
                continue
            if not _is_useful_term(term, definition):
                continue
            seen_terms.add(key)
            glossary.append(GlossaryTerm(term=term, definition=definition))
            if len(glossary) >= max_terms:
                break
    if isinstance(raw_terms, list) and raw_terms and not glossary:
        warnings.append("Nenhum termo de glossário passou no filtro de utilidade.")

    summary = Summary(
        title=title,
        discipline=discipline,
        topics=topics,
        glossary=glossary,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        provider=getattr(ai, "used", "") or "",
    )
    if not topics and not glossary:
        warnings.append("Resumo vazio: o material pode não ter conteúdo de estudo utilizável.")
    return summary, warnings


def render_markdown(summary: Summary) -> str:
    """Markdown do resumo — vai para ~/Documentos/Estudos e pode ser indexado pelo RAG."""
    lines = [
        f"# Resumo — {summary.title or 'Material'}",
        "",
        f"- Gerado em: {summary.created_at or datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- Disciplina: {summary.discipline}",
    ]
    if summary.source:
        lines.append(f"- Origem: {summary.source}")
    if summary.provider:
        lines.append(f"- Modelo: {summary.provider}")
    lines += ["", "## Tópicos", ""]
    if summary.topics:
        lines += [f"- {topic}" for topic in summary.topics]
    else:
        lines.append("- (nenhum tópico aproveitável)")
    lines += ["", "## Glossário", ""]
    if summary.glossary:
        for term in summary.glossary:
            lines.append(f"- **{term.term}**: {term.definition}")
    else:
        lines.append("- (nenhum termo aproveitável)")
    lines.append("")
    return "\n".join(lines)


def save(summary: Summary, path: str) -> str:
    """Grava o resumo; nunca sobrescreve (ganha sufixo -2, -3)."""
    target = os.path.expanduser(path)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(target):
        stem, ext = os.path.splitext(target)
        counter = 2
        while os.path.exists(f"{stem}-{counter}{ext}"):
            counter += 1
        target = f"{stem}-{counter}{ext}"
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(summary))
    return target


def build_default_ai(mode: str = "auto") -> StudyAI:
    """Atalho para o CLI:StudyAI no modo pedido."""
    return StudyAI(mode=mode)
