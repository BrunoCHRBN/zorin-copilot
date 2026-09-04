# Decisão de design: ações da IA são objetos tipados e validados — nunca execução direta de strings arbitrárias de shell sem consentimento.

"""Definição do catálogo de ações estruturadas que o modelo de IA pode propor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    CLICK = "click"
    TYPE_TEXT = "type_text"
    LAUNCH_APP = "launch_app"
    NOTIFY = "notify"
    WINDOW_LAYOUT = "window_layout"
    COMMAND = "command"


@dataclass
class DesktopAction:
    action_type: ActionType
    target: str  # Nome do botão, rótulo do campo ou nome do aplicativo
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    requires_confirmation: bool = False

    def describe(self) -> str:
        if self.description:
            return self.description
        if self.action_type == ActionType.CLICK:
            return f"Clicar no elemento '{self.target}'"
        if self.action_type == ActionType.TYPE_TEXT:
            text = self.params.get("text", "")
            return f"Digitar '{text}' no campo '{self.target}'"
        if self.action_type == ActionType.LAUNCH_APP:
            return f"Abrir o aplicativo '{self.target}'"
        if self.action_type == ActionType.NOTIFY:
            return f"Notificação: {self.params.get('message', self.target)}"
        if self.action_type == ActionType.WINDOW_LAYOUT:
            return f"Ajustar layout de janelas: {self.target}"
        return f"Ação {self.action_type.value} em {self.target}"


@dataclass
class ActionPlan:
    thought: str
    actions: list[DesktopAction] = field(default_factory=list)
    raw_response: str = ""

    @property
    def is_empty(self) -> bool:
        return len(self.actions) == 0

    @property
    def has_high_risk_actions(self) -> bool:
        return any(a.requires_confirmation for a in self.actions)
