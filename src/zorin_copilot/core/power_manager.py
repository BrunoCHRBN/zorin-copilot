# Decisão de design: Gerenciamento adaptativo de energia (Battery-Aware Throttling)
# Monitora o barramento D-Bus do UPower (ou /sys/class/power_supply) para adequar a
# taxa de amostragem da RNN e predições proativas conforme a alimentação (AC vs. Bateria).

"""Monitor de energia e perfis de consumo para o Zorin Copilot."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PowerProfile:
    """Configurações adaptativas de amostragem e processamento conforme energia."""

    is_battery: bool
    stream_sample_rate_hz: float  # Frequência máxima de ingestão de eventos
    idle_sleep_sec: float  # Tempo sem eventos para suspender inferências
    enable_proactive_predictions: bool  # Predições antecipadas de intenção
    vlm_visual_grounding_timeout: float  # Timeout para VLM local


class PowerManager:
    """Gerenciador central de perfis de energia do sistema."""

    _instance: PowerManager | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._listeners: list[Callable[[PowerProfile], None]] = []
        self._is_battery: bool = self._detect_on_battery()
        self._profile: PowerProfile = self._build_profile(self._is_battery)

    @classmethod
    def get_default(cls) -> PowerManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @property
    def current_profile(self) -> PowerProfile:
        return self._profile

    @property
    def is_on_battery(self) -> bool:
        return self._is_battery

    def refresh(self) -> PowerProfile:
        """Atualiza a detecção de bateria e notifica ouvintes se o perfil mudou."""
        new_state = self._detect_on_battery()
        if new_state != self._is_battery:
            self._is_battery = new_state
            self._profile = self._build_profile(new_state)
            logger.info(
                "Perfil de energia alterado: %s",
                "BATERIA (Modo Econômico)" if new_state else "TOMADA AC (Alto Desempenho)",
            )
            self._notify_listeners()
        return self._profile

    def add_listener(self, callback: Callable[[PowerProfile], None]) -> None:
        """Registra um ouvinte para alterações no perfil de energia."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[PowerProfile], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            try:
                listener(self._profile)
            except Exception as exc:
                logger.error("Erro no listener de PowerManager: %s", exc)

    def _build_profile(self, is_battery: bool) -> PowerProfile:
        if is_battery:
            # Perfil econômico: preserva bateria limitando inferências desnecessárias
            return PowerProfile(
                is_battery=True,
                stream_sample_rate_hz=10.0,
                idle_sleep_sec=3.0,
                enable_proactive_predictions=False,
                vlm_visual_grounding_timeout=10.0,
            )
        # Perfil AC de alto desempenho
        return PowerProfile(
            is_battery=False,
            stream_sample_rate_hz=60.0,
            idle_sleep_sec=12.0,
            enable_proactive_predictions=True,
            vlm_visual_grounding_timeout=20.0,
        )

    def _detect_on_battery(self) -> bool:
        """Tenta detectar estado de bateria via D-Bus UPower e fallback para sysfs."""
        # 1. Tentativa via Gio.DBus (nativo no ambiente PyGObject do Zorin Copilot)
        try:
            from gi.repository import Gio, GLib  # type: ignore

            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            val = bus.call_sync(
                "org.freedesktop.UPower",
                "/org/freedesktop/UPower",
                "org.freedesktop.DBus.Properties",
                "Get",
                GLib.Variant("(ss)", ("org.freedesktop.UPower", "OnBattery")),
                None,
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
            if val and len(val) > 0:
                inner = val.unpack()[0]
                return bool(inner)
        except Exception:
            pass

        # 2. Tentativa via biblioteca dbus padrão (se instalada)
        try:
            import dbus  # type: ignore

            system_bus = dbus.SystemBus()
            upower = system_bus.get_object("org.freedesktop.UPower", "/org/freedesktop/UPower")
            prop = dbus.Interface(upower, "org.freedesktop.DBus.Properties")
            return bool(prop.Get("org.freedesktop.UPower", "OnBattery"))
        except Exception:
            pass

        # 3. Fallback para leitura direta em /sys/class/power_supply
        try:
            ps_dir = Path("/sys/class/power_supply")
            if ps_dir.exists():
                for supply in ps_dir.iterdir():
                    type_file = supply / "type"
                    if type_file.exists():
                        stype = type_file.read_text(encoding="utf-8").strip().lower()
                        if stype == "battery":
                            status_file = supply / "status"
                            if status_file.exists():
                                status = status_file.read_text(encoding="utf-8").strip().lower()
                                if status == "discharging":
                                    return True
        except Exception:
            pass

        # Padrão seguro para PCs desktop: considera ligado na tomada AC
        return False
