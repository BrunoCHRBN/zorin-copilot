"""Testes unitários para o cliente de integração com n8n."""

from unittest.mock import MagicMock, patch
import pytest
import requests

from zorin_copilot.integrations.n8n_client import (
    N8nClient,
    N8nClientError,
    N8nExecutionResult,
)


def test_n8n_client_init():
    client = N8nClient(base_url="http://localhost:5678/", api_key="secret-123")
    assert client.base_url == "http://localhost:5678"
    assert client.api_key == "secret-123"
    assert client._headers()["X-N8N-API-KEY"] == "secret-123"


@patch("requests.get")
def test_n8n_health_check_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_get.return_value = mock_resp

    client = N8nClient()
    assert client.is_healthy() is True
    mock_get.assert_called_once_with(
        "http://127.0.0.1:5678/healthz",
        headers=client._headers(),
        timeout=3.0,
    )


@patch("requests.get")
def test_n8n_health_check_failure(mock_get):
    mock_get.side_effect = requests.ConnectionError("Connection refused")

    client = N8nClient()
    assert client.is_healthy() is False


@patch("requests.post")
def test_n8n_trigger_webhook_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"status": "processed", "id": 42}
    mock_post.return_value = mock_resp

    client = N8nClient()
    res = client.trigger_webhook("webhook/teste", {"item": "relatorio"})

    assert isinstance(res, N8nExecutionResult)
    assert res.success is True
    assert res.data == {"status": "processed", "id": 42}
    assert res.error is None


@patch("requests.post")
def test_n8n_trigger_webhook_error(mock_post):
    mock_post.side_effect = requests.RequestException("Timeout")

    client = N8nClient()
    res = client.trigger_webhook("webhook/teste", {})

    assert res.success is False
    assert "Falha de conexão" in res.error


@patch("requests.get")
def test_n8n_list_workflows(mock_get):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"data": [{"id": "1", "name": "Sync Drive"}]}
    mock_get.return_value = mock_resp

    client = N8nClient(api_key="valid-key")
    workflows = client.list_workflows()

    assert len(workflows) == 1
    assert workflows[0]["name"] == "Sync Drive"


def test_n8n_list_workflows_requires_key():
    client = N8nClient(api_key=None)
    assert client.list_workflows() == []
