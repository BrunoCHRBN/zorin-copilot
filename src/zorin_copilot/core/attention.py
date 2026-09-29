# Decisão de design: a decisão de **interromper** é separada do efeito de interromper.
# Um assistente ambiente que fala sozinho vive ou morre por esta peça: sem ela, cada
# evento da casa vira uma fala, e o Jarvis vira um alarme. Interromper não se desfaz —
# a frase já foi dita e a atenção do usuário já foi tomada.
#
# Este módulo é **puro**: sem GTK, sem rede, sem áudio, sem relógio. Tudo o que ele
# precisa saber entra por parâmetro (hora, ociosidade, se há sessão ao vivo, quantas
# falas já houve). É o mesmo padrão de `core/board_outcome.py` e `core/ha_announce.py`
# — e pelo mesmo motivo: a regra tem de ser exercitável sem display.
#
# A ORDEM das condições **é** a regra, e vai da mais fundamental para a mais
# circunstancial. Segurança primeiro (uma detecção de fumaça não espera a janela de
# silêncio), depois a capacidade (não se fala por cima da própria fala), depois o
# contexto do usuário, e só no fim a natureza do evento.
"""Política de atenção: decide se vale interromper, e com que intensidade.

O módulo responde a uma pergunta só — *isto merece atenção agora, e de que tipo?* —
e devolve um dos quatro níveis:

| Nível | O que acontece | Quando |
|---|---|---|
| `SPEAK` | fala em voz alta | o usuário está presente e o evento importa |
| `NOTIFY` | aviso visual silencioso | dá para mostrar, mas não para falar |
| `BADGE` | marca persistente, sem alerta | acumula para o usuário ver depois |
| `IGNORE` | nada | nem vale registrar um selo |

`NOTIFY` e `BADGE` são coisas diferentes de propósito: `NOTIFY` é um alerta
transitório ("olhe agora"), `BADGE` é um contador persistente ("aconteceu, veja
quando puder"). É essa distinção que permite trocar gotejamento por digesto em vez
de simplesmente calar.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulário
# ---------------------------------------------------------------------------


class AttentionLevel(str, Enum):
    """Os quatro níveis de interrupção, do mais forte ao mais discreto."""

    SPEAK = "speak"
    NOTIFY = "notify"
    BADGE = "badge"
    IGNORE = "ignore"


class Severity(str, Enum):
    """Quanto o evento importa por si só, antes de qualquer contexto.

    Note que a severidade **não** é urgência de interrupção: um evento `ROUTINE`
    ainda pode ser falado se o usuário está presente e o orçamento permite. O que
    `URGENT` compra é furar a fila — ver `decide_attention`.
    """

    URGENT = "urgent"
    NORMAL = "normal"
    ROUTINE = "routine"


class AttentionReason(str, Enum):
    """Por que este nível, e não outro.

    Enum e não booleano pelo motivo de sempre: quando nada é falado, é o motivo
    que se precisa ler no log. "Não interrompi" tem causas muito diferentes —
    recurso desligado, janela de silêncio, orçamento estourado, usuário ausente.
    """

    DISABLED = "disabled"
    URGENT = "urgent"
    SILENCED = "silenced"
    SCREEN_LOCKED = "screen_locked"
    LIVE_SESSION = "live_session"
    SPEAKING = "speaking"
    BUDGET_EXHAUSTED = "budget_exhausted"
    AWAY = "away"
    ROUTINE = "routine"
    NORMAL = "normal"


# ---------------------------------------------------------------------------
# Severidade do evento
# ---------------------------------------------------------------------------

#: Classes de `binary_sensor` que são **segurança**, não conveniência. Uma delas
#: disparando fura janela de silêncio, orçamento e sessão ao vivo.
_URGENT_DEVICE_CLASSES = frozenset({"smoke", "gas", "moisture", "carbon_monoxide"})

#: Domínios que informam sem alarmar: alguém chegou, a porta trancou. São a
#: **exceção** — todo domínio que não está aqui nem é sensor de segurança cai em
#: `ROUTINE` pelo caso geral. Não existe um conjunto `_ROUTINE_DOMAINS` explícito
#: de propósito: ele seria código morto, porque devolveria exatamente o mesmo que
#: o caso geral. (Descoberto por mutação: remover aquele ramo não mudava desfecho
#: nenhum, o que é a definição de um ramo que não precisa existir.)
_NORMAL_DOMAINS = frozenset({"person", "lock"})

#: Classes de `binary_sensor` corriqueiras apesar de virem de um sensor "de verdade".
#: Movimento dispara o tempo todo; tratá-lo como `NORMAL` faria o assistente falar
#: a cada passo dado dentro de casa.
_ROUTINE_DEVICE_CLASSES = frozenset({"motion", "occupancy", "presence"})


def _state_key(value: Any) -> str:
    """Normaliza um estado do HA. Mesma regra de `ha_announce._state_key`."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value).strip().lower()


def severity_for(event: Any, *, explicitly_selected: bool = False) -> Severity:
    """Classifica o evento em `URGENT`/`NORMAL`/`ROUTINE`. Pura.

    O **default é `ROUTINE`**, e isso é deliberado. O próprio `ha_announce.py`
    já fixou o princípio: *"silêncio é o default seguro — o erro barato aqui é
    falar de menos, não de mais"*. Uma entidade nova entra muda até alguém
    decidir que ela merece voz.

    `explicitly_selected` é o contrapeso, e sem ele a classificação viraria uma
    jaula: diz que o usuário **nomeou esta entidade** no filtro de anúncios
    (`ha_announce_entities`). Nomear é intenção — quem escreveu `light.sala` na
    lista quer ouvir sobre ela, e rebaixá-la a `ROUTINE` seria contrariar o
    pedido. Só a **segurança** fica acima disso: um sensor de fumaça é urgente
    por si, nomeado ou não.

    Só o **disparo** de um sensor de segurança é `URGENT`; a volta ao normal
    ("a fumaça dissipou") é `NORMAL`. Uma detecção que se encerra não é o momento
    de interromper com força — é o momento de informar.
    """
    base = _base_severity(event)
    if explicitly_selected and base is Severity.ROUTINE:
        return Severity.NORMAL
    return base


def is_explicitly_selected(event: Any, entity_filter: set[str] | None) -> bool:
    """Diz se o usuário **nomeou** esta entidade no filtro de anúncios.

    Mora aqui, e não em `ui/app.py`, porque é parte da regra de severidade — e a
    regra é justamente o que este módulo permite testar sem display. Filtro vazio
    (ou `None`) é "todas as entidades", e nesse caso ninguém foi nomeado.
    """
    if not entity_filter or not isinstance(event, dict):
        return False
    entity_id = str(event.get("entity_id") or "").strip()
    return bool(entity_id) and entity_id in entity_filter


def _base_severity(event: Any) -> Severity:
    """A severidade do evento **sem** considerar a escolha explícita do usuário."""
    if not isinstance(event, dict):
        return Severity.ROUTINE

    domain = str(event.get("domain") or "").strip().lower()
    state = _state_key(event.get("state"))
    attributes = event.get("attributes")
    device_class = ""
    if isinstance(attributes, dict):
        device_class = str(attributes.get("device_class") or "").strip().lower()

    if domain == "binary_sensor":
        if device_class in _URGENT_DEVICE_CLASSES:
            # `on` é a detecção. `off` é o alívio — informa, não alarma.
            return Severity.URGENT if state == "on" else Severity.NORMAL
        if device_class in _ROUTINE_DEVICE_CLASSES:
            return Severity.ROUTINE
        if device_class:
            # Porta, janela, garagem: `NORMAL` pelo caso geral, explicitado aqui
            # para que o motivo fique legível.
            return Severity.NORMAL
        return Severity.ROUTINE

    if domain in _NORMAL_DOMAINS:
        return Severity.NORMAL

    # Luz, tomada, persiana, e qualquer domínio novo: informa, não interrompe.
    return Severity.ROUTINE


# ---------------------------------------------------------------------------
# Contexto, orçamento e política
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttentionContext:
    """O que se sabe sobre o usuário e o próprio assistente **agora**.

    Tudo escalar e fornecido pelo chamador: é isto que mantém a regra pura e
    testável sem relógio, sem tela e sem microfone.

    `idle_seconds` aceita `None`, e `None` **não** é zero: significa "não sei".
    Quando não se sabe, a condição de ausência é **pulada** em vez de chutada —
    um palpite errado aqui ou silencia o assistente sem motivo, ou o faz falar
    para uma cadeira vazia, e os dois são piores do que não usar a condição.
    Medido nesta máquina: `org.freedesktop.ScreenSaver.GetActive` responde, mas
    `GetSessionIdleTime` não existe, então hoje o que se sabe de verdade é a
    tela bloqueada — a ociosidade vai como `None`.
    """

    hour: int = 12
    screen_locked: bool = False
    live_session_active: bool = False
    speaking: bool = False
    idle_seconds: float | None = None


@dataclass(frozen=True)
class InterruptionBudget:
    """Quanto já se interrompeu na última hora, e quanto se admite.

    `max_per_hour <= 0` significa **nada cabe** — não "sem limite". É a leitura
    conservadora, e ela é o **oposto** da que `decide_consolidation` faz com um
    intervalo negativo. Vale explicar por que a assimetria é correta:

    - Na consolidação, um valor sem sentido que virasse "nunca rodar" deixaria o
      recurso **invisivelmente parado** — ninguém descobre que a memória nunca é
      podada. Ali o permissivo é o seguro.
    - Aqui, um valor sem sentido que virasse "sem limite" deixaria o assistente
      **visivelmente falante** — e é exatamente o defeito que este módulo existe
      para impedir. `ha_announce.py` já fixou o princípio: *"o erro barato é falar
      de menos, não de mais"*.

    Não há sentinela para "sem limite" de propósito: um valor que significa o
    contrário do que aparenta é o tipo de coisa que se digita por engano. Quem
    quer mais folga aumenta o número.
    """

    spoken_last_hour: int = 0
    max_per_hour: int = 6

    @property
    def exhausted(self) -> bool:
        if self.max_per_hour <= 0:
            return True
        return self.spoken_last_hour >= self.max_per_hour

    @property
    def remaining(self) -> int:
        """Quantas falas ainda cabem. Nunca negativo."""
        return max(0, self.max_per_hour - self.spoken_last_hour)


@dataclass(frozen=True)
class AttentionPolicy:
    """Os ajustes do usuário. Todos com default que **reduz** interrupção."""

    enabled: bool = True
    silence_windows: tuple[tuple[int, int], ...] = ()
    away_seconds: float = 1800.0
    max_per_hour: int = 6


#: Default de silêncio: noite. Escolhido por ser a janela em que quase todo
#: evento da casa é ruído puro — e o único default capaz de, sozinho, evitar a
#: impressão de "alarme que fala sozinho". Vazio desliga a janela.
DEFAULT_SILENCE_SPEC = "23-7"

#: Meia hora sem interação = o usuário saiu de perto. Falar para uma cadeira
#: vazia não informa ninguém e ainda fica no histórico como se tivesse informado.
DEFAULT_AWAY_SECONDS = 1800.0

#: Seis falas por hora é generoso para uma casa e ainda deixa espaço para o
#: usuário notar que existe um limite. É ponto de partida, não dogma.
DEFAULT_MAX_PER_HOUR = 6


def parse_silence_windows(raw: Any) -> tuple[tuple[int, int], ...]:
    """Converte `"23-7,13-14"` em `((23, 7), (13, 14))`.

    Tolera lixo devolvendo as janelas válidas que conseguir ler — uma janela
    malformada não pode invalidar as outras nem derrubar o arranque. Faixa fora
    de 0–23, texto vazio e formato errado são descartados em silêncio; o
    resultado é sempre um conjunto utilizável (possivelmente vazio).
    """
    if not isinstance(raw, str):
        return ()
    janelas: list[tuple[int, int]] = []
    for parte in raw.split(","):
        trecho = parte.strip()
        if not trecho or "-" not in trecho:
            continue
        inicio_txt, _, fim_txt = trecho.partition("-")
        try:
            inicio = int(inicio_txt.strip())
            fim = int(fim_txt.strip())
        except ValueError:
            continue
        if 0 <= inicio <= 23 and 0 <= fim <= 23:
            janelas.append((inicio, fim))
    return tuple(janelas)


def is_within_silence(hour: int, windows: tuple[tuple[int, int], ...]) -> bool:
    """Diz se a hora cai em alguma janela de silêncio.

    Janela com o fim **menor** que o início atravessa a meia-noite: `23-7` cobre
    23, 0, 1, 2, 3, 4, 5 e 6. O fim é exclusivo (`7` já não é silêncio), o que
    faz `23-7` e `7-23` serem complementares sem hora contada duas vezes.
    """
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        return False
    for inicio, fim in windows:
        if inicio <= fim:
            if inicio <= hour < fim:
                return True
        elif hour >= inicio or hour < fim:
            return True
    return False


# ---------------------------------------------------------------------------
# A decisão
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttentionDecision:
    """O nível escolhido, o motivo, e a severidade que o originou."""

    level: AttentionLevel
    reason: AttentionReason
    severity: Severity

    @property
    def should_speak(self) -> bool:
        return self.level is AttentionLevel.SPEAK

    @property
    def should_notify(self) -> bool:
        return self.level is AttentionLevel.NOTIFY

    @property
    def should_badge(self) -> bool:
        return self.level is AttentionLevel.BADGE

    @property
    def should_ignore(self) -> bool:
        return self.level is AttentionLevel.IGNORE

    @property
    def interrupts_now(self) -> bool:
        """Se algo é mostrado ou dito **agora** (fala ou aviso visual)."""
        return self.level in (AttentionLevel.SPEAK, AttentionLevel.NOTIFY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "reason": self.reason.value,
            "severity": self.severity.value,
            "should_speak": self.should_speak,
            "should_notify": self.should_notify,
            "should_badge": self.should_badge,
            "should_ignore": self.should_ignore,
        }


def decide_attention(
    *,
    severity: Severity,
    context: AttentionContext,
    budget: InterruptionBudget,
    policy: AttentionPolicy,
) -> AttentionDecision:
    """Decide o nível de interrupção para um evento já validado.

    Espera um evento que **já passou** por `ha_announce.decide_announcement` — ou
    seja, já tem frase e já venceu os filtros de estado e entidade. Aqui só se
    decide *com que força* aquilo chega. Duplicar os filtros criaria duas cópias
    da mesma regra, que divergiriam em silêncio.

    A ordem das condições é a regra:

    1. **Desligado** — nada acontece, e o motivo é o mais fundamental de todos.
    2. **Urgente** — segurança fura a fila inteira: janela de silêncio, orçamento,
       tela bloqueada e sessão ao vivo. Uma detecção de fumaça às 3h com o
       orçamento estourado **tem** de ser dita; é exatamente o evento para o qual
       o resto da política seria um erro fatal. A única coisa que a urgência não
       vence é a própria fala em andamento — não dá para falar por cima de si
       mesmo —, e nesse caso ela vira `NOTIFY`, para ao menos ser vista já.
    3. **Falando agora** — não se atropela a própria fala; acumula.
    4. **Janela de silêncio** — ignora, sem selo. Ver a nota abaixo.
    5. **Tela bloqueada** — ninguém está olhando; acumula para quando voltar.
    6. **Sessão ao vivo** — o usuário está conversando por voz: aviso visual
       silencioso em vez de fala.
    7. **Orçamento estourado** — já se interrompeu o suficiente nesta hora;
       acumula em vez de avisar.
    8. **Usuário ausente** — falar para uma cadeira vazia não informa ninguém.
       Pulada quando a ociosidade é desconhecida (`None`).
    9. **Evento corriqueiro** — informa, não interrompe.
    10. Caso contrário, **fala**.

    Nota sobre a janela de silêncio ser `IGNORE` e não `BADGE`: durante a noite,
    um evento corriqueiro não é informação pendente — é ruído. Marcá-lo deixaria
    um selo esperando o usuário toda manhã por coisas que ele não queria saber.
    Evento que importa à noite já foi atendido pela condição 2.
    """
    if not policy.enabled:
        return AttentionDecision(AttentionLevel.IGNORE, AttentionReason.DISABLED, severity)

    if severity is Severity.URGENT:
        # A fala em andamento é limite de **capacidade**, não preferência: não há
        # como sobrepor áudio. Fora isso, urgência vence todas as condições abaixo.
        if context.speaking:
            return AttentionDecision(
                AttentionLevel.NOTIFY, AttentionReason.URGENT, severity
            )
        return AttentionDecision(AttentionLevel.SPEAK, AttentionReason.URGENT, severity)

    if context.speaking:
        return AttentionDecision(AttentionLevel.BADGE, AttentionReason.SPEAKING, severity)

    if is_within_silence(context.hour, policy.silence_windows):
        return AttentionDecision(AttentionLevel.IGNORE, AttentionReason.SILENCED, severity)

    if context.screen_locked:
        return AttentionDecision(
            AttentionLevel.BADGE, AttentionReason.SCREEN_LOCKED, severity
        )

    if context.live_session_active:
        # A sessão ao vivo rebaixa a **fala** para aviso visual — mas não promove
        # um evento corriqueiro. Um `ROUTINE` não seria falado de qualquer forma,
        # então transformá-lo em aviso visível daria a ele **mais** atenção do que
        # ele teria sem a sessão, que é o contrário do que a condição existe para
        # fazer. O motivo continua sendo `live_session`: é o que se quer ler no log.
        nivel = (
            AttentionLevel.BADGE
            if severity is Severity.ROUTINE
            else AttentionLevel.NOTIFY
        )
        return AttentionDecision(nivel, AttentionReason.LIVE_SESSION, severity)

    if budget.exhausted:
        return AttentionDecision(
            AttentionLevel.BADGE, AttentionReason.BUDGET_EXHAUSTED, severity
        )

    # `None` = não sei quanto tempo o usuário está fora. Não se chuta: a condição
    # é pulada e o evento segue para as seguintes. Ver `AttentionContext`.
    if context.idle_seconds is not None and context.idle_seconds >= policy.away_seconds:
        return AttentionDecision(AttentionLevel.BADGE, AttentionReason.AWAY, severity)

    if severity is Severity.ROUTINE:
        return AttentionDecision(AttentionLevel.BADGE, AttentionReason.ROUTINE, severity)

    return AttentionDecision(AttentionLevel.SPEAK, AttentionReason.NORMAL, severity)
