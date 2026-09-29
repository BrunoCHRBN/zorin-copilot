# Decisão de design: **envelope compartilhado, e NÃO um barramento próprio.**
#
# O roteiro (`docs/PROPOSTA_JARVIS_AMBIENTE.md`, P1) pede "um fluxo de eventos com um
# único `subscribe`" e chama a peça de "barramento de percepção". Medido antes de
# escrever, o pedido se desfaz em duas partes — uma certa e uma prematura:
#
# 1. **O nome `perception` está tomado, e por outra coisa.** `core/perception.py` já
#    existe e é o canal de **visão do operador** (árvore AT-SPI2 + screenshot +
#    grounding VLM), consumido por `cli.py`, `ui/app.py` e `ai/browser_runtime.py`.
#    Aqui "percepção" significa *o que o agente vê na tela*, não *que eventos
#    chegaram*. Chamar esta peça de barramento de percepção criaria dois sentidos
#    para a mesma palavra dentro do mesmo pacote — daí `ambient_events`.
#
# 2. **Uma classe de fan-out seria a terceira cópia de algo já resolvido duas vezes.**
#    `StudySession.subscribe/publish` (`core/study_session.py:108`) e
#    `HomeAssistantRealtimeChannel.subscribe` (`core/home_assistant_realtime.py:246`)
#    já implementam, ambos, exatamente as três garantias que um barramento daria:
#    cópia da lista sob a trava com as chamadas **fora** dela, isolamento de exceção
#    por assinante, e cancelamento idempotente. Um terceiro fan-out seria a
#    duplicação que este repo já documentou como defeito duas vezes.
#
# O que **falta** de verdade é vocabulário: um formato único para que um consumidor
# saiba de onde veio o evento sem conhecer o formato cru do produtor. É só isso que
# este módulo entrega, e é por isso que ele não tem classe de barramento.
#
# **Precondição para a classe existir** (registrada para não se perder): uma **segunda
# fonte que interrompa**. Hoje há uma fonte que interrompe (a casa) e uma que não deve
# interromper nunca (arquivos) — não há nada a arbitrar entre elas, e um fan-out sem
# disputa é estrutura sem função. Quando `TemporalStateTracker` ou o `PowerManager`
# tiverem consumidor vivo (hoje: **0 referências** cada um), o agregador passa a valer.
"""Vocabulário comum para os eventos do ambiente: um formato, várias fontes.

Duas entradas de eventos já existem, cada uma com sua thread e seu formato cru:

| Fonte | Formato cru | Quem produz |
|---|---|---|
| Casa | `dict` normalizado do Home Assistant | thread do WebSocket |
| Arquivos | tupla `(kind, path)` | thread de debounce do monitor |

Sem um envelope, todo consumidor teria de saber as duas formas — e o custo disso
aparece na terceira fonte, quando alguém tem de lembrar de todos os `if` que
precisam de um ramo novo. Com o envelope, o consumidor lê `source`/`kind` e roteia.

Os adaptadores abaixo são o **único** lugar que conhece o formato cru de cada
produtor. Mudou a forma de um evento lá, muda-se um adaptador aqui.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AmbientSource(str, Enum):
    """De onde veio o evento.

    Enum e não string solta: um `"homa"` digitado errado viraria um filtro que não
    casa com nada, e o sintoma seria silêncio — indistinguível de "não houve
    evento". Já é o vocabulário que `AttentionReason` usa para nomear desfechos,
    pelo mesmo motivo.
    """

    HOME = "home"
    DOCUMENTS = "documents"


class DocumentKind(str, Enum):
    """O que aconteceu com o arquivo. Espelha o que `DocumentFileWatcher` emite."""

    INDEXED = "index"
    DELETED = "delete"


#: `kind` do evento da casa. **É a palavra do próprio produtor**, não uma tradução:
#: o normalizador do canal emite `kind="state"` (`home_assistant_realtime.py`), e o
#: envelope repete o termo para que quem conhece o formato cru reconheça o evento.
#: Mesma regra de `DocumentKind`, que usa `"index"`/`"delete"` — as palavras que
#: `DocumentFileWatcher` emite. Traduzir vocabulário aqui obrigaria quem depura a
#: manter dois dicionários na cabeça.
#:
#: O canal também emite `kind="connection"` (a conexão caiu, voltou). Ele **não** é
#: percepção: é saúde do próprio canal. Ver `home_event`.
HOME_STATE = "state"


@dataclass(frozen=True)
class AmbientEvent:
    """Um evento já normalizado, com a mesma forma para todas as fontes.

    `payload` guarda o evento **cru** do produtor, e existe porque a decisão
    específica de cada fonte precisa do formato original: a severidade de um evento
    da casa depende de `domain` e `device_class`, que não fazem sentido para um
    arquivo. O envelope é o que o consumidor lê para **rotear**; o payload é o que
    ele lê para **decidir**.
    """

    source: AmbientSource
    kind: str
    subject: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Forma estável para log e diagnóstico — **sem** o payload cru.

        O payload fica de fora de propósito: ele pode carregar um caminho de arquivo
        ou o estado inteiro de uma entidade, e isto aqui vai para log e para
        resposta de ferramenta.
        """
        return {
            "source": self.source.value,
            "kind": self.kind,
            "subject": self.subject,
        }


def home_event(raw: Any) -> AmbientEvent | None:
    """Envelope para um evento do canal de tempo real do Home Assistant.

    Devolve `None` para o que não é evento de estado. O canal também emite
    `kind="connection"` (a conexão caiu, a conexão voltou), e `event_matches` deixa
    passar **de propósito** — mas "a conexão caiu" é saúde do próprio canal, não
    percepção da casa. Filtrar aqui, num lugar só, evita que cada consumidor tenha
    de lembrar disso (era o que `decide_announcement` fazia, e fazia sozinho).
    """
    if not isinstance(raw, dict) or raw.get("kind") != "state":
        return None
    return AmbientEvent(
        source=AmbientSource.HOME,
        kind=HOME_STATE,
        subject=str(raw.get("entity_id") or "").strip(),
        payload=raw,
    )


def document_event(kind: Any, path: Any) -> AmbientEvent | None:
    """Envelope para um evento do monitor de arquivos.

    O monitor emite `("index", caminho)` e `("delete", caminho)`. Um `kind`
    desconhecido devolve `None` em vez de virar um evento sem significado — mesma
    escolha de `home_event`, e pelo mesmo motivo: o envelope não deve transportar o
    que ninguém sabe interpretar.
    """
    try:
        conhecido = DocumentKind(str(kind))
    except ValueError:
        return None
    caminho = str(path or "").strip()
    if not caminho:
        return None
    return AmbientEvent(
        source=AmbientSource.DOCUMENTS,
        kind=conhecido.value,
        subject=caminho,
        payload={"path": caminho},
    )
