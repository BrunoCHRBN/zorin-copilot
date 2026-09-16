# Decisão de design: Cliente MCP stdio nativo via subprocesso e JSON-RPC 2.0.
# Sem dependências de frameworks assíncronos pesados no núcleo: utiliza threading
# da biblioteca padrão com leitura linha-a-linha de stdout/stderr, tornando-o 100%
# compatível com threads de segundo plano do GTK e do AgentLoop.

"""Cliente de conexão com servidores MCP via transporte Stdio (subprocesso)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from typing import Any

from .protocol import (
    MCP_PROTOCOL_VERSION,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPCallResult,
    MCPResource,
    MCPServerState,
    MCPTool,
)

logger = logging.getLogger(__name__)


class MCPClient:
    """Cliente que gerencia um servidor MCP rodando como processo filho via stdio."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd

        self.state: MCPServerState = MCPServerState.DISCONNECTED
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.last_error: str = ""

        self.auto_restart: bool = True
        self.max_restarts: int = 3
        self._restart_count: int = 0
        self._restarting: bool = False
        self.on_notification: Any | None = None
        self.on_tools_changed: Any | None = None
        self.on_state_changed: Any | None = None

        self._process: subprocess.Popen | None = None
        self._next_req_id: int = 1
        self._lock = threading.RLock()
        self._pending_requests: dict[int | str, tuple[threading.Event, list[JSONRPCResponse | None]]] = {}
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stopping: bool = False

    def _set_state(self, new_state: MCPServerState) -> None:
        """Atualiza o estado e notifica ouvintes se houve alteração."""
        changed = False
        with self._lock:
            if self.state != new_state:
                self.state = new_state
                changed = True
        if changed and self.on_state_changed:
            try:
                self.on_state_changed(self.name, new_state)
            except Exception as exc:
                logger.debug(f"[MCP:{self.name}] Erro no callback on_state_changed: {exc}")

    @property
    def is_running(self) -> bool:
        """Indica se o processo filho está ativo e em estado conectado."""
        return (
            self.state == MCPServerState.CONNECTED
            and self._process is not None
            and self._process.poll() is None
        )

    # ------------------------------------------------------------------
    # Ciclo de Vida
    # ------------------------------------------------------------------

    def start(self, timeout: float = 10.0) -> bool:
        """Inicia o subprocesso e realiza o handshake 'initialize' com o servidor MCP."""
        with self._lock:
            if self.state == MCPServerState.CONNECTED and self._process and self._process.poll() is None:
                return True

            self._set_state(MCPServerState.CONNECTING)
            self.last_error = ""
            self._stopping = False

        # Monta ambiente mesclado com variáveis do sistema
        full_env = os.environ.copy()
        for k, v in self.env.items():
            full_env[k] = os.path.expandvars(str(v))

        full_cmd = [self.command] + self.args

        try:
            self._process = subprocess.Popen(
                full_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self.cwd,
                env=full_env,
            )
        except Exception as exc:
            self.last_error = f"Falha ao executar comando '{self.command}': {exc}"
            self._set_state(MCPServerState.ERROR)
            logger.error(f"[MCP:{self.name}] {self.last_error}")
            return False

        # Dispara thread de leitura de stdout
        self._reader_thread = threading.Thread(
            target=self._stdout_reader_loop, daemon=True, name=f"mcp-out-{self.name}"
        )
        self._reader_thread.start()

        # Dispara thread de leitura de stderr para log
        self._stderr_thread = threading.Thread(
            target=self._stderr_reader_loop, daemon=True, name=f"mcp-err-{self.name}"
        )
        self._stderr_thread.start()

        # Executa handshake de inicialização
        init_params = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {},
            },
            "clientInfo": {
                "name": "zorin-copilot",
                "version": "1.0.0",
            },
        }

        resp = self.send_request("initialize", init_params, timeout=timeout)
        if not resp or resp.is_error:
            err_msg = resp.error.get("message") if resp and resp.error else "Sem resposta no timeout"
            self.last_error = f"Handshake 'initialize' falhou: {err_msg}"
            self._set_state(MCPServerState.ERROR)
            logger.error(f"[MCP:{self.name}] {self.last_error}")
            self.stop()
            return False

        result = resp.result or {}
        self.server_info = result.get("serverInfo", {})
        self.capabilities = result.get("capabilities", {})

        # Notifica que a inicialização foi concluída com sucesso
        self.send_notification("notifications/initialized", {})

        self._restart_count = 0
        self._set_state(MCPServerState.CONNECTED)
        logger.info(
            f"[MCP:{self.name}] Conectado com sucesso: {self.server_info.get('name', 'server')} "
            f"(v{self.server_info.get('version', '?')})"
        )
        return True

    def stop(self) -> None:
        """Finaliza o subprocesso de forma graciosa."""
        with self._lock:
            self._stopping = True
            self._set_state(MCPServerState.DISCONNECTED)

            # Cancela requisições pendentes
            for evt, container in self._pending_requests.values():
                evt.set()
            self._pending_requests.clear()

        proc = self._process
        self._process = None

        if proc:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass

            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Comunicação JSON-RPC 2.0
    # ------------------------------------------------------------------

    def send_request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 20.0
    ) -> JSONRPCResponse | None:
        """Envia requisição síncrona JSON-RPC e aguarda a resposta."""
        with self._lock:
            req_id = self._next_req_id
            self._next_req_id += 1
            evt = threading.Event()
            container: list[JSONRPCResponse | None] = [None]
            self._pending_requests[req_id] = (evt, container)

        req = JSONRPCRequest(id=req_id, method=method, params=params or {})
        msg_str = json.dumps(req.to_dict()) + "\n"

        proc = self._process
        if not proc or not proc.stdin or proc.poll() is not None:
            with self._lock:
                self._pending_requests.pop(req_id, None)
            return None

        try:
            proc.stdin.write(msg_str)
            proc.stdin.flush()
        except Exception as exc:
            logger.debug(f"[MCP:{self.name}] Erro ao escrever requisição no stdin: {exc}")
            with self._lock:
                self._pending_requests.pop(req_id, None)
            return None

        # Aguarda resposta
        signaled = evt.wait(timeout=timeout)
        with self._lock:
            self._pending_requests.pop(req_id, None)

        if not signaled:
            logger.warning(f"[MCP:{self.name}] Requisição {method} (id={req_id}) expirou após {timeout}s")
            return None

        return container[0]

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Envia notificação unidirecional (sem esperar resposta)."""
        proc = self._process
        if not proc or not proc.stdin or proc.poll() is not None:
            return

        notif = JSONRPCNotification(method=method, params=params or {})
        msg_str = json.dumps(notif.to_dict()) + "\n"
        try:
            proc.stdin.write(msg_str)
            proc.stdin.flush()
        except Exception as exc:
            logger.debug(f"[MCP:{self.name}] Erro ao enviar notificação: {exc}")

    # ------------------------------------------------------------------
    # Métodos de Alto Nível do Protocolo MCP
    # ------------------------------------------------------------------

    def list_tools(self, timeout: float = 10.0) -> list[MCPTool]:
        """Consulta as ferramentas exportadas pelo servidor (tools/list)."""
        if self.state != MCPServerState.CONNECTED:
            if not self.start():
                return []

        resp = self.send_request("tools/list", {}, timeout=timeout)
        if not resp or resp.is_error:
            logger.warning(f"[MCP:{self.name}] Falha ao listar ferramentas: {resp.error if resp else 'timeout'}")
            return []

        tools_data = resp.result.get("tools", []) if isinstance(resp.result, dict) else []
        tools: list[MCPTool] = []
        for td in tools_data:
            if not isinstance(td, dict) or "name" not in td:
                continue
            tools.append(
                MCPTool(
                    name=td["name"],
                    server_name=self.name,
                    description=td.get("description", ""),
                    input_schema=td.get("inputSchema", {}),
                )
            )
        return tools

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, timeout: float = 35.0
    ) -> MCPCallResult:
        """Invoca uma ferramenta remota do servidor MCP (tools/call)."""
        if self.state != MCPServerState.CONNECTED:
            if not self.start():
                return MCPCallResult(
                    is_error=True,
                    content=[{"type": "text", "text": f"Servidor MCP '{self.name}' indisponível: {self.last_error}"}],
                )

        params = {"name": name, "arguments": arguments or {}}
        resp = self.send_request("tools/call", params, timeout=timeout)

        if not resp:
            return MCPCallResult(
                is_error=True,
                content=[{"type": "text", "text": f"Timeout ao executar ferramenta '{name}' no servidor '{self.name}'."}],
            )

        if resp.is_error:
            err_msg = resp.error.get("message", "Erro desconhecido") if resp.error else "Erro"
            return MCPCallResult(
                is_error=True,
                content=[{"type": "text", "text": f"Erro MCP ({self.name}): {err_msg}"}],
                raw=resp.error or {},
            )

        result = resp.result or {}
        is_err = bool(result.get("isError", False))
        content = result.get("content", [])
        return MCPCallResult(content=content, is_error=is_err, raw=result)

    def list_resources(self, timeout: float = 10.0) -> list[MCPResource]:
        """Lista recursos expostos pelo servidor (resources/list)."""
        if self.state != MCPServerState.CONNECTED:
            if not self.start():
                return []

        resp = self.send_request("resources/list", {}, timeout=timeout)
        if not resp or resp.is_error:
            return []

        res_data = resp.result.get("resources", []) if isinstance(resp.result, dict) else []
        resources: list[MCPResource] = []
        for rd in res_data:
            if isinstance(rd, dict) and "uri" in rd:
                resources.append(
                    MCPResource(
                        uri=rd["uri"],
                        name=rd.get("name", rd["uri"]),
                        server_name=self.name,
                        description=rd.get("description", ""),
                        mime_type=rd.get("mimeType", ""),
                    )
                )
        return resources

    def read_resource(self, uri: str, timeout: float = 15.0) -> str:
        """Lê o conteúdo de um recurso pelo seu URI (resources/read)."""
        if self.state != MCPServerState.CONNECTED:
            if not self.start():
                return f"Erro: Servidor MCP '{self.name}' não conectado."

        resp = self.send_request("resources/read", {"uri": uri}, timeout=timeout)
        if not resp or resp.is_error:
            return f"Erro ao ler recurso '{uri}'."

        contents = resp.result.get("contents", []) if isinstance(resp.result, dict) else []
        text_parts = []
        for c in contents:
            if isinstance(c, dict) and "text" in c:
                text_parts.append(c["text"])
        return "\n".join(text_parts)

    # ------------------------------------------------------------------
    # Loops de leitura em segundo plano
    # ------------------------------------------------------------------

    def _stdout_reader_loop(self) -> None:
        proc = self._process
        if not proc or not proc.stdout:
            return

        while not self._stopping:
            try:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                self._handle_incoming_message(line)
            except Exception as exc:
                if not self._stopping:
                    logger.debug(f"[MCP:{self.name}] Erro no loop de stdout: {exc}")
                break

        with self._lock:
            should_restart = (
                not self._stopping
                and self.auto_restart
                and self._restart_count < self.max_restarts
                and not self._restarting
            )

        if should_restart:
            self._schedule_auto_restart()
        else:
            with self._lock:
                if not self._stopping:
                    self._set_state(MCPServerState.DISCONNECTED)
                    logger.debug(f"[MCP:{self.name}] Processo stdout encerrou.")

    def _schedule_auto_restart(self) -> None:
        """Tenta reconectar o servidor com backoff exponencial caso tenha caído inesperadamente."""
        self._restarting = True
        self._restart_count += 1
        delay = min(12.0, 1.5 ** self._restart_count)
        logger.warning(
            f"[MCP:{self.name}] Processo encerrou inesperadamente. Tentando auto-recuperação "
            f"{self._restart_count}/{self.max_restarts} em {delay:.1f}s..."
        )
        self._set_state(MCPServerState.CONNECTING)

        def restart_worker():
            time.sleep(delay)
            self.stop()
            success = self.start()
            self._restarting = False
            if success:
                logger.info(f"[MCP:{self.name}] Recuperado com sucesso via auto-restart.")
                if self.on_tools_changed:
                    try:
                        self.on_tools_changed(self.name)
                    except Exception as exc:
                        logger.debug(f"[MCP:{self.name}] Erro ao notificar tools alteradas: {exc}")
            else:
                logger.error(f"[MCP:{self.name}] Auto-recuperação falhou na tentativa {self._restart_count}.")
                self._set_state(MCPServerState.ERROR)

        threading.Thread(target=restart_worker, daemon=True, name=f"mcp-restart-{self.name}").start()

    def _stderr_reader_loop(self) -> None:
        proc = self._process
        if not proc or not proc.stderr:
            return

        while not self._stopping:
            try:
                line = proc.stderr.readline()
                if not line:
                    break
                line_str = line.strip()
                if line_str:
                    logger.debug(f"[MCP:{self.name}:stderr] {line_str}")
            except Exception:
                break

    def _handle_incoming_message(self, raw_line: str) -> None:
        """Faz o parsing da resposta ou notificação JSON-RPC recebida."""
        try:
            data = json.loads(raw_line)
        except Exception:
            # Algumas saídas podem conter logs misturados no stdout
            logger.debug(f"[MCP:{self.name}] Linha não-JSON ignorada: {raw_line[:100]}")
            return

        if not isinstance(data, dict):
            return

        # Resposta a uma requisição enviada pelo cliente
        if "id" in data and data["id"] is not None:
            req_id = data["id"]
            resp = JSONRPCResponse.from_dict(data)
            with self._lock:
                entry = self._pending_requests.get(req_id)
                if entry:
                    evt, container = entry
                    container[0] = resp
                    evt.set()
        elif "method" in data:
            # Notificação do servidor (ex: logging/message, notifications/tools/list_changed)
            method = data.get("method", "")
            params = data.get("params") or {}
            logger.debug(f"[MCP:{self.name}] Notificação recebida: {method}")

            if self.on_notification:
                try:
                    self.on_notification(method, params)
                except Exception as exc:
                    logger.debug(f"[MCP:{self.name}] Erro no callback on_notification: {exc}")

            if method == "notifications/tools/list_changed":
                logger.info(f"[MCP:{self.name}] Notificação tools/list_changed recebida.")
                if self.on_tools_changed:
                    try:
                        self.on_tools_changed(self.name)
                    except Exception as exc:
                        logger.debug(f"[MCP:{self.name}] Erro no callback on_tools_changed: {exc}")
