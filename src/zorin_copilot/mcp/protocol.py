# Decisão de design: Model Context Protocol (MCP) puro e desacoplado de transportes específicos.
# Segue a especificação oficial aberta JSON-RPC 2.0 (protocolVersion: 2024-11-05).

"""Modelos de dados e constantes do protocolo MCP (Model Context Protocol)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

MCP_PROTOCOL_VERSION = "2024-11-05"


class MCPServerState(str, Enum):
    """Estado do ciclo de vida de conexão com o servidor MCP."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class JSONRPCRequest:
    """Requisição JSON-RPC 2.0."""
    id: int | str
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }


@dataclass
class JSONRPCNotification:
    """Notificação unidirecional JSON-RPC 2.0 (sem id nem resposta)."""
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
            "params": self.params,
        }


@dataclass
class JSONRPCResponse:
    """Resposta JSON-RPC 2.0 recebida do servidor."""
    id: int | str
    result: Any = None
    error: dict[str, Any] | None = None
    jsonrpc: str = "2.0"

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JSONRPCResponse:
        return cls(
            id=data.get("id", 0),
            result=data.get("result"),
            error=data.get("error"),
            jsonrpc=data.get("jsonrpc", "2.0"),
        )


@dataclass
class MCPTool:
    """Ferramenta exposta por um servidor MCP."""
    name: str
    server_name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        """Nome único qualificado para evitar colisão entre múltiplos servidores MCP."""
        return f"mcp__{self.server_name}__{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "server_name": self.server_name,
            "qualified_name": self.qualified_name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class MCPResource:
    """Recurso ou arquivo exposto por um servidor MCP."""
    uri: str
    name: str
    server_name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class MCPCallResult:
    """Resultado retornado pela execução de tools/call."""
    content: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Extrai o texto concatenado dos blocos de conteúdo textual."""
        parts = []
        for item in self.content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        if not parts and self.raw:
            return str(self.raw)
        return "\n".join(parts)
