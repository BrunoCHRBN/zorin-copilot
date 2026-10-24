# Decisão de design: subagente é um `AgentLoop` normal com TRÊS coisas trocadas —
# contexto isolado (histórico vazio e `session_context` vazio), registro RESTRITO e
# orçamento PRÓPRIO. Não há um segundo motor de raciocínio aqui: reaproveitar o laço
# significa que todos os freios já medidos (limite de passos, tempo, falha repetida,
# anti-loop, aprovação por risco) valem de graça para o subagente.
#
# A restrição de ferramentas é feita no SCHEMA, não por checagem depois do fato: o
# subagente só enxerga as ferramentas da sua allowlist, então ele não pode pedir uma
# ferramenta proibida e ser recusado — ele não sabe que ela existe. E como nenhum
# especialista do catálogo autoriza `run_subagent`, a recursão é impedida **por
# construção**: não há como um subagente abrir outro, nem por acidente nem por
# alucinação do modelo.
#
# Módulo puro: stdlib + `agent`/`agent_tools`, sem GTK, sem rede, sem provedor. Quem
# sabe construir um planejador é o chamador (`planner` / `planner_for` injetados), o
# que mantém tudo testável sem display e sem servidor de modelo.

"""Subagentes especializados: contexto isolado, ferramentas restritas, orçamento próprio."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from ..core.decision_trace import record as trace_record
from .agent import (
    STOP_ABORTED,
    STOP_MAX_STEPS,
    STOP_TIMEOUT,
    AgentLoop,
    AgentResult,
    Step,
)
from .agent_tools import FINISH_TOOLS, ToolRegistry

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Allowlist
# --------------------------------------------------------------------------- #


def matches_allowlist(name: str, patterns: Iterable[str]) -> bool:
    """`name` casa com algum padrão? Padrão terminado em `*` é prefixo.

    Um padrão de prefixo existe para ferramentas dinâmicas (as de MCP, cujo nome
    é `mcp__servidor__ferramenta` e não é conhecido em tempo de escrita).
    """
    candidate = (name or "").strip()
    if not candidate:
        return False
    for raw in patterns:
        pattern = str(raw or "").strip()
        if not pattern:
            continue
        if pattern.endswith("*"):
            if candidate.startswith(pattern[:-1]):
                return True
        elif candidate == pattern:
            return True
    return False


def resolve_allow(registry: ToolRegistry, patterns: Iterable[str]) -> list[str]:
    """Nomes REAIS do registro que casam com os padrões.

    Sempre acrescenta as ferramentas de encerramento: sem `done` no schema o
    subagente não sabe que pode terminar e só para por estouro de orçamento —
    um subagente que nunca conclui é indistinguível de um que falhou.
    """
    patterns = tuple(patterns)
    available = registry.names()
    resolved = [name for name in available if matches_allowlist(name, patterns)]
    for finisher in sorted(FINISH_TOOLS):
        if finisher in available and finisher not in resolved:
            resolved.append(finisher)
    return resolved


def restrict_registry(registry: ToolRegistry, patterns: Iterable[str]) -> ToolRegistry:
    """Registro só com as ferramentas que casam com `patterns` (original intacto)."""
    return registry.restricted(resolve_allow(registry, patterns))


# --------------------------------------------------------------------------- #
# Contrato
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SubagentSpec:
    """O contrato de um subagente: quem é, o que pode, quanto pode gastar."""

    #: Identificador estável (é o que o modelo passa em `run_subagent`).
    name: str
    #: Rótulo humano, para o chat.
    title: str
    #: Modelo do objetivo. `{input}` é substituído pela entrada do estágio.
    objective_template: str
    #: Ferramentas autorizadas (nome exato ou prefixo terminado em `*`).
    allow: tuple[str, ...]
    #: Orçamento de passos deste estágio.
    max_steps: int = 8
    #: Orçamento de tempo deste estágio, em segundos.
    max_seconds: float = 120.0
    #: O que este estágio entrega ao próximo (documentação do pipeline).
    produces: str = ""

    def build_objective(self, data: str, *, target_window: str = "") -> str:
        """Objetivo concreto deste estágio a partir da entrada encadeada e janela alvo opcional."""
        text = (data or "").strip()
        try:
            base = self.objective_template.format(input=text)
        except (KeyError, IndexError, ValueError):
            # Template com chave desconhecida ou chave literal: não vale derrubar
            # a execução por causa de um `{` solto — anexa e segue.
            base = f"{self.objective_template}\n\n{text}".strip()

        win = (target_window or "").strip()
        if win:
            base = (
                f"{base}\n\n"
                f"ESCOPO DE JANELA DEDICADO: Opere prioritariamente na janela/aplicativo '{win}'. "
                f"Use `inspect_window(query='{win}')` para focar e extrair a árvore AT-SPI desta janela "
                f"e salve os dados coletados em `scratchpad_write`."
            )
        return base


def chain_input(previous_input: str, stage_output: str) -> str:
    """Entrada do próximo estágio: a saída do anterior.

    Se o estágio não produziu texto, a entrada anterior segue. Alimentar o
    próximo com vazio o faria trabalhar às cegas — e "sem entrada" nunca é a
    intenção de quem encadeou, é ausência de saída.
    """
    return (stage_output or "").strip() or (previous_input or "")


# --------------------------------------------------------------------------- #
# Estado do pipeline (explícito, observável)
# --------------------------------------------------------------------------- #

#: Fases de uma notificação de progresso.
PHASE_START = "start"
PHASE_END = "end"

#: Motivos de parada do pipeline. Como no `AgentLoop`, toda execução termina com
#: exatamente um deles — nunca "porque sim".
PIPELINE_DONE = "done"
PIPELINE_NO_STAGES = "no_stages"
PIPELINE_NO_PLANNER = "no_planner"
PIPELINE_STAGE_FAILED = "stage_failed"
PIPELINE_ABORTED = "aborted"
PIPELINE_TIMEOUT = "timeout"

PIPELINE_MESSAGES = {
    PIPELINE_DONE: "Pipeline concluído.",
    PIPELINE_NO_STAGES: "O pipeline não tem estágios.",
    PIPELINE_NO_PLANNER: "Nenhum planejador disponível para o subagente.",
    PIPELINE_STAGE_FAILED: "Um estágio do pipeline falhou.",
    PIPELINE_ABORTED: "Pipeline interrompido pelo usuário.",
    PIPELINE_TIMEOUT: "Orçamento total de tempo do pipeline esgotado.",
}

#: Piso do orçamento de um estágio, em segundos. Existe só para nunca entregar
#: orçamento zero ao `AgentLoop` — não é um "mínimo útil" de tempo.
_MIN_STAGE_SECONDS = 0.001

#: Gravidade de um estágio concluído, na linguagem da folha de estilo. São os
#: mesmos três nomes que `AgentExecutionWidget.add_step` já usa para os passos.
SEVERITY_SUCCESS = "success"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

#: Rótulo de cada gravidade, no card. `interrompido` e `falhou` são coisas
#: diferentes para quem lê: a primeira pede mais orçamento, a segunda pede
#: investigação. Chamar as duas de "interrompido" esconde o defeito.
SEVERITY_LABELS = {
    SEVERITY_SUCCESS: "concluído",
    SEVERITY_WARNING: "interrompido",
    SEVERITY_ERROR: "falhou",
}

#: Paradas por orçamento: o estágio entregou trabalho PARCIAL e não quebrou.
#: Pintá-las de vermelho mandaria o usuário caçar um defeito que não existe;
#: pintá-las de verde esconderia que o estágio não terminou.
_PARTIAL_STOPS = frozenset({STOP_MAX_STEPS, STOP_TIMEOUT, STOP_ABORTED})


def stage_severity(run: Any) -> str:
    """Gravidade de um estágio, para o ícone e o rótulo do card.

    `SubagentRun.ok` é só `stop_reason == "done"` — um booleano que joga fora o
    MOTIVO. Um estágio parado por `max_steps` e um que quebrou com
    `no_provider` chegam os dois como `ok=False`, mas pedem reações opostas.
    Aqui o `ok` decide o caso feliz e o `stop_reason` separa "acabou o
    orçamento" de "quebrou". Motivo desconhecido cai em `error` de propósito:
    um catálogo novo que acrescente um motivo deve aparecer, não passar batido.
    """
    if run is None:
        return SEVERITY_WARNING
    if getattr(run, "ok", False):
        return SEVERITY_SUCCESS
    if str(getattr(run, "stop_reason", "") or "") in _PARTIAL_STOPS:
        return SEVERITY_WARNING
    return SEVERITY_ERROR


def stage_label(run: Any) -> str:
    """Rótulo do estágio concluído ("concluído" / "interrompido" / "falhou")."""
    return SEVERITY_LABELS[stage_severity(run)]


@dataclass
class SubagentRun:
    """O que UM estágio produziu. É também o que o chat mostra."""

    name: str
    title: str
    objective: str
    input: str
    output: str = ""
    ok: bool = False
    stop_reason: str = ""
    error: str = ""
    elapsed: float = 0.0
    steps: list[Step] = field(default_factory=list)
    target_window: str = ""

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "title": self.title,
            "objective": self.objective,
            "input": self.input,
            "output": self.output,
            "ok": self.ok,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "elapsed": round(self.elapsed, 3),
            "step_count": self.step_count,
        }
        if self.target_window:
            payload["target_window"] = self.target_window
        return payload


@dataclass
class PipelineState:
    """Estado explícito do pipeline no momento da notificação.

    Carrega a posição (`index`/`total`), o estágio corrente e o histórico de
    execuções — é o que permite a UI mostrar "2 de 3" sem manter estado próprio
    nem inferir nada.
    """

    phase: str
    index: int
    total: int
    spec: SubagentSpec | None = None
    runs: list[SubagentRun] = field(default_factory=list)
    elapsed: float = 0.0
    #: Preenchido apenas em `PHASE_END`.
    run: SubagentRun | None = None

    @property
    def position(self) -> str:
        """Posição legível: `"2/3"`."""
        return f"{self.index + 1}/{self.total}"

    @property
    def name(self) -> str:
        return self.spec.name if self.spec else ""

    @property
    def title(self) -> str:
        return self.spec.title if self.spec else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "index": self.index,
            "total": self.total,
            "position": self.position,
            "name": self.name,
            "title": self.title,
            "elapsed": round(self.elapsed, 3),
            "runs": [r.to_dict() for r in self.runs],
            "run": self.run.to_dict() if self.run else None,
        }


@dataclass
class PipelineResult:
    """Resultado completo do pipeline. Serializável."""

    status: str
    runs: list[SubagentRun] = field(default_factory=list)
    output: str = ""
    error: str = ""
    elapsed: float = 0.0
    run_id: str = ""

    @property
    def ok(self) -> bool:
        return self.status == PIPELINE_DONE

    @property
    def message(self) -> str:
        return PIPELINE_MESSAGES.get(self.status, self.status)

    @property
    def stage_count(self) -> int:
        return len(self.runs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "elapsed": round(self.elapsed, 3),
            "run_id": self.run_id,
            "stage_count": self.stage_count,
            "runs": [r.to_dict() for r in self.runs],
        }


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


class SubagentPipeline:
    """Encadeia subagentes: a saída de um estágio é a entrada do próximo.

    Cada estágio roda num `AgentLoop` próprio, com registro restrito e orçamento
    do `SubagentSpec`. O pipeline não conhece provedor: recebe `planner` (ou
    `planner_for`, quando cada especialista deve usar um modelo diferente).
    """

    def __init__(
        self,
        stages: Sequence[SubagentSpec],
        registry: ToolRegistry,
        planner: Any | None = None,
        *,
        planner_for: Callable[[SubagentSpec], Any] | None = None,
        max_total_seconds: float = 600.0,
        memory: Any | None = None,
        on_stage: Callable[[PipelineState], None] | None = None,
        on_step: Callable[[SubagentSpec, Step], None] | None = None,
        on_approval: Callable[[Step], bool] | None = None,
        run_id: str | None = None,
    ) -> None:
        self.stages = list(stages)
        self.registry = registry
        self.planner = planner
        self.planner_for = planner_for
        # Sem piso: um teto total de 0 significa "sem orçamento", e o pipeline
        # deve recusar começar — e não ganhar 1 segundo de graça por causa de um
        # `max()` defensivo. (Foi exatamente esse piso que fez um teste de teto
        # esgotado passar a medir o caso oposto.)
        self.max_total_seconds = max(0.0, float(max_total_seconds))
        self.memory = memory
        self.on_stage = on_stage
        self.on_step = on_step
        self.on_approval = on_approval
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self._abort = threading.Event()
        self._current_loop: AgentLoop | None = None

    # -- controle ----------------------------------------------------------- #

    def abort(self) -> None:
        """Pede parada. Encerra no fim do passo corrente do estágio corrente."""
        self._abort.set()
        loop = self._current_loop
        if loop is not None:
            loop.abort()

    def reset(self) -> None:
        """Limpa o sinal de parada, permitindo reusar o pipeline.

        Segue o `AgentLoop.reset()` de propósito: `run()` **não** limpa o sinal
        sozinho. Limpar automaticamente faria um `abort()` disparado antes do
        início ser silenciosamente ignorado — o botão de parada pareceria não
        funcionar.
        """
        self._abort.clear()

    @property
    def aborted(self) -> bool:
        return self._abort.is_set()

    # -- execução ----------------------------------------------------------- #

    def run(
        self,
        data: str,
        *,
        target_window: str = "",
        scratchpad_key: str = "",
    ) -> PipelineResult:
        """Executa os estágios em sequência. Nunca levanta."""
        started = time.monotonic()

        if not self.stages:
            return PipelineResult(
                status=PIPELINE_NO_STAGES,
                error=PIPELINE_MESSAGES[PIPELINE_NO_STAGES],
                elapsed=round(time.monotonic() - started, 3),
                run_id=self.run_id,
            )

        runs: list[SubagentRun] = []
        current = (data or "").strip()
        clean_window = (target_window or "").strip()
        clean_sp_key = (scratchpad_key or "").strip()

        for index, spec in enumerate(self.stages):
            if self._abort.is_set():
                return self._finish(PIPELINE_ABORTED, runs, started)
            if time.monotonic() - started >= self.max_total_seconds:
                return self._finish(PIPELINE_TIMEOUT, runs, started)

            planner = self._planner_for(spec)
            if planner is None:
                return self._finish(PIPELINE_NO_PLANNER, runs, started)

            objective = spec.build_objective(current, target_window=clean_window)
            self._notify(PHASE_START, index, spec, runs, started)

            run = self._run_stage(
                spec,
                planner,
                objective,
                current,
                started,
                target_window=clean_window,
            )
            runs.append(run)
            self._notify(PHASE_END, index, spec, runs, started, run=run)

            # Persiste achados consolidados do estágio na memória de trabalho (Scratchpad)
            # compartilhada com o agente pai e os próximos estágios.
            scratchpad = getattr(self.registry, "scratchpad", None)
            if isinstance(scratchpad, dict) and run.output.strip():
                scratchpad[f"subagent_{spec.name}"] = run.output.strip()
                if clean_sp_key and index == len(self.stages) - 1:
                    scratchpad[clean_sp_key] = run.output.strip()

            if not run.ok:
                return self._finish(
                    PIPELINE_STAGE_FAILED,
                    runs,
                    started,
                    error=(
                        f"O subagente '{spec.name}' não concluiu "
                        f"({run.stop_reason}): {run.error or run.output or 'sem detalhe'}"
                    ),
                )

            # Encadeamento: a saída vira a entrada do próximo.
            current = chain_input(current, run.output)

        return self._finish(PIPELINE_DONE, runs, started, output=runs[-1].output)

    # -- internos ----------------------------------------------------------- #

    def _planner_for(self, spec: SubagentSpec) -> Any | None:
        if self.planner_for is not None:
            try:
                return self.planner_for(spec)
            except Exception:
                logger.exception("planner_for falhou para o subagente '%s'", spec.name)
                return None
        return self.planner

    def _run_stage(
        self,
        spec: SubagentSpec,
        planner: Any,
        objective: str,
        stage_input: str,
        started: float,
        *,
        target_window: str = "",
    ) -> SubagentRun:
        """Roda UM estágio. Devolve sempre um `SubagentRun` — nunca levanta."""
        stage_started = time.monotonic()
        remaining = self.max_total_seconds - (stage_started - started)
        # O teto do estágio nunca passa do que sobrou do total. O piso é um
        # epsilon (e não 1 s) só para não entregar orçamento zero ao `AgentLoop`,
        # que pararia antes do primeiro passo por um motivo que não é do estágio.
        budget_seconds = min(float(spec.max_seconds), max(_MIN_STAGE_SECONDS, remaining))
        budget_steps = max(1, int(spec.max_steps))

        stage_registry = restrict_registry(self.registry, spec.allow)
        if target_window:
            try:
                from ..core.window_manager import WindowManager

                win = WindowManager.find_window(target_window)
                if win:
                    WindowManager.focus_window(win.id or win.app)
                    fence = getattr(stage_registry, "_fence", None)
                    if fence is not None and hasattr(fence, "set_chosen_window"):
                        fence.set_chosen_window(win)
            except Exception as exc:
                logger.debug("Não foi possível pré-focar janela '%s' no subagente: %s", target_window, exc)

        trace_record(
            "subagent",
            "start",
            span=spec.name,
            objective=objective,
            tools=stage_registry.names(),
            max_steps=budget_steps,
            max_seconds=budget_seconds,
        )

        loop = AgentLoop(
            planner,
            stage_registry,
            max_steps=budget_steps,
            max_seconds=budget_seconds,
            memory=self.memory,
            # Contexto ISOLADO: o subagente não recebe o histórico da conversa
            # principal. É o ponto do recurso — se recebesse, não haveria alívio
            # de contexto e o subagente repetiria o que o pai já fez.
            session_context=[],
            run_id=f"{self.run_id}-{spec.name}",
        )
        self._current_loop = loop

        def _forward_step(step: Step) -> None:
            if self.on_step is None:
                return
            try:
                self.on_step(spec, step)
            except Exception:
                logger.debug("on_step do subagente falhou", exc_info=True)

        result: AgentResult = loop.run(
            objective,
            on_step=_forward_step,
            on_approval=self.on_approval,
        )
        self._current_loop = None

        run = SubagentRun(
            name=spec.name,
            title=spec.title,
            objective=objective,
            input=stage_input,
            output=result.final_answer or "",
            ok=bool(result.success),
            stop_reason=result.stop_reason,
            error=result.error or "",
            elapsed=round(time.monotonic() - stage_started, 3),
            steps=list(result.steps),
            target_window=target_window,
        )
        trace_record(
            "subagent",
            "end",
            span=spec.name,
            ok=run.ok,
            stop_reason=run.stop_reason,
            steps=run.step_count,
            output_chars=len(run.output),
        )
        return run

    def _notify(
        self,
        phase: str,
        index: int,
        spec: SubagentSpec,
        runs: list[SubagentRun],
        started: float,
        *,
        run: SubagentRun | None = None,
    ) -> None:
        """Notifica o progresso. Observador quebrado não derruba o pipeline."""
        if self.on_stage is None:
            return
        state = PipelineState(
            phase=phase,
            index=index,
            total=len(self.stages),
            spec=spec,
            runs=list(runs),
            elapsed=round(time.monotonic() - started, 3),
            run=run,
        )
        try:
            self.on_stage(state)
        except Exception:
            logger.debug("on_stage do pipeline falhou", exc_info=True)

    def _finish(
        self,
        status: str,
        runs: list[SubagentRun],
        started: float,
        *,
        output: str = "",
        error: str = "",
    ) -> PipelineResult:
        if not output and runs:
            output = runs[-1].output
        return PipelineResult(
            status=status,
            runs=runs,
            output=output,
            error=error or ("" if status == PIPELINE_DONE else PIPELINE_MESSAGES.get(status, status)),
            elapsed=round(time.monotonic() - started, 3),
            run_id=self.run_id,
        )


# --------------------------------------------------------------------------- #
# Catálogo de especialistas
# --------------------------------------------------------------------------- #
#
# As allowlists são **listas de nomes reais** do `ToolRegistry`, não categorias
# inventadas: um nome errado aqui não dá erro — simplesmente some do schema e o
# subagente fica sem a ferramenta sem ninguém perceber. Por isso há teste que
# resolve cada allowlist contra o registro de verdade e exige que todo nome
# produza pelo menos uma ferramenta.
#
# Nenhum especialista autoriza `run_subagent`: é o que impede a recursão.

_RESEARCHER = SubagentSpec(
    name="researcher",
    title="Pesquisador",
    objective_template=(
        "Investigue a fundo o assunto abaixo e produza um relatório em português do Brasil.\n"
        "Regras de rigor:\n"
        "- Cite a origem de cada afirmação factual (URL ou nome do documento).\n"
        "- Separe explicitamente o que foi VERIFICADO numa fonte do que é INFERÊNCIA sua.\n"
        "- Se uma fonte não puder ser lida, diga que não pôde — nunca presuma o conteúdo.\n"
        "- Termine com as conclusões e o que ficou em aberto.\n\n"
        "Assunto: {input}"
    ),
    allow=(
        "web_search",
        "deep_web_search",
        "read_web_page",
        "read_open_webpage",
        "academic_search",
        "search_documents",
        "read_document_page",
        "read_file",
        "list_directory",
        "search_code",
        "scratchpad_write",
        "scratchpad_read",
        "plan_update",
    ),
    max_steps=12,
    max_seconds=240.0,
    produces="relatório com fontes e ressalvas do que não pôde ser verificado",
)

_REFACTORER = SubagentSpec(
    name="refactorer",
    title="Refatorador",
    objective_template=(
        "Refatore o código conforme o pedido abaixo, em português do Brasil.\n"
        "Método:\n"
        "1. LEIA antes de escrever: localize os pontos com `search_code` e leia os arquivos.\n"
        "2. Faça a menor mudança que resolve o pedido; não reformate o que não foi pedido.\n"
        "3. Depois de editar, RODE os testes do projeto para confirmar que nada quebrou.\n"
        "4. Se os testes falharem, corrija ou reverta com `undo_last` — não declare sucesso.\n"
        "5. Relate: arquivos tocados, o que mudou e o resultado real dos testes.\n\n"
        "Pedido: {input}"
    ),
    allow=(
        "search_code",
        "read_file",
        "list_directory",
        "git_status",
        "git_diff",
        "git_log",
        "system_processes",
        "write_document",
        "run_command",
        "undo_last",
        "scratchpad_write",
        "scratchpad_read",
        "plan_update",
    ),
    max_steps=15,
    max_seconds=300.0,
    produces="relatório de mudanças com o resultado real da verificação",
)

_A11Y_AUDITOR = SubagentSpec(
    name="a11y_auditor",
    title="Auditor de acessibilidade",
    objective_template=(
        "Audite a acessibilidade da interface descrita abaixo, em português do Brasil.\n"
        "Método:\n"
        "1. Obtenha a árvore de acessibilidade da janela (`inspect_window`, `get_ui_tree`, `list_open_windows`).\n"
        "2. Procure os defeitos clássicos: controle sem nome acessível, ícone usado como "
        "único rótulo, ordem de foco incoerente, alvo de clique pequeno demais, "
        "informação transmitida só por cor, texto cortado.\n"
        "3. Para cada achado, diga o widget exato, qual é o defeito e como corrigir.\n"
        "4. NÃO clique nem altere nada: esta é uma auditoria de leitura.\n"
        "5. Se a árvore não estiver acessível, relate isso como bloqueio — não invente.\n\n"
        "Alvo: {input}"
    ),
    allow=(
        "list_open_windows",
        "inspect_window",
        "get_ui_tree",
        "find_element",
        "locate_element",
        "locate_element_visual",
        "find_on_screen",
        "read_screen_text",
        "capture_screen",
        "focus_window",
        "scroll_page",
        "scratchpad_write",
        "scratchpad_read",
        "plan_update",
    ),
    max_steps=12,
    max_seconds=180.0,
    produces="lista de achados com widget, defeito e correção sugerida",
)

_WINDOW_ANALYST = SubagentSpec(
    name="window_analyst",
    title="Analista de janelas",
    objective_template=(
        "Inspecione e cruze informações entre as janelas abertas conforme o pedido abaixo, "
        "em português do Brasil.\n"
        "Método:\n"
        "1. Mapeie as janelas com `list_open_windows` e inspecione cada janela alvo com `inspect_window`.\n"
        "2. Extraia os dados factuais de cada janela e grave imediatamente no Scratchpad com `scratchpad_write`.\n"
        "3. Compare e consolide os dados entre os aplicativos sem inventar valores ausentes.\n"
        "4. Entregue um relatório estruturado indicando de qual janela cada dado foi extraído.\n\n"
        "Pedido: {input}"
    ),
    allow=(
        "list_open_windows",
        "inspect_window",
        "focus_window",
        "get_ui_tree",
        "find_element",
        "locate_element",
        "read_screen_text",
        "scroll_page",
        "scratchpad_write",
        "scratchpad_read",
        "plan_update",
    ),
    max_steps=12,
    max_seconds=180.0,
    produces="dados estruturados extraídos e comparados entre múltiplas janelas",
)

#: Especialistas disponíveis, por nome.
CATALOG: dict[str, SubagentSpec] = {
    spec.name: spec for spec in (_RESEARCHER, _REFACTORER, _A11Y_AUDITOR, _WINDOW_ANALYST)
}


def catalog_names() -> list[str]:
    """Nomes do catálogo, ordenados (para mensagens de erro e descrições)."""
    return sorted(CATALOG)


def get_spec(name: str) -> SubagentSpec | None:
    """Especialista do catálogo pelo nome (aceita variações de caixa/espaço)."""
    return CATALOG.get((name or "").strip().lower())


def build_pipeline(
    names: Sequence[str],
    registry: ToolRegistry,
    planner: Any | None = None,
    **kwargs: Any,
) -> SubagentPipeline:
    """Monta um pipeline a partir de nomes do catálogo.

    Nome desconhecido vira `ValueError` aqui, na montagem — falhar cedo é melhor
    que um pipeline que roda e descobre no meio que um estágio não existe.
    """
    stages: list[SubagentSpec] = []
    for raw in names:
        spec = get_spec(raw)
        if spec is None:
            raise ValueError(
                f"Especialista desconhecido: '{raw}'. Disponíveis: {', '.join(catalog_names())}."
            )
        stages.append(spec)
    return SubagentPipeline(stages, registry, planner, **kwargs)
