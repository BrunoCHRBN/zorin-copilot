# Decisão de design: Gerenciador de servidores MCP com persistência padrão em
# ~/.config/zorin-copilot/mcp_servers.json, compaginando com o padrão da indústria
# (Claude Desktop, Cursor, Antigravity).

"""Gerenciador central de servidores MCP (Model Context Protocol)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .client import MCPClient
from .protocol import MCPCallResult, MCPPrompt, MCPResource, MCPServerState, MCPTool

try:
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
    HAS_GIO = True
except Exception:
    HAS_GIO = False

logger = logging.getLogger(__name__)


def get_default_config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "zorin-copilot")


def get_default_mcp_config_path() -> str:
    return os.path.join(get_default_config_dir(), "mcp_servers.json")


DEFAULT_CONFIG_DIR = get_default_config_dir()
DEFAULT_MCP_CONFIG_PATH = get_default_mcp_config_path()

#: O nome do servidor é a **chave** do objeto `mcpServers` no JSON. Restringir a
#: pontuação evita que um nome com aspas ou chaves quebre o arquivo inteiro — e
#: que um nome com barra vire caminho em algum consumidor futuro.
_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Template inicial com servidores MCP padrão sugeridos (desabilitados por padrão para não gastar recursos)
DEFAULT_MCP_TEMPLATE = {
    "mcpServers": {
        "git": {
            "command": "uvx",
            "args": ["mcp-server-git"],
            "env": {},
            "enabled": False,
            "description": "Operações avançadas em repositórios Git",
        },
        "sqlite": {
            "command": "uvx",
            "args": ["mcp-server-sqlite", "--db-path", "${HOME}/.local/share/zorin-copilot/sample.db"],
            "env": {},
            "enabled": False,
            "description": "Consultas e análises em bancos de dados SQLite",
        },
        "fetch": {
            "command": "uvx",
            "args": ["mcp-server-fetch"],
            "env": {},
            "enabled": False,
            "description": "Download e conversão limpa de páginas web em Markdown",
        },
        "cloud-sample": {
            "transport": "sse",
            "url": "https://mcp.exemplo.com/sse",
            "headers": {
                "Authorization": "Bearer ${MCP_REMOTE_TOKEN}"
            },
            "enabled": False,
            "description": "Exemplo de servidor MCP remoto corporativo via HTTP Server-Sent Events (SSE)",
        },
    }
}


@dataclass
class MCPServerConfig:
    """Configuração de inicialização de um servidor MCP (Stdio ou SSE remoto)."""
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""
    cwd: str | None = None
    transport: str = "stdio"
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "enabled": self.enabled,
            "description": self.description,
        }
        if self.transport == "sse" or (self.url and not self.command):
            d["transport"] = "sse"
            d["url"] = self.url or ""
            if self.headers:
                d["headers"] = self.headers
        else:
            d["transport"] = "stdio"
            d["command"] = self.command
            d["args"] = self.args
            d["env"] = self.env
            d["cwd"] = self.cwd
        return d

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> MCPServerConfig:
        url = data.get("url")
        command = data.get("command", "")
        raw_transport = data.get("transport")
        if raw_transport:
            transport = str(raw_transport).strip().lower()
        else:
            transport = "sse" if url and not command else "stdio"

        return cls(
            name=name,
            command=command,
            args=data.get("args") or [],
            env=data.get("env") or {},
            enabled=bool(data.get("enabled", True)),
            description=data.get("description", ""),
            cwd=data.get("cwd"),
            transport=transport,
            url=url,
            headers=data.get("headers") or {},
        )


class MCPManager:
    """Gerencia configuração, conexão, ferramentas e roteamento de chamadas MCP."""

    def __init__(self, config_path: str | None = None) -> None:
        self.config_path = config_path or DEFAULT_MCP_CONFIG_PATH
        self._clients: dict[str, MCPClient] = {}
        self._server_configs: dict[str, MCPServerConfig] = {}
        self._cached_tools: list[MCPTool] = []
        self._cached_prompts: list[MCPPrompt] = []
        self._lock = threading.RLock()
        self._change_listeners: list[Callable[[], None]] = []
        self._file_monitor: Any | None = None
        self._debounce_timer: threading.Timer | None = None

        self._ensure_config_file()
        self.load_config()
        self.start_file_watcher()

    def add_change_listener(self, cb: Callable[[], None]) -> None:
        """Registra um ouvinte para alterações de estado de servidores ou ferramentas."""
        with self._lock:
            if cb not in self._change_listeners:
                self._change_listeners.append(cb)

    def remove_change_listener(self, cb: Callable[[], None]) -> None:
        """Remove um ouvinte previamente registrado."""
        with self._lock:
            if cb in self._change_listeners:
                self._change_listeners.remove(cb)

    def _notify_listeners(self) -> None:
        with self._lock:
            listeners = list(self._change_listeners)
        for cb in listeners:
            try:
                cb()
            except Exception as exc:
                logger.debug(f"[MCPManager] Erro ao notificar listener: {exc}")

    def start_file_watcher(self) -> None:
        """Inicia o monitoramento inotify/Gio para recarga automática ao salvar mcp_servers.json."""
        if not HAS_GIO or self._file_monitor is not None:
            return
        try:
            gfile = Gio.File.new_for_path(self.config_path)
            self._file_monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._file_monitor.connect("changed", self._on_config_file_event)
            logger.debug(f"[MCPManager] Monitoramento de arquivo ativo em {self.config_path}")
        except Exception as exc:
            logger.debug(f"[MCPManager] Não foi possível iniciar monitor de arquivo: {exc}")

    def stop_file_watcher(self) -> None:
        """Para o monitoramento de arquivo."""
        if self._file_monitor is not None:
            try:
                self._file_monitor.cancel()
            except Exception:
                pass
            self._file_monitor = None

    def _on_config_file_event(self, _monitor, _file, _other_file, event_type) -> None:
        if not HAS_GIO:
            return
        ev_done = getattr(Gio.FileMonitorEvent, "CHANGES_DONE_HINT", None)
        ev_changed = getattr(Gio.FileMonitorEvent, "CHANGED", None)
        if event_type not in (ev_done, ev_changed):
            return

        with self._lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(0.5, self._on_debounced_config_reload)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _on_debounced_config_reload(self) -> None:
        logger.info("[MCPManager] Alteração detectada em mcp_servers.json. Recarregando servidores...")
        self.load_config()
        self.start_all_enabled()
        self.refresh_tools_cache()
        self.refresh_prompts_cache()
        self._notify_listeners()

    def _ensure_config_file(self) -> None:
        """Garante que a pasta e o arquivo de configuração existam."""
        try:
            folder = os.path.dirname(self.config_path)
            if not os.path.exists(folder):
                os.makedirs(folder, exist_ok=True)

            if not os.path.exists(self.config_path):
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_MCP_TEMPLATE, f, indent=2, ensure_ascii=False)
                logger.info(f"Arquivo de configuração MCP inicializado em {self.config_path}")
        except Exception as exc:
            logger.debug(f"Erro ao inicializar arquivo MCP: {exc}")

    def load_config(self) -> dict[str, MCPServerConfig]:
        """Lê o arquivo mcp_servers.json e atualiza as configurações de servidores."""
        with self._lock:
            self._server_configs.clear()

            if not os.path.exists(self.config_path):
                return self._server_configs

            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                servers = data.get("mcpServers", {})
                for name, s_data in servers.items():
                    if isinstance(s_data, dict):
                        self._server_configs[name] = MCPServerConfig.from_dict(name, s_data)
            except Exception as exc:
                logger.error(f"Erro ao carregar configuração MCP ({self.config_path}): {exc}")

            return dict(self._server_configs)

    def save_config(self) -> bool:
        """Salva as configurações atuais de volta para mcp_servers.json."""
        with self._lock:
            out = {"mcpServers": {}}
            for name, cfg in self._server_configs.items():
                out["mcpServers"][name] = cfg.to_dict()

            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(out, f, indent=2, ensure_ascii=False)
                return True
            except Exception as exc:
                logger.error(f"Erro ao salvar configuração MCP: {exc}")
                return False

    def get_server_configs(self) -> dict[str, MCPServerConfig]:
        with self._lock:
            return dict(self._server_configs)

    def set_server_enabled(self, name: str, enabled: bool) -> bool:
        """Ativa ou desativa um servidor MCP específico."""
        with self._lock:
            cfg = self._server_configs.get(name)
            if not cfg:
                return False
            cfg.enabled = enabled

        self.save_config()

        if not enabled:
            self.stop_server(name)
        else:
            self.start_server(name)
        return True

    def enable_server(self, name: str, enabled: bool = True) -> bool:
        """Alias conveniente para set_server_enabled."""
        return self.set_server_enabled(name, enabled)

    def add_server(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        description: str = "",
        enabled: bool = True,
    ) -> tuple[bool, str]:
        """Acrescenta um servidor stdio à configuração e grava no arquivo.

        Devolve ``(ok, mensagem)`` em vez de levantar: a origem é um formulário
        preenchido à mão, e um nome repetido ou um comando vazio são erros de
        digitação, não falhas do programa. A mensagem é escrita para ser mostrada
        ao usuário como está.
        """
        clean_name = (name or "").strip()
        clean_command = (command or "").strip()

        if not clean_name:
            return False, "Dê um nome ao servidor."
        if not _SERVER_NAME_RE.match(clean_name):
            return False, (
                "O nome aceita apenas letras, números, ponto, hífen e sublinhado."
            )
        if not clean_command:
            return False, "Informe o comando que inicia o servidor."

        # Relê o arquivo antes de escrever. `save_config` grava o dicionário
        # inteiro, então um servidor acrescentado fora do app desde o último
        # load seria apagado em silêncio pela nossa gravação.
        self.load_config()

        with self._lock:
            if clean_name in self._server_configs:
                return False, f'Já existe um servidor chamado "{clean_name}".'
            self._server_configs[clean_name] = MCPServerConfig(
                name=clean_name,
                command=clean_command,
                args=list(args or []),
                enabled=enabled,
                description=description,
            )

        if not self.save_config():
            return False, "Não foi possível gravar o arquivo de configuração."

        if enabled:
            self.start_server(clean_name)
        self._notify_listeners()
        return True, f'Servidor "{clean_name}" adicionado.'

    def remove_server(self, name: str) -> tuple[bool, str]:
        """Remove um servidor da configuração e do arquivo."""
        self.load_config()

        with self._lock:
            if name not in self._server_configs:
                return False, f'Não há servidor chamado "{name}".'
            del self._server_configs[name]

        self.stop_server(name)
        if not self.save_config():
            return False, "Não foi possível gravar o arquivo de configuração."

        self._notify_listeners()
        return True, f'Servidor "{name}" removido.'

    # ------------------------------------------------------------------
    # Ciclo de Vida dos Clientes
    # ------------------------------------------------------------------

    def _create_client(self, cfg: MCPServerConfig) -> MCPClient:
        """Instancia um novo MCPClient (Stdio ou SSE) a partir da configuração fornecida."""
        client = MCPClient(
            name=cfg.name,
            command=cfg.command,
            args=cfg.args,
            env=cfg.env,
            cwd=cfg.cwd,
            transport=cfg.transport,
            url=cfg.url,
            headers=cfg.headers,
        )
        client.on_tools_changed = self._on_client_tools_changed
        client.on_prompts_changed = self._on_client_prompts_changed
        client.on_state_changed = self._on_client_state_changed
        return client

    def _on_client_tools_changed(self, server_name: str) -> None:
        logger.info(f"[MCP:{server_name}] Atualizando ferramentas após notificação remota.")
        self.refresh_tools_cache()
        self._notify_listeners()

    def _on_client_prompts_changed(self, server_name: str) -> None:
        logger.info(f"[MCP:{server_name}] Atualizando prompts após notificação remota.")
        self.refresh_prompts_cache()
        self._notify_listeners()

    def _on_client_state_changed(self, server_name: str, new_state: MCPServerState) -> None:
        logger.debug(f"[MCP:{server_name}] Estado alterado para {new_state.value}")
        self._notify_listeners()

    def get_running_servers(self) -> list[str]:
        """Retorna os nomes dos servidores MCP atualmente conectados e em execução."""
        with self._lock:
            return [name for name, client in self._clients.items() if client.is_running]

    def get_client(self, name: str) -> MCPClient | None:
        with self._lock:
            return self._clients.get(name)

    def start_server(self, name: str) -> bool:
        """Inicia um servidor MCP configurado caso esteja habilitado."""
        with self._lock:
            cfg = self._server_configs.get(name)
            if not cfg:
                return False
            if not cfg.enabled:
                return False

            client = self._clients.get(name)
            if not client:
                client = self._create_client(cfg)
                self._clients[name] = client

        started = client.start()
        self.refresh_tools_cache()
        self.refresh_prompts_cache()
        return started

    def stop_server(self, name: str) -> None:
        """Para um servidor MCP em execução."""
        with self._lock:
            client = self._clients.pop(name, None)

        if client:
            client.stop()
        self.refresh_tools_cache()
        self.refresh_prompts_cache()

    def start_all_enabled(self) -> None:
        """Inicia todos os servidores configurados com enabled=True."""
        configs = self.get_server_configs()
        for name, cfg in configs.items():
            if cfg.enabled:
                self.start_server(name)
        self.refresh_tools_cache()
        self.refresh_prompts_cache()

    def stop_all(self) -> None:
        """Encerra todos os servidores ativos."""
        self.stop_file_watcher()
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()

        for c in clients:
            try:
                c.stop()
            except Exception:
                pass
        self._cached_tools.clear()
        self._cached_prompts.clear()

    # ------------------------------------------------------------------
    # Recursos (MCP Resources)
    # ------------------------------------------------------------------

    def get_all_resources(self) -> list[MCPResource]:
        """Retorna recursos exportados por todos os servidores MCP conectados."""
        all_res: list[MCPResource] = []
        with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            if client.is_running or client.state == MCPServerState.CONNECTED:
                try:
                    res = client.list_resources()
                    all_res.extend(res)
                except Exception as exc:
                    logger.debug(f"[MCP:{client.name}] Erro ao listar recursos: {exc}")
        return all_res

    def read_resource(self, uri: str) -> str:
        """Lê o conteúdo textual de um recurso pelo seu URI em qualquer servidor conectado."""
        with self._lock:
            clients = list(self._clients.values())
        for client in clients:
            if client.is_running or client.state == MCPServerState.CONNECTED:
                try:
                    content = client.read_resource(uri)
                    if content and not content.startswith("Erro"):
                        return content
                except Exception:
                    continue
        return f"Recurso '{uri}' não encontrado em nenhum servidor MCP ativo."

    def reload(self) -> None:
        """Recarrega a configuração e reconecta os servidores habilitados."""
        self.stop_all()
        self.load_config()
        self.start_all_enabled()

    # ------------------------------------------------------------------
    # Ferramentas
    # ------------------------------------------------------------------

    def refresh_tools_cache(self) -> list[MCPTool]:
        """Consulta as ferramentas de todos os servidores MCP conectados."""
        all_tools: list[MCPTool] = []
        with self._lock:
            clients = list(self._clients.values())

        for client in clients:
            if client.is_running or client.state == MCPServerState.CONNECTED:
                try:
                    tools = client.list_tools()
                    all_tools.extend(tools)
                except Exception as exc:
                    logger.debug(f"[MCP:{client.name}] Erro ao listar ferramentas: {exc}")

        with self._lock:
            self._cached_tools = all_tools
        return all_tools

    def get_all_tools(self) -> list[MCPTool]:
        """Retorna as ferramentas atualmente em cache ou as atualiza se vazio."""
        with self._lock:
            if self._cached_tools:
                return list(self._cached_tools)
        return self.refresh_tools_cache()

    def get_tool(self, qualified_or_name: str) -> MCPTool | None:
        """Busca uma ferramenta pelo nome qualificado (mcp__server__tool) ou simples."""
        tools = self.get_all_tools()
        for t in tools:
            if t.qualified_name == qualified_or_name or t.name == qualified_or_name:
                return t
        return None

    def call_tool(
        self, qualified_or_name: str, arguments: dict[str, Any] | None = None
    ) -> MCPCallResult:
        """
        Executa uma ferramenta MCP, identificando o servidor por prefixo.
        
        Suporta:
          - 'mcp__git__git_status' -> server: 'git', tool: 'git_status'
          - 'git_status' (quando prefixado por nome do servidor)
          - 'git:git_status'
        """
        server_name = ""
        tool_name = qualified_or_name

        if qualified_or_name.startswith("mcp__"):
            parts = qualified_or_name.split("__", 2)
            if len(parts) >= 3:
                server_name = parts[1]
                tool_name = parts[2]
        elif ":" in qualified_or_name:
            server_name, tool_name = qualified_or_name.split(":", 1)
        else:
            # Tenta descobrir o servidor pelo nome da ferramenta no cache
            tools = self.get_all_tools()
            for t in tools:
                if t.name == qualified_or_name or t.qualified_name == qualified_or_name:
                    server_name = t.server_name
                    tool_name = t.name
                    break

        if not server_name:
            return MCPCallResult(
                is_error=True,
                content=[{"type": "text", "text": f"Não foi possível identificar o servidor MCP para '{qualified_or_name}'."}],
            )

        client = self.get_client(server_name)
        if not client:
            # Tenta iniciar sob demanda se estiver configurado e habilitado
            cfg = self.get_server_configs().get(server_name)
            if cfg and cfg.enabled:
                self.start_server(server_name)
                client = self.get_client(server_name)

        if not client:
            return MCPCallResult(
                is_error=True,
                content=[{"type": "text", "text": f"Servidor MCP '{server_name}' não está ativo."}],
            )

        return client.call_tool(tool_name, arguments or {})

    # ------------------------------------------------------------------
    # Prompts (MCP Prompts)
    # ------------------------------------------------------------------

    def refresh_prompts_cache(self) -> list[MCPPrompt]:
        """Consulta os prompts de todos os servidores MCP conectados.

        Um servidor que não anuncia a capability ``prompts`` responde com erro
        JSON-RPC a ``prompts/list``; o cliente devolve lista vazia nesse caso, de
        modo que aqui não há nada a filtrar.
        """
        all_prompts: list[MCPPrompt] = []
        with self._lock:
            clients = list(self._clients.values())

        for client in clients:
            if client.is_running or client.state == MCPServerState.CONNECTED:
                try:
                    prompts = client.list_prompts()
                    all_prompts.extend(prompts)
                except Exception as exc:
                    logger.debug(f"[MCP:{client.name}] Erro ao listar prompts: {exc}")

        with self._lock:
            self._cached_prompts = all_prompts
        return all_prompts

    def get_all_prompts(self) -> list[MCPPrompt]:
        """Retorna os prompts em cache ou os atualiza se o cache estiver vazio."""
        with self._lock:
            if self._cached_prompts:
                return list(self._cached_prompts)
        return self.refresh_prompts_cache()

    def get_prompt(self, qualified_or_name: str) -> MCPPrompt | None:
        """Busca um prompt pelo nome qualificado (mcp__server__prompt) ou simples."""
        prompts = self.get_all_prompts()
        for p in prompts:
            if p.qualified_name == qualified_or_name or p.name == qualified_or_name:
                return p
        return None

    def _resolve_prompt_target(self, qualified_or_name: str) -> tuple[str, str]:
        """Separa ``(servidor, prompt)`` aceitando as mesmas formas de ``call_tool``.

        Formas aceitas:
          - ``mcp__git__code_review`` -> ``("git", "code_review")``
          - ``git:code_review``       -> ``("git", "code_review")``
          - ``code_review``           -> descoberto pelo cache de prompts
        """
        server_name = ""
        prompt_name = qualified_or_name

        if qualified_or_name.startswith("mcp__"):
            parts = qualified_or_name.split("__", 2)
            if len(parts) >= 3:
                server_name = parts[1]
                prompt_name = parts[2]
        elif ":" in qualified_or_name:
            server_name, prompt_name = qualified_or_name.split(":", 1)
        else:
            for p in self.get_all_prompts():
                if p.name == qualified_or_name or p.qualified_name == qualified_or_name:
                    server_name = p.server_name
                    prompt_name = p.name
                    break

        return server_name, prompt_name

    def render_prompt(
        self, qualified_or_name: str, arguments: dict[str, Any] | None = None
    ) -> str:
        """Materializa um prompt MCP como texto, identificando o servidor por prefixo.

        Devolve sempre texto legível — inclusive em erro — porque o destino é a
        caixa de entrada da paleta de comandos, que não trata exceções.
        """
        server_name, prompt_name = self._resolve_prompt_target(qualified_or_name)

        if not server_name:
            return (
                f"Não foi possível identificar o servidor MCP para o prompt "
                f"'{qualified_or_name}'."
            )

        client = self.get_client(server_name)
        if not client:
            # Mesmo comportamento de `call_tool`: sobe sob demanda se configurado.
            cfg = self.get_server_configs().get(server_name)
            if cfg and cfg.enabled:
                self.start_server(server_name)
                client = self.get_client(server_name)

        if not client:
            return f"Servidor MCP '{server_name}' não está ativo."

        return client.get_prompt(prompt_name, arguments or {})

    # ------------------------------------------------------------------
    # Status dos Servidores
    # ------------------------------------------------------------------

    def get_status_summary(self) -> list[dict[str, Any]]:
        """Retorna informações completas de status para UI ou auditoria."""
        configs = self.get_server_configs()
        tools = self.get_all_tools()
        prompts = self.get_all_prompts()

        tools_by_server: dict[str, list[MCPTool]] = {}
        for t in tools:
            tools_by_server.setdefault(t.server_name, []).append(t)

        prompts_by_server: dict[str, list[MCPPrompt]] = {}
        for p in prompts:
            prompts_by_server.setdefault(p.server_name, []).append(p)

        summary = []
        for name, cfg in configs.items():
            client = self.get_client(name)
            state = client.state if client else (MCPServerState.DISABLED if not cfg.enabled else MCPServerState.DISCONNECTED)
            last_err = client.last_error if client else ""
            server_tools = tools_by_server.get(name, [])
            server_prompts = prompts_by_server.get(name, [])

            cmd_display = cfg.url if cfg.transport == "sse" or (cfg.url and not cfg.command) else f"{cfg.command} {' '.join(cfg.args)}".strip()
            summary.append({
                "name": name,
                "command": cmd_display,
                "transport": cfg.transport,
                "url": cfg.url,
                "enabled": cfg.enabled,
                "description": cfg.description,
                "state": state.value,
                "error": last_err,
                "tool_count": len(server_tools),
                "tools": [t.name for t in server_tools],
                "tool_details": [
                    {"name": t.name, "description": t.description, "qualified_name": t.qualified_name}
                    for t in server_tools
                ],
                "prompt_count": len(server_prompts),
                "prompt_details": [
                    {"name": p.name, "description": p.description, "qualified_name": p.qualified_name}
                    for p in server_prompts
                ],
            })
        return summary

