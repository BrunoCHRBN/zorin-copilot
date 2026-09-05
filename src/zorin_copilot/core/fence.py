# Decisão de design: cerca espacial matemática rígida (Spatial Fencing) para controle de tela no Wayland.
# Garante que cliques e teclas só possam ser emitidos dentro do monitor ou bounding box autorizado pelo usuário (default: Monitor Principal AOC 27"),
# bloqueando sumariamente qualquer tentativa de interação fora do escopo ou em áreas protegidas (Red Zones como barra de tarefas).

"""Gerenciador de cercas espaciais e segurança de tela para automações no desktop."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class FenceMode(str, Enum):
    PRIMARY_ONLY = "primary_only"
    MONITOR_INDEX = "monitor_index"
    MONITOR_NAME = "monitor_name"
    ALL_MONITORS = "all_monitors"
    CUSTOM_BOUNDS = "custom_bounds"


@dataclass
class MonitorInfo:
    """Informações geométricas e metadados de um monitor conectado."""

    index: int
    name: str
    model: str
    x: int
    y: int
    width: int
    height: int
    is_primary: bool = False
    scale: float = 1.0

    @property
    def max_x(self) -> int:
        return self.x + self.width

    @property
    def max_y(self) -> int:
        return self.y + self.height

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.max_x and self.y <= y < self.max_y

    def clamp(self, x: int, y: int) -> tuple[int, int]:
        cx = max(self.x, min(x, self.max_x - 1))
        cy = max(self.y, min(y, self.max_y - 1))
        return cx, cy

    def relative_to_absolute(self, rel_x: float, rel_y: float) -> tuple[int, int]:
        """Converte coordenadas relativas [0.0, 1.0] (da visão multimodal) para coordenadas absolutas de tela."""
        # Se vier na escala 0-1000 (comum em detecção de objetos do Gemini), normaliza
        if rel_x > 1.0:
            rel_x /= 1000.0
        if rel_y > 1.0:
            rel_y /= 1000.0

        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))

        abs_x = self.x + int(rel_x * self.width)
        abs_y = self.y + int(rel_y * self.height)
        return self.clamp(abs_x, abs_y)


@dataclass
class RedZone:
    """Região proibida para automações (ex: barra de tarefas, notificações)."""

    name: str
    x: int
    y: int
    width: int
    height: int
    description: str = ""

    @property
    def max_x(self) -> int:
        return self.x + self.width

    @property
    def max_y(self) -> int:
        return self.y + self.height

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.max_x and self.y <= y < self.max_y


class ScreenFenceManager:
    """Gerencia cercas de segurança espacial e valida coordenadas antes da execução física."""

    def __init__(self, monitors: list[MonitorInfo] | None = None):
        self._monitors: list[MonitorInfo] = monitors if monitors is not None else self.detect_monitors()
        self.mode: FenceMode = FenceMode.PRIMARY_ONLY
        self.active_monitor_index: int = 0
        self.custom_bounds: tuple[int, int, int, int] | None = None  # (min_x, min_y, max_x, max_y)
        self.red_zones: list[RedZone] = []
        self._emergency_stopped: bool = False

        # Define monitor primário inicial
        self._select_default_primary()
        self._setup_default_red_zones()

    def _select_default_primary(self) -> None:
        if not self._monitors:
            return
        # Procura marcado como primary ou busca o AOC ou primeiro
        for i, m in enumerate(self._monitors):
            if m.is_primary or "aoc" in m.name.lower() or "aoc" in m.model.lower():
                self.active_monitor_index = i
                return
        self.active_monitor_index = 0

    def _setup_default_red_zones(self) -> None:
        """Configura zonas de proteção padrão do Zorin OS (ex: barra de tarefas inferior)."""
        self.red_zones.clear()
        for m in self._monitors:
            # Barra de tarefas do Zorin (48px na parte inferior da tela)
            taskbar = RedZone(
                name=f"taskbar_monitor_{m.index}",
                x=m.x,
                y=m.max_y - 48,
                width=m.width,
                height=48,
                description=f"Barra de tarefas do Zorin OS no monitor {m.name}",
            )
            self.red_zones.append(taskbar)

            # Barra superior / Top bar (se houver, 32px no topo)
            topbar = RedZone(
                name=f"topbar_monitor_{m.index}",
                x=m.x,
                y=m.y,
                width=m.width,
                height=32,
                description=f"Painel superior do sistema no monitor {m.name}",
            )
            self.red_zones.append(topbar)

    @classmethod
    def detect_monitors(cls) -> list[MonitorInfo]:
        """Detecta monitores disponíveis via GDK 4 ou fallback seguro."""
        monitors: list[MonitorInfo] = []
        try:
            import gi
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk

            display = Gdk.Display.get_default()
            if display:
                gdk_monitors = display.get_monitors()
                count = gdk_monitors.get_n_items()
                for i in range(count):
                    m = gdk_monitors.get_item(i)
                    geom = m.get_geometry()
                    desc = m.get_description() or f"Monitor {i}"
                    model = m.get_model() or ""
                    # Heurística: no setup do Zorin, monitor em x=1920 ou nome AOC é primário
                    is_primary = bool(i == 0 or "aoc" in desc.lower())
                    scale = m.get_scale_factor() if hasattr(m, "get_scale_factor") else 1.0

                    monitors.append(
                        MonitorInfo(
                            index=i,
                            name=desc,
                            model=model,
                            x=geom.x,
                            y=geom.y,
                            width=geom.width,
                            height=geom.height,
                            is_primary=is_primary,
                            scale=scale,
                        )
                    )
        except Exception as exc:
            logger.debug(f"Detecção Gdk indisponível: {exc}")

        # Fallback se GDK não encontrar monitores (ex: testes sem X11/Wayland rodando)
        if not monitors:
            monitors = [
                MonitorInfo(
                    index=0,
                    name="AOC 27\"",
                    model="AOC 27G2",
                    x=1920,
                    y=0,
                    width=1920,
                    height=1080,
                    is_primary=True,
                ),
                MonitorInfo(
                    index=1,
                    name="VIE 24\"",
                    model="VIE 24",
                    x=0,
                    y=148,
                    width=1920,
                    height=1080,
                    is_primary=False,
                ),
            ]
        return monitors

    @property
    def monitors(self) -> list[MonitorInfo]:
        return list(self._monitors)

    def get_active_monitor(self) -> MonitorInfo | None:
        """Retorna o monitor atualmente ativo como cerca de proteção."""
        if not self._monitors:
            return None
        if 0 <= self.active_monitor_index < len(self._monitors):
            return self._monitors[self.active_monitor_index]
        return self._monitors[0]

    def set_active_monitor(self, identifier: int | str) -> bool:
        """Define o monitor ativo por índice (0, 1) ou nome/termo ('aoc', 'vie', 'principal', 'secundaria')."""
        if isinstance(identifier, int):
            if 0 <= identifier < len(self._monitors):
                self.active_monitor_index = identifier
                self.mode = FenceMode.MONITOR_INDEX
                logger.info(f"Cerca definida para monitor {self.active_monitor_index}: {self._monitors[identifier].name}")
                return True
            return False

        query = str(identifier).strip().lower()
        if query in ("principal", "primaria", "main", "primary"):
            for i, m in enumerate(self._monitors):
                if m.is_primary or "aoc" in m.name.lower():
                    self.active_monitor_index = i
                    self.mode = FenceMode.PRIMARY_ONLY
                    return True

        if query in ("secundaria", "segunda", "auxiliar", "secondary"):
            for i, m in enumerate(self._monitors):
                if not m.is_primary or "vie" in m.name.lower():
                    self.active_monitor_index = i
                    self.mode = FenceMode.MONITOR_INDEX
                    return True

        # Busca por nome/modelo
        for i, m in enumerate(self._monitors):
            if query in m.name.lower() or query in m.model.lower():
                self.active_monitor_index = i
                self.mode = FenceMode.MONITOR_NAME
                return True

        return False

    def set_all_monitors(self) -> None:
        """Libera a cerca para permitir qualquer monitor conectado."""
        self.mode = FenceMode.ALL_MONITORS

    def set_custom_bounds(self, min_x: int, min_y: int, max_x: int, max_y: int) -> None:
        """Define limites personalizados (ex: limites de uma janela específica)."""
        self.custom_bounds = (min_x, min_y, max_x, max_y)
        self.mode = FenceMode.CUSTOM_BOUNDS

    def trigger_emergency_stop(self) -> None:
        """Ativa o Kill Switch imediato, impedindo qualquer automação."""
        self._emergency_stopped = True
        logger.warning("KILL SWITCH ATIVADO: Todas as ações de entrada física foram bloqueadas.")

    def reset_emergency_stop(self) -> None:
        self._emergency_stopped = False

    @property
    def is_emergency_stopped(self) -> bool:
        return self._emergency_stopped

    def is_coordinate_allowed(self, x: int, y: int) -> tuple[bool, str]:
        """Valida matematicamente se uma coordenada absoluta é permitida para cliques ou ações."""
        if self._emergency_stopped:
            return False, "Bloqueado: Parada de emergência (Kill Switch) está ativa."

        # 1. Verifica se está dentro de uma Zona Proibida (Red Zone)
        for rz in self.red_zones:
            if rz.contains(x, y):
                return False, f"Bloqueado: Coordenada ({x}, {y}) atinge área restrita do sistema ({rz.name} - {rz.description})."

        # 2. Valida pelo modo da cerca
        if self.mode == FenceMode.ALL_MONITORS:
            for m in self._monitors:
                if m.contains(x, y):
                    return True, f"Permitido no monitor {m.name}."
            return False, f"Bloqueado: Coordenada ({x}, {y}) não pertence a nenhum monitor conectado."

        if self.mode == FenceMode.CUSTOM_BOUNDS:
            if not self.custom_bounds:
                return False, "Bloqueado: Limites personalizados não foram definidos."
            bx1, by1, bx2, by2 = self.custom_bounds
            if bx1 <= x < bx2 and by1 <= y < by2:
                return True, "Permitido dentro dos limites da janela autorizada."
            return False, f"Bloqueado: Coordenada ({x}, {y}) fora dos limites da janela autorizada."

        # Modos baseados no monitor ativo (PRIMARY_ONLY, MONITOR_INDEX, MONITOR_NAME)
        active_m = self.get_active_monitor()
        if not active_m:
            return False, "Bloqueado: Nenhum monitor ativo configurado."

        if active_m.contains(x, y):
            return True, f"Permitido dentro do monitor ativo '{active_m.name}'."

        return (
            False,
            f"Bloqueado: Coordenada ({x}, {y}) está fora do monitor autorizado '{active_m.name}' "
            f"(Limites: X={active_m.x}..{active_m.max_x}, Y={active_m.y}..{active_m.max_y}).",
        )

    def convert_relative_point(self, rel_x: float, rel_y: float) -> tuple[int, int]:
        """Converte coordenadas relativas [0.0, 1.0] para coordenadas absolutas no monitor ativo."""
        active_m = self.get_active_monitor()
        if not active_m:
            return int(rel_x * 1920), int(rel_y * 1080)
        return active_m.relative_to_absolute(rel_x, rel_y)

    def get_status_summary(self) -> dict[str, Any]:
        """Resumo estruturado para o HUD e configurações."""
        active_m = self.get_active_monitor()
        return {
            "mode": self.mode.value,
            "active_monitor": active_m.name if active_m else "Nenhum",
            "active_monitor_index": self.active_monitor_index,
            "total_monitors": len(self._monitors),
            "monitors": [
                {"index": m.index, "name": m.name, "primary": m.is_primary, "width": m.width, "height": m.height}
                for m in self._monitors
            ],
            "red_zones_count": len(self.red_zones),
            "kill_switch_active": self._emergency_stopped,
        }
