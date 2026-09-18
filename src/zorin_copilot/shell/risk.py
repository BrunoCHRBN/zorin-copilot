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
# NOTA: `end_session` NÃO entra aqui — o risco depende do argumento `mode`
# (standby é seguro, quit não). Ver `RiskPolicy.classify`.
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
    "end_session": "encerramento completo do aplicativo",
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

    def __init__(self) -> None:
        self._custom_risk_tools: dict[str, tuple[RiskLevel, str]] = {}

    def register_risk(self, name: str, level: RiskLevel, description: str = "") -> None:
        """Registra uma regra explícita de risco para uma ferramenta específica (ex: ferramentas MCP)."""
        self._custom_risk_tools[name] = (level, description)

    def classify(self, name: str, args: dict[str, Any] | None = None) -> tuple[RiskLevel, str]:
        """Retorna (nível de risco, descrição). Descrição vazia quando SAFE."""
        args = args or {}
        name = (name or "").strip()

        if name in self._custom_risk_tools:
            return self._custom_risk_tools[name]

        if name in HIGH_RISK_TOOLS:
            return RiskLevel.CONFIRM, RISK_DESCRIPTION.get(name, name)

        # Classificação heurística de ferramentas MCP dinâmicas
        if name.startswith("mcp__") or name.startswith("mcp_"):
            lower = name.lower()
            mutating_verbs = (
                "write", "create", "delete", "drop", "exec", "run",
                "update", "insert", "commit", "push", "remove", "kill",
            )
            if any(verb in lower for verb in mutating_verbs):
                return RiskLevel.CONFIRM, f"execução de ferramenta MCP de mutação/sistema ({name})"

        if name == "keyboard_hotkey":
            hotkey = _normalize_hotkey(args.get("keys"))
            if hotkey in DESTRUCTIVE_HOTKEYS:
                return RiskLevel.CONFIRM, "atalho destrutivo (fecha/encerra aplicativo)"

        # Arg-aware de propósito: "standby" só encerra a chamada e deixa o
        # Copilot em espera ouvindo a palavra de ativação — é seguro e não pode
        # exigir confirmação, senão toda despedida viraria uma pergunta chata.
        # Só "quit" mata o processo, e esse passa pelo gate.
        if name == "end_session":
            mode = str(args.get("mode", "standby") or "standby").strip().lower()
            if mode == "quit":
                return RiskLevel.CONFIRM, RISK_DESCRIPTION.get(name, "encerramento do aplicativo")

        if name == "vscode_workspace":
            action = str(args.get("action", "")).strip().lower()
            if action in ("write_code", "create_file", "patch_code"):
                f_path = args.get("file_path", "")
                if f_path:
                    try:
                        from pathlib import Path
                        raw_path = Path(f_path).expanduser()
                        if not raw_path.is_absolute():
                            from ..core.vscode import VSCodeManager
                            base = VSCodeManager.get_active_workspace() or Path.cwd()
                            target = base / raw_path
                        else:
                            target = raw_path
                        if target.exists() and target.is_file() and target.stat().st_size > 50:
                            return RiskLevel.CONFIRM, f"atualização de arquivo de código existente '{target.name}' no VS Code"
                    except Exception:
                        pass

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
