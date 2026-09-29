# Decisão de design: o cliente REST do Home Assistant só enxerga o que ele
# pergunta. Para o Copilot ser *responsivo* (saber que a porta abriu, que a luz
# mudou, que alguém acionou um botão) ele precisa de PUSH. Este módulo fala o
# WebSocket API do Home Assistant — e o faz sem nenhuma dependência externa:
# implementa o handshake e o enquadramento RFC 6455 sobre `asyncio` puro, para
# caber no perfil do projeto (cliente REST também é `urllib` puro).
#
# Tudo que é decisão de dado (normalizar evento, casar filtro, calcular backoff)
# é função pura e de nível de módulo — testável sem servidor nem socket. O
# transporte é isolado em `_connect`/`_session`, para poder ser substituído.

"""Cliente de eventos em tempo real do Home Assistant (WebSocket, sem dependências)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import ssl
import struct
import threading
import time
from collections import deque
from typing import Any, Callable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

__all__ = [
    "HomeAssistantRealtime",
    "get_realtime",
    "reset_realtime",
    "normalize_state_changed",
    "event_matches",
    "backoff_delay",
    "MAX_BACKOFF",
]

# Teto do backoff de reconexão. Sem teto, uma queda de energia viraria espera
# de horas; com 30s, a casa volta a ser observada em no máximo meio minuto.
MAX_BACKOFF = 30.0

# Quantos eventos manter no histórico em memória (responde "o que acabou de
# acontecer?" sem tocar no banco do Home Assistant).
RECENT_BUFFER = 200

# Opcodes RFC 6455
_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


def backoff_delay(attempt: int, base: float = 1.0, cap: float = MAX_BACKOFF) -> float:
    """Backoff exponencial com teto. `attempt` começa em 0 (1s, 2s, 4s...)."""
    if attempt < 0:
        attempt = 0
    return float(min(cap, base * (2 ** attempt)))


def normalize_state_changed(event: dict[str, Any]) -> dict[str, Any] | None:
    """Achata um evento ``state_changed`` do HA num dicionário estável.

    Devolve ``None`` quando não é um evento de estado utilizável — assim o
    chamador pode filtrar sem se preocupar com o formato cru do Home Assistant.
    """
    if not isinstance(event, dict):
        return None
    data = event.get("data") or {}
    entity_id = data.get("entity_id")
    if not entity_id:
        return None
    new_state = data.get("new_state") or {}
    old_state = data.get("old_state") or {}
    attrs = new_state.get("attributes") or {}
    domain = entity_id.split(".")[0] if "." in entity_id else ""
    return {
        "kind": "state",
        "entity_id": entity_id,
        "domain": domain,
        "state": new_state.get("state"),
        "old_state": old_state.get("state"),
        "attributes": attrs,
        "friendly_name": attrs.get("friendly_name") or entity_id,
        "time_fired": event.get("time_fired") or new_state.get("last_changed"),
    }


def event_matches(
    event: dict[str, Any],
    entity_filter: set[str] | None = None,
    domains: set[str] | None = None,
) -> bool:
    """Diz se um evento normalizado interessa a um assinante.

    Eventos de sistema (``kind != "state"``, ex.: conexão) sempre passam: um
    assinante precisa saber que a fonte caiu, mesmo filtrando por entidade.
    """
    if event.get("kind") != "state":
        return True
    if entity_filter:
        eid = str(event.get("entity_id") or "")
        if eid not in entity_filter:
            return False
    if domains:
        dom = str(event.get("domain") or "")
        if dom not in domains:
            return False
    return True


def _build_client_frame(payload: bytes, opcode: int = _OP_TEXT) -> bytes:
    """Monta um frame de cliente (SEMPRE mascarado, exigência do RFC 6455)."""
    header = bytearray()
    header.append(0x80 | (opcode & 0x0F))  # FIN + opcode
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytearray(payload)
    for i in range(len(masked)):
        masked[i] ^= mask[i % 4]
    return bytes(header) + bytes(masked)


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes, bool]:
    """Lê um frame do servidor. Devolve ``(opcode, payload, fin)``."""
    b1, b2 = await reader.readexactly(2)
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if masked else b""
    payload = await reader.readexactly(length) if length else b""
    if mask:
        payload = bytes(p ^ mask[i % 4] for i, p in enumerate(payload))
    return opcode, payload, fin


class HomeAssistantRealtime:
    """Assina ``state_changed`` do Home Assistant e publica num barramento local.

    Ciclo de vida: ``start()`` sobe uma thread com um laço asyncio próprio;
    ``stop()`` encerra. Reconexão automática com backoff. Assinantes recebem
    eventos normalizados por callback (roda na thread do cliente — mantenha o
    callback curto; para trabalho pesado, enfileire).
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        *,
        entity_filter: str | None = None,
    ) -> None:
        from .config import CopilotConfig

        cfg = CopilotConfig.load()
        raw_url = url if url is not None else cfg.ha_url
        self.url = (raw_url or "http://localhost:8123").rstrip("/")
        self.token = token if token is not None else (
            cfg.ha_token or os.environ.get("HOME_ASSISTANT_TOKEN", "")
        )
        raw_filter = entity_filter if entity_filter is not None else getattr(cfg, "ha_realtime_entities", "")
        self.entity_filter: set[str] = {
            e.strip() for e in str(raw_filter or "").split(",") if e.strip()
        }

        self._subscribers: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._recent: deque[dict[str, Any]] = deque(maxlen=RECENT_BUFFER)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._stop_event = threading.Event()
        self._connected = threading.Event()
        self._started = False
        self._msg_id = 0

    # ------------------------------------------------------------------ config
    def is_configured(self) -> bool:
        return bool(self.url and self.token.strip())

    def is_running(self) -> bool:
        return self._started and self._thread is not None and self._thread.is_alive()

    def is_connected(self) -> bool:
        return self._connected.is_set()

    # --------------------------------------------------------------- lifecycle
    def start(self) -> bool:
        """Sobe o cliente numa thread. Idempotente. Devolve se está ativo."""
        if not self.is_configured():
            logger.warning("Home Assistant não configurado: cliente em tempo real não iniciado.")
            return False
        with self._lock:
            if self.is_running():
                return True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="HARealtime"
            )
            self._started = True
            self._thread.start()
        return True

    def stop(self, timeout: float = 3.0) -> None:
        """Encerra o cliente e aguarda a thread terminar."""
        self._stop_event.set()
        loop = self._loop
        writer = self._writer
        if loop and writer is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._close_writer(writer), loop)
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._started = False
        self._connected.clear()

    async def _close_writer(self, writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    # ------------------------------------------------------------------- pub/sub
    def subscribe(
        self,
        callback: Callable[[dict[str, Any]], None],
        *,
        entity_filter: set[str] | None = None,
        domains: set[str] | None = None,
    ) -> Callable[[], None]:
        """Registra ``callback`` e devolve uma função de cancelamento."""
        entry = {
            "callback": callback,
            "entities": entity_filter or None,
            "domains": domains or None,
        }
        with self._lock:
            self._subscribers.append(entry)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(entry)
                except ValueError:
                    pass

        return _unsubscribe

    def unsubscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s["callback"] is not callback]

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        """Últimos eventos observados (mais novo por último)."""
        with self._lock:
            items = list(self._recent)
        if limit and limit > 0:
            return items[-limit:]
        return items

    # -------------------------------------------------------------- dispatch
    def handle_message(self, message: dict[str, Any]) -> None:
        """Processa uma mensagem já decodificada do WebSocket.

        Separado do laço de I/O de propósito: é aqui que vive a regra de negócio
        (o que é evento, o que é resposta de comando) e é por aqui que os testes
        injetam eventos sem abrir socket.
        """
        msg_type = message.get("type")
        if msg_type == "event":
            normalized = normalize_state_changed(message.get("event") or {})
            if normalized:
                self._emit(normalized)
        # 'result' / 'auth_ok' etc. não geram eventos de estado.

    def _emit(self, event: dict[str, Any]) -> None:
        if event.get("kind") == "state":
            with self._lock:
                self._recent.append(event)
        with self._lock:
            subscribers = list(self._subscribers)
        for sub in subscribers:
            if not event_matches(event, sub.get("entities"), sub.get("domains")):
                continue
            try:
                sub["callback"](event)
            except Exception as exc:  # um assinante quebrado não derruba os outros
                logger.debug("Assinante de eventos HA falhou: %s", exc)

    def _emit_connection(self, state: str, detail: str = "") -> None:
        self._emit({
            "kind": "connection",
            "state": state,
            "detail": detail,
            "time_fired": time.time(),
        })

    # ------------------------------------------------------------------- I/O
    def _ws_url(self) -> str:
        parsed = urlparse(self.url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        host = parsed.hostname or "localhost"
        netloc = host if not parsed.port else f"{host}:{parsed.port}"
        path = (parsed.path or "").rstrip("/") + "/api/websocket"
        return f"{scheme}://{netloc}{path}"

    def _run(self) -> None:
        """Alvo da thread: laço asyncio com reconexão."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._reconnect_loop())
        except Exception as exc:
            logger.debug("Cliente HA em tempo real encerrado: %s", exc)
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            self._loop.close()
            self._loop = None

    async def _reconnect_loop(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            try:
                await self._session()
                attempt = 0  # sessão saudável: zera o backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                delay = backoff_delay(attempt)
                logger.info(
                    "HA em tempo real: desconectado (%s). Reconectando em %.0fs.",
                    exc, delay,
                )
                self._connected.clear()
                self._emit_connection("disconnected", str(exc))
                attempt += 1
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise

    async def _session(self) -> None:
        reader, writer = await self._connect()
        self._writer = writer
        try:
            await self._authenticate(reader, writer)
            self._connected.set()
            self._emit_connection("connected", self.url)
            await self._subscribe(reader, writer)
            await self._read_loop(reader, writer)
        finally:
            self._connected.clear()
            self._writer = None
            await self._close_writer(writer)

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        parsed = urlparse(self.url)
        host = parsed.hostname or "localhost"
        secure = parsed.scheme == "https"
        port = parsed.port or (443 if secure else 80)
        ssl_ctx = ssl.create_default_context() if secure else None
        reader, writer = await asyncio.open_connection(
            host, port, ssl=ssl_ctx, server_hostname=host if secure else None
        )
        key = base64.b64encode(os.urandom(16)).decode()
        path = (parsed.path or "").rstrip("/") + "/api/websocket"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()
        status_line = (await reader.readline()).decode("latin-1").strip()
        if "101" not in status_line:
            raise ConnectionError(f"Handshake WebSocket recusado: {status_line}")
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        return reader, writer

    async def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send_json(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write(_build_client_frame(json.dumps(payload).encode("utf-8")))
        await writer.drain()

    async def _authenticate(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        opcode, payload, _ = await _read_frame(reader)
        message = json.loads(payload.decode("utf-8")) if payload else {}
        if message.get("type") != "auth_required":
            raise ConnectionError(f"Esperava 'auth_required', veio '{message.get('type')}'.")
        await self._send_json(writer, {"type": "auth", "access_token": self.token.strip()})
        opcode, payload, _ = await _read_frame(reader)
        message = json.loads(payload.decode("utf-8")) if payload else {}
        if message.get("type") == "auth_invalid":
            raise PermissionError("Token do Home Assistant inválido (auth_invalid).")
        if message.get("type") != "auth_ok":
            raise ConnectionError(f"Falha na autenticação: '{message.get('type')}'.")

    async def _subscribe(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        msg_id = await self._next_id()
        await self._send_json(writer, {
            "id": msg_id,
            "type": "subscribe_events",
            "event_type": "state_changed",
        })

    async def _read_loop(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        buffer = bytearray()
        while not self._stop_event.is_set():
            opcode, payload, fin = await _read_frame(reader)
            if opcode == _OP_CLOSE:
                raise ConnectionError("Servidor fechou a conexão WebSocket.")
            if opcode == _OP_PING:
                writer.write(_build_client_frame(payload, opcode=_OP_PONG))
                await writer.drain()
                continue
            if opcode == _OP_PONG:
                continue
            if opcode in (_OP_TEXT, _OP_BINARY, _OP_CONT):
                buffer.extend(payload)
                if not fin:
                    continue
                raw = bytes(buffer)
                buffer.clear()
                if opcode == _OP_BINARY:
                    continue
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                self.handle_message(message)

    # --------------------------------------------------------- sincronização
    def snapshot_states(self) -> dict[str, dict[str, Any]]:
        """Estado mais recente conhecido por entidade, a partir do buffer.

        Permite responder "como está a luz?" sem bater na API REST — o buffer já
        tem o último ``state_changed`` de cada entidade observada.
        """
        latest: dict[str, dict[str, Any]] = {}
        with self._lock:
            for evt in self._recent:
                if evt.get("kind") == "state":
                    latest[evt["entity_id"]] = evt
        return latest


# ------------------------------------------------------------------ singleton
_realtime_instance: HomeAssistantRealtime | None = None
_realtime_lock = threading.Lock()


def get_realtime(
    url: str | None = None, token: str | None = None
) -> HomeAssistantRealtime:
    """Instância compartilhada (uma conexão WebSocket por processo).

    Se ``url``/``token`` forem informados e divergirem do singleton atual, ele é
    recriado — evita a armadilha de o gerenciador apontar para uma casa e o
    cliente de eventos continuar escutando outra.
    """
    global _realtime_instance
    with _realtime_lock:
        if _realtime_instance is None:
            _realtime_instance = HomeAssistantRealtime(url, token)
        else:
            new_url = (url or "").rstrip("/") if url is not None else None
            if (new_url is not None and _realtime_instance.url != new_url) or (
                token is not None and _realtime_instance.token != token
            ):
                _realtime_instance.stop()
                _realtime_instance = HomeAssistantRealtime(url, token)
        return _realtime_instance


def reset_realtime() -> None:
    """Encerra e descarta o singleton (usado em testes e ao trocar de config)."""
    global _realtime_instance
    with _realtime_lock:
        if _realtime_instance is not None:
            _realtime_instance.stop()
            _realtime_instance = None
