# Decisão de design: a revisão é uma ETAPA SEPARADA do envio, e por dois motivos.
# (1) O Bruno pediu que o e-mail seja SEMPRE confirmado antes de enviar, com
#     correção gramatical/ortográfica conforme o contexto que ele fornecer — ou
#     seja, revisar e enviar são momentos distintos, e a confirmação fica no meio.
# (2) Ela degrada de forma HONESTA: sem provedor de IA disponível, `reviewed` volta
#     `False` e o texto original é preservado. O contrário — devolver o texto como
#     se tivesse sido revisado — seria a pior falha possível aqui: o usuário
#     enviaria um e-mail achando que foi corrigido.

"""Revisão gramatical/ortográfica de rascunhos de e-mail antes do envio."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Esquema pedido ao modelo. `generate_with_tool` devolve um dict que o obedece.
REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "Assunto corrigido."},
        "body": {"type": "string", "description": "Corpo corrigido."},
        "changes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lista curta do que foi corrigido (uma frase por item).",
        },
    },
    "required": ["subject", "body", "changes"],
}

#: Assunto muito longo é cortado na maioria dos clientes. 78 é o limite clássico.
MAX_SUBJECT_CHARS = 78

_DOUBLE_SPACE_RE = re.compile(r"[^\S\n]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")


@dataclass
class ReviewResult:
    """Resultado da revisão. `reviewed=False` significa "não revisado", nunca "ok"."""

    subject: str
    body: str
    changes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    reviewed: bool = False
    note: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def lint_draft(subject: str, body: str) -> list[str]:
    """Checagens determinísticas — sem IA, sem rede, testáveis.

    São problemas objetivos (assunto vazio, espaço antes de vírgula). Julgamento de
    estilo fica com o modelo; aqui só entra o que é inequívoco.
    """
    issues: list[str] = []
    subject = str(subject or "")
    body = str(body or "")

    if not subject.strip():
        issues.append("Assunto vazio.")
    elif len(subject.strip()) > MAX_SUBJECT_CHARS:
        issues.append(
            f"Assunto com {len(subject.strip())} caracteres (acima de {MAX_SUBJECT_CHARS}); "
            "pode ser cortado no cliente."
        )
    if not body.strip():
        issues.append("Corpo vazio.")

    if _DOUBLE_SPACE_RE.search(body):
        issues.append("Espaços duplicados no corpo.")
    if _SPACE_BEFORE_PUNCT_RE.search(body):
        issues.append("Espaço antes de pontuação (ex.: 'texto ,' ).")
    if any(line != line.rstrip() for line in body.splitlines()):
        issues.append("Linhas com espaço em branco no fim.")

    return issues


def _build_prompt(subject: str, body: str, context: str) -> str:
    ctx = (context or "").strip() or "não informado — use tom neutro e profissional"
    return (
        "Revise o e-mail abaixo em português do Brasil: corrija ortografia, acentuação, "
        "concordância, regência e pontuação.\n"
        "Regras: NÃO invente informação, NÃO altere o sentido, NÃO traduza, NÃO adicione "
        "saudações ou despedidas que não existam.\n"
        f"Contexto fornecido pelo usuário (tom, destinatário, propósito): {ctx}\n"
        "Se o texto já estiver correto, devolva-o idêntico e liste 'nenhuma correção'.\n\n"
        f"ASSUNTO:\n{subject}\n\nCORPO:\n{body}\n"
    )


def _default_provider() -> Any | None:
    """Provedor de LLM da config. Import tardio — evita ciclo `core`↔`ai`."""
    try:
        from ..ai.providers import get_llm_provider
        from .config import CopilotConfig

        return get_llm_provider(CopilotConfig.load())
    except Exception as exc:
        logger.debug("Provedor indisponível para a revisão: %s", exc)
        return None


def review_draft(
    subject: str,
    body: str,
    context: str = "",
    provider: Any = None,
) -> ReviewResult:
    """Revisa assunto e corpo. Nunca levanta: falha vira `reviewed=False`.

    `provider` é injetável para teste (qualquer objeto com `is_configured()` e
    `generate_with_tool(prompt, schema)`).
    """
    subject = str(subject or "")
    body = str(body or "")
    issues = lint_draft(subject, body)

    prov = provider if provider is not None else _default_provider()
    if prov is None:
        return ReviewResult(
            subject=subject,
            body=body,
            issues=issues,
            reviewed=False,
            note="Nenhum provedor de IA configurado — o texto NÃO foi revisado.",
        )
    try:
        if hasattr(prov, "is_configured") and not prov.is_configured():
            return ReviewResult(
                subject=subject,
                body=body,
                issues=issues,
                reviewed=False,
                note="Provedor de IA não configurado — o texto NÃO foi revisado.",
            )
    except Exception as exc:
        logger.debug("Falha ao consultar is_configured(): %s", exc)

    try:
        parsed = prov.generate_with_tool(_build_prompt(subject, body, context), REVIEW_SCHEMA)
    except Exception as exc:
        logger.warning("Revisão do e-mail falhou: %s", exc)
        return ReviewResult(
            subject=subject,
            body=body,
            issues=issues,
            reviewed=False,
            note=f"Falha na revisão ({type(exc).__name__}) — o texto NÃO foi revisado.",
        )

    if not isinstance(parsed, dict):
        return ReviewResult(
            subject=subject,
            body=body,
            issues=issues,
            reviewed=False,
            note="Resposta da revisão em formato inesperado — o texto NÃO foi revisado.",
        )

    changes = [str(c) for c in (parsed.get("changes") or []) if str(c).strip()]
    return ReviewResult(
        subject=str(parsed.get("subject", subject)),
        body=str(parsed.get("body", body)),
        changes=changes,
        issues=issues,
        reviewed=True,
    )
