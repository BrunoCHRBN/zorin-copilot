# Decisão de design: registro em memória dos resultados de execução. O fluxo de chat é
# reconstruído do zero a cada troca de tópico, então sem este registro o usuário perderia
# o rastro do que deu certo e — mais importante — do que falhou e por quê.

"""Memória de execução de ações propostas pela IA."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..ai.actions import DesktopAction

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from .executor import ExecutionReport


@dataclass(frozen=True)
class ActionOutcome:
    """Resultado imutável da execução de uma ação, pronto para exibição."""

    success: bool
    message: str
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def from_report(cls, report: ExecutionReport) -> ActionOutcome:
        return cls(success=report.success, message=report.message)


class ActionOutcomeRegistry:
    """Guarda o resultado de ações já executadas, indexado por uma chave estável.

    A chave combina o id do turno com a posição e a identidade da ação, de forma que
    reconstruir o mesmo turno recupere o mesmo resultado.
    """

    def __init__(self) -> None:
        self._outcomes: dict[str, ActionOutcome] = {}

    @staticmethod
    def make_key(turn_id: str, index: int, action: DesktopAction) -> str:
        """Chave determinística para (turno, posição, ação)."""
        return f"{turn_id}|{index}|{action.action_type.value}|{action.target}"

    def record(self, key: str, outcome: ActionOutcome) -> None:
        self._outcomes[key] = outcome

    def record_report(self, key: str, report: ExecutionReport) -> None:
        self.record(key, ActionOutcome.from_report(report))

    def get(self, key: str) -> ActionOutcome | None:
        return self._outcomes.get(key)

    def clear(self) -> None:
        self._outcomes.clear()

    def __len__(self) -> int:
        return len(self._outcomes)
