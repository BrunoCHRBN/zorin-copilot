# Decisão de design: Adaptador do ecossistema MCP para os subsistemas do Zorin Copilot.
# Converte ferramentas MCP para `ToolSpec` do Modo Agente, mapeia esquemas para
# JSON Schema e classifica risco com salvaguardas de confirmação obrigatória.

"""Adaptador que conecta ferramentas MCP ao ToolRegistry, RiskPolicy e IntentEngine."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from ..shell.risk import RiskLevel, RiskPolicy
from .manager import MCPManager
from .protocol import MCPTool

if TYPE_CHECKING:
    from ..ai.agent_tools import ToolRegistry

logger = logging.getLogger(__name__)

# Palavras-chave indicativas de mutações ou ações de alto risco em ferramentas MCP
MUTATING_KEYWORDS = frozenset({
    "write", "create", "delete", "remove", "drop", "exec", "execute",
    "run", "update", "insert", "modify", "commit", "push", "kill",
    "install", "uninstall", "post", "put", "patch", "alter",
})


def is_mcp_tool_mutating(tool_name: str, description: str = "") -> bool:
    """Verifica se a ferramenta MCP altera o sistema ou realiza operações de risco."""
    target = f"{tool_name} {description}".lower()
    return any(kw in target for kw in MUTATING_KEYWORDS)


def convert_mcp_input_schema(input_schema: dict[str, Any] | None) -> dict[str, Any]:
    """Normaliza o inputSchema MCP para o formato esperado pelo ToolRegistry e provedores."""
    if not input_schema or not isinstance(input_schema, dict):
        return {"type": "OBJECT", "properties": {}, "required": []}

    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])

    return {
        "type": "OBJECT",
        "properties": properties,
        "required": required if isinstance(required, list) else [],
    }


def register_mcp_tools_in_registry(
    registry: "ToolRegistry",
    manager: MCPManager,
) -> int:
    """
    Descobre todas as ferramentas MCP ativas e as registra no ToolRegistry do Modo Agente.
    
    Returns:
        Número de ferramentas MCP registradas com sucesso.
    """
    from ..ai.agent_tools import ToolSpec

    tools = manager.get_all_tools()
    count = 0

    for tool in tools:
        # Registra com o nome qualificado (mcp__server__tool)
        qual_name = tool.qualified_name
        mutating = is_mcp_tool_mutating(tool.name, tool.description)
        schema = convert_mcp_input_schema(tool.input_schema)

        def make_handler(t_name: str):
            def handler(args: dict[str, Any]) -> dict[str, Any]:
                res = manager.call_tool(t_name, args)
                out = {
                    "ok": not res.is_error,
                    "content": res.text,
                    "output": res.text,
                    "is_error": res.is_error,
                    "raw": res.raw,
                }
                if res.is_error:
                    out["error"] = res.text
                return out
            return handler

        spec = ToolSpec(
            name=qual_name,
            description=f"[MCP:{tool.server_name}] {tool.description or tool.name}",
            parameters=schema,
            handler=make_handler(qual_name),
            mutating=mutating,
        )
        registry.register(spec)

        # Registra a regra de risco correspondente na RiskPolicy
        if mutating and hasattr(registry.policy, "register_risk"):
            registry.policy.register_risk(
                qual_name,
                RiskLevel.CONFIRM,
                f"execução de ferramenta MCP '{tool.name}' do servidor '{tool.server_name}'",
            )

        count += 1
        logger.debug(f"Ferramenta MCP registrada no agente: {qual_name} (mutating={mutating})")

    # Se houver recursos disponíveis nos servidores MCP, expõe ferramenta de leitura segura
    try:
        resources = manager.get_all_resources()
        if resources:
            res_list_desc = ", ".join(f"{r.name} ({r.uri})" for r in resources[:5])
            if len(resources) > 5:
                res_list_desc += f" e mais {len(resources)-5}..."

            def read_res_handler(args: dict[str, Any]) -> dict[str, Any]:
                uri = args.get("uri", "")
                if not uri:
                    return {"ok": False, "error": "Parâmetro 'uri' é obrigatório."}
                content = manager.read_resource(uri)
                ok = not content.startswith("Erro") and "não encontrado" not in content
                return {"ok": ok, "content": content, "output": content}

            res_spec = ToolSpec(
                name="mcp__read_resource",
                description=f"Lê um recurso exposto por servidor MCP ativo. Disponíveis: {res_list_desc}",
                parameters={
                    "type": "object",
                    "properties": {
                        "uri": {"type": "string", "description": "URI do recurso MCP a ser lido"}
                    },
                    "required": ["uri"],
                },
                handler=read_res_handler,
                mutating=False,
            )
            registry.register(res_spec)
            count += 1
    except Exception as exc:
        logger.debug(f"Erro ao registrar recursos MCP: {exc}")

    return count
