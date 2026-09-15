# Decisão de design: o teste de CLI não usa modelo nenhum — o roteador é substituído
# por um planejador de roteiro fixo. O que se valida aqui é a FIAÇÃO: parser, handler,
# flags, aprovação no terminal e código de saída.

"""Testes do subcomando `agent` da CLI."""

from __future__ import annotations

import json

import pytest

from zorin_copilot import cli
from zorin_copilot.ai.agent import ScriptedPlanner, ToolCall
from zorin_copilot.ai.agent_router import RouteDecision


@pytest.fixture
def roteiro_fixo(monkeypatch):
    """Substitui o roteamento por um planejador de roteiro fixo."""

    def instalar(decisions):
        def route(self, objective, mode):
            return RouteDecision(ScriptedPlanner(list(decisions)), "local", "roteiro de teste", "simple")

        monkeypatch.setattr(cli.AgentRouter, "route", route)

    return instalar


def test_agent_dry_run_mostra_o_plano_e_sai_com_zero(roteiro_fixo, capsys, tmp_path):
    roteiro_fixo(
        [
            ToolCall("write_document", {"filename": "a.md", "content": "x", "directory": str(tmp_path)}),
            ToolCall("done", {"answer": "escreveria o arquivo"}),
        ]
    )
    code = cli.main(["agent", "escrever um arquivo", "--dry-run", "--no-audit"])

    out = capsys.readouterr().out
    assert code == 0
    assert "write_document" in out
    assert "Simulação" in out or "simulação" in out
    assert list(tmp_path.iterdir()) == []  # dry-run não escreve


def test_agent_json_devolve_o_resultado_serializado(roteiro_fixo, capsys):
    roteiro_fixo([ToolCall("done", {"answer": "pronto"})])
    code = cli.main(["agent", "fazer nada", "--json", "--no-audit"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["stop_reason"] == "done"
    assert payload["final_answer"] == "pronto"


def test_agent_sem_modelo_disponivel_sai_com_erro(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.AgentRouter,
        "route",
        lambda self, objective, mode: RouteDecision(None, "local", "modelo local indisponível", "simple"),
    )
    code = cli.main(["agent", "abrir o Firefox", "--no-audit"])
    assert code == 1
    assert "Nenhum modelo disponível" in capsys.readouterr().out


def test_agent_pede_aprovacao_e_respeita_recusa(roteiro_fixo, monkeypatch, capsys, tmp_path):
    roteiro_fixo([ToolCall("write_document", {"filename": "a.md", "content": "x", "directory": str(tmp_path)})])
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    code = cli.main(["agent", "escrever arquivo", "--no-audit"])

    out = capsys.readouterr().out
    assert code == 1
    assert "rejected" in out
    assert list(tmp_path.iterdir()) == []


def test_agent_yes_aprova_automaticamente(roteiro_fixo, capsys, tmp_path):
    roteiro_fixo(
        [
            ToolCall("write_document", {"filename": "a.md", "content": "x", "directory": str(tmp_path)}),
            ToolCall("done", {"answer": "escrito"}),
        ]
    )
    code = cli.main(["agent", "escrever arquivo", "--yes", "--no-audit"])
    assert code == 0
    assert (tmp_path / "a.md").exists()


def test_agent_respeita_max_steps(roteiro_fixo, capsys, tmp_path):
    # list_directory é só leitura e funciona headless: o limite de passos é o
    # único freio que vai disparar.
    roteiro_fixo([ToolCall("list_directory", {"path": str(tmp_path)})] * 20)
    code = cli.main(["agent", "ler a tela", "--max-steps", "2", "--no-audit"])

    out = capsys.readouterr().out
    assert code == 1
    assert "max_steps" in out
    assert "Passos: 2" in out


def test_parser_expõe_as_flags_do_modo_agente():
    parser = cli.build_parser()
    args = parser.parse_args(
        ["agent", "objetivo", "--dry-run", "--max-steps", "5", "--max-seconds", "10", "--local-only"]
    )
    assert args.command == "agent"
    assert args.dry_run is True
    assert args.max_steps == 5
    assert args.max_seconds == 10.0
    assert args.local_only is True
