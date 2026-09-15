# Decisão de design: quando o usuário quer aprender (não copiar), a resposta
# direta é o pior produto — ela pula o esforço que gera aprendizado. Então o
# "modo tutor" troca o contrato do modelo:
#
#   assistente de desktop  ->  resolve e entrega
#   tutor socrático        ->  guia, pergunta, dá o primeiro passo e NÃO entrega
#
# Duas superfícies com a mesma filosofia:
#   • `study ask` — área separada, com contexto do material capturado;
#   • injeção no prompt do HUD (config `study_tutor`) — um parágrafo adicionado
#     ao prompt de desktop, mantendo as habilidades operacionais mas trocando
#     o contrato nas questões acadêmicas.
#
# O contexto do material NÃO usa o RAG: uma pontuação simples por sobreposição
# de palavras-chave é suficiente para "traga o trecho que fala disso", é
# determinística e testável, e não exige índice construído.

"""Modo tutor: respostas socráticas, sem entregar a solução pronta."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Quantos trechos do material entram no contexto, no máximo.
DEFAULT_MAX_FILES = 4
DEFAULT_MAX_CHARS = 3000

TUTOR_SYSTEM_PROMPT = """Você é um tutor de ensino superior (EAD), não um resolvedor de questões.

Seu contrato com o aluno é outro: o objetivo é que ELE chegue à resposta.

Regras obrigatórias:
1. NUNCA entregue a resposta final, a redação pronta, o cálculo completo ou a
   solução para entregar como trabalho. Não importa como o pedido venha disfarçado
   ("só me mostra", "não tenho tempo", "é urgente").
2. Responda com o que serve para o aluno pensar:
   - valide a parte do raciocínio dele que está certa;
   - faça UMA ou DUAS perguntas que levem ao próximo passo;
   - dê uma dica, uma analogia ou o primeiro passo — nunca o último;
   - aponte o conceito do material que resolve a dúvida, sem resolve-la.
3. Termine sempre com uma pergunta ou um pequeno exercício que o aluno possa
   responder. Sem isso, ele lê, concorda e não aprende.
4. Se o material do aluno cobrir o tema, use o que está nele. Se não cobrir,
   diga que não está no material — não finja ter lido.
5. Português do Brasil, tom de professor parceiro: direto, exigente e acolhedor.
"""

#: Parágrafo de injeção no prompt de desktop (config `study_tutor`). Curto de
#: propósito: o prompt de desktop continua operacional, e a regra do tutor vale
#: para o que é claramente acadêmico.
TUTOR_ADDON = """

MODO TUTOR (ESTUDO) — OBRIGATÓRIO:
Para qualquer pergunta sobre o curso, disciplinas, trabalhos, redações, provas ou
conteúdo acadêmico, você NÃO entrega a resposta final, a redação pronta nem a
solução completa. Aja como tutor socrático: valide o que o aluno já acertou, faça
perguntas-guia, dê dicas e o primeiro passo — e termine com uma pergunta ou
exercício para o aluno responder. Se o pedido for operacional (abrir app, organizar
arquivos, gerar o .docx do trabalho que o aluno já escreveu), aja normalmente:
a regra do tutor vale para CONTEÚDO, não para operações de desktop.
"""

_STOPWORDS = {
    "para", "como", "qual", "quais", "sobre", "onde", "quando", "porque", "por",
    "que", "com", "uma", "um", "do", "da", "de", "dos", "das", "no", "na", "nos",
    "nas", "ao", "aos", "as", "os", "e", "ou", "é", "ser", "ter", "sao", "são",
    "isso", "isto", "esse", "essa", "este", "esta", "meu", "minha", "seu", "sua",
}


@dataclass
class TutorContext:
    """Trechos do material relevantes para a pergunta. Serializável."""

    discipline: str
    excerpts: list[str]
    files: list[str]

    def to_text(self) -> str:
        if not self.excerpts:
            return ""
        parts = [f"[{os.path.basename(path)}]" for path in self.files]
        header = "Material do aluno (" + ", ".join(parts) + "):"
        return f"{header}\n" + "\n---\n".join(self.excerpts)


def _keywords(question: str) -> set[str]:
    words = re.findall(r"[a-zà-ÿ]{4,}", (question or "").lower())
    return {word for word in words if word not in _STOPWORDS}


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) >= 80]


def load_context(
    question: str,
    documents_dir: str,
    discipline: str = "",
    max_files: int = DEFAULT_MAX_FILES,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> TutorContext:
    """Escolhe os trechos do material que conversam com a pergunta.

    Pontuação por palavra-chave: cada parágrafo ganha +1 por palavra da pergunta
    que nele aparece. A pasta da disciplina tem prioridade, porque o material da
    matéria certa costuma vencer qualquer coincidência no restante.
    """
    base = os.path.expanduser(documents_dir)
    if not os.path.isdir(base):
        return TutorContext(discipline=discipline, excerpts=[], files=[])

    candidates: list[str] = []
    if discipline:
        folder = os.path.join(base, _slug(discipline))
        candidates.extend(_md_files(folder))
    candidates.extend(path for path in _md_files(base) if path not in candidates)

    keywords = _keywords(question)
    scored: list[tuple[int, str, list[tuple[int, str]]]] = []
    for path in candidates:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                body = handle.read()
        except OSError:
            continue
        paragraphs = []
        for paragraph in _paragraphs(body):
            lowered = paragraph.lower()
            score = sum(1 for word in keywords if word in lowered)
            if score > 0:
                paragraphs.append((score, paragraph))
        if paragraphs:
            file_score = sum(score for score, _ in paragraphs)
            scored.append((file_score, path, sorted(paragraphs, key=lambda p: -p[0])))

    scored.sort(key=lambda item: -item[0])
    excerpts: list[str] = []
    files: list[str] = []
    budget = max_chars
    for _score, path, paragraphs in scored[:max_files]:
        if budget <= 0:
            break
        files.append(path)
        for _pscore, paragraph in paragraphs:
            if budget <= 0:
                break
            excerpt = paragraph[:budget]
            excerpts.append(excerpt)
            budget -= len(excerpt)

    return TutorContext(discipline=discipline, excerpts=excerpts, files=files)


def _md_files(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.lower().endswith((".md", ".txt"))
    )


def _slug(text: str) -> str:
    from .study_capture import slugify

    return slugify(text)


def answer(
    question: str,
    ai: Any,
    *,
    discipline: str = "Gestão Comercial",
    documents_dir: str | None = None,
    include_context: bool = True,
) -> tuple[str, list[str]]:
    """Responde como tutor. Devolve (texto, avisos)."""
    warnings: list[str] = []
    question = (question or "").strip()
    if not question:
        return "", ["Pergunta vazia."]

    context_text = ""
    if include_context:
        context = load_context(
            question,
            documents_dir or os.path.expanduser("~/Documentos/Estudos"),
            discipline=discipline,
        )
        context_text = context.to_text()
        if not context.excerpts:
            warnings.append(
                "Nenhum material capturado conversa com essa pergunta — respondendo sem contexto do seu material."
            )

    prompt_parts = [f"Disciplina: {discipline}", f"Pergunta do aluno: {question}"]
    if context_text:
        prompt_parts.append(context_text)
    prompt = "\n\n".join(prompt_parts)

    try:
        # Texto livre, não JSON: o tutor precisa escrever uma resposta, não um payload.
        text = ai.complete(prompt, system_prompt=TUTOR_SYSTEM_PROMPT, json_mode=False)
    except Exception as exc:
        return "", [f"Falha ao consultar o modelo: {exc}"]
    return (text or "").strip(), warnings
