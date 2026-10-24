# Decisão de design: estes testes cobrem os FREIOS do agente, não a inteligência dele.
# Dublês de planejador e de registro permitem afirmar comportamentos que só aparecem
# em execução real (limite de passos, tempo, falha repetida, aborto, aprovação) sem
# precisar de desktop, rede ou modelo.

"""Testes do núcleo do modo agente."""

from __future__ import annotations

import threading
import time

import pytest

from zorin_copilot.ai.agent import (
    STOP_ABORTED,
    STOP_DONE,
    STOP_ERROR,
    STOP_LOOP,
    STOP_MAX_STEPS,
    STOP_REJECTED,
    STOP_REPEATED,
    STOP_TIMEOUT,
    AgentDecision,
    AgentLoop,
    AgentResult,
    ScriptedPlanner,
    Step,
    ToolCall,
)
from zorin_copilot.ai.agent_tools import ToolRegistry


class StubRegistry(ToolRegistry):
    """Registro que devolve respotas programadas e conta chamadas."""

    def __init__(self, results: dict | None = None, default: dict | None = None) -> None:
        super().__init__()
        self.results = results or {}
        self.default = default or {"ok": True, "message": "feito"}
        self.calls: list[tuple[str, dict]] = []

    def call(self, name: str, args: dict | None = None, contract=None) -> dict:
        # `contract` é opaco aqui, como em `ToolRegistry.call`: é o gancho que
        # registros validadores usam. O stub precisa espelhar a assinatura da
        # base, senão `AgentLoop` — que sempre repassa o contrato do passo —
        # quebra com TypeError em vez de exercitar o laço.
        self.calls.append((name, dict(args or {})))
        if name in self.results:
            return dict(self.results[name])
        return dict(self.default)


class SleepyRegistry(StubRegistry):
    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay

    def call(self, name, args=None, contract=None):
        time.sleep(self.delay)
        return super().call(name, args, contract=contract)


class FakeMemory:
    def __init__(self) -> None:
        self.logs: list[tuple] = []

    def log_action(self, prompt, action_type, target, params, success, message):
        self.logs.append((prompt, action_type, target, params, success, message))


def loop_for(planner, registry, **kwargs) -> AgentLoop:
    return AgentLoop(planner, registry, **kwargs)


# --------------------------------------------------------------------------- #
# Encerramento
# --------------------------------------------------------------------------- #


def test_done_encerra_com_sucesso_e_sem_executar_mais_nada():
    registry = StubRegistry()
    planner = ScriptedPlanner([ToolCall("done", {"answer": "Tudo pronto."})])
    result = loop_for(planner, registry).run("objetivo qualquer")

    assert result.success is True
    assert result.stop_reason == STOP_DONE
    assert result.final_answer == "Tudo pronto."
    assert registry.calls == []


def test_resposta_final_em_prosa_tambem_encerra():
    result = loop_for(ScriptedPlanner([AgentDecision(final_answer="Resolvi.")]), StubRegistry()).run("x")
    assert result.stop_reason == STOP_DONE
    assert result.final_answer == "Resolvi."


def test_done_sem_resposta_ainda_e_sucesso():
    result = loop_for(ScriptedPlanner([ToolCall("done")]), StubRegistry()).run("x")
    assert result.success is True
    assert result.final_answer == "Objetivo concluído."


# --------------------------------------------------------------------------- #
# Limites
# --------------------------------------------------------------------------- #


def test_limite_de_passos_para_a_execucao():
    planner = ScriptedPlanner([ToolCall("get_ui_tree")] * 10)
    result = loop_for(planner, StubRegistry(), max_steps=3).run("x")

    assert result.stop_reason == STOP_MAX_STEPS
    assert result.success is False
    assert len(result.steps) == 3


def test_tempo_limite_para_a_execucao():
    planner = ScriptedPlanner([ToolCall("get_ui_tree")] * 10)
    loop = loop_for(planner, SleepyRegistry(0.06), max_seconds=0.05)
    result = loop.run("x")

    assert result.stop_reason == STOP_TIMEOUT


def test_primeiro_passo_respeita_limite_de_um_passo():
    result = loop_for(ScriptedPlanner([ToolCall("get_ui_tree")] * 5), StubRegistry(), max_steps=1).run("x")
    assert len(result.steps) == 1
    assert result.stop_reason == STOP_MAX_STEPS


# --------------------------------------------------------------------------- #
# Falha repetida
# --------------------------------------------------------------------------- #


def test_mesma_acao_falhando_duas_vezes_parao_loop():
    failing = StubRegistry(default={"ok": False, "error": "elemento não encontrado"})
    planner = ScriptedPlanner([ToolCall("click_element", {"uid": "1.0"})] * 5)
    result = loop_for(planner, failing, max_repeats=2).run("clicar em salvar")

    assert result.stop_reason == STOP_REPEATED
    assert len(result.steps) == 2
    assert "2x seguidas" in result.error


def test_falhas_com_argumentos_diferentes_nao_contam_como_repeticao():
    failing = StubRegistry(default={"ok": False, "error": "falhou"})
    planner = ScriptedPlanner(
        [
            ToolCall("click_element", {"uid": "1.0"}),
            ToolCall("click_element", {"uid": "1.1"}),
            ToolCall("click_element", {"uid": "1.0"}),
            ToolCall("click_element", {"uid": "1.1"}),
            ToolCall("done", {"answer": "ok"}),
        ]
    )
    result = loop_for(planner, failing, max_repeats=2).run("x")
    assert result.stop_reason == STOP_DONE


def test_falha_seguida_de_sucesso_nao_dispara_repeticao():
    registry = StubRegistry()
    registry.results = {}
    calls = {"n": 0}

    def alternar(name, args=None, contract=None):
        # `contract` faz parte da assinatura de `ToolRegistry.call`; o
        # `AgentLoop` sempre o repassa, então um substituto que não o aceite
        # quebra com TypeError antes de exercitar a lógica de repetição.
        calls["n"] += 1
        return {"ok": calls["n"] % 2 == 1, "error": "falha alternada"}

    registry.call = alternar  # type: ignore[assignment]
    planner = ScriptedPlanner([ToolCall("get_ui_tree")] * 6)
    result = loop_for(planner, registry, max_repeats=2, max_steps=6).run("x")
    assert result.stop_reason == STOP_MAX_STEPS


def test_loop_repetitivo_identico_interrompe_com_stop_loop():
    class RepetitiveLLMPlanner:
        name = "llm"
        def decide(self, *args, **kwargs):
            return AgentDecision(tool_call=ToolCall("find_on_screen", {"query": "Máximo"}))

    registry = StubRegistry(default={"ok": True, "result": "encontrado"})
    loop = loop_for(RepetitiveLLMPlanner(), registry, max_steps=10)
    result = loop.run("testar loop")

    assert result.stop_reason == STOP_LOOP
    assert len(result.steps) == 4
    assert "Loop detectado" in result.error


def test_loop_ciclico_alternante_interrompe_com_stop_loop():
    class CyclicLLMPlanner:
        name = "llm"
        def __init__(self):
            self.count = 0
        def decide(self, *args, **kwargs):
            self.count += 1
            if self.count % 2 == 1:
                return AgentDecision(tool_call=ToolCall("find_on_screen", {"query": "Preço"}))
            return AgentDecision(tool_call=ToolCall("mouse_click", {"x": 200, "y": 300}))

    registry = StubRegistry(default={"ok": True, "result": "ok"})
    loop = loop_for(CyclicLLMPlanner(), registry, max_steps=10)
    result = loop.run("testar loop ciclico")

    assert result.stop_reason == STOP_LOOP
    assert len(result.steps) == 6
    assert "Loop cíclico detectado" in result.error


# --------------------------------------------------------------------------- #
# Aprovação
# --------------------------------------------------------------------------- #


def test_acao_de_risco_sem_aprovador_e_recusada_sem_executar():
    registry = StubRegistry()
    planner = ScriptedPlanner([ToolCall("write_document", {"filename": "a.md", "content": "x"})])
    result = loop_for(planner, registry).run("escrever arquivo")

    assert result.stop_reason == STOP_REJECTED
    assert registry.calls == []
    assert result.steps[0].requires_approval is True
    assert result.steps[0].risk == "confirm"


def test_aprovacao_negada_encerra_sem_executar():
    registry = StubRegistry()
    planner = ScriptedPlanner([ToolCall("organize_directory", {"directory": "/tmp"})])
    result = loop_for(planner, registry).run("organizar", on_approval=lambda step: False)

    assert result.stop_reason == STOP_REJECTED
    assert registry.calls == []


def test_aprovacao_concedida_executa():
    registry = StubRegistry()
    planner = ScriptedPlanner(
        [
            ToolCall("write_document", {"filename": "a.md", "content": "x"}),
            ToolCall("done", {"answer": "arquivo escrito"}),
        ]
    )
    result = loop_for(planner, registry).run("escrever", on_approval=lambda step: True)

    assert result.success is True
    assert registry.calls[0][0] == "write_document"


def test_aprovador_que_explode_conta_como_recusa():
    def explode(step):
        raise RuntimeError("UI fechou")

    registry = StubRegistry()
    planner = ScriptedPlanner([ToolCall("write_document", {"filename": "a.md", "content": "x"})])
    result = loop_for(planner, registry).run("escrever", on_approval=explode)

    assert result.stop_reason == STOP_REJECTED
    assert registry.calls == []


def test_acao_segura_nao_pede_aprovacao():
    registry = StubRegistry()
    planner = ScriptedPlanner([ToolCall("get_ui_tree"), ToolCall("done", {"answer": "ok"})])
    result = loop_for(planner, registry).run("ler a tela")

    assert result.success is True
    assert result.steps[0].requires_approval is False


# --------------------------------------------------------------------------- #
# Aborto
# --------------------------------------------------------------------------- #


def test_abort_encerra_no_proximo_passo():
    registry = StubRegistry()
    planner = ScriptedPlanner([ToolCall("get_ui_tree")] * 10)
    loop = loop_for(planner, registry)

    def on_step(step):
        loop.abort()

    result = loop.run("x", on_step=on_step)

    assert result.stop_reason == STOP_ABORTED
    assert len(result.steps) == 1


def test_reset_permite_nova_execucao_apos_aborto():
    registry = StubRegistry()
    loop = loop_for(ScriptedPlanner([ToolCall("done", {"answer": "ok"})] * 2), registry)
    loop.abort()
    assert loop.aborted is True
    loop.reset()
    result = loop.run("x")
    assert result.success is True


class SleepyPlanner:
    """Encapsula um `ScriptedPlanner` e dorme antes de cada `decide`.

    Serve para exercitar o aborto cooperativo quando o planejador está no meio
    de uma chamada demorada: o sinal de parada só é visto no topo do `while`
    (agent.py), depois que o `decide` atual devolve.
    """

    name = "sonolento"

    def __init__(self, decisions, delay: float = 2.0) -> None:
        self.inner = ScriptedPlanner(list(decisions))
        self.delay = delay

    def decide(self, objective, tools, history, session_context=None):
        time.sleep(self.delay)
        return self.inner.decide(objective, tools, history, session_context=session_context)


def test_abort_encerra_ate_quando_planejador_dorme():
    """Defeito 1 / Frente A.1: o aborto cooperativo interrompe mesmo com o
    planejador dormindo dentro de `decide()` — não travado para sempre."""
    registry = StubRegistry()
    planner = SleepyPlanner([ToolCall("noop")] * 5, delay=2.0)
    loop = AgentLoop(planner, registry, max_steps=10, max_seconds=300.0)

    captured: dict[str, object] = {}

    def target():
        captured["result"] = loop.run("x")

    t = threading.Thread(target=target, daemon=True)
    t.start()
    # Deixa o planejador entrar em `decide` (dormindo) antes de abortar.
    time.sleep(0.2)
    loop.abort()
    t.join(timeout=3.0)

    assert not t.is_alive(), "o loop não encerrou após o aborto (travou)"
    result = captured["result"]
    assert result is not None
    assert result.stop_reason == STOP_ABORTED


# --------------------------------------------------------------------------- #
# Planejador com problema
# --------------------------------------------------------------------------- #


def test_erro_do_planejador_encerra_com_error():
    result = loop_for(
        ScriptedPlanner([AgentDecision(error="Ollama fora do ar")]), StubRegistry()
    ).run("x")
    assert result.stop_reason == STOP_ERROR
    assert "Ollama" in result.error


def test_planejador_que_explode_nao_derruba_o_loop():
    class Exploding:
        name = "explosivo"

        def decide(self, objective, tools, history):
            raise RuntimeError("boom")

    result = loop_for(Exploding(), StubRegistry()).run("x")
    assert result.stop_reason == STOP_ERROR
    assert "boom" in result.error


# --------------------------------------------------------------------------- #
# Dry-run / plano
# --------------------------------------------------------------------------- #


def test_plan_nao_executa_nada_no_desktop(tmp_path):
    registry = ToolRegistry()
    planner = ScriptedPlanner(
        [
            ToolCall("write_document", {"filename": "a.md", "content": "x", "directory": str(tmp_path)}),
            ToolCall("done", {"answer": "escreveria o arquivo"}),
        ]
    )
    result = loop_for(planner, registry).plan("escrever um arquivo")

    assert result.dry_run is True
    assert result.steps[0].observation["dry_run"] is True
    assert list(tmp_path.iterdir()) == []


def test_plan_marca_risco_de_cada_passo():
    planner = ScriptedPlanner(
        [
            ToolCall("get_ui_tree"),
            ToolCall("organize_directory"),
            ToolCall("done", {"answer": "ok"}),
        ]
    )
    result = loop_for(planner, ToolRegistry()).plan("organizar downloads")

    assert result.steps[0].risk == "safe"
    assert result.steps[1].risk == "confirm"
    assert result.steps[1].requires_approval is True


# --------------------------------------------------------------------------- #
# Auditoria e formato
# --------------------------------------------------------------------------- #


def test_cada_passo_e_auditado_com_run_id():
    memory = FakeMemory()
    planner = ScriptedPlanner([ToolCall("get_ui_tree"), ToolCall("done", {"answer": "ok"})])
    loop = loop_for(planner, StubRegistry(), memory=memory, run_id="abc123")
    result = loop.run("ler a tela")

    assert result.run_id == "abc123"
    assert len(memory.logs) == 1
    _prompt, action_type, _target, params, success, _message = memory.logs[0]
    assert action_type == "get_ui_tree"
    assert params["agent_run_id"] == "abc123"
    assert params["step"] == 0
    assert success is True


def test_to_dict_e_serializavel():
    import json

    planner = ScriptedPlanner([ToolCall("get_ui_tree"), ToolCall("done", {"answer": "ok"})])
    result = loop_for(planner, StubRegistry()).run("x")
    payload = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "get_ui_tree" in payload
    assert result.to_dict()["stop_reason"] == STOP_DONE


def test_on_step_recebe_cada_passo_executado():
    seen: list[str] = []
    planner = ScriptedPlanner([ToolCall("get_ui_tree"), ToolCall("done", {"answer": "ok"})])
    loop_for(planner, StubRegistry()).run("x", on_step=lambda step: seen.append(step.tool))
    assert seen == ["get_ui_tree"]


def test_historico_entregue_ao_planejador_cresce():
    planner = ScriptedPlanner(
        [ToolCall("get_ui_tree"), ToolCall("get_ui_tree"), ToolCall("done", {"answer": "ok"})]
    )
    loop_for(planner, StubRegistry()).run("x")
    assert len(planner.calls[0]) == 0
    assert len(planner.calls[1]) == 1


def test_continuidade_com_initial_steps():
    # Simula continuar uma execução que parou com 2 passos
    step0 = Step(index=0, tool="get_ui_tree", ok=True)
    step1 = Step(index=1, tool="get_ui_tree", ok=True)
    planner = ScriptedPlanner([ToolCall("get_ui_tree"), ToolCall("done", {"answer": "concluído"})])
    
    result = loop_for(planner, StubRegistry(), max_steps=4).run("x", initial_steps=[step0, step1])
    assert result.success is True
    assert result.stop_reason == STOP_DONE
    assert len(result.steps) == 3
    assert result.steps[2].index == 2


def test_from_dict_reconstroi_resultado():
    planner = ScriptedPlanner([ToolCall("get_ui_tree"), ToolCall("done", {"answer": "feito"})])
    result = loop_for(planner, StubRegistry()).run("x")
    d = result.to_dict()

    rebuilt = AgentResult.from_dict(d)
    assert rebuilt.objective == result.objective
    assert rebuilt.stop_reason == result.stop_reason
    assert len(rebuilt.steps) == len(result.steps)
    assert rebuilt.steps[0].tool == "get_ui_tree"


def test_get_step_budget_adaptativo():
    from zorin_copilot.ai.agent_router import get_step_budget

    # Tarefa simples
    steps_simple, secs_simple = get_step_budget("abrir o terminal", base_max_steps=15, adaptive=True)
    assert steps_simple <= 10
    assert secs_simple <= 120.0

    # Tarefa complexa (análise / síntese / commits)
    steps_complex, secs_complex = get_step_budget(
        "analisar os commits recentes e o que pode ser melhorado", base_max_steps=15, adaptive=True
    )
    assert steps_complex >= 30
    assert secs_complex >= 480.0

    # Sem adaptativo respeita o base
    steps_fixed, secs_fixed = get_step_budget("qualquer coisa", base_max_steps=20, base_max_seconds=250.0, adaptive=False)
    assert steps_fixed == 20
    assert secs_fixed == 250.0


def test_loop_executa_lote_paralelo_safe_em_um_unico_passo():
    registry = ToolRegistry()
    batch_decision = AgentDecision(
        tool_calls=[
            ToolCall("scratchpad_write", {"key": "aba_1", "content": "Preço A: R$ 200"}, rationale="salvar cotações em lote"),
            ToolCall("scratchpad_write", {"key": "aba_2", "content": "Preço B: R$ 180"}, rationale="salvar cotações em lote"),
        ]
    )
    planner = ScriptedPlanner([batch_decision, ToolCall("done", {"answer": "Cotações salvas em lote"})])
    result = loop_for(planner, registry).run("comparar preços entre abas")

    assert result.success is True
    assert result.stop_reason == STOP_DONE
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.ok is True
    assert step.batch_tools == ["scratchpad_write", "scratchpad_write"]
    assert step.observation["batch"] is True
    assert step.observation["count"] == 2
    assert registry.scratchpad == {"aba_1": "Preço A: R$ 200", "aba_2": "Preço B: R$ 180"}
    registry.close()


def test_loop_encaminha_scratchpad_para_planejador():
    registry = ToolRegistry(scratchpad={"nota_inicial": "valor 42"})
    seen_scratchpads: list[dict] = []

    class ScratchpadAwarePlanner:
        name = "aware"

        def decide(self, objective, tools, history, scratchpad=None):
            seen_scratchpads.append(dict(scratchpad or {}))
            if not history:
                return AgentDecision(tool_call=ToolCall("scratchpad_write", {"key": "nova", "content": "ok"}))
            return AgentDecision(final_answer="concluído com scratchpad")

    result = loop_for(ScratchpadAwarePlanner(), registry).run("usar memória de trabalho")
    assert result.success is True
    assert seen_scratchpads[0] == {"nota_inicial": "valor 42"}
    assert seen_scratchpads[1] == {"nota_inicial": "valor 42", "nova": "ok"}
    registry.close()


