"""Testes para o motor de busca de código e inspeção de processos do sistema."""

from __future__ import annotations

import os
import tempfile
import pytest

from zorin_copilot.ai.agent_tools import ToolRegistry
from zorin_copilot.ai.live import GeminiLiveClient
from zorin_copilot.core.system_inspect import (
    CodeMatch,
    CodeSearchEngine,
    CodeSearchResult,
    ProcessInfo,
    ProcessInspector,
)


@pytest.fixture
def temp_code_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Cria arquivos de teste
        f1 = os.path.join(tmpdir, "main.py")
        with open(f1, "w", encoding="utf-8") as f:
            f.write(
                "import os\n"
                "import sys\n"
                "def calculate_total(x, y):\n"
                "    # Calcula o total\n"
                "    return x + y\n"
                "def run():\n"
                "    print(calculate_total(10, 20))\n"
            )

        f2 = os.path.join(tmpdir, "utils.py")
        with open(f2, "w", encoding="utf-8") as f:
            f.write(
                "def format_name(name):\n"
                "    return name.strip().title()\n"
                "CALCULATE_FACTOR = 42\n"
            )

        f3 = os.path.join(tmpdir, "readme.txt")
        with open(f3, "w", encoding="utf-8") as f:
            f.write("Documentação do projeto de teste.\n")

        # Subpasta ignorada (.git)
        git_dir = os.path.join(tmpdir, ".git")
        os.makedirs(git_dir, exist_ok=True)
        with open(os.path.join(git_dir, "config"), "w", encoding="utf-8") as f:
            f.write("calculate_in_git = True\n")

        yield tmpdir


def test_code_match_and_result_to_dict():
    match = CodeMatch(
        file="app.py",
        line=42,
        content="def hello():",
        context_before=["# header"],
        context_after=["    return 'hi'"],
    )
    d = match.to_dict()
    assert d["file"] == "app.py"
    assert d["line"] == 42
    assert d["context_before"] == ["# header"]
    assert d["context_after"] == ["    return 'hi'"]

    res = CodeSearchResult(
        pattern="hello",
        path=".",
        engine="ripgrep",
        matches=[match],
        total_matches=1,
        truncated=False,
        elapsed_sec=0.015,
    )
    res_dict = res.to_dict()
    assert res_dict["ok"] is True
    assert res_dict["success"] is True
    assert res_dict["total_matches"] == 1
    assert "app.py" in res_dict["summary"]
    assert "L42" in res_dict["summary"]


def test_code_search_ripgrep_and_fallback(temp_code_dir):
    engine = CodeSearchEngine.get_default()

    # Busca ripgrep ou fallback
    res = engine.search(
        pattern="calculate_total",
        path=temp_code_dir,
        file_glob="*.py",
        case_sensitive=True,
    )
    assert res.total_matches >= 2
    assert any(m.file == "main.py" or m.file.endswith("main.py") for m in res.matches)

    # Teste de case-insensitive
    res_ci = engine.search(
        pattern="calculate_factor",
        path=temp_code_dir,
        case_sensitive=False,
    )
    assert res_ci.total_matches >= 1

    # Teste de busca sem correspondências
    res_none = engine.search(
        pattern="NON_EXISTENT_FUNCTION_12345",
        path=temp_code_dir,
    )
    assert res_none.total_matches == 0
    assert len(res_none.matches) == 0


def test_code_search_python_fallback_explicit(temp_code_dir):
    engine = CodeSearchEngine(default_timeout=5.0)

    res = engine._search_python(
        pattern="calculate",
        target_path=temp_code_dir,
        file_glob="*.py",
        case_sensitive=False,
        is_regex=True,
        max_results=2,
        context_lines=1,
        t_start=0.0,
    )
    assert res.engine == "python_fallback"
    assert len(res.matches) == 2
    assert res.truncated is True
    # Verifica se ignorou a pasta .git
    for m in res.matches:
        assert ".git" not in m.file

    # Contexto verificado
    first_match = res.matches[0]
    assert isinstance(first_match.context_before, list)
    assert isinstance(first_match.context_after, list)


def test_code_search_invalid_regex(temp_code_dir):
    engine = CodeSearchEngine()
    with pytest.raises(ValueError, match="Expressão regular inválida"):
        engine._search_python(
            pattern="[unclosed-bracket",
            target_path=temp_code_dir,
            file_glob="",
            case_sensitive=False,
            is_regex=True,
            max_results=10,
            context_lines=0,
            t_start=0.0,
        )


def test_code_search_nonexistent_path():
    engine = CodeSearchEngine.get_default()
    with pytest.raises(FileNotFoundError):
        engine.search(pattern="foo", path="/path/that/does/not/exist_12345")


def test_process_inspector_list_and_table():
    inspector = ProcessInspector.get_default()
    procs = inspector.list_processes(limit=5)
    assert isinstance(procs, list)
    assert len(procs) <= 5
    if procs:
        p0 = procs[0]
        assert isinstance(p0.pid, int)
        assert p0.pid > 0
        assert isinstance(p0.command, str)
        assert isinstance(p0.cpu_percent, float)
        assert isinstance(p0.memory_percent, float)

        table = inspector.format_table(procs)
        assert "PID" in table
        assert "%CPU" in table
        assert "%MEM" in table
        assert str(p0.pid) in table


def test_process_inspector_filtering():
    inspector = ProcessInspector.get_default()
    # Filtra pelo processo atual (Python / pytest)
    procs = inspector.list_processes(filter_query="python", limit=5)
    for p in procs:
        assert "python" in p.command.lower() or "python" in p.args.lower()


def test_process_inspector_proc_fallback():
    inspector = ProcessInspector.get_default()
    procs = inspector._list_processes_proc(filter_query="", sort_by="pid", limit=5)
    assert isinstance(procs, list)
    if procs:
        assert procs[0].pid > 0


def test_tool_registry_search_code_and_processes(temp_code_dir):
    registry = ToolRegistry()
    assert "search_code" in registry.names()
    assert "system_processes" in registry.names()

    # Erro de parâmetro ausente
    res_err = registry.call("search_code", {})
    assert res_err["ok"] is False
    assert "obrigatório" in res_err["error"]

    # Busca com sucesso
    res_ok = registry.call(
        "search_code",
        {
            "pattern": "calculate_total",
            "path": temp_code_dir,
            "file_glob": "*.py",
        },
    )
    assert res_ok["ok"] is True
    assert res_ok["total_matches"] >= 2
    assert "summary" in res_ok

    # Inspeção de processos com sucesso
    res_proc = registry.call("system_processes", {"limit": 3})
    assert res_proc["ok"] is True
    assert "processes" in res_proc
    assert len(res_proc["processes"]) <= 3
    assert "summary" in res_proc


def test_gemini_live_client_dispatch_tools(temp_code_dir):
    client = GeminiLiveClient()

    # Despacho de search_code
    res_search = client._dispatch_tool(
        "search_code",
        {
            "pattern": "calculate_total",
            "path": temp_code_dir,
            "file_glob": "*.py",
        },
    )
    assert res_search["success"] is True
    assert res_search["total_matches"] >= 2

    # Despacho de system_processes
    res_procs = client._dispatch_tool(
        "system_processes",
        {"limit": 2},
    )
    assert res_procs["success"] is True
    assert "processes" in res_procs
    assert len(res_procs["processes"]) <= 2
