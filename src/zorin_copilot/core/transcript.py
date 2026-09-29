"""Vocabulário único de papéis e forma de entrada da transcrição (Entrega G / P1-7).

Antes desta entrega os dois clientes de voz discordavam em **dois** eixos:

- **Nome do papel:** `ai/live.py` emitia `"assistant"`; `ai/local_voice.py` emitia
  `"agent"`. O mesmo conceito, dois nomes — e o `ui/live_view.py` só não quebrava porque
  mapeia *qualquer* papel desconhecido para assistente (funcionava por acidente).
- **Forma da entrada:** `ai/live.py` guardava tuplas `(role, text)`; `ai/local_voice.py`
  guardava dicts `{"role","text"}` — e os dois devolviam isso sob a **mesma** chave
  `"transcripts"` em `get_session_summary()`.

Aqui ficam o vocabulário canônico, a normalização (que também lê dados antigos) e a forma
única. Este módulo é deliberadamente **puro**: sem GTK, sem rede, sem estado.

**Limite declarado:** isto é *atribuição de turno* (usuário vs. copiloto), não *diarização
multi-falante*. Numa reunião com três pessoas, todas aparecem como `user`.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Papel canônico do humano que fala com o assistente.
ROLE_USER = "user"
#: Papel canônico do assistente.
ROLE_ASSISTANT = "assistant"

#: Nomes de papel já usados (ou aceitos na entrada) → papel canônico.
#: `"agent"` é o rótulo histórico de `local_voice.py`; `"você"/"voce"` apareciam em
#: `ui/live_view.py` como entradas reconhecidas.
LEGACY_ROLE_ALIASES: dict[str, str] = {
    "user": ROLE_USER,
    "usuário": ROLE_USER,
    "usuario": ROLE_USER,
    "você": ROLE_USER,
    "voce": ROLE_USER,
    "assistant": ROLE_ASSISTANT,
    "agent": ROLE_ASSISTANT,
    "agente": ROLE_ASSISTANT,
    "copilot": ROLE_ASSISTANT,
}


def normalize_role(role: Any) -> str:
    """Devolve o papel canônico de `role`.

    Papel desconhecido (ou vazio) cai em `ROLE_ASSISTANT`, que é exatamente o *fallback*
    que `ui/live_view.py` já aplicava: `"user" if role in (...) else "assistant"`. Manter
    esse sentido evita que uma entrada inesperada passe a ser atribuída ao **usuário** —
    o erro mais perigoso dos dois, porque poria palavras do copiloto na boca da pessoa.
    """
    if role is None:
        return ROLE_ASSISTANT
    chave = str(role).strip().lower()
    return LEGACY_ROLE_ALIASES.get(chave, ROLE_ASSISTANT)


def make_entry(role: Any, text: Any) -> dict[str, str]:
    """Monta a entrada canônica da transcrição: `{"role": ..., "text": ...}`."""
    return {"role": normalize_role(role), "text": "" if text is None else str(text)}


def entry_role(entry: Any) -> str:
    """Lê o papel de uma entrada — **tolerante** a dict novo e a tupla legada.

    Dados já persistidos (ou gravados por uma versão anterior) podem estar em qualquer das
    duas formas; a leitura não pode explodir por causa disso.
    """
    if isinstance(entry, Mapping):
        return normalize_role(entry.get("role"))
    if isinstance(entry, (tuple, list)) and len(entry) >= 1:
        return normalize_role(entry[0])
    return ROLE_ASSISTANT


def entry_text(entry: Any) -> str:
    """Lê o texto de uma entrada — tolerante a dict novo e a tupla legada."""
    if isinstance(entry, Mapping):
        return str(entry.get("text") or "")
    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
        return str(entry[1] or "")
    return ""


def extract_transcription_text(node: Any) -> str:
    """Extrai o texto de um nó de transcrição do Gemini Live.

    O campo pode chegar como `{"text": "..."}` ou já como string, dependendo do formato do
    servidor. Extrair de forma tolerante evita perder a fala do usuário por causa de um
    detalhe de envelope.
    """
    if node is None:
        return ""
    if isinstance(node, Mapping):
        return str(node.get("text") or "").strip()
    return str(node).strip()
