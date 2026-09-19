# Decisão de design: Rastreamento de estado temporal contínuo com atenuação exponencial.
# Evita o "Drift do Almoço": o vetor de estado oculto (h_t) sofre decaimento exponencial
# pelo tempo ocioso (Δt), e é zerado automaticamente quando o computador é bloqueado
# (via D-Bus ScreenSaver / hypridle), garantindo que um retorno após 1 hora inicie um ciclo novo.

"""Rastreador de estado temporal da RNN e decaimento contínuo de contexto."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class TemporalState:
    """Representação do estado oculto contínuo da RNN com controle temporal."""

    dimension: int = 32
    hidden_vector: list[float] = field(default_factory=list)
    last_update_ts: float = field(default_factory=time.time)
    decay_lambda: float = 0.005  # Meia-vida de ~2.3 minutos de ociosidade
    active_session_id: str = ""

    def __post_init__(self) -> None:
        if not self.hidden_vector:
            self.hidden_vector = [0.0] * self.dimension

    def get_decayed_state(self, current_ts: float | None = None) -> list[float]:
        """Calcula o vetor de estado aplicando a atenuação exponencial pelo tempo decorrido."""
        now = current_ts if current_ts is not None else time.time()
        delta_t = max(0.0, now - self.last_update_ts)
        factor = math.exp(-self.decay_lambda * delta_t)
        return [val * factor for val in self.hidden_vector]

    def update(
        self,
        input_vector: Sequence[float],
        current_ts: float | None = None,
    ) -> list[float]:
        """Aplica atualização recorrente h_t = tanh(h_decayed * 0.7 + input * 0.3)."""
        now = current_ts if current_ts is not None else time.time()
        decayed = self.get_decayed_state(now)

        new_hidden = []
        for i in range(self.dimension):
            in_val = input_vector[i] if i < len(input_vector) else 0.0
            # Combinação linear simples com ativação não-linear tanh
            combined = (decayed[i] * 0.7) + (in_val * 0.3)
            # Aproximação segura de tanh
            val = math.tanh(combined)
            new_hidden.append(val)

        self.hidden_vector = new_hidden
        self.last_update_ts = now
        return list(self.hidden_vector)

    def reset(self) -> None:
        """Reinicia o estado oculto a zero (chamado ao bloquear a tela ou trocar de usuário)."""
        self.hidden_vector = [0.0] * self.dimension
        self.last_update_ts = time.time()


class TemporalStateManager:
    """Gerenciador singleton do estado temporal com integração D-Bus para bloqueio de tela."""

    _instance: TemporalStateManager | None = None
    _lock = threading.Lock()

    def __init__(self, dimension: int = 32) -> None:
        self.state = TemporalState(dimension=dimension)
        self._listener_thread: threading.Thread | None = None
        self._running = False

    @classmethod
    def get_default(cls) -> TemporalStateManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def register_event(self, feature_vector: Sequence[float]) -> list[float]:
        """Registra um evento temporal e atualiza o estado oculto da RNN."""
        with self._lock:
            return self.state.update(feature_vector)

    def get_current_state(self) -> list[float]:
        """Retorna o vetor de estado atual com decaimento aplicado."""
        with self._lock:
            return self.state.get_decayed_state()

    def on_screen_locked(self) -> None:
        """Manipulador chamado ao detectar tela travada ou suspensão da sessão."""
        with self._lock:
            self.state.reset()

    def start_screensaver_listener(self) -> None:
        """Inicia escuta passiva de bloqueio de tela via Gio.DBus (PyGObject)."""
        if self._running:
            return

        def _worker():
            try:
                from gi.repository import Gio, GLib  # type: ignore

                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

                def on_signal(connection, sender, path, iface, signal, params):
                    if signal in ("ActiveChanged", "Locked"):
                        if params and len(params) > 0:
                            is_locked = bool(params[0])
                            if is_locked:
                                self.on_screen_locked()

                bus.signal_subscribe(
                    None,
                    "org.freedesktop.ScreenSaver",
                    None,
                    None,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    on_signal,
                )
                loop = GLib.MainLoop()
                loop.run()
            except Exception:
                pass

        self._running = True
        self._listener_thread = threading.Thread(
            target=_worker,
            name="ScreenSaverStateListener",
            daemon=True,
        )
        self._listener_thread.start()
