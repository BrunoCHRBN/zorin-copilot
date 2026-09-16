# Decisão de design: Pacote MCP (Model Context Protocol) para extensibilidade do Zorin Copilot.

"""Pacote de suporte e integração com servidores MCP (Model Context Protocol)."""

from __future__ import annotations

from .adapter import register_mcp_tools_in_registry
from .client import MCPClient
from .manager import MCPServerConfig, MCPManager
from .protocol import (
    MCP_PROTOCOL_VERSION,
    MCPCallResult,
    MCPResource,
    MCPServerState,
    MCPTool,
)

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MCPCallResult",
    "MCPClient",
    "MCPManager",
    "MCPResource",
    "MCPServerConfig",
    "MCPServerState",
    "MCPTool",
    "register_mcp_tools_in_registry",
]
