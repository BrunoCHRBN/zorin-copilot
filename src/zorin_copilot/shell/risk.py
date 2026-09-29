# Decisão de design: política de risco pura e determinística para o loop de tool-calling
# ao vivo. Mantém-se independente de GTK/AT-SPI para ser testável headless. O live client
# consulta classify() antes de executar; ações de risco exigem confirmação explícita.

"""Classificação de risco de ferramentas executadas pelo agente em tempo real."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Sequence


class RiskLevel(Enum):
    """Nível de risco de uma chamada de ferramenta.

    - ``SAFE``: executa imediatamente — sem pergunta e sem aviso especial.
    - ``AUTO``: executa **sem perguntar**, mas o passo é sinalizado para aviso
      posterior ("anuncia depois"). É o nível da autonomia graduada: só deve ser
      atribuído a ações **reversíveis** e de baixo alcance. Nenhuma regra embutida
      o produz hoje — quem o usa é :meth:`RiskPolicy.register_risk` (ferramentas
      MCP e as regras de zona de confiança/reversibilidade que virão). Ver
      ``docs/ANEXO_AUTONOMIA_PREREQUISITOS.md``.
    - ``CONFIRM``: bloqueia até confirmação explícita do usuário.
    """

    SAFE = "safe"
    AUTO = "auto"
    CONFIRM = "confirm"


# Níveis de autonomia do usuário. Governam o TETO do que executa sem pergunta:
# - "low": nada além de SAFE executa sem confirmação — um AUTO é rebaixado a CONFIRM.
# - "standard": comportamento histórico — SAFE e AUTO executam; CONFIRM pergunta.
# - "high": como "standard", mais a promoção de ações reversíveis em zona de
#   confiança a AUTO (hoje: escrita de arquivo em `trusted_directories`).
AUTONOMY_LEVELS = ("low", "standard", "high")
DEFAULT_AUTONOMY_LEVEL = "standard"


def normalize_autonomy_level(value: Any) -> str:
    """Normaliza o nível de autonomia; valor ausente/inválido cai em ``"low"``.

    Fail-closed e pelo lado que se VÊ: um valor sem sentido nunca concede MAIS
    autonomia — cai no nível mais restritivo (mais perguntas), não no default.
    """
    candidate = str(value or "").strip().lower()
    return candidate if candidate in AUTONOMY_LEVELS else "low"


# Ferramentas que, por natureza, podem causar efeitos difíceis de desfazer ou expor dados.
# NOTA: `end_session` NÃO entra aqui — o risco depende do argumento `mode`
# (standby é seguro, quit não). Ver `RiskPolicy.classify`.
HIGH_RISK_TOOLS = {
    "email_compose",
    "write_document",
    "organize_directory",
    "network_release_port",
    "network_reconnect",
    "docker_container_action",
    "system_service_action",
}

# Descrição amigável exibida ao usuário quando a confirmação é exigida.
RISK_DESCRIPTION = {
    "email_compose": "envio de e-mail",
    "write_document": "escrita/sobrescrita de arquivo",
    "organize_directory": "reorganização de arquivos",
    "end_session": "encerramento completo do aplicativo",
    "network_release_port": "liberação de porta e encerramento de processo",
    "network_reconnect": "reconexão da interface de rede",
    "docker_container_action": "ação de ciclo de vida no container Docker (restart/stop)",
    "system_service_action": "alteração de estado de serviço de sistema (systemd)",
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

    def __init__(
        self,
        autonomy_level: str = DEFAULT_AUTONOMY_LEVEL,
        trusted_dirs: Sequence[str] | None = None,
    ) -> None:
        self._custom_risk_tools: dict[str, tuple[RiskLevel, str]] = {}
        self.autonomy_level = normalize_autonomy_level(autonomy_level)
        # Zonas de confiança. Vazio = nenhuma promoção a AUTO (default conservador);
        # quem injeta é o composition root (a UI passa `config.trusted_directories`).
        self._trusted_dirs: list[Path] = []
        self.set_trusted_dirs(trusted_dirs)

    def register_risk(self, name: str, level: RiskLevel, description: str = "") -> None:
        """Registra uma regra explícita de risco para uma ferramenta específica (ex: ferramentas MCP)."""
        self._custom_risk_tools[name] = (level, description)

    def _in_trusted_zone(self, file_path: str) -> bool:
        """True se `file_path` está dentro de uma zona de confiança configurada."""
        if not self._trusted_dirs:
            return False
        try:
            target = Path(str(file_path)).expanduser().resolve()
        except Exception:
            return False
        return any(target == base or base in target.parents for base in self._trusted_dirs)

    def set_trusted_dirs(self, trusted_dirs: Sequence[str] | None) -> None:
        """Reconfigura as zonas de confiança (também chamado ao salvar preferências)."""
        self._trusted_dirs = [
            Path(str(d)).expanduser().resolve() for d in (trusted_dirs or ())
        ]

    def classify(self, name: str, args: dict[str, Any] | None = None) -> tuple[RiskLevel, str]:
        """Retorna (nível de risco, descrição), já com o teto de autonomia aplicado."""
        level, desc = self._classify_base(name, args)
        # Teto de autonomia: em "low", nada além de SAFE executa sem pergunta —
        # um AUTO rebaixa para CONFIRM (o lado que se VÊ).
        if self.autonomy_level == "low" and level is RiskLevel.AUTO:
            return RiskLevel.CONFIRM, desc
        return level, desc

    def _classify_base(self, name: str, args: dict[str, Any] | None = None) -> tuple[RiskLevel, str]:
        """Classificação por nome + argumentos, sem o teto de autonomia."""
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

        if name == "run_command":
            cmd = str(args.get("command", "")).strip().lower()
            dangerous_tokens = (
                "rm ", "rmdir", "shutdown", "reboot", "mkfs", "dd ", "chmod", "chown",
                "> ", ">>", "sudo", "systemctl", "kill", "pkill", "git push", "git reset", "git clean",
            )
            if any(t in cmd for t in dangerous_tokens):
                return RiskLevel.CONFIRM, f"execução de comando terminal sensível '{cmd[:60]}'"

        if name in ("write_file", "edit_file"):
            fpath = args.get("file_path") or args.get("path") or ""
            # Autonomia alta + zona de confiança: a escrita é reversível (há
            # snapshot/undo) e o alvo é um diretório que o usuário marcou como
            # confiável → AUTO (executa e avisa depois). Fora da zona, ou no nível
            # padrão, continua CONFIRM.
            if self.autonomy_level == "high" and fpath and self._in_trusted_zone(fpath):
                return RiskLevel.AUTO, f"escrita em zona de confiança ('{fpath}')"
            return RiskLevel.CONFIRM, f"escrita ou modificação no arquivo '{fpath}'"

        # Agenda é um compromisso do mundo real — e, com o Google conectado, sai
        # da máquina. `list` e `freebusy` são leitura e ficam SAFE; `create`,
        # `update` e `delete` pedem confirmação, como `write_document` e
        # `email_compose`. O default do argumento é "create" (ver a ferramenta),
        # então a ausência de `action` também confirma: um valor sem sentido erra
        # para o lado que se VÊ.
        if name == "calendar_event":
            action = str(args.get("action", "create") or "create").strip().lower()
            if action in ("create", "update", "delete"):
                return RiskLevel.CONFIRM, f"alteração na agenda ({action})"

        # Drive: `list`/`search`/`get` são leitura e ficam SAFE. As escritas na
        # nuvem e o `download` (que grava um arquivo local, podendo sobrescrever)
        # pedem confirmação — mesmo critério de `write_file`/`email_compose`.
        if name == "google_drive":
            action = str(args.get("action", "list") or "list").strip().lower()
            if action in ("upload", "download", "mkdir", "move", "rename", "trash", "delete"):
                return RiskLevel.CONFIRM, f"alteração no Google Drive ({action})"

        # Gmail: `list`/`get`/`labels` são leitura e `review` só consulta a IA — os
        # três ficam SAFE. `send` (o envio SEMPRE confirmado que o Bruno pediu),
        # `draft` e as marcações de lida/não lida alteram a conta e pedem confirmação.
        if name == "google_gmail":
            action = str(args.get("action", "list") or "list").strip().lower()
            if action in ("send", "draft", "mark_read", "mark_unread"):
                return RiskLevel.CONFIRM, f"alteração no Gmail ({action})"

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
