# Decisão de design: a exportação vive em `core/` e não em `ui/` porque não depende de
# GTK: é uma função pura (sessão -> texto). Isso mantém o recurso testável sem display
# e reutilizável pela CLI. O diálogo de "salvar como" fica na janela; o formato do
# arquivo fica aqui.
#
# O Markdown gerado é o *fonte* da conversa, não o markup Pango de exibição
# (`ui/markdown.py`): respostas são copiadas literalmente para que blocos de código,
# tabelas e links cheguem intactos ao arquivo.

"""Exportação de conversas do Zorin Copilot para Markdown."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Optional

from .session import ChatTurn, TopicSession

__all__ = [
    "exportable_turns",
    "render_conversation_markdown",
    "slugify",
    "suggest_filename",
]

#: Separador entre turnos. Uma régua horizontal é o idioma mais portável entre
#: renderizadores (GitHub, Obsidian, VS Code) e não colide com o frontmatter,
#: que só é interpretado no início absoluto do arquivo.
_TURN_SEPARATOR = "\n\n---\n\n"

#: Título usado quando a sessão ainda não recebeu nenhum prompt.
_DEFAULT_TITLE = "Conversa sem título"

#: Placeholders para turnos com um dos lados vazio (ex.: erro de streaming).
_EMPTY_PROMPT = "_(sem texto)_"
_EMPTY_ANSWER = "_(sem resposta)_"


def exportable_turns(session: TopicSession) -> list[ChatTurn]:
    """Turnos que fazem sentido exportar, na ordem cronológica.

    Inclui o turno "órfão" guardado em memória quando o tópico não está fixado
    (`_last_unpinned_turn`): ele já aparece na tela, então também deve aparecer
    no arquivo. A checagem é por identidade, porque `ChatTurn` é um dataclass
    comparável por valor e dois turnos iguais seriam confundidos.
    """
    turns: list[ChatTurn] = list(session.turns)
    pending = getattr(session, "_last_unpinned_turn", None)
    if pending is not None and not any(t is pending for t in turns):
        turns.append(pending)
    return [t for t in turns if t.prompt.strip() or t.answer.strip()]


def slugify(text: str, limit: int = 60) -> str:
    """Converte um título em nome de arquivo seguro (ASCII, minúsculo, sem espaços).

    Acentos são decompostos e descartados ("Análise" -> "analise") para evitar
    problemas em sistemas de arquivo e ao compartilhar o arquivo.
    """
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    if len(slug) > limit:
        slug = slug[:limit].rstrip("-")
    return slug


def suggest_filename(session: TopicSession, now: Optional[datetime] = None) -> str:
    """Nome sugerido para o arquivo: `<titulo>-<AAAA-MM-DD>.md`."""
    base = slugify(getattr(session, "title", "") or "") or "conversa"
    stamp = (now or datetime.now()).strftime("%Y-%m-%d")
    return f"{base}-{stamp}.md"


def _yaml_scalar(value: object) -> str:
    """Escapa um valor para a linha de frontmatter.

    Sempre entre aspas duplas: assim títulos com `:` `#` ou `- ` (muito comuns em
    prompts do usuário) não quebram o YAML.
    """
    text = " ".join(str(value).split())
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _format_timestamp(value: object) -> str:
    """`dd/mm/AAAA HH:MM` a partir de um epoch, ou "" se não der."""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _render_turn(turn: ChatTurn) -> str:
    stamp = _format_timestamp(turn.timestamp)
    heading = "## Você" + (f" · {stamp}" if stamp else "")
    prompt = turn.prompt.strip() or _EMPTY_PROMPT
    answer = turn.answer.strip() or _EMPTY_ANSWER
    return "\n".join([heading, "", prompt, "", "## Copilot", "", answer])


def render_conversation_markdown(
    session: TopicSession,
    *,
    provider: str = "",
    model: str = "",
    exported_at: Optional[datetime] = None,
) -> str:
    """Renderiza a sessão como um documento Markdown com frontmatter YAML.

    Devolve `""` quando não há turnos exportáveis — a UI usa isso para avisar que
    não há nada a salvar em vez de gravar um arquivo vazio.
    """
    turns = exportable_turns(session)
    if not turns:
        return ""

    now = exported_at or datetime.now()
    title = (getattr(session, "title", "") or "").strip() or _DEFAULT_TITLE

    head = [
        "---",
        f"title: {_yaml_scalar(title)}",
        'source: "zorin-copilot"',
        f"topic_id: {_yaml_scalar(getattr(session, 'id', ''))}",
    ]
    if provider:
        head.append(f"provider: {_yaml_scalar(provider)}")
    if model:
        head.append(f"model: {_yaml_scalar(model)}")
    head += [
        f"turn_count: {len(turns)}",
        f"created_at: {_yaml_scalar(getattr(session, 'created_at', ''))}",
        f"updated_at: {_yaml_scalar(getattr(session, 'updated_at', ''))}",
        f"exported_at: {_yaml_scalar(now.isoformat(timespec='seconds'))}",
        "---",
        "",
        f"# {title}",
        "",
    ]

    meta = [f"Exportado do Zorin Copilot em {now.strftime('%d/%m/%Y %H:%M')}"]
    if provider or model:
        meta.append(" · ".join([p for p in (provider, model) if p]))
    head.append("> " + " · ".join(meta))

    body = _TURN_SEPARATOR.join(_render_turn(t) for t in turns)
    return "\n".join(head) + "\n\n" + body + "\n"
