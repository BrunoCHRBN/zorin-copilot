# Decisão de design: política de risco pura e determinística para o loop de tool-calling
# ao vivo. Mantém-se independente de GTK/AT-SPI para ser testável headless. O live client
# consulta classify() antes de executar; ações de risco exigem confirmação explícita.

"""Classificação de risco de ferramentas executadas pelo agente em tempo real."""

from __future__ import annotations

from enum import Enum
from typing import Any


class RiskLevel(Enum):
    SAFE = "safe"
    CONFIRM = "confirm"


# Ferramentas que, por natureza, podem causar efeitos difíceis de desfazer ou expor dados.
HIGH_RISK_TOOLS = {
    "email_compose",
    "write_document",
    "organize_directory",
}

# Descrição amigável exibida ao usuário quando a confirmação é exigida.
RISK_DESCRIPTION = {
    "email_compose": "envio de e-mail",
    "write_document": "escrita/sobrescrita de arquivo",
    "organize_directory": "reorganização de arquivos",
}

# Atalhos cuja execução tipicamente fecha/encerra aplicativos ou perde estado.
DESTRUCTIVE_HOTKEYS = {
    "alt+f4",
    "alt+fn+f4",
    "ctrl+q",
    "ctrl+shift+q",
    "ctrl+w",
    "ctrl+shift+w",
    "super+q",
    "alt+f4",
}


def _normalize_hotkey(keys: Any) -> str:
    """Normaliza uma lista de teclas (['alt','f4']) em 'alt+f4' minúsculo e estável."""
    if not isinstance(keys, (list, tuple)):
        return ""
    return "+".join(str(k).strip().lower() for k in keys if str(k).strip())


class RiskPolicy:
    """Classifica o risco de uma chamada de ferramenta com base em nome + argumentos."""

    def classify(self, name: str, args: dict[str, Any] | None = None) -> tuple[RiskLevel, str]:
        """Retorna (nível de risco, descrição). Descrição vazia quando SAFE."""
        args = args or {}
        name = (name or "").strip()

        if name in HIGH_RISK_TOOLS:
            return RiskLevel.CONFIRM, RISK_DESCRIPTION.get(name, name)

        if name == "keyboard_hotkey":
            hotkey = _normalize_hotkey(args.get("keys"))
            if hotkey in DESTRUCTIVE_HOTKEYS:
                return RiskLevel.CONFIRM, "atalho destrutivo (fecha/encerra aplicativo)"

        return RiskLevel.SAFE, ""


# Aplicativos onde o agente NUNCA deve atuar (banco, gerenciador de senhas, etc.).
# Pode ser estendido em runtime a partir das preferências do usuário.
BLOCKED_APPS: set[str] = set()


def is_blocked_app(name: str | None) -> bool:
    """True se `name` (nome do app em foco) está na lista de bloqueio."""
    if not name:
        return False
    n = name.strip().lower()
    return any(blocked.strip().lower() == n for blocked in BLOCKED_APPS)
