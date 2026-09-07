# Decisão de design: ações da IA são objetos tipados e validados — nunca execução direta de strings arbitrárias de shell sem consentimento.

"""Definição do catálogo de ações estruturadas que o modelo de IA pode propor."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ActionType(str, Enum):
    CLICK = "click"
    TYPE_TEXT = "type_text"
    LAUNCH_APP = "launch_app"
    OPEN_URL = "open_url"
    SYSTEM_CONTROL = "system_control"
    NOTIFY = "notify"
    WINDOW_LAYOUT = "window_layout"
    COMMAND = "command"
    ANSWER = "answer"
    CAPTURE_SCREEN = "capture_screen"
    FIX_COMMAND = "fix_command"
    SMART_OCR = "smart_ocr"
    MEDIA_CONTROL = "media_control"
    WRITE_FILE = "write_file"
    ORGANIZE_FILES = "organize_files"
    OPEN_DOCUMENT = "open_document"
    READ_PAGE = "read_page"
    DEEP_RESEARCH = "deep_research"


@dataclass
class DesktopAction:
    action_type: ActionType
    target: str  # Nome do botão, app, controle ou mensagem
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    requires_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "target": self.target,
            "params": self.params,
            "description": self.description,
            "requires_confirmation": self.requires_confirmation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DesktopAction:
        return cls(
            action_type=ActionType(data["action_type"]),
            target=data.get("target", ""),
            params=data.get("params") or {},
            description=data.get("description", ""),
            requires_confirmation=bool(data.get("requires_confirmation", False)),
        )

    def describe(self) -> str:
        if self.description:
            return self.description
        if self.action_type == ActionType.LAUNCH_APP:
            return f"Abrir aplicativo '{self.target}'"
        if self.action_type == ActionType.OPEN_URL:
            return f"Abrir link no navegador: '{self.target}'"
        if self.action_type == ActionType.SYSTEM_CONTROL:
            return f"Ajustar sistema: {self.target}"
        if self.action_type == ActionType.CLICK:
            return f"Clicar no elemento '{self.target}'"
        if self.action_type == ActionType.TYPE_TEXT:
            text = self.params.get("text", "")
            return f"Digitar '{text}' no campo '{self.target}'"
        if self.action_type == ActionType.NOTIFY:
            return f"Notificação: {self.params.get('message', self.target)}"
        if self.action_type == ActionType.WINDOW_LAYOUT:
            return f"Ajustar layout de janelas: {self.target}"
        if self.action_type == ActionType.CAPTURE_SCREEN:
            return f"Capturar e analisar {'área da tela' if self.target == 'area' else 'tela inteira'}"
        if self.action_type == ActionType.ANSWER:
            return f"Resposta: {self.target}"
        if self.action_type == ActionType.FIX_COMMAND:
            cmd = self.params.get("command") or self.target
            return f"Auto-Cura: {cmd}"
        if self.action_type == ActionType.SMART_OCR:
            kind = self.params.get("kind", "conteúdo")
            return f"Copiar {kind} extraído da tela"
        if self.action_type == ActionType.MEDIA_CONTROL:
            act = self.params.get("action", self.target)
            player = self.params.get("player", "")
            return f"Controle de mídia: {act} ({player})" if player else f"Controle de mídia: {act}"
        if self.action_type == ActionType.WRITE_FILE:
            fname = self.params.get("filename", self.target)
            return f"Salvar documento '{fname}'"
        if self.action_type == ActionType.ORGANIZE_FILES:
            folder = self.params.get("directory", self.target)
            return f"Organizar pasta '{folder}'"
        if self.action_type == ActionType.OPEN_DOCUMENT:
            page = self.params.get("page_number", 1)
            page_str = f" na pág. {page}" if page > 1 else ""
            return f"Abrir documento '{self.target}'{page_str}"
        if self.action_type == ActionType.READ_PAGE:
            url_val = self.params.get("url") or self.target
            return f"Ler conteúdo da página web '{url_val}'"
        if self.action_type == ActionType.DEEP_RESEARCH:
            return f"Pesquisa aprofundada sobre '{self.target}'"
        return f"Ação {self.action_type.value} em {self.target}"


@dataclass
class ActionPlan:
    thought: str
    actions: list[DesktopAction] = field(default_factory=list)
    raw_response: str = ""
    extracted_text: str | None = None
    extracted_kind: str = "text"

    @property
    def is_empty(self) -> bool:
        return len(self.actions) == 0

    @property
    def has_high_risk_actions(self) -> bool:
        return any(a.requires_confirmation for a in self.actions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thought": self.thought,
            "actions": [a.to_dict() for a in self.actions],
            "raw_response": self.raw_response,
            "extracted_text": self.extracted_text,
            "extracted_kind": self.extracted_kind,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionPlan:
        """Reconstrói um plano a partir do JSON da sessão.

        Ações inválidas (enum desconhecido, formato corrompido) são descartadas
        em vez de derrubar a leitura do histórico: melhor reabrir uma conversa
        sem os botões de ação do que não conseguir reabri-la.
        """
        plan = cls(
            thought=data.get("thought", ""),
            raw_response=data.get("raw_response", ""),
            extracted_text=data.get("extracted_text"),
            extracted_kind=data.get("extracted_kind", "text"),
        )
        for raw in data.get("actions") or []:
            try:
                plan.actions.append(DesktopAction.from_dict(raw))
            except (ValueError, TypeError, KeyError, AttributeError):
                logger.debug("Ação ignorada ao recarregar plano: %r", raw)
        return plan
