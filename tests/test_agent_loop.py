# Decisão de design: estes testes cobrem os FREIOS do agente, não a inteligência dele.
# Dublês de planejador e de registro permitem afirmar comportamentos que só aparecem
# em execução real (limite de passos, tempo, falha repetida, aborto, aprovação) sem
# precisar de desktop, rede ou modelo.

"""Testes do núcleo do modo agente."""

from __future__ import annotations

import time

import pytest

from zorin_copilot.ai.agent import (
    STOP_ABORTED,
    STOP_DONE,
    STOP_ERROR,
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

    def call(self, name: str, args: dict | None = None) -> dict:
        self.calls.append((name, dict(args or {})))
        if name in self.results:
            return dict(self.results[name])
        return dict(self.default)


class SleepyRegistry(StubRegistry):
    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay

    def call(self, name, args=None):
        time.sleep(self.delay)
        return super().call(name, args)


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

    def alternar(name, args=None):
        calls["n"] += 1
        return {"ok": calls["n"] % 2 == 1, "error": "falha alternada"}

    registry.call = alternar  # type: ignore[assignment]
    planner = ScriptedPlanner([ToolCall("get_ui_tree")] * 6)
    result = loop_for(planner, registry, max_repeats=2, max_steps=6).run("x")
    assert result.stop_reason == STOP_MAX_STEPS


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

