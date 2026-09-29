# Decisão de design: funções puras para transformar um `MCPPrompt` em algo que a
# paleta de comandos entenda. Ficam fora de `ui/` de propósito — o mesmo mapeamento
# é usado pelo roteamento do `run_palette_command` e pelos testes, e nada aqui pode
# depender de GTK.

"""Utilitários puros para apresentar prompts MCP na paleta de comandos.

Um prompt MCP é **texto**, não ação: escolhê-lo na paleta não executa nada no
sistema, apenas devolve um texto pronto para o modelo. Toda a lógica de nome,
busca e coleta de argumentos vive aqui, sem GTK e sem rede, para poder ser
testada isoladamente.
"""

from __future__ import annotations

import re
from typing import Any

from .protocol import MCPPrompt, MCPPromptArgument

#: Prefixo que marca um comando da paleta como "preenche a entrada", em vez de
#: disparar um handler. Precisa ser improvável de colidir com os nomes estáticos
#: (`app.*`) e continuar legível em log.
PROMPT_COMMAND_PREFIX = "mcp-prompt::"

#: Palavras que não ajudam a busca e só inflam o haystack.
_STOPWORDS = frozenset({
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "para",
    "por", "com", "um", "uma", "no", "na", "nos", "nas", "ao", "aos", "que",
    "the", "of", "to", "for", "and", "with", "an", "in", "on",
})

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+")


def prompt_command_name(prompt: MCPPrompt) -> str:
    """Nome do comando da paleta para um prompt, derivado do nome qualificado.

    Usa o nome qualificado (``mcp__server__prompt``) e não o simples: dois
    servidores podem expor um prompt chamado ``review``, e o comando precisa
    apontar para um deles sem ambiguidade.
    """
    return f"{PROMPT_COMMAND_PREFIX}{prompt.qualified_name}"


def is_prompt_command(name: str) -> bool:
    """True se o nome do comando da paleta pertence a um prompt MCP."""
    return bool(name) and name.startswith(PROMPT_COMMAND_PREFIX)


def qualified_name_from_command(name: str) -> str:
    """Caminho inverso de :func:`prompt_command_name`.

    Devolve ``""`` quando o nome não é de um prompt, para que o chamador possa
    tratar o caso sem inspecionar o prefixo de novo.
    """
    if not is_prompt_command(name):
        return ""
    return name[len(PROMPT_COMMAND_PREFIX):]


def prettify_prompt_name(name: str) -> str:
    """``code_review`` / ``code-review`` / ``codeReview`` -> ``Code review``.

    O nome vem do servidor e costuma ser um identificador, não um rótulo. Só a
    primeira palavra é capitalizada: manter o resto em minúsculas evita o
    "Code Review De Pull Request" que denuncia texto gerado por máquina.
    """
    raw = (name or "").strip()
    if not raw:
        return ""

    # Separa camelCase antes de trocar os separadores, senão "codeReview" vira
    # uma palavra só e perde a fronteira.
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    words = [w for w in re.split(r"[_\-\s.]+", spaced) if w]
    if not words:
        return raw

    first = words[0]
    # Só a inicial: preserva siglas ("api" -> "Api" é feio, mas "API" -> "API").
    head = first[:1].upper() + first[1:]
    tail = [w.lower() for w in words[1:]]
    return " ".join([head, *tail])


def _words(text: str) -> list[str]:
    """Palavras úteis (sem stopwords, com 2+ caracteres) de um texto livre."""
    out: list[str] = []
    for match in _WORD_RE.findall((text or "").lower()):
        if len(match) < 2 or match in _STOPWORDS:
            continue
        out.append(match)
    return out


def prompt_keywords(prompt: MCPPrompt) -> tuple[str, ...]:
    """Palavras-chave de busca derivadas do próprio prompt.

    Inclui o nome do servidor e o do prompt separados, porque a busca da paleta
    casa por palavra: sem isso, procurar por "review" não acharia
    ``mcp__github__code_review``, cujo título é "Code review" mas cujo nome
    completo nunca aparece inteiro na consulta.
    """
    collected: list[str] = ["mcp", "prompt", prompt.server_name]
    collected.extend(_words(prompt.name))
    collected.extend(_words(prompt.description))

    seen: set[str] = set()
    unique: list[str] = []
    for word in collected:
        low = word.lower()
        if low and low not in seen:
            seen.add(low)
            unique.append(low)
    return tuple(unique)


def prompt_subtitle(prompt: MCPPrompt) -> str:
    """Linha secundária do comando: a descrição, ou o que resta de útil.

    Nunca devolve vazio — uma linha sem subtítulo deixa o item visualmente mais
    baixo que os vizinhos, o que parece defeito de layout.
    """
    description = (prompt.description or "").strip()
    if description:
        return description

    # Plural escrito por extenso, não por sufixo: "opcional" + "s" dá
    # "opcionals", que é o tipo de detalhe que denuncia texto gerado.
    required = len(prompt.required_arguments)
    if required:
        noun = "argumento" if required == 1 else "argumentos"
        adjective = "obrigatório" if required == 1 else "obrigatórios"
        return f"{required} {noun} {adjective}"

    total = len(prompt.arguments)
    if total:
        noun = "argumento" if total == 1 else "argumentos"
        adjective = "opcional" if total == 1 else "opcionais"
        return f"{total} {noun} {adjective}"

    return f"Prompt do servidor {prompt.server_name}"


def missing_required_arguments(
    prompt: MCPPrompt, arguments: dict[str, Any] | None
) -> list[MCPPromptArgument]:
    """Argumentos obrigatórios ainda sem valor preenchido.

    Um valor só conta como preenchido se tiver conteúdo depois de remover os
    espaços: uma caixa de texto deixada em branco é indistinguível de não
    respondida para o servidor, mas é indistinguível de "preenchida" para o
    código que só faz ``if name in arguments``.
    """
    values = arguments or {}
    missing: list[MCPPromptArgument] = []
    for arg in prompt.required_arguments:
        value = values.get(arg.name)
        if value is None or not str(value).strip():
            missing.append(arg)
    return missing


def clean_arguments(
    values: dict[str, Any] | None, prompt: MCPPrompt | None = None
) -> dict[str, str]:
    """Normaliza os valores coletados no diálogo para envio ao servidor.

    - descarta chaves com valor vazio (o servidor receberia ``""`` e trataria
      como argumento presente);
    - aplica ``strip`` (espaço perdido nas pontas é erro de digitação, não
      conteúdo);
    - quando ``prompt`` é informado, mantém apenas argumentos que o servidor
      declarou — mandar um campo extra faz servidores estritos rejeitarem a
      chamada inteira.
    """
    if not values:
        return {}

    allowed = {a.name for a in prompt.arguments} if prompt is not None else None

    cleaned: dict[str, str] = {}
    for key, raw in values.items():
        if not isinstance(key, str) or not key:
            continue
        if allowed is not None and key not in allowed:
            continue
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        cleaned[key] = text
    return cleaned
