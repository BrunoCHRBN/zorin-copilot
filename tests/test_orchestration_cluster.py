"""Testes unitários para o cluster de orquestração e tools."""

from unittest.mock import MagicMock
import pytest

from zorin_copilot.ai.orchestration.tools import (
    CopilotTool,
    create_n8n_tool,
    create_desktop_input_tool,
    create_shell_tool,
)
from zorin_copilot.ai.orchestration.cluster import (
    OrchestrationCluster,
    OrchestrationResult,
)
from zorin_copilot.integrations.n8n_client import N8nExecutionResult


def test_copilot_tool_run():
    tool = CopilotTool(name="soma", description="soma dois valores", func=lambda a, b: a + b)
    assert tool.run(2, 3) == 5


def test_create_n8n_tool():
    mock_n8n = MagicMock()
    mock_n8n.trigger_webhook.return_value = N8nExecutionResult(success=True, data={"result": "ok"})

    tool = create_n8n_tool(mock_n8n)
    res = tool.run(webhook_path="teste", payload_json='{"chave": "valor"}')
    assert '"result": "ok"' in res


def test_create_desktop_input_tool():
    mock_driver = MagicMock()
    mock_driver.click.return_value = (True, "Clique executado com sucesso.")

    tool = create_desktop_input_tool(mock_driver)
    res = tool.run(action_type="click", x=100, y=200)
    assert "Clique executado" in res
    mock_driver.click.assert_called_once_with(100, 200)


def test_create_shell_tool_danger_block():
    tool = create_shell_tool()
    res = tool.run(command="rm -rf /")
    assert "COMANDO BLOQUEADO" in res


def test_orchestration_cluster_native_sequential():
    cluster = OrchestrationCluster()
    res = cluster.execute("Organizar arquivos da pasta Downloads")

    assert isinstance(res, OrchestrationResult)
    assert res.success is True
    assert len(res.steps) >= 3
    assert res.steps[0].role == "Planner"
    assert res.steps[1].role == "OS-Executor"
    assert res.steps[2].role == "Critic/Verifier"
    assert "Organizar arquivos" in res.goal
