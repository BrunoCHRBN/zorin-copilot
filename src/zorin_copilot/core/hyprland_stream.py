# Decisão de design: Streaming assíncrono de eventos do Hyprland com Circuit Breaker.
# Conecta ao Socket 2 do Hyprland em tempo real, filtra eventos pelo PrivacyGatekeeper,
# armazena uma janela temporal deslizante em RAM (TemporalRingBuffer) e reconecta de
# forma resiliente via exponential backoff caso o compositor seja recarregado (hyprctl reload).

"""Motor de streaming de eventos em tempo real do compositor Hyprland."""

from __future__ import annotations

import collections
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from .privacy_gate import PrivacyGatekeeper

logger = logging.getLogger(__name__)


class StreamState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass(frozen=True)
class HyprlandEvent:
    """Evento emitido pelo socket2 do Hyprland devidamente parseado e sanitizado."""

    raw: str
    name: str
    params: list[str]
    timestamp: float
    app: str = ""
    title: str = ""
    address: str = ""
    workspace: str = ""
    is_sensitive: bool = False

    @classmethod
    def from_raw_line(cls, line: str) -> HyprlandEvent | None:
        raw = line.strip()
        if not raw or ">>" not in raw:
            return None

        event_name, data = raw.split(">>", 1)
        params = data.split(",") if data else []
        now = time.time()

        app = ""
        title = ""
        address = ""
        workspace = ""

        # Parseamento dos eventos mais frequentes e relevantes para o assistente
        if event_name == "activewindow":
            # activewindow>>class,title
            if len(params) >= 1:
                app = params[0].strip()
            if len(params) >= 2:
                title = ",".join(params[1:]).strip()
        elif event_name == "activewindowv2":
            # activewindowv2>>address
            if params:
                address = params[0].strip()
        elif event_name == "workspace":
            # workspace>>name
            if params:
                workspace = params[0].strip()
        elif event_name == "openwindow":
            # openwindow>>address,workspace,class,title
            if len(params) >= 1:
                address = params[0].strip()
            if len(params) >= 2:
                workspace = params[1].strip()
            if len(params) >= 3:
                app = params[2].strip()
            if len(params) >= 4:
                title = ",".join(params[3:]).strip()
        elif event_name == "closewindow":
            # closewindow>>address
            if params:
                address = params[0].strip()

        # Portão cego de privacidade: verifica se a janela/app é confidencial
        privacy_result = PrivacyGatekeeper.should_ingest_window(app_name=app, window_title=title)
        if not privacy_result.allowed:
            return cls(
                raw=f"{event_name}>><REDACTED_SENSITIVE>",
                name=event_name,
                params=[],
                timestamp=now,
                app="<REDACTED_APP>",
                title="<REDACTED_TITLE>",
                address=address,
                workspace=workspace,
                is_sensitive=True,
            )

        return cls(
            raw=raw,
            name=event_name,
            params=params,
            timestamp=now,
            app=app,
            title=title,
            address=address,
            workspace=workspace,
            is_sensitive=False,
        )


class TemporalRingBuffer:
    """Buffer circular em memória com janela deslizante temporal thread-safe."""

    def __init__(self, capacity: int = 150) -> None:
        self._capacity = capacity
        self._deque: collections.deque[HyprlandEvent] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()

    def append(self, event: HyprlandEvent) -> None:
        with self._lock:
            self._deque.append(event)

    def get_recent(self, count: int = 10) -> list[HyprlandEvent]:
        with self._lock:
            items = list(self._deque)
            return items[-count:] if count <= len(items) else items

    def get_all(self) -> list[HyprlandEvent]:
        with self._lock:
            return list(self._deque)

    def clear(self) -> None:
        with self._lock:
            self._deque.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._deque)


def resolve_socket2_path() -> Path | None:
    """Localiza o socket2.sock do Hyprland verificando os diretórios suportados."""
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "").strip()
    if not sig:
        return None

    uid = os.getuid() if hasattr(os, "getuid") else 1000
    candidates = [
        # 1. $XDG_RUNTIME_DIR/hypr/$SIG/.socket2.sock (Hyprland v0.40+)
        Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")) / "hypr" / sig / ".socket2.sock",
        # 2. /run/user/$UID/hypr/$SIG/.socket2.sock
        Path(f"/run/user/{uid}/hypr/{sig}/.socket2.sock"),
        # 3. /tmp/hypr/$SIG/.socket2.sock (Hyprland legado)
        Path(f"/tmp/hypr/{sig}/.socket2.sock"),
    ]

    for path in candidates:
        if path.exists():
            return path
    return None


class HyprlandStream:
    """Cliente de streaming resiliente para o socket2 do Hyprland com Circuit Breaker."""

    def __init__(
        self,
        buffer_capacity: int = 150,
        custom_socket_path: Path | str | None = None,
    ) -> None:
        self._custom_socket_path = Path(custom_socket_path) if custom_socket_path else None
        self.ring_buffer = TemporalRingBuffer(capacity=buffer_capacity)
        self._listeners: list[Callable[[HyprlandEvent], None]] = []

        self._state: StreamState = StreamState.DISCONNECTED
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> StreamState:
        with self._lock:
            return self._state

    def add_listener(self, callback: Callable[[HyprlandEvent], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[HyprlandEvent], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def start(self) -> None:
        """Inicia a thread de escuta assíncrona do socket."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name="HyprlandStreamWorker",
                daemon=True,
            )
            self._thread.start()
            logger.info("Motor de streaming do Hyprland inicializado.")

    def stop(self) -> None:
        """Encerra a thread de streaming com segurança."""
        with self._lock:
            self._running = False
            self._state = StreamState.DISCONNECTED

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        logger.info("Motor de streaming do Hyprland finalizado.")

    def _resolve_target_socket(self) -> Path | None:
        if self._custom_socket_path:
            return self._custom_socket_path
        return resolve_socket2_path()

    def _run_loop(self) -> None:
        retry_delay = 0.5

        while self._running:
            sock_path = self._resolve_target_socket()
            if not sock_path or not sock_path.exists():
                with self._lock:
                    self._state = StreamState.DISCONNECTED
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 5.0)
                continue

            client: socket.socket | None = None
            try:
                with self._lock:
                    self._state = StreamState.CONNECTING

                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(5.0)
                client.connect(str(sock_path))

                with self._lock:
                    self._state = StreamState.CONNECTED
                    retry_delay = 0.5  # Reset no backoff após conexão bem-sucedida

                logger.debug("Conectado ao socket2 do Hyprland: %s", sock_path)

                # Buffer de leitura contínua
                buffer = ""
                client.settimeout(1.0)

                while self._running:
                    try:
                        chunk = client.recv(4096)
                        if not chunk:
                            # Compositor fechou a conexão
                            break
                        buffer += chunk.decode("utf-8", errors="replace")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            event = HyprlandEvent.from_raw_line(line)
                            if event is not None:
                                self.ring_buffer.append(event)
                                self._dispatch_event(event)
                    except socket.timeout:
                        # Timeout normal para permitir que a flag self._running seja checada
                        continue
                    except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                        logger.debug("Socket do Hyprland desconectado: %s", exc)
                        break

            except Exception as exc:
                logger.debug("Falha na conexão com o Hyprland socket2: %s", exc)
            finally:
                if client:
                    try:
                        client.close()
                    except Exception:
                        pass
                with self._lock:
                    if self._running:
                        self._state = StreamState.RECONNECTING

            if self._running:
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 6.0)

    def _dispatch_event(self, event: HyprlandEvent) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as exc:
                logger.error("Exceção no ouvinte de HyprlandEvent: %s", exc)
