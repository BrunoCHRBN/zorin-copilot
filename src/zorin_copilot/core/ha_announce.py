# Decisão de design: a decisão de anunciar é separada do efeito de falar.
# Falar não se desfaz — o ar já foi movido e o usuário já foi interrompido. Logo
# "vale interromper?" tem de ser testável sem display, sem microfone e sem
# alto-falante. Este módulo é **puro** (sem GTK, sem rede, sem áudio), no mesmo
# espírito de `core/board_outcome.py`.
#
# A ORDEM das condições é a regra, e vai da mais fundamental para a mais
# circunstancial: assim o motivo relatado é o mais informativo possível. Se um
# evento nem é de estado, "estou falando agora" seria um motivo enganoso.
"""Decisão e frase de anúncio falado para eventos do Home Assistant.

O módulo responde a duas perguntas da mesma família — *vale falar, e o quê?*:

1. **Evento de estado** (`decide_announcement`): o canal de tempo real viu uma
   entidade mudar e é preciso decidir se aquilo merece interromper alguém.
2. **Mensagem pedida de fora** (`parse_announce_command`): a ponte HA→desktop
   recebeu uma mensagem escrita à mão numa automação e quer que o Copilot a fale.

Quem fala é a janela (`ui/app.py`); aqui não há nem áudio nem GTK, para que as
regras possam ser exercitadas em teste sem display.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .home_assistant_realtime import event_matches


class AnnounceDecision(str, Enum):
    """Desfechos nomeados.

    Enum e não booleano de propósito: um `bool` jogaria fora o motivo, e é
    exatamente o motivo que se precisa ler no log quando nada é falado.
    """

    ANNOUNCE = "announce"
    DISABLED = "disabled"
    NOT_STATE = "not_state"
    NO_STATE = "no_state"
    FILTERED = "filtered"
    UNCHANGED = "unchanged"
    NO_PHRASE = "no_phrase"
    BUSY = "busy"


#: Verbos por domínio e estado. Escolhidos por serem **invariáveis em gênero**:
#: o nome amigável pode ser "Porta", "Sensor" ou "Luz" e não há como descobrir o
#: gênero. Uma frase que erre a concordância ("A Sensor abriu") soa pior do que
#: uma que a evite — então o sujeito é sempre o próprio nome da entidade e o
#: verbo não flexiona.
_DOMAIN_VERBS: dict[str, dict[str, str]] = {
    "light": {"on": "acendeu", "off": "apagou"},
    "switch": {"on": "ligou", "off": "desligou"},
    "lock": {"locked": "trancou", "unlocked": "destrancou"},
    "cover": {"open": "abriu", "closed": "fechou"},
    "person": {"home": "chegou em casa", "not_home": "saiu de casa"},
}

#: `binary_sensor` informa on/off, mas o significado depende da classe do
#: aparelho. Frase vazia = transição reconhecida e **deliberadamente silenciosa**
#: (o fim de um movimento não é um evento que valha interromper alguém).
_BINARY_SENSOR_VERBS: dict[str, dict[str, str]] = {
    "door": {"on": "abriu", "off": "fechou"},
    "window": {"on": "abriu", "off": "fechou"},
    "garage_door": {"on": "abriu", "off": "fechou"},
    "opening": {"on": "abriu", "off": "fechou"},
    "lock": {"on": "destrancou", "off": "trancou"},
    "motion": {"on": "detectou movimento", "off": ""},
    "smoke": {"on": "detectou fumaça", "off": ""},
    "moisture": {"on": "detectou água", "off": ""},
    "gas": {"on": "detectou gás", "off": ""},
}

#: Estados que nunca viram frase. `unknown`/`unavailable` ficam de fora nesta
#: primeira fatia de propósito: anunciar indisponibilidade é decisão de produto
#: (e de política de atenção), não um detalhe de formatação. Silêncio é o
#: default seguro — o erro barato aqui é falar de menos, não de mais.
_SILENT_STATES = frozenset({"unknown", "unavailable"})


@dataclass(frozen=True)
class Announcement:
    """Resultado da decisão. Carrega o motivo junto com a frase."""

    decision: AnnounceDecision
    text: str = ""
    entity_id: str = ""
    friendly_name: str = ""
    state: str = ""

    @property
    def should_speak(self) -> bool:
        """Só `ANNOUNCE` com frase falável conta como "deve falar".

        A checagem do texto não é redundante: ela impede que um desfecho novo,
        adicionado sem frase, passe a ser tratado como sucesso por um leitor que
        só olhasse o enum.
        """
        return self.decision is AnnounceDecision.ANNOUNCE and bool(self.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "should_speak": self.should_speak,
            "text": self.text,
            "entity_id": self.entity_id,
            "friendly_name": self.friendly_name,
            "state": self.state,
        }


def parse_entity_filter(raw: str | None) -> set[str]:
    """Converte a string de config ("light.a, sensor.b") num conjunto limpo.

    Vazio devolve conjunto vazio, que o chamador traduz para `None` (= todas as
    entidades). A distinção importa: conjunto vazio como filtro não casaria com
    entidade nenhuma.
    """
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


def _state_key(value: Any) -> str:
    """Normaliza um estado do HA para comparação e casamento de tabela."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value).strip().lower()


def _verb_for(domain: str, state: str, device_class: str) -> str:
    """Verbo da transição, ou string vazia quando ela é silenciosa/desconhecida."""
    if domain == "binary_sensor":
        table = _BINARY_SENSOR_VERBS.get(device_class, {})
        return table.get(state, "")
    return _DOMAIN_VERBS.get(domain, {}).get(state, "")


def announcement_text(event: dict[str, Any]) -> str:
    """Frase curta para o evento, ou `""` quando a transição é silenciosa.

    **Não há fallback genérico** ("mudou para X") de propósito: um domínio sem
    tabela (um `sensor.*` de temperatura, por exemplo) viraria ruído a cada
    variação de leitura. Entidade nova fica muda até alguém decidir a frase dela
    — e há teste fixando essa ausência, para que ninguém acrescente o catch-all
    barulhento mais tarde sem perceber.
    """
    if not isinstance(event, dict):
        return ""
    name = str(event.get("friendly_name") or event.get("entity_id") or "").strip()
    if not name:
        return ""
    attributes = event.get("attributes")
    device_class = ""
    if isinstance(attributes, dict):
        device_class = str(attributes.get("device_class") or "").strip().lower()
    verb = _verb_for(
        str(event.get("domain") or "").strip().lower(),
        _state_key(event.get("state")),
        device_class,
    )
    if not verb:
        return ""
    return f"{name} {verb}."


def decide_announcement(
    event: Any,
    *,
    enabled: bool,
    entity_filter: set[str] | None = None,
    domains: set[str] | None = None,
    busy: bool = False,
) -> Announcement:
    """Decide se um evento da casa deve virar fala.

    ``busy`` é a condição de "já estou falando": quem fala por conta própria
    precisa saber se atropelaria a própria fala (ou a fala de uma sessão ao vivo
    em andamento). Ela é um parâmetro — e não uma consulta ao sintetizador —
    justamente para que a regra continue pura e testável.

    A ordem das condições é a regra; ver o comentário no topo do módulo.
    """
    if not enabled:
        return Announcement(AnnounceDecision.DISABLED)

    if not isinstance(event, dict) or event.get("kind") != "state":
        return Announcement(AnnounceDecision.NOT_STATE)

    entity_id = str(event.get("entity_id") or "").strip()
    if not entity_id:
        return Announcement(AnnounceDecision.NOT_STATE)

    state = _state_key(event.get("state"))
    if not state or state in _SILENT_STATES:
        return Announcement(AnnounceDecision.NO_STATE, entity_id=entity_id)

    # Reusa a regra de filtro do canal em vez de reimplementá-la: duas cópias
    # divergiriam em silêncio. O ramo "eventos de sistema sempre passam" do
    # `event_matches` nunca é alcançado aqui — a checagem de `kind` acima já
    # barrou tudo o que não é estado, e um anunciante **não** quer acordar para
    # avisar que a conexão caiu.
    if not event_matches(event, entity_filter, domains):
        return Announcement(AnnounceDecision.FILTERED, entity_id=entity_id, state=state)

    if _state_key(event.get("old_state")) == state:
        return Announcement(AnnounceDecision.UNCHANGED, entity_id=entity_id, state=state)

    text = announcement_text(event)
    if not text:
        return Announcement(AnnounceDecision.NO_PHRASE, entity_id=entity_id, state=state)

    friendly_name = str(event.get("friendly_name") or entity_id).strip()

    if busy:
        # A frase viaja mesmo assim: o chamador pode preferir guardá-la para um
        # resumo em vez de descartá-la. O que não acontece é falar por cima.
        return Announcement(
            AnnounceDecision.BUSY,
            text=text,
            entity_id=entity_id,
            friendly_name=friendly_name,
            state=state,
        )

    return Announcement(
        AnnounceDecision.ANNOUNCE,
        text=text,
        entity_id=entity_id,
        friendly_name=friendly_name,
        state=state,
    )


# ---------------------------------------------------------------------------
# Anúncio pedido de fora (ponte HA→desktop / linha de comando)
# ---------------------------------------------------------------------------
#
# A ponte `scripts/ha_bridge.py` recebe mensagens escritas à mão numa automação
# ("a lavadora terminou") e precisa que o Copilot as fale. Ela **não** deve falar
# sozinha: enquanto houver duas bocas — o Piper do app e o `spd-say` da ponte —
# não há uma presença, há dois programas falando na mesma casa, com timbres
# diferentes e sem nenhuma arbitragem entre eles.
#
# Então a ponte pede, e o app fala. O pedido chega pela instância única do GTK
# (`zorin-copilot --announce "texto"`), que é o IPC que o projeto já usa para
# `--toggle/--voice/--dictate`. A decisão de aceitar esse pedido mora aqui.

#: Códigos de saída do `--announce`. A ponte só precisa distinguir "aceito" de
#: "não aceito" para decidir se cai no `spd-say`, mas nomeá-los deixa o
#: diagnóstico legível em quem chamou.
EXIT_SPOKEN = 0
EXIT_NO_TARGET = 3
EXIT_EMPTY = 4

#: Teto do que se fala de uma mensagem de fora. Uma automação pode carregar um
#: texto longo; ler um parágrafo em voz alta não é avisar, é palestrar. O corte é
#: **dito no log** e devolvido em `truncated` — nunca silencioso, porque truncar
#: sem avisar apaga informação do usuário sem que ele saiba.
MAX_SPOKEN_CHARS = 400


class AnnounceRequest(str, Enum):
    """Desfechos de um pedido de anúncio vindo da linha de comando."""

    SPEAK = "speak"
    NO_TARGET = "no_target"
    EMPTY = "empty"


@dataclass(frozen=True)
class AnnounceCommand:
    """O que fazer com o pedido, e por quê."""

    action: AnnounceRequest
    text: str = ""
    truncated: bool = False

    @property
    def should_speak(self) -> bool:
        return self.action is AnnounceRequest.SPEAK and bool(self.text)

    @property
    def exit_code(self) -> int:
        """Código de saída correspondente, para quem chamou pela linha de comando."""
        if self.action is AnnounceRequest.SPEAK:
            return EXIT_SPOKEN
        if self.action is AnnounceRequest.EMPTY:
            return EXIT_EMPTY
        return EXIT_NO_TARGET

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "should_speak": self.should_speak,
            "text": self.text,
            "truncated": self.truncated,
            "exit_code": self.exit_code,
        }


def truncate_for_speech(text: str, max_chars: int = MAX_SPOKEN_CHARS) -> tuple[str, bool]:
    """Encurta um texto longo cortando numa fronteira de palavra.

    Devolve ``(texto, truncado)``. Cortar no meio de uma palavra faz o
    sintetizador pronunciar um fragmento esquisito; cortar na última fronteira
    antes do limite soa como uma frase que acabou.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    head = text[:max_chars]
    cut = head.rfind(" ")
    # Sem espaço nenhum no trecho (uma URL gigante, por exemplo): corta no limite
    # mesmo, que é melhor do que falar a coisa inteira.
    return (head[:cut] if cut > 0 else head), True


def parse_announce_command(
    text: Any,
    *,
    is_remote: bool,
    max_chars: int = MAX_SPOKEN_CHARS,
) -> AnnounceCommand:
    """Decide o que fazer com um pedido de anúncio da linha de comando.

    ``is_remote`` é o que separa *falar* de *não falar*: vem de
    `Gio.ApplicationCommandLine.get_is_remote()`, que é ``True`` quando esta
    linha de comando foi **encaminhada por um processo secundário para a
    instância já em execução**. Se for ``False``, este processo acabou de virar a
    instância primária — ou seja, o app **não estava rodando** — e falar seria
    abrir a interface inteira só para dizer uma frase. Melhor recusar e deixar
    quem chamou decidir (a ponte cai no `spd-say`).

    A ordem das condições é a regra, como em `decide_announcement`: validade
    antes de circunstância. Um pedido vazio não tem o que falar mesmo que haja
    alguém escutando, e "não há app" seria um motivo enganoso para ele.
    """
    if not isinstance(text, str) or not text.strip():
        return AnnounceCommand(AnnounceRequest.EMPTY)

    if not is_remote:
        return AnnounceCommand(AnnounceRequest.NO_TARGET)

    spoken, truncated = truncate_for_speech(text.strip(), max_chars)
    return AnnounceCommand(AnnounceRequest.SPEAK, text=spoken, truncated=truncated)
