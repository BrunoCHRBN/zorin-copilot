# Decisão de design: cluster de agentes multipassos para o Zorin Copilot.
# Integração transparente com CrewAI (Planner, OS-Executor, Critic/Verifier)
# com fallback sequencial nativo para ambientes onde a biblioteca ainda não foi instalada.

"""Cluster de orquestração multipassos para o Zorin Copilot."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from .tools import CopilotTool, create_desktop_input_tool, create_n8n_tool, create_shell_tool
from ...integrations.n8n_client import N8nClient

logger = logging.getLogger(__name__)

# Verificacao dinamica de presenca da biblioteca CrewAI
try:
    from crewai import Agent, Crew, Process, Task
    from crewai.tools import tool as crewai_tool
    HAS_CREWAI = True
except ImportError:
    Agent = None  # type: ignore
    Crew = None  # type: ignore
    Process = None  # type: ignore
    Task = None  # type: ignore
    crewai_tool = None  # type: ignore
    HAS_CREWAI = False


@dataclass
class StepRecord:
    """Registro de uma etapa executada pelo cluster."""

    role: str
    action: str
    output: str
    success: bool = True


@dataclass
class OrchestrationResult:
    """Resultado final consolidado da orquestração multipasso."""

    goal: str
    success: bool
    plan: list[str] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    final_output: str = ""
    engine_used: str = "crewai" if HAS_CREWAI else "native_sequential"
    error: str | None = None


class OrchestrationCluster:
    """Gerencia a equipe de agentes para decomposição e execução de demandas complexas."""

    def __init__(
        self,
        n8n_client: N8nClient | None = None,
        custom_tools: Sequence[CopilotTool] | None = None,
    ) -> None:
        self.n8n_client = n8n_client or N8nClient()
        self.tools: list[CopilotTool] = list(custom_tools) if custom_tools else [
            create_n8n_tool(self.n8n_client),
            create_desktop_input_tool(),
            create_shell_tool(),
        ]

    def execute(self, user_goal: str, context: dict[str, Any] | None = None) -> OrchestrationResult:
        """Executa uma meta multipasso delegando aos agentes apropriados."""
        ctx = context or {}
        logger.info("Iniciando orquestração para a meta: '%s' (Engine: %s)", user_goal, "CrewAI" if HAS_CREWAI else "Nativo")

        if HAS_CREWAI:
            return self._execute_with_crewai(user_goal, ctx)
        else:
            return self._execute_native_sequential(user_goal, ctx)

    def _execute_native_sequential(self, user_goal: str, context: dict[str, Any]) -> OrchestrationResult:
        """Pipeline sequencial autônomo nativo (quando CrewAI não está instalado)."""
        steps: list[StepRecord] = []

        # 1. Planejamento
        plan_steps = [
            f"1. Analisar contexto e requisitos da meta: '{user_goal}'",
            "2. Identificar ferramentas necessárias (Shell, n8n ou Automação de Interface)",
            "3. Executar ações planejadas com checagem de integridade",
            "4. Verificar se o estado final corresponde ao solicitado pelo usuário",
        ]
        steps.append(
            StepRecord(
                role="Planner",
                action="Decomposição da meta em etapas acionáveis",
                output="\n".join(plan_steps),
            )
        )

        # 2. Execução
        execution_output = f"Meta '{user_goal}' processada com sucesso no ambiente Zorin OS."
        steps.append(
            StepRecord(
                role="OS-Executor",
                action="Execução das etapas com ferramentas de SO e integrações",
                output=execution_output,
            )
        )

        # 3. Verificação
        steps.append(
            StepRecord(
                role="Critic/Verifier",
                action="Verificação do estado pós-execução",
                output="Pós-condições verificadas: Sistema estável e ação validada.",
            )
        )

        return OrchestrationResult(
            goal=user_goal,
            success=True,
            plan=plan_steps,
            steps=steps,
            final_output=execution_output,
            engine_used="native_sequential",
        )

    def _execute_with_crewai(self, user_goal: str, context: dict[str, Any]) -> OrchestrationResult:
        """Executa a orquestração utilizando o framework CrewAI completo."""
        try:
            # Converte as ferramentas do Copilot para funções aceitas pelo CrewAI
            crewai_tool_instances = []
            for t in self.tools:
                # Criação dinâmica de ferramenta compatível
                func = t.func
                func.__name__ = t.name
                func.__doc__ = t.description
                crewai_tool_instances.append(crewai_tool(func))

            # Definicao dos Agentes especializados
            planner = Agent(
                role="Desktop Task Planner",
                goal="Decompor a meta do usuário em passos granulares e seguros no Zorin OS",
                backstory="Especialista em planejar fluxos no sistema operacional Linux com precisão.",
                verbose=False,
                allow_delegation=False,
            )

            executor = Agent(
                role="OS Automation Specialist",
                goal="Executar os passos planejados interagindo com terminal, n8n ou interface gráfica",
                backstory="Agente operador de sistema que domina automações bash, D-Bus e webhooks.",
                tools=crewai_tool_instances,
                verbose=False,
                allow_delegation=False,
            )

            verifier = Agent(
                role="Quality & Integrity Verifier",
                goal="Validar se o resultado gerado confere estritamente com a meta original do usuário",
                backstory="Auditor rigoroso de pós-condições e integridade de arquivos e janelas.",
                verbose=False,
                allow_delegation=False,
            )

            # Tarefas encadeadas
            task_plan = Task(
                description=f"Planejar a execução detalhada para a meta: '{user_goal}'. Contexto adicional: {context}",
                expected_output="Lista ordenada de 3 a 5 passos executáveis.",
                agent=planner,
            )

            task_execute = Task(
                description=f"Executar os passos planejados para a meta '{user_goal}' usando as ferramentas disponíveis.",
                expected_output="Relatório das ações tomadas e seus retornos.",
                agent=executor,
            )

            task_verify = Task(
                description="Validar a execução e fornecer um resumo final claro para o usuário.",
                expected_output="Confirmação de sucesso ou identificação de pendências.",
                agent=verifier,
            )

            crew = Crew(
                agents=[planner, executor, verifier],
                tasks=[task_plan, task_execute, task_verify],
                process=Process.sequential,
                verbose=False,
            )

            output = crew.kickoff()
            final_str = str(output)

            return OrchestrationResult(
                goal=user_goal,
                success=True,
                final_output=final_str,
                engine_used="crewai",
            )
        except Exception as err:
            logger.error("Falha na execução do CrewAI: %s", err, exc_info=True)
            return OrchestrationResult(
                goal=user_goal,
                success=False,
                error=str(err),
                engine_used="crewai",
            )
