# Decisão de design: ferramentas modulares para os agentes do Zorin Copilot.
# Compatíveis com o ecossistema CrewAI / LangChain quando presentes,
# mantendo uma interface pura para execução desacoplada.

"""Ferramentas de sistema e integrações para os agentes de orquestração."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from ...integrations.n8n_client import N8nClient, N8nExecutionResult
from ...shell.input_driver import VirtualInputDriver
from ...shell.risk import RiskLevel, RiskPolicy

logger = logging.getLogger(__name__)

DESTRUCTIVE_COMMAND_TOKENS: tuple[str, ...] = (
    "rm -rf",
    "sudo",
    "dd if=",
    "mkfs",
    ":(){ :|:& };:",
    "> /dev/sd",
    "shutdown",
    "reboot",
)


class CopilotTool:
    """Representação agnóstica de ferramenta para agentes."""

    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[..., Any],
    ) -> None:
        self.name = name
        self.description = description
        self.func = func

    def run(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self.func(*args, **kwargs)
        except Exception as err:
            logger.error("Erro na execução da tool %s: %s", self.name, err, exc_info=True)
            return f"Erro na execução da ferramenta {self.name}: {err}"


def create_n8n_tool(client: N8nClient | None = None) -> CopilotTool:
    """Cria a ferramenta de disparo de workflows do n8n."""
    active_client = client or N8nClient()

    def _trigger_n8n(webhook_path: str, payload_json: str = "{}") -> str:
        try:
            data = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
        except json.JSONDecodeError:
            return "Erro: payload_json precisa ser um JSON válido."

        res: N8nExecutionResult = active_client.trigger_webhook(webhook_path, data)
        if res.success:
            return json.dumps(res.data, ensure_ascii=False)
        return f"Falha no workflow n8n: {res.error}"

    return CopilotTool(
        name="trigger_n8n_workflow",
        description=(
            "Aciona um workflow determinístico no n8n local (ex: webhook/enviar-email, "
            "webhook/planilha). Argumentos: webhook_path (string) e payload_json (string JSON)."
        ),
        func=_trigger_n8n,
    )


def create_desktop_input_tool(input_driver: VirtualInputDriver | None = None) -> CopilotTool:
    """Cria a ferramenta de interação de teclado e mouse."""
    driver = input_driver or VirtualInputDriver()

    def _execute_input(action_type: str, x: int = 0, y: int = 0, text: str = "") -> str:
        act = action_type.lower()
        if act == "click":
            success, msg = driver.click(x, y)
            return msg or ("Clique executado com sucesso." if success else "Falha ao executar clique.")
        elif act == "type":
            success, msg = driver.type_text(text)
            return msg or (f"Texto '{text}' digitado com sucesso." if success else "Falha ao digitar texto.")
        elif act == "key":
            success, msg = driver.hotkey(text)
            return msg or (f"Tecla '{text}' pressionada." if success else "Falha ao pressionar tecla.")
        return f"Tipo de ação desconhecido: {action_type}. Use 'click', 'type' ou 'key'."

    return CopilotTool(
        name="simulate_desktop_input",
        description=(
            "Simula cliques de mouse ou digitação de teclado na tela do Zorin OS. "
            "Argumentos: action_type ('click', 'type', 'key'), x (int), y (int), text (string)."
        ),
        func=_execute_input,
    )


def create_shell_tool(risk_policy: RiskPolicy | None = None) -> CopilotTool:
    """Cria ferramenta de execução de comandos bash com análise de risco prévia."""
    import subprocess

    policy = risk_policy or RiskPolicy()

    def _run_shell(command: str) -> str:
        cmd_clean = command.strip()
        # Verificacao de tokens destrutivos
        for token in DESTRUCTIVE_COMMAND_TOKENS:
            if token in cmd_clean:
                return (
                    f"COMANDO BLOQUEADO: '{command}' contém token de alto risco ('{token}'). "
                    "Exige autorização explícita do usuário via interface antes de prosseguir."
                )

        level, desc = policy.classify("shell_execute", {"command": command})
        if level is RiskLevel.CONFIRM:
            return (
                f"COMANDO BLOQUEADO: '{command}' classificado como de alto risco ({desc}). "
                "Exige autorização explícita do usuário via interface antes de prosseguir."
            )

        try:
            res = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = res.stdout.strip()
            if res.returncode != 0:
                output += f"\nSTDERR: {res.stderr.strip()}"
            return output or "(Comando executado sem retorno textual)"
        except subprocess.TimeoutExpired:
            return "Erro: O comando excedeu o tempo limite de 30 segundos."
        except Exception as err:
            return f"Erro ao executar comando: {err}"

    return CopilotTool(
        name="run_sandboxed_shell",
        description="Executa um comando de terminal seguro e retorna stdout/stderr. Argumento: command (string).",
        func=_run_shell,
    )
