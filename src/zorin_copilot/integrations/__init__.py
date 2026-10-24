"""Módulo de integrações externas do Zorin Copilot."""

from .n8n_client import N8nClient, N8nClientError, N8nExecutionResult

__all__ = ["N8nClient", "N8nClientError", "N8nExecutionResult"]
