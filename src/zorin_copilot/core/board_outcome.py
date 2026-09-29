# Decisão de design: o gancho `board_opener` tinha um contrato **otimista**. Ele
# devolvia a frase "Quadro Negro aberto" de forma síncrona, mas o trabalho real
# acontece depois — na main thread (via `GLib.idle_add`) ou numa thread de
# resolução model-backed. Quando **nada** abria (problema não-algébrico e sem
# provedor de IA configurado, ou a resolução falhando), o app ainda dizia
# "aberto" e gravava `success: True` no log da sessão.
#
# Pior: os três consumidores do gancho — voz local, ferramenta `study_show_board`
# e o caminho Gemini Live — hard-codavam o sucesso cada um por si, então não havia
# um lugar onde corrigir. Este módulo dá ao contrato um **desfecho explícito**, e a
# decisão de qual desfecho cabe é **pura** (`decide_path`): testável sem UI, sem
# provedor e sem display. Os consumidores passam a LER o desfecho em vez de
# presumi-lo.
#
# A regra que governa tudo aqui: **não afirmar o que não se verificou.** Por isso
# "não sei" nunca vira "abriu".

"""Desfecho da abertura do Quadro Negro (puro e determinístico)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: O quadro foi mostrado (ou será, de imediato). Decidido sem depender do modelo.
BOARD_OK = "ok"
#: A resolução foi escalada ao modelo e está em andamento. O desfecho **ainda não
#: é conhecido** — por isso não é sucesso nem falha.
BOARD_PENDING = "pending"
#: Nada abriu e nada vai abrir: o problema não é algébrico e não há provedor de IA
#: configurado. O remédio é do usuário (configurar um provedor).
BOARD_UNAVAILABLE = "unavailable"
#: Nada abriu: a resolução foi tentada e falhou. O remédio é tentar de novo.
BOARD_FAILED = "failed"
#: O gancho não informou desfecho nenhum. **Não** é `pending`: em `pending` fomos
#: nós que iniciamos a resolução e sabemos disso; aqui não se sabe nem se o pedido
#: foi aceito. Fica separado porque o log de auditoria grava um booleano — e
#: "não sei" não pode ser gravado como sucesso.
BOARD_UNKNOWN = "unknown"

_MESSAGES = {
    BOARD_OK: "Quadro Negro aberto",
    BOARD_PENDING: "Gerando o raciocínio no Quadro Negro…",
    BOARD_UNAVAILABLE: "Sem provedor de IA configurado para resolver este problema no quadro.",
    BOARD_FAILED: "Não consegui montar o quadro para este problema.",
    BOARD_UNKNOWN: "Comando do Quadro Negro enviado; não foi possível confirmar a abertura.",
}


def decide_path(*, has_graph: bool, has_problem: bool, provider_ready: bool) -> str:
    """Qual rota a abertura do quadro toma. Pura — só os três booleanos importam.

    A **ordem** das condições é a regra, não um detalhe:
      - **sem problema**, o quadro abre reusando a última solução da conversa —
        não precisa de modelo nem de provedor;
      - **com grafo**, a álgebra pura (`math_solver.try_solve`) já resolveu;
      - só quando há problema, não há grafo **e** não há provedor a abertura é
        impossível. Era exatamente esse caso que antes saía como sucesso.
    """
    if has_graph or not has_problem:
        return BOARD_OK
    if not provider_ready:
        return BOARD_UNAVAILABLE
    return BOARD_PENDING


def message_for(status: str, focus: str = "") -> str:
    """A frase do desfecho — o que a voz fala e o que o modelo repete."""
    base = _MESSAGES.get(status, _MESSAGES[BOARD_FAILED])
    if status == BOARD_OK and focus:
        return f"{base} com destaque em “{focus}”."
    if base.endswith(("…", ".")):
        return base
    return base + "."


@dataclass(frozen=True)
class BoardOutcome:
    """O que de fato aconteceu quando se pediu para abrir o quadro."""

    status: str
    message: str = ""
    detail: str = ""

    @property
    def opened(self) -> bool:
        """O quadro foi mostrado e o desfecho é conhecido."""
        return self.status == BOARD_OK

    @property
    def pending(self) -> bool:
        return self.status == BOARD_PENDING

    @property
    def accepted(self) -> bool:
        """O pedido foi aceito e algo vai acontecer. `pending` conta como aceito.

        **Não** use isto para afirmar "abriu" — para isso existe `opened`. A
        distinção é o ponto do módulo: o log de auditoria registra o pedido
        aceito, enquanto a frase falada não pode dizer "aberto" antes de o quadro
        existir.

        `unknown` **não** conta: em `pending` sabemos que iniciamos a resolução;
        em `unknown` não se sabe nem se o pedido chegou. Um booleano de auditoria
        não pode ser preenchido com uma suposição.
        """
        return self.status in (BOARD_OK, BOARD_PENDING)

    def to_dict(self) -> dict[str, Any]:
        """Forma serializável, consumida pela voz e pela ferramenta."""
        return {
            "ok": self.accepted,
            "opened": self.opened,
            "pending": self.pending,
            "status": self.status,
            "message": self.message,
            # Sem detalhe próprio, a mensagem **já é** o motivo. Um consumidor que
            # só leia `error` — o modelo, ao ver `ok: False` — ficaria sem saber
            # por quê, que é o mesmo defeito de antes em outra roupa.
            "error": self.detail or ("" if self.accepted else self.message),
        }


def outcome_from_payload(payload: Any) -> BoardOutcome:
    """Lê o que o gancho devolveu, **sem confiar no formato**.

    Gancho ausente, `None`, dict vazio ou formato irreconhecível → `unknown`, e
    nunca `ok`: não saber o desfecho é motivo para **não** afirmar sucesso, não
    para presumi-lo. Um gancho que devolva apenas `{"message": ...}` (o formato
    antigo) cai nesse caso de propósito — era justamente ele que mentia, porque
    a mensagem de sucesso não vinha acompanhada de nada verificável.

    A frase do gancho, quando existe, é **preservada**: quem escreveu a mensagem
    foi ele, e reescrevê-la por cima surpreenderia. O veredito (`status`,
    `opened`) é nosso e não sobe junto — repetir a frase de um gancho não é
    afirmar que a ação aconteceu.
    """
    if isinstance(payload, Mapping):
        status = str(payload.get("status") or "").strip()
        if status in _MESSAGES:
            return BoardOutcome(
                status=status,
                message=str(payload.get("message") or message_for(status)),
                detail=str(payload.get("error") or ""),
            )
        message = str(payload.get("message") or "")
        # Sem `status` (gancho de outra implementação): só um booleano explícito
        # autoriza afirmar desfecho. A ausência dele, não.
        if payload.get("opened") is True or payload.get("ok") is True:
            return BoardOutcome(BOARD_OK, message=message or message_for(BOARD_OK))
        if payload.get("ok") is False:
            return BoardOutcome(
                BOARD_FAILED,
                message=message or message_for(BOARD_FAILED),
                detail=str(payload.get("error") or ""),
            )
        return BoardOutcome(
            BOARD_UNKNOWN,
            message=message or message_for(BOARD_UNKNOWN),
            detail="O gancho do Quadro Negro não informou o desfecho.",
        )
    return BoardOutcome(
        BOARD_UNKNOWN,
        message=message_for(BOARD_UNKNOWN),
        detail="O gancho do Quadro Negro não informou o desfecho.",
    )
