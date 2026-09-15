# Decisão de design: overlay do cursor fantasma da IA (Ghost Cursor / Computer-Use).
# A superfície cobre a tela inteira em Wayland através do protocolo wlr-layer-shell
# (camada 'overlay', exclusive zone 0, sem foco de teclado).
#
# REGRA CRÍTICA DE USABILIDADE:
# A janela é 100% transpassável a eventos de ponteiro (click-through):
#   1. widget.set_can_target(False)
#   2. Gdk.Surface.set_input_region(cairo.Region()) no realize.
# Isso garante que a presença do cursor da IA JAMAIS bloqueia ou rouba cliques do usuário real.
#
# EXECUÇÃO SUAVE (60 FPS):
# O movimento do cursor utiliza interpolação cúbica com amortecimento (ease-out cubic)
# através do add_tick_callback do GTK4. Ao ficar inativo, o tick callback é pausado,
# resultando em ZERO consumo de CPU (0%) em repouso.

"""Overlay do Cursor Fantasma da IA (Visualização de Operador e Computer-Use)."""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Fallback headless / sem display
_GI_AVAILABLE = False
_GTK_AVAILABLE = False

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo
    import cairo
    _GI_AVAILABLE = True
    _GTK_AVAILABLE = True
except (ImportError, ValueError) as exc:
    logger.debug("GTK4 / PangoCairo indisponível para Ghost Cursor: %s", exc)
    GLib = None  # type: ignore
    Gtk = None  # type: ignore
    Gdk = None  # type: ignore
    cairo = None  # type: ignore
    Pango = None  # type: ignore
    PangoCairo = None  # type: ignore


def _ease_out_cubic(t: float) -> float:
    """Função de amortecimento cúbico para movimento natural."""
    clamped = max(0.0, min(1.0, float(t)))
    return 1.0 - (1.0 - clamped) ** 3


def _parse_hex_color(hex_code: str, fallback: tuple[float, float, float] = (0.0, 0.82, 1.0)) -> tuple[float, float, float]:
    """Converte string '#RRGGBB' em tupla de floats (0.0..1.0)."""
    try:
        cleaned = hex_code.strip().lstrip("#")
        if len(cleaned) == 6:
            r = int(cleaned[0:2], 16) / 255.0
            g = int(cleaned[2:4], 16) / 255.0
            b = int(cleaned[4:6], 16) / 255.0
            return (r, g, b)
    except Exception:
        pass
    return fallback


@dataclass
class ClickRipple:
    """Onda de choque circular que expande e dissipa após um clique."""
    x: float
    y: float
    start_time: float
    duration: float = 0.38  # segundos
    max_radius: float = 44.0
    color: tuple[float, float, float] = (0.0, 0.82, 1.0)
    line_width: float = 3.0


class GhostCursorWindow:
    """Superfície de camada fullscreen transparente onde o cursor da IA é desenhado."""

    def __init__(self, application: Any | None = None, primary_color: str = "#00d2ff"):
        if not _GTK_AVAILABLE:
            raise RuntimeError("GTK4 não está disponível no ambiente.")

        # Criação da janela GTK4
        self.win = Gtk.Window(application=application) if application else Gtk.Window()
        self.win.set_title("Zorin Copilot Ghost Cursor")
        self.win.set_decorated(False)
        self.win.set_resizable(False)
        self.win.set_can_target(False)
        self.win.add_css_class("ghost-cursor-window")

        self.primary_color = _parse_hex_color(primary_color)
        self._blur_namespace = "zorin-copilot-ghost-cursor"

        # Tenta ancorar via gtk4-layer-shell
        self._init_layer_shell()

        # Configura transparência e desativação total de captura de ponteiro
        self.win.connect("realize", self._on_realize)

        # Estado de coordenadas e interpolação
        self.current_x: float = 960.0
        self.current_y: float = 540.0
        self.start_x: float = 960.0
        self.start_y: float = 540.0
        self.target_x: float = 960.0
        self.target_y: float = 540.0
        self.move_start_time: float = 0.0
        self.move_duration: float = 0.22
        self.is_moving: bool = False

        # Opacidade do cursor e da etiqueta
        self.cursor_opacity: float = 0.0
        self.target_cursor_opacity: float = 0.0
        self.label_text: str = ""
        self.label_opacity: float = 0.0
        self.target_label_opacity: float = 0.0

        self.last_action_time: float = 0.0
        self.idle_timeout_sec: float = 2.5
        self.ripples: list[ClickRipple] = []

        # Tick callback e drawing area
        self._tick_id: int = 0
        self._drawing_area = Gtk.DrawingArea()
        self._drawing_area.set_can_target(False)
        self._drawing_area.set_draw_func(self._on_draw)
        self.win.set_child(self._drawing_area)

    def _init_layer_shell(self) -> bool:
        """Ancora como overlay de camada fullscreen em compositores wlroots/Wayland."""
        try:
            from . import layer_shell

            if not layer_shell.is_supported():
                return False
            ok = layer_shell.anchor_fullscreen(self.win, layer="overlay")
            if ok:
                layer_shell.set_namespace(self.win, self._blur_namespace)
            return ok
        except Exception as exc:
            logger.debug("layer-shell indisponível para ghost cursor: %s", exc)
            return False

    def _on_realize(self, widget: Any) -> None:
        """Define região de entrada vazia: a superfície fica 100% click-through."""
        try:
            surface = widget.get_surface()
            if surface and cairo:
                # Região vazia = passa todos os eventos de mouse/teclado para as janelas abaixo
                empty_region = cairo.Region()
                surface.set_input_region(empty_region)
                logger.debug("Ghost Cursor configurado como click-through (input_region vazia).")
        except Exception as exc:
            logger.warning("Falha ao configurar click-through no Ghost Cursor: %s", exc)

    def present(self) -> None:
        """Exibe a janela overlay."""
        if hasattr(self.win, "present"):
            self.win.present()
        else:
            self.win.show()

    def start_animation_loop(self) -> None:
        """Ativa o tick callback de 60fps se ainda não estiver rodando."""
        if self._tick_id == 0 and hasattr(self._drawing_area, "add_tick_callback"):
            self._tick_id = self._drawing_area.add_tick_callback(self._on_tick)

    def _on_tick(self, widget: Any, frame_clock: Any) -> bool:
        """Loop de animação por frame (GTK4 frame clock)."""
        now = time.monotonic()
        needs_redraw = False

        # 1. Movimento do cursor
        if self.is_moving:
            elapsed = now - self.move_start_time
            if self.move_duration <= 0.001 or elapsed >= self.move_duration:
                self.current_x = self.target_x
                self.current_y = self.target_y
                self.is_moving = False
            else:
                progress = elapsed / self.move_duration
                ease = _ease_out_cubic(progress)
                self.current_x = self.start_x + (self.target_x - self.start_x) * ease
                self.current_y = self.start_y + (self.target_y - self.start_y) * ease
            needs_redraw = True

        # 2. Fade in/out do cursor
        if abs(self.cursor_opacity - self.target_cursor_opacity) > 0.01:
            step = 0.12 if self.cursor_opacity < self.target_cursor_opacity else 0.06
            if self.cursor_opacity < self.target_cursor_opacity:
                self.cursor_opacity = min(self.target_cursor_opacity, self.cursor_opacity + step)
            else:
                self.cursor_opacity = max(self.target_cursor_opacity, self.cursor_opacity - step)
            needs_redraw = True
        else:
            self.cursor_opacity = self.target_cursor_opacity

        # 3. Fade in/out da etiqueta de ação
        if abs(self.label_opacity - self.target_label_opacity) > 0.01:
            step = 0.15 if self.label_opacity < self.target_label_opacity else 0.08
            if self.label_opacity < self.target_label_opacity:
                self.label_opacity = min(self.target_label_opacity, self.label_opacity + step)
            else:
                self.label_opacity = max(self.target_label_opacity, self.label_opacity - step)
            needs_redraw = True
        else:
            self.label_opacity = self.target_label_opacity

        # 4. Atualiza ondas de choque (ripples)
        if self.ripples:
            active_ripples = []
            for r in self.ripples:
                if now - r.start_time < r.duration:
                    active_ripples.append(r)
            if len(active_ripples) != len(self.ripples) or active_ripples:
                needs_redraw = True
            self.ripples = active_ripples

        # 5. Ociosidade: fade out suave quando passar o tempo limite
        if now - self.last_action_time > self.idle_timeout_sec and self.target_cursor_opacity > 0.0:
            self.target_cursor_opacity = 0.0
            self.target_label_opacity = 0.0

        if needs_redraw:
            widget.queue_draw()

        # Se tudo estiver parado e transparente, pausa o tick callback para economizar CPU
        if (
            not self.is_moving
            and not self.ripples
            and self.cursor_opacity <= 0.01
            and self.target_cursor_opacity == 0.0
            and self.label_opacity <= 0.01
        ):
            self._tick_id = 0
            return False  # GLib remove o callback

        return True  # Continua no próximo frame

    def _on_draw(self, area: Any, cr: Any, width: int, height: int, user_data: Any = None) -> None:
        """Renderiza o cursor neon, anéis de clique e a pílula de intenção."""
        now = time.monotonic()

        # 1. Desenha as ondas de clique (Ripples)
        for r in self.ripples:
            age = now - r.start_time
            if age < 0 or age >= r.duration:
                continue
            prog = age / r.duration
            ease = _ease_out_cubic(prog)
            rad = 8.0 + (r.max_radius - 8.0) * ease
            alpha = (1.0 - prog) * 0.85
            cr.save()
            cr.set_source_rgba(r.color[0], r.color[1], r.color[2], alpha)
            cr.set_line_width(max(1.0, r.line_width * (1.0 - prog)))
            cr.arc(r.x, r.y, rad, 0, 2 * math.pi)
            cr.stroke()
            cr.restore()

        # Se o cursor estiver invisível, termina
        if self.cursor_opacity <= 0.01:
            return

        cx = self.current_x
        cy = self.current_y

        # 2. Desenha o Ponteiro Neon da IA
        cr.save()
        cr.translate(cx, cy)

        # Desenho do cursor em vetor estilizado
        # Ponta em (0, 0)
        cr.move_to(0, 0)
        cr.line_to(0, 22)
        cr.line_to(5.5, 16.5)
        cr.line_to(11, 26)
        cr.line_to(14.5, 24.5)
        cr.line_to(9, 15)
        cr.line_to(16.5, 15)
        cr.close_path()

        # Contorno neon brilhante
        col = self.primary_color
        cr.set_source_rgba(col[0], col[1], col[2], 0.95 * self.cursor_opacity)
        cr.set_line_width(2.4)
        cr.stroke_preserve()

        # Preenchimento escuro com leve transparência
        cr.set_source_rgba(0.06, 0.09, 0.18, 0.92 * self.cursor_opacity)
        cr.fill()

        # Retículo / centelha de pulso no vértice do ponteiro (0, 0)
        pulse = 3.5 + 1.2 * math.sin(now * 7.0)
        cr.arc(0, 0, pulse, 0, 2 * math.pi)
        cr.set_source_rgba(col[0], col[1], col[2], 0.90 * self.cursor_opacity)
        cr.fill()

        cr.restore()

        # 3. Desenha o crachá de ação da IA (Floating Action Pill)
        if self.label_opacity > 0.01 and self.label_text and Pango and PangoCairo:
            cr.save()
            layout = PangoCairo.create_layout(cr)
            layout.set_text(self.label_text)
            desc = Pango.FontDescription.from_string("Sans Bold 9.5")
            layout.set_font_description(desc)

            _ink, logical = layout.get_pixel_extents()
            text_w = logical.width
            text_h = logical.height

            pill_w = text_w + 28
            pill_h = max(26, text_h + 10)

            # Posiciona ao lado direito-inferior do cursor, ajustando se sair da tela
            bx = cx + 22
            by = cy + 16
            if bx + pill_w > width - 12:
                bx = max(12, cx - pill_w - 8)
            if by + pill_h > height - 12:
                by = max(12, cy - pill_h - 8)

            # Fundo da pílula (Dark Glass + Neon Border)
            radius = pill_h / 2.0
            cr.new_sub_path()
            cr.arc(bx + pill_w - radius, by + radius, radius, -math.pi / 2, math.pi / 2)
            cr.arc(bx + radius, by + pill_h - radius, radius, math.pi / 2, 3 * math.pi / 2)
            cr.close_path()

            # Preenchimento escuro suave
            cr.set_source_rgba(0.08, 0.11, 0.20, 0.88 * self.label_opacity)
            cr.fill_preserve()

            # Borda neon
            cr.set_source_rgba(col[0], col[1], col[2], 0.75 * self.label_opacity)
            cr.set_line_width(1.3)
            cr.stroke()

            # Ponto luminoso de status dentro da pílula
            dot_x = bx + 10
            dot_y = by + pill_h / 2.0
            cr.arc(dot_x, dot_y, 3.2, 0, 2 * math.pi)
            cr.set_source_rgba(col[0], col[1], col[2], 0.95 * self.label_opacity)
            cr.fill()

            # Texto da ação
            cr.move_to(bx + 18, by + (pill_h - text_h) / 2.0)
            cr.set_source_rgba(0.95, 0.97, 1.0, 0.95 * self.label_opacity)
            PangoCairo.show_layout(cr, layout)

            cr.restore()


class GhostCursorOverlay:
    """Gerenciador centralizado e thread-safe do cursor fantasma do agente."""

    _instance: GhostCursorOverlay | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.enabled: bool = True
        self.speed_ms: int = 250
        self.primary_color: str = "#00d2ff"
        self._window: GhostCursorWindow | None = None
        self._initialized: bool = False
        self._app: Any | None = None

        # Histórico em memória para testes e inspeção headless
        self.last_target: tuple[float, float] = (0.0, 0.0)
        self.last_label: str = ""
        self.last_action_kind: str = ""
        self.ripple_count: int = 0

        self._load_config()

    @classmethod
    def get_default(cls) -> GhostCursorOverlay:
        """Devolve a instância única do gerenciador."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = GhostCursorOverlay()
            return cls._instance

    def _load_config(self) -> None:
        try:
            from ..core.config import CopilotConfig
            cfg = CopilotConfig.load()
            self.enabled = bool(getattr(cfg, "ghost_cursor_enabled", True))
            self.primary_color = str(getattr(cfg, "ghost_cursor_color", "#00d2ff"))
            self.speed_ms = int(getattr(cfg, "ghost_cursor_anim_speed_ms", 250))
        except Exception as exc:
            logger.debug("Falha ao carregar configurações do Ghost Cursor: %s", exc)

    def initialize(self, application: Any | None = None) -> bool:
        """Inicializa a superfície gráfica (deve ser chamado na thread principal do GTK)."""
        if self._initialized and self._window is not None:
            return True

        self._app = application
        if not _GTK_AVAILABLE:
            logger.info("Ghost Cursor operando em modo headless/diagnóstico (GTK indisponível).")
            self._initialized = True
            return False

        try:
            self._window = GhostCursorWindow(application=application, primary_color=self.primary_color)
            self._window.present()
            self._initialized = True
            logger.info("Ghost Cursor Overlay inicializado com sucesso.")
            return True
        except Exception as exc:
            logger.warning("Não foi possível criar a janela do Ghost Cursor: %s", exc)
            self._initialized = True
            return False

    @property
    def is_active(self) -> bool:
        return self.enabled and self._initialized and (self._window is not None)

    # ------------------------------------------------------------------ #
    # API Pública Thread-Safe
    # ------------------------------------------------------------------ #

    def point_to(self, x: float, y: float, duration_ms: int | None = None, label: str = "") -> None:
        """Move o cursor da IA para a coordenada (x, y) de forma suave."""
        dur = duration_ms if duration_ms is not None else self.speed_ms
        self.last_target = (float(x), float(y))
        self.last_label = label
        self.last_action_kind = "move"

        if not self.enabled:
            return

        if _GTK_AVAILABLE and GLib:
            GLib.idle_add(self._main_point_to, float(x), float(y), float(dur) / 1000.0, label)

    def click_at(
        self,
        x: float,
        y: float,
        button: str = "left",
        double: bool = False,
        label: str = "",
        wait_glide: bool = False,
    ) -> None:
        """Desloca o cursor para (x, y), pulsa ondas de clique e exibe crachá da ação."""
        self.last_target = (float(x), float(y))
        lbl = label or (f"Duplo clique ({button})" if double else f"Clicando ({button})")
        self.last_label = lbl
        self.last_action_kind = "double_click" if double else "click"
        self.ripple_count += 2 if double else 1

        if not self.enabled:
            return

        dur_sec = float(self.speed_ms) / 1000.0

        if _GTK_AVAILABLE and GLib:
            GLib.idle_add(self._main_click_at, float(x), float(y), button, double, lbl, dur_sec)

        # Se chamado a partir de uma thread secundária e wait_glide estiver ativo,
        # faz uma pausa breve para permitir que o cursor alcance a posição antes do clique físico
        if wait_glide and threading.current_thread() is not threading.main_thread():
            time.sleep(min(0.20, dur_sec * 0.8))

    def type_at(self, x: float, y: float, text: str = "", label: str = "") -> None:
        """Move o cursor até o campo e sinaliza digitação iminente."""
        lbl = label or (f"Digitando: {text[:22]}..." if text else "Digitando...")
        self.point_to(x, y, label=lbl)
        self.last_action_kind = "type"

    def show_action(self, label: str, x: float | None = None, y: float | None = None) -> None:
        """Atualiza a etiqueta com o objetivo atual da IA."""
        self.last_label = label
        if not self.enabled:
            return

        if _GTK_AVAILABLE and GLib:
            GLib.idle_add(self._main_show_action, label, x, y)

    def hide(self) -> None:
        """Oculta o cursor fantasma e remove os rótulos."""
        self.last_label = ""
        if _GTK_AVAILABLE and GLib:
            GLib.idle_add(self._main_hide)

    def get_state(self) -> dict[str, Any]:
        """Inspeciona o estado atual do cursor (para testes e telemetria)."""
        w = self._window
        if w is None:
            return {
                "active": False,
                "current": self.last_target,
                "target": self.last_target,
                "label": self.last_label,
                "opacity": 0.0,
                "action_kind": self.last_action_kind,
                "ripple_count": self.ripple_count,
            }
        return {
            "active": True,
            "current": (w.current_x, w.current_y),
            "target": (w.target_x, w.target_y),
            "label": w.label_text,
            "opacity": w.cursor_opacity,
            "label_opacity": w.label_opacity,
            "is_moving": w.is_moving,
            "ripples": len(w.ripples),
            "action_kind": self.last_action_kind,
            "ripple_count": self.ripple_count,
        }

    # ------------------------------------------------------------------ #
    # Execução na Thread Principal (Main Loop do GTK)
    # ------------------------------------------------------------------ #

    def _ensure_window(self) -> GhostCursorWindow | None:
        if self._window is None and _GTK_AVAILABLE:
            self.initialize(self._app)
        return self._window

    def _main_point_to(self, x: float, y: float, duration_sec: float, label: str) -> None:
        win = self._ensure_window()
        if win is None:
            return

        now = time.monotonic()
        win.start_x = win.current_x
        win.start_y = win.current_y
        win.target_x = x
        win.target_y = y
        win.move_start_time = now
        win.move_duration = max(0.05, duration_sec)
        win.is_moving = True

        win.target_cursor_opacity = 1.0
        if label:
            win.label_text = label
            win.target_label_opacity = 1.0

        win.last_action_time = now
        win.start_animation_loop()

    def _main_click_at(
        self,
        x: float,
        y: float,
        button: str,
        double: bool,
        label: str,
        duration_sec: float,
    ) -> None:
        win = self._ensure_window()
        if win is None:
            return

        now = time.monotonic()
        # 1. Ajusta alvo e movimento
        win.start_x = win.current_x
        win.start_y = win.current_y
        win.target_x = x
        win.target_y = y
        win.move_start_time = now
        win.move_duration = max(0.05, duration_sec)
        win.is_moving = True

        win.target_cursor_opacity = 1.0
        if label:
            win.label_text = label
            win.target_label_opacity = 1.0

        win.last_action_time = now

        # 2. Cor do ripple por botão
        ripple_col = win.primary_color
        if button.lower() in ("right", "direito"):
            ripple_col = (1.0, 0.75, 0.2)  # Âmbar para botão direito
        elif button.lower() in ("middle", "meio"):
            ripple_col = (0.7, 0.4, 1.0)  # Púrpura para botão do meio

        # 3. Adiciona ripples
        win.ripples.append(ClickRipple(x=x, y=y, start_time=now, color=ripple_col))
        if double:
            # Segunda onda ligeiramente defasada
            win.ripples.append(
                ClickRipple(x=x, y=y, start_time=now + 0.12, color=ripple_col, max_radius=52.0)
            )

        win.start_animation_loop()

    def _main_show_action(self, label: str, x: float | None, y: float | None) -> None:
        win = self._ensure_window()
        if win is None:
            return

        now = time.monotonic()
        if x is not None and y is not None:
            win.current_x = float(x)
            win.current_y = float(y)
            win.target_x = float(x)
            win.target_y = float(y)
            win.is_moving = False

        win.label_text = label
        win.target_label_opacity = 1.0
        win.target_cursor_opacity = 1.0
        win.last_action_time = now
        win.start_animation_loop()

    def _main_hide(self) -> None:
        win = self._ensure_window()
        if win is None:
            return

        win.target_cursor_opacity = 0.0
        win.target_label_opacity = 0.0
        win.last_action_time = 0.0
        win.start_animation_loop()
