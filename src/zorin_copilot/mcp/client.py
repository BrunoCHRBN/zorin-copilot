# Decisão de design: Cliente MCP com suporte dual nativo a transporte Stdio (subprocesso local)
# e SSE / HTTP Streaming (servidores remotos / nuvem), seguindo a especificação aberta
# Model Context Protocol (2024-11-05).
# Utiliza exclusivamente a biblioteca padrão Python (subprocess, urllib.request, threading),
# garantindo zero dependências externas pesadas e total compatibilidade com GTK4/Adwaita.

"""Cliente de conexão com servidores MCP via transportes Stdio e SSE (HTTP Streaming)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .protocol import (
    MCP_PROTOCOL_VERSION,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPCallResult,
    MCPPrompt,
    MCPPromptArgument,
    MCPResource,
    MCPServerState,
    MCPTool,
)

logger = logging.getLogger(__name__)


class BaseMCPClient:
    """Classe base que implementa o protocolo JSON-RPC 2.0 e as primitivas do MCP."""

    def __init__(self, name: str) -> None:
        self.name = name
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
        self.on_prompts_changed: Any | None = None
        self.on_state_changed: Any | None = None

        self._next_req_id: int = 1
        self._lock = threading.RLock()
        self._pending_requests: dict[int | str, tuple[threading.Event, list[JSONRPCResponse | None]]] = {}
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
        """Indica se o transporte está ativo e em estado conectado."""
        return (
            self.state == MCPServerState.CONNECTED
            and not self._stopping
            and self._check_transport_alive()
        )

    def _check_transport_alive(self) -> bool:
        """Verifica se o transporte subjacente permanece ativo."""
        return True

    def _do_connect(self, timeout: float) -> bool:
        """Implementado pela subclasse para estabelecer o transporte físico."""
        raise NotImplementedError

    def _do_disconnect(self) -> None:
        """Implementado pela subclasse para encerrar o transporte físico."""
        raise NotImplementedError

    def _do_send_raw(self, msg_str: str, timeout: float = 20.0) -> bool:
        """Implementado pela subclasse para enviar texto bruto pelo canal."""
        raise NotImplementedError

    def start(self, timeout: float = 10.0) -> bool:
        """Inicia a conexão e executa o handshake 'initialize' com o servidor MCP."""
        with self._lock:
            if self.state == MCPServerState.CONNECTED and self.is_running:
                return True

            self._set_state(MCPServerState.CONNECTING)
            self.last_error = ""
            self._stopping = False

        if not self._do_connect(timeout=timeout):
            self._set_state(MCPServerState.ERROR)
            return False

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
        """Finaliza a conexão de forma limpa e graciosa."""
        with self._lock:
            self._stopping = True
            self._set_state(MCPServerState.DISCONNECTED)

            # Cancela requisições pendentes
            for evt, container in self._pending_requests.values():
                evt.set()
            self._pending_requests.clear()

        try:
            self._do_disconnect()
        except Exception as exc:
            logger.debug(f"[MCP:{self.name}] Erro ao desconectar transporte: {exc}")

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

        if not self._do_send_raw(msg_str, timeout=timeout):
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
        notif = JSONRPCNotification(method=method, params=params or {})
        msg_str = json.dumps(notif.to_dict()) + "\n"
        self._do_send_raw(msg_str, timeout=10.0)

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

    def list_prompts(self, timeout: float = 10.0) -> list[MCPPrompt]:
        """Consulta os prompts (templates) exportados pelo servidor (prompts/list).

        Um prompt MCP não executa nada: devolve texto pronto para o modelo. Por
        isso ele é listado separadamente das ferramentas — na paleta de comandos
        ele preenche a entrada em vez de agir no sistema.
        """
        if self.state != MCPServerState.CONNECTED:
            if not self.start():
                return []

        resp = self.send_request("prompts/list", {}, timeout=timeout)
        if not resp or resp.is_error:
            # Servidor que não anuncia a capability 'prompts' responde com erro
            # JSON-RPC (-32601 Method not found). Isso não é uma falha do cliente:
            # apenas significa que este servidor não tem prompts.
            logger.debug(
                f"[MCP:{self.name}] Sem prompts disponíveis: "
                f"{resp.error if resp else 'timeout'}"
            )
            return []

        prompts_data = resp.result.get("prompts", []) if isinstance(resp.result, dict) else []
        prompts: list[MCPPrompt] = []
        for pd in prompts_data:
            if not isinstance(pd, dict) or "name" not in pd:
                continue
            prompts.append(
                MCPPrompt(
                    name=pd["name"],
                    server_name=self.name,
                    description=pd.get("description", ""),
                    arguments=self._parse_prompt_arguments(pd.get("arguments")),
                )
            )
        return prompts

    @staticmethod
    def _parse_prompt_arguments(raw: Any) -> list[MCPPromptArgument]:
        """Normaliza a lista ``arguments`` de um prompt MCP.

        A especificação define ``arguments`` como opcional; servidores omitem o
        campo quando o prompt não recebe parâmetros. Itens sem ``name`` são
        descartados por serem inutilizáveis na coleta de argumentos da UI.
        """
        if not isinstance(raw, list):
            return []
        parsed: list[MCPPromptArgument] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            parsed.append(
                MCPPromptArgument(
                    name=str(name),
                    description=item.get("description", "") or "",
                    required=bool(item.get("required", False)),
                )
            )
        return parsed

    @staticmethod
    def _prompt_message_text(message: Any) -> str:
        """Extrai o texto legível de uma mensagem devolvida por ``prompts/get``.

        O campo ``content`` de uma mensagem pode ser um objeto único
        (``TextContent`` / ``EmbeddedResource``) ou uma lista deles. Recursos
        embutidos trazem o texto em ``resource.text``.
        """
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        blocks = content if isinstance(content, list) else [content]

        parts: list[str] = []
        for block in blocks:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("resource"), dict):
                    resource_text = block["resource"].get("text")
                    if isinstance(resource_text, str):
                        parts.append(resource_text)
        return "\n".join(part for part in parts if part)

    def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        timeout: float = 15.0,
    ) -> str:
        """Materializa um prompt com os argumentos fornecidos (prompts/get).

        Retorna o texto já pronto para ser enviado ao modelo — a concatenação
        das mensagens na ordem em que o servidor as devolveu. Em caso de erro
        devolve uma mensagem legível (nunca ``None``) para que a UI possa
        mostrá-la sem tratamento especial.
        """
        if self.state != MCPServerState.CONNECTED:
            if not self.start():
                return f"Erro: Servidor MCP '{self.name}' não conectado."

        # A especificação exige que 'arguments' seja um objeto; enviamos sempre
        # um dict (possivelmente vazio) para não quebrar servidores estritos.
        params = {"name": name, "arguments": arguments or {}}
        resp = self.send_request("prompts/get", params, timeout=timeout)

        if not resp:
            return f"Timeout ao obter o prompt '{name}' no servidor '{self.name}'."

        if resp.is_error:
            err_msg = resp.error.get("message", "Erro desconhecido") if resp.error else "Erro"
            return f"Erro MCP ({self.name}): {err_msg}"

        result = resp.result if isinstance(resp.result, dict) else {}
        messages = result.get("messages", [])
        texts = [self._prompt_message_text(m) for m in messages]
        text = "\n\n".join(t for t in texts if t)

        if not text:
            # Fallback: alguns servidores devolvem apenas 'description'.
            return result.get("description", "") or ""
        return text

    def _handle_incoming_message(self, raw_line: str) -> None:
        """Faz o parsing da resposta ou notificação JSON-RPC recebida."""
        try:
            data = json.loads(raw_line)
        except Exception:
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

            elif method == "notifications/prompts/list_changed":
                # Prompts são cache separado do de ferramentas: sem este gancho a
                # paleta de comandos mostraria a lista antiga até o app reiniciar.
                logger.info(f"[MCP:{self.name}] Notificação prompts/list_changed recebida.")
                if self.on_prompts_changed:
                    try:
                        self.on_prompts_changed(self.name)
                    except Exception as exc:
                        logger.debug(f"[MCP:{self.name}] Erro no callback on_prompts_changed: {exc}")

    def _schedule_auto_restart(self) -> None:
        """Tenta reconectar o servidor com backoff exponencial caso a conexão tenha caído inesperadamente."""
        self._restarting = True
        self._restart_count += 1
        delay = min(12.0, 1.5 ** self._restart_count)
        logger.warning(
            f"[MCP:{self.name}] Conexão encerrou inesperadamente. Tentando auto-recuperação "
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


class MCPStdioClient(BaseMCPClient):
    """Cliente que gerencia um servidor MCP rodando como processo filho local via Stdio."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd

        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def _check_transport_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _do_connect(self, timeout: float) -> bool:
        # Monta ambiente mesclado com variáveis do sistema
        full_env = os.environ.copy()
        for k, v in self.env.items():
            full_env[k] = os.path.expandvars(str(v))

        # Expande variáveis de ambiente e caminhos de usuário nos argumentos
        expanded_args = [os.path.expanduser(os.path.expandvars(str(arg))) for arg in self.args]
        full_cmd = [self.command] + expanded_args

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
        return True

    def _do_disconnect(self) -> None:
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

    def _do_send_raw(self, msg_str: str, timeout: float = 20.0) -> bool:
        proc = self._process
        if not proc or not proc.stdin or proc.poll() is not None:
            return False

        try:
            proc.stdin.write(msg_str)
            proc.stdin.flush()
            return True
        except Exception as exc:
            logger.debug(f"[MCP:{self.name}] Erro ao escrever requisição no stdin: {exc}")
            return False

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


class MCPSSEClient(BaseMCPClient):
    """Cliente que gerencia a conexão com servidores MCP remotos via HTTP Server-Sent Events (SSE)."""

    def __init__(
        self,
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(name=name)
        self.url = (url or "").strip()
        self.headers = headers or {}

        self._post_endpoint: str | None = None
        self._sse_response: Any | None = None
        self._sse_thread: threading.Thread | None = None
        self._endpoint_event = threading.Event()

    def _check_transport_alive(self) -> bool:
        return self._sse_response is not None and not self._stopping

    def _get_request_headers(self) -> dict[str, str]:
        req_headers = {
            "User-Agent": "zorin-copilot/1.0",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        for k, v in self.headers.items():
            req_headers[k] = os.path.expandvars(str(v))
        return req_headers

    def _do_connect(self, timeout: float) -> bool:
        if not self.url:
            self.last_error = "URL do servidor SSE não informada."
            logger.error(f"[MCP-SSE:{self.name}] {self.last_error}")
            return False

        self._endpoint_event.clear()
        self._post_endpoint = None

        req = urllib.request.Request(self.url, headers=self._get_request_headers(), method="GET")
        try:
            self._sse_response = urllib.request.urlopen(req, timeout=timeout)
        except Exception as exc:
            self.last_error = f"Falha ao conectar via SSE em '{self.url}': {exc}"
            logger.error(f"[MCP-SSE:{self.name}] {self.last_error}")
            return False

        # Inicia leitura da stream SSE
        self._sse_thread = threading.Thread(
            target=self._sse_reader_loop, daemon=True, name=f"mcp-sse-{self.name}"
        )
        self._sse_thread.start()

        # Aguarda a descoberta do endpoint de POST transmitido pelo evento 'endpoint'
        # Damos uma janela de até 3 segundos ou o timeout especificado
        wait_time = min(timeout, 3.0)
        dispatched = self._endpoint_event.wait(timeout=wait_time)
        if not dispatched and not self._post_endpoint:
            # Fallback amigável: assume a própria URL base caso o servidor aceite POST no mesmo caminho
            self._post_endpoint = self.url
            logger.debug(f"[MCP-SSE:{self.name}] Evento 'endpoint' não recebido; fallback para {self.url}")

        return True

    def _do_disconnect(self) -> None:
        resp = self._sse_response
        self._sse_response = None
        self._endpoint_event.set()

        if resp:
            try:
                resp.close()
            except Exception:
                pass

    def _do_send_raw(self, msg_str: str, timeout: float = 20.0) -> bool:
        target_url = self._post_endpoint or self.url
        data_bytes = msg_str.encode("utf-8")
        post_headers = self._get_request_headers()
        post_headers["Content-Type"] = "application/json"
        post_headers["Accept"] = "application/json, text/event-stream, */*"

        req = urllib.request.Request(target_url, data=data_bytes, headers=post_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace").strip()
                # Se o servidor responder diretamente com o corpo JSON-RPC no POST
                if body:
                    self._handle_incoming_message(body)
                return True
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace").strip() if hasattr(exc, "read") else ""
            self.last_error = f"Erro HTTP {exc.code} ao enviar mensagem para {target_url}: {err_body or exc.reason}"
            logger.debug(f"[MCP-SSE:{self.name}] {self.last_error}")
            return False
        except Exception as exc:
            self.last_error = f"Erro de rede ao enviar mensagem POST para {target_url}: {exc}"
            logger.debug(f"[MCP-SSE:{self.name}] {self.last_error}")
            return False

    def _sse_reader_loop(self) -> None:
        resp = self._sse_response
        if not resp:
            return

        current_event = "message"
        data_lines: list[str] = []

        try:
            while not self._stopping:
                raw_line = resp.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

                if not line:
                    # Linha em branco delimita fim de evento SSE
                    if data_lines:
                        content = "\n".join(data_lines)
                        if current_event == "endpoint":
                            resolved_endpoint = urllib.parse.urljoin(self.url, content.strip())
                            self._post_endpoint = resolved_endpoint
                            self._endpoint_event.set()
                            logger.info(f"[MCP-SSE:{self.name}] Endpoint POST registrado: {resolved_endpoint}")
                        elif current_event in ("message", ""):
                            self._handle_incoming_message(content)
                    current_event = "message"
                    data_lines = []
                    continue

                if line.startswith(":"):
                    continue  # Comentário ou keep-alive ping SSE
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

        except Exception as exc:
            if not self._stopping:
                logger.debug(f"[MCP-SSE:{self.name}] Loop SSE interrompido: {exc}")

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
                    logger.debug(f"[MCP-SSE:{self.name}] Stream SSE desconectada.")


class MCPClient(BaseMCPClient):
    """Fábrica e classe base unificada para clientes MCP (compatível com Stdio e SSE)."""

    def __new__(
        cls,
        name: str,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        transport: str = "stdio",
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        transport_normalized = (transport or "stdio").strip().lower()
        if transport_normalized == "sse" or (url and not command):
            instance = super().__new__(MCPSSEClient)
            instance.__init__(name=name, url=url or "", headers=headers)
            return instance

        instance = super().__new__(MCPStdioClient)
        instance.__init__(name=name, command=command or "", args=args, env=env, cwd=cwd)
        return instance
