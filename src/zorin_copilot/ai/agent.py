# Decisão de design: o loop do agente é uma máquina de estados PURA — stdlib + dataclasses,
# sem GTK, sem AT-SPI, sem conhecer provedor nenhum. Todo acesso ao desktop entra pelo
# `ToolRegistry` injetado; todo raciocínio entra pelo `planner` injetado. É o que permite
# testar parada, aprovação e limites sem xvfb e sem rede.
#
# Regra de ouro: **o modelo nunca decide quando parar**. Ele pode pedir para parar chamando
# `done`; todo outro encerramento vem de uma condição explícita deste arquivo (limite de
# passos, tempo, falha repetida, aborto, rejeição). Modelo que "esquece" de terminar não
# trava o desktop nem queima tokens indefinidamente.

"""Núcleo do modo agente: planeja e executa ações no desktop sob supervisão."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..shell.risk import RiskLevel
from .agent_tools import FINISH_TOOLS, ToolRegistry

logger = logging.getLogger(__name__)

#: Motivos de parada. Cada execução termina com exatamente um deles — nunca "porque sim".
STOP_DONE = "done"
STOP_MAX_STEPS = "max_steps"
STOP_TIMEOUT = "timeout"
STOP_REPEATED = "repeated_failure"
STOP_ABORTED = "aborted"
STOP_REJECTED = "rejected"
STOP_ERROR = "error"
STOP_NO_PROVIDER = "no_provider"

STOP_MESSAGES = {
    STOP_DONE: "Objetivo concluído.",
    STOP_MAX_STEPS: "Limite de passos atingido.",
    STOP_TIMEOUT: "Tempo limite atingido.",
    STOP_REPEATED: "A mesma ação falhou repetidas vezes.",
    STOP_ABORTED: "Execução interrompida pelo usuário.",
    STOP_REJECTED: "Ação recusada pela política de risco/aprovação.",
    STOP_ERROR: "Falha irrecuperável do planejador.",
    STOP_NO_PROVIDER: "Nenhum provedor disponível para o modo solicitado.",
}

DEFAULT_MAX_STEPS = 15
DEFAULT_MAX_SECONDS = 300.0
DEFAULT_MAX_REPEATS = 2


# --------------------------------------------------------------------------- #
# Contratos
# --------------------------------------------------------------------------- #


@dataclass
class ToolCall:
    """Uma chamada de ferramenta pedida pelo planejador."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class AgentDecision:
    """O que o planejador quer fazer a seguir.

    Ou vem `tool_call`, ou vem `final_answer` (encerramento), ou vem `error`
    (falha do provedor — o loop para com `STOP_ERROR`).
    """

    tool_call: ToolCall | None = None
    final_answer: str = ""
    error: str = ""


class AgentPlanner(Protocol):
    """Planejador: dado o objetivo e o histórico, decide UM passo por vez."""

    name: str

    def decide(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
        session_context: Sequence[dict[str, str]] | None = None,
    ) -> AgentDecision:
        ...


@dataclass
class Step:
    """Um passo executado (ou recusado) — é também o que a UI mostra."""

    index: int
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    risk: str = "safe"
    requires_approval: bool = False
    approved: bool | None = None
    ok: bool | None = None
    observation: Any = None
    error: str = ""
    elapsed: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "args": dict(self.args),
            "rationale": self.rationale,
            "risk": self.risk,
            "requires_approval": self.requires_approval,
            "approved": self.approved,
            "ok": self.ok,
            "observation": self.observation,
            "error": self.error,
            "elapsed": self.elapsed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Step:
        return cls(
            index=int(data.get("index", 0)),
            tool=str(data.get("tool", "")),
            args=dict(data.get("args") or {}),
            rationale=str(data.get("rationale", "")),
            risk=str(data.get("risk", "safe")),
            requires_approval=bool(data.get("requires_approval", False)),
            approved=data.get("approved"),
            ok=data.get("ok"),
            observation=data.get("observation"),
            error=str(data.get("error", "")),
            elapsed=float(data.get("elapsed", 0.0)),
        )


@dataclass
class AgentResult:
    """Resultado completo de uma execução. Serializável (CLI --json, UI, logs)."""

    objective: str
    success: bool
    stop_reason: str
    final_answer: str = ""
    error: str = ""
    elapsed: float = 0.0
    provider: str = ""
    dry_run: bool = False
    run_id: str = ""
    steps: list[Step] = field(default_factory=list)

    @property
    def stop_message(self) -> str:
        return STOP_MESSAGES.get(self.stop_reason, self.stop_reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "success": self.success,
            "stop_reason": self.stop_reason,
            "stop_message": self.stop_message,
            "final_answer": self.final_answer,
            "error": self.error,
            "elapsed": round(self.elapsed, 3),
            "provider": self.provider,
            "dry_run": self.dry_run,
            "run_id": self.run_id,
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentResult:
        steps = [
            Step.from_dict(s)
            for s in data.get("steps", [])
            if isinstance(s, dict)
        ]
        return cls(
            objective=str(data.get("objective", "")),
            success=bool(data.get("success", False)),
            stop_reason=str(data.get("stop_reason", "")),
            final_answer=str(data.get("final_answer", "")),
            error=str(data.get("error", "")),
            elapsed=float(data.get("elapsed", 0.0)),
            provider=str(data.get("provider", "")),
            dry_run=bool(data.get("dry_run", False)),
            run_id=str(data.get("run_id", "")),
            steps=steps,
        )


class ScriptedPlanner:
    """Planejador de roteiro fixo — usado nos testes e em `--script` de depuração.

    Aceita `ToolCall` ou `AgentDecision`. Quando o roteiro acaba, encerra.
    """

    def __init__(self, decisions: list[ToolCall | AgentDecision], name: str = "scripted") -> None:
        self.name = name
        self._queue = list(decisions)
        self.calls: list[list[dict[str, Any]]] = []  # histórico recebido, para asserts

    def decide(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        history: list[dict[str, Any]],
        session_context: Sequence[dict[str, str]] | None = None,
    ) -> AgentDecision:
        self.calls.append(list(history))
        if not self._queue:
            return AgentDecision(final_answer="(roteiro esgotado)")
        item = self._queue.pop(0)
        if isinstance(item, AgentDecision):
            return item
        return AgentDecision(tool_call=item)


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #


class AgentLoop:
    """Executa um objetivo no desktop passo a passo, com freios explícitos."""

    def __init__(
        self,
        planner: AgentPlanner,
        registry: ToolRegistry,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        max_repeats: int = DEFAULT_MAX_REPEATS,
        memory: Any | None = None,
        session_context: Sequence[dict[str, str]] | None = None,
        run_id: str | None = None,
    ) -> None:
        self.planner = planner
        self.registry = registry
        self.max_steps = max(1, int(max_steps))
        self.max_seconds = float(max_seconds)
        self.max_repeats = max(1, int(max_repeats))
        self.memory = memory
        self.session_context = list(session_context or [])
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self._abort = threading.Event()
        self._running = False
        self._last_error = ""

    # -- controle ----------------------------------------------------------- #

    def abort(self) -> None:
        """Pede parada. O loop encerra ao fim do passo corrente."""
        self._abort.set()

    def reset(self) -> None:
        """Limpa o sinal de parada para permitir nova execução com o mesmo loop."""
        self._abort.clear()
        self._last_error = ""

    @property
    def aborted(self) -> bool:
        return self._abort.is_set()

    @property
    def running(self) -> bool:
        return self._running

    # -- execução ----------------------------------------------------------- #

    def plan(self, objective: str, **kwargs: Any) -> AgentResult:
        """Gera o plano SEM tocar no desktop.

        É `run()` com um registro em dry-run: o planejador raciocina de verdade,
        mas toda ferramenta mutante devolve "eu faria X".
        """
        return self.run(objective, dry_run=True, **kwargs)

    def run(
        self,
        objective: str,
        *,
        dry_run: bool = False,
        session_context: Sequence[dict[str, str]] | None = None,
        on_step: Callable[[Step], None] | None = None,
        on_approval: Callable[[Step], bool] | None = None,
        initial_steps: Sequence[Step] | None = None,
    ) -> AgentResult:
        """Executa o objetivo. Sempre devolve um `AgentResult` — nunca levanta."""
        if self.planner is None:
            return self._result(objective, STOP_NO_PROVIDER, dry_run=dry_run, error="Planejador ausente.")

        ctx = list(session_context) if session_context is not None else self.session_context
        registry = self.registry.for_dry_run() if dry_run else self.registry
        started = time.monotonic()
        steps: list[Step] = list(initial_steps or [])
        self._running = True

        try:
            while True:
                if self._abort.is_set():
                    return self._finish(steps, objective, STOP_ABORTED, started, dry_run)

                # Ferramentas dinâmicas relevantes ao objetivo, contexto e passos ativos
                active_tools = {s.tool for s in steps}
                tools = registry.schema(
                    objective=objective,
                    session_context=ctx,
                    active_tools=active_tools,
                )

                if len(steps) >= self.max_steps:
                    final_answer = self._synthesize_on_max_steps(
                        objective, tools, steps, started, dry_run, session_context=ctx
                    )
                    return self._finish(
                        steps,
                        objective,
                        STOP_MAX_STEPS,
                        started,
                        dry_run,
                        final_answer=final_answer,
                    )

                if time.monotonic() - started > self.max_seconds:
                    return self._finish(steps, objective, STOP_TIMEOUT, started, dry_run)

                step_start_time = time.monotonic()
                decision = self._ask_planner(objective, tools, steps, started, dry_run, session_context=ctx)
                if decision is None:
                    return self._finish(steps, objective, STOP_ERROR, started, dry_run, error=self._last_error)

                if decision.error:
                    return self._finish(
                        steps, objective, STOP_ERROR, started, dry_run, error=decision.error
                    )

                if decision.tool_call is None:
                    return self._finish(
                        steps,
                        objective,
                        STOP_DONE,
                        started,
                        dry_run,
                        final_answer=decision.final_answer or "Objetivo concluído.",
                    )

                call = decision.tool_call
                step = self._new_step(len(steps), call, registry)
                if step is None:
                    return self._finish(
                        steps,
                        objective,
                        STOP_DONE,
                        started,
                        dry_run,
                        final_answer=call.args.get("answer", "") or "Objetivo concluído.",
                    )

                # Aprovação: sem callback, ações de risco são RECUSADAS (não executadas).
                if step.requires_approval and not dry_run:
                    if on_approval is None:
                        step.error = "Ação exige aprovação e nenhum aprovador foi fornecido."
                        steps.append(step)
                        self._audit(objective, step)
                        return self._finish(
                            steps, objective, STOP_REJECTED, started, dry_run, error=step.error
                        )
                    try:
                        step.approved = bool(on_approval(step))
                    except Exception as exc:
                        step.approved = False
                        step.error = f"Aprovador falhou: {exc}"
                    if not step.approved:
                        step.error = step.error or "Ação recusada pelo usuário."
                        steps.append(step)
                        self._audit(objective, step)
                        return self._finish(
                            steps, objective, STOP_REJECTED, started, dry_run, error=step.error
                        )

                observation = registry.call(step.tool, step.args)
                step.ok = bool(observation.get("ok"))
                step.observation = observation
                if not step.ok:
                    step.error = str(observation.get("error") or observation.get("message") or "falha")
                # Tempo real gasto nesta etapa individual
                step.elapsed = round(time.monotonic() - step_start_time, 3)
                steps.append(step)
                self._audit(objective, step)

                if on_step is not None:
                    try:
                        on_step(step)
                    except Exception:  # observador não derruba a execução
                        logger.debug("on_step falhou", exc_info=True)

                # Mesma ação falhando N vezes seguidas = o plano está errado.
                # Insistir só queima tokens e arrisca efeito colateral repetido.
                repeated = self._repeated_failure(steps)
                if repeated:
                    return self._finish(steps, objective, STOP_REPEATED, started, dry_run, error=repeated)
        finally:
            self._running = False

    # -- internos ----------------------------------------------------------- #

    def _ask_planner(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        steps: list[Step],
        started: float,
        dry_run: bool,
        session_context: Sequence[dict[str, str]] | None = None,
    ) -> AgentDecision | None:
        self._last_error = ""
        try:
            import inspect
            sig = inspect.signature(self.planner.decide)
            kwargs: dict[str, Any] = {}
            if "session_context" in sig.parameters:
                kwargs["session_context"] = session_context
            if "max_steps" in sig.parameters:
                kwargs["max_steps"] = self.max_steps
            return self.planner.decide(
                objective,
                tools,
                [s.to_dict() for s in steps],
                **kwargs,
            )
        except Exception as exc:
            logger.exception("Planejador '%s' falhou", getattr(self.planner, "name", "?"))
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None

    def _synthesize_on_max_steps(
        self,
        objective: str,
        tools: list[dict[str, Any]],
        steps: list[Step],
        started: float,
        dry_run: bool,
        session_context: Sequence[dict[str, str]] | None = None,
    ) -> str:
        """Sintetiza os resultados obtidos quando o limite de passos é atingido."""
        if not steps or self.planner is None:
            return ""
        # Não sintetiza em planejadores de script fixo (testes unitários determinísticos)
        if not hasattr(self.planner, "provider") and getattr(self.planner, "name", "") == "scripted":
            return ""
        try:
            synthesis_prompt = (
                f"{objective}\n\n"
                "[AVISO DE SISTEMA]: O limite de passos foi atingido antes da conclusão formal. "
                "Com base exclusivamente nas etapas executadas e observações obtidas até aqui, "
                "apresente uma resposta consolidada com o que foi descoberto e próximos passos recomendados."
            )
            decision = self._ask_planner(
                synthesis_prompt,
                tools,
                steps,
                started,
                dry_run,
                session_context=session_context,
            )
            if decision and decision.final_answer:
                return decision.final_answer
            if decision and decision.tool_call and decision.tool_call.name in FINISH_TOOLS:
                return str(decision.tool_call.args.get("answer", "") or "")
        except Exception:
            logger.debug("Falha na síntese pós-limite de passos", exc_info=True)
        return ""

    def _new_step(self, index: int, call: ToolCall, registry: ToolRegistry) -> Step | None:
        """Monta o passo. `None` = ferramenta de encerramento (`done`/`finish`)."""
        name = (call.name or "").strip()
        if name in FINISH_TOOLS:
            return None
        level, description = registry.classify(name, call.args)
        return Step(
            index=index,
            tool=name,
            args=dict(call.args or {}),
            rationale=call.rationale,
            risk=level.value,
            requires_approval=level == RiskLevel.CONFIRM,
        )

    def _finish(
        self,
        steps: list[Step],
        objective: str,
        stop_reason: str,
        started: float,
        dry_run: bool,
        *,
        final_answer: str = "",
        error: str = "",
    ) -> AgentResult:
        success = stop_reason == STOP_DONE
        return AgentResult(
            objective=objective,
            success=success,
            stop_reason=stop_reason,
            steps=steps,
            final_answer=final_answer,
            error=error or ("" if success else STOP_MESSAGES.get(stop_reason, stop_reason)),
            elapsed=round(time.monotonic() - started, 3),
            provider=getattr(self.planner, "name", ""),
            dry_run=dry_run,
            run_id=self.run_id,
        )

    def _repeated_failure(self, steps: list[Step]) -> str:
        """Últimas `max_repeats` falhas idênticas e consecutivas → mensagem de parada."""
        tail = steps[-self.max_repeats :]
        if len(tail) < self.max_repeats:
            return ""
        keys = [_failure_key(step) for step in tail]
        if any(key is None for key in keys) or len(set(keys)) != 1:
            return ""
        step = tail[-1]
        return f"'{step.tool}' falhou {self.max_repeats}x seguidas com os mesmos argumentos: {step.error}"

    def _audit(self, objective: str, step: Step) -> None:
        """Registra o passo na auditoria. Best-effort: memória indisponível não para o loop."""
        if self.memory is None:
            return
        try:
            self.memory.log_action(
                objective,
                step.tool,
                _summarize(step.args, 200),
                {
                    "agent_run_id": self.run_id,
                    "step": step.index,
                    "rationale": step.rationale[:200],
                    "risk": step.risk,
                    "approved": step.approved,
                },
                bool(step.ok),
                (step.error or _summarize(step.observation, 300))[:500],
            )
        except Exception:
            logger.debug("Auditoria do passo %s falhou", step.index, exc_info=True)

    def _result(self, objective: str, stop_reason: str, *, dry_run: bool, error: str = "") -> AgentResult:
        return AgentResult(
            objective=objective,
            success=False,
            stop_reason=stop_reason,
            error=error or STOP_MESSAGES.get(stop_reason, stop_reason),
            dry_run=dry_run,
            provider=getattr(self.planner, "name", ""),
            run_id=self.run_id,
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _failure_key(step: Step) -> str | None:
    """Chave estável de uma falha: ferramenta + argumentos. `None` se o passo não falhou."""
    if step.ok is not False:
        return None
    try:
        args = json.dumps(step.args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args = repr(step.args)
    return f"{step.tool}:{args}"


def _summarize(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"
