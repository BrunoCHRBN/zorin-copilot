# Decisão de design: cliente de integração com o serviço n8n rodando localmente (systemd)
# ou remotamente. Permite que o Zorin Copilot delegue tarefas determinísticas (APIs de email,
# Notion, planilhas, CRM, mensageria) para workflows do n8n com garantia de consistência.

"""Cliente de integração com o n8n para automações determinísticas multipassos."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
import requests

logger = logging.getLogger(__name__)

DEFAULT_N8N_BASE_URL = "http://127.0.0.1:5678"


class N8nClientError(Exception):
    """Erro de comunicação ou execução com a API/Webhook do n8n."""


@dataclass
class N8nExecutionResult:
    """Resultado estruturado de execução de workflow no n8n."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    execution_id: str | None = None
    error: str | None = None


class N8nClient:
    """Cliente HTTP síncrono para a API REST e Webhooks do n8n."""

    def __init__(
        self,
        base_url: str = DEFAULT_N8N_BASE_URL,
        api_key: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ZorinCopilot/1.0",
        }
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key
        return headers

    def is_healthy(self) -> bool:
        """Verifica se o serviço local do n8n está acessível e pronto."""
        try:
            resp = requests.get(
                f"{self.base_url}/healthz",
                headers=self._headers(),
                timeout=min(self.timeout, 3.0),
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def trigger_webhook(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        method: str = "POST",
    ) -> N8nExecutionResult:
        """Dispara um webhook configurado no n8n.
        
        Args:
            path: Caminho relativo do webhook (ex: 'webhook/processar-fatura' ou 'webhook-test/...').
            payload: Carga de dados JSON a enviar para o workflow.
            method: Método HTTP (POST, GET, etc.).
        """
        clean_path = path.lstrip("/")
        url = f"{self.base_url}/{clean_path}"
        data = payload or {}

        try:
            if method.upper() == "GET":
                resp = requests.get(url, params=data, headers=self._headers(), timeout=self.timeout)
            else:
                resp = requests.post(url, json=data, headers=self._headers(), timeout=self.timeout)

            resp.raise_for_status()
            try:
                response_data = resp.json()
            except ValueError:
                response_data = {"text": resp.text}

            return N8nExecutionResult(success=True, data=response_data)
        except requests.RequestException as err:
            logger.error("Falha ao acionar webhook n8n em %s: %s", url, err)
            return N8nExecutionResult(
                success=False,
                error=f"Falha de conexão com o webhook n8n: {err}",
            )

    def list_workflows(self, active_only: bool = False) -> list[dict[str, Any]]:
        """Lista workflows cadastrados na instância (requer API Key)."""
        if not self.api_key:
            logger.warning("list_workflows exige api_key configurada.")
            return []

        url = f"{self.base_url}/api/v1/workflows"
        params = {"active": "true"} if active_only else {}
        try:
            resp = requests.get(url, params=params, headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except requests.RequestException as err:
            logger.error("Erro ao listar workflows do n8n: %s", err)
            raise N8nClientError(f"Não foi possível listar workflows: {err}") from err

    def execute_workflow(
        self,
        workflow_id: str,
        payload: dict[str, Any] | None = None,
    ) -> N8nExecutionResult:
        """Executa um workflow manualmente via API REST v1 do n8n."""
        if not self.api_key:
            raise N8nClientError("A execução direta de workflow por ID exige api_key configurada.")

        url = f"{self.base_url}/api/v1/workflows/{workflow_id}/run"
        try:
            resp = requests.post(
                url,
                json=payload or {},
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return N8nExecutionResult(
                success=True,
                data=data.get("data", {}),
                execution_id=data.get("executionId"),
            )
        except requests.RequestException as err:
            logger.error("Erro ao executar workflow %s: %s", workflow_id, err)
            return N8nExecutionResult(
                success=False,
                error=f"Erro na execução do workflow: {err}",
            )
