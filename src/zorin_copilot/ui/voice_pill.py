# Decisão de design: Janela flutuante minimalista em formato de pílula (Dynamic Island)
# para conversação de voz ao vivo. Mantém a janela principal oculta, permitindo interação
# multimodal por voz sem obstruir a área de trabalho ou o fluxo de trabalho do usuário.
#
# Upgrade (Dynamic Island v2): além do visualizer, a pílula agora consome 4 sinais adicionais
# que o GeminiLiveClient/LocalLiveVoiceClient já emitem mas eram descartados —
# on_tool_executed (chip transitório), on_video_state_change (REC dot pulsante),
# on_privacy_state_change (borda tingida), on_window_focus_change (tooltip de contexto) — e
# adiciona posicionamento inteligente (snap ao monitor sob o cursor), auto-hide ocioso,
# menu de descoberta via clique direito, atalhos de teclado (Esc, Space, Ctrl+M, i, p, ?)
# e push-to-interrupt (Space hold). A paleta de cores por estado é resolvida em CSS
# (5 probe labels invisíveis) e cacheada em Python, permitindo re-tematar a pílula via
# ~/.config/zorin-copilot/user.css sem editar este arquivo.

"""Janela flutuante compacta (Dynamic Island / Pílula) para voz ao vivo do Zorin Copilot."""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Any, Callable, Optional

import gi

from .gi_versions import require_gtk4  # noqa: E402
require_gtk4()
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from ..ai.live import GeminiLiveClient, LiveVoiceState
from ..ai.local_voice import LocalLiveVoiceClient
from ..core.config import CopilotConfig
from .style import setup_glass_pill_window

logger = logging.getLogger("zorin_copilot.ui.voice_pill")


# Fallback de cores (RGB 0..1) por estado — usado quando get_color() não devolve nada
# (ex.: display ausente em testes headless). Os valores coincidem com as regras .pill-probe-*
# em zorin-copilot.css; se você mudar um, mude o outro.
_FALLBACK_PALETTE: dict[LiveVoiceState, tuple[float, float, float]] = {
    LiveVoiceState.LISTENING:  (0.08, 0.65, 0.94),
    LiveVoiceState.SPEAKING:   (0.57, 0.25, 0.67),
    LiveVoiceState.THINKING:   (0.96, 0.76, 0.07),
    LiveVoiceState.EXECUTING:  (0.18, 0.76, 0.49),
    LiveVoiceState.ERROR:      (0.88, 0.20, 0.20),
    LiveVoiceState.CONNECTING: (0.08, 0.65, 0.94),
}

# Cantos suportados pelo place_smart(). A chave é o nome em CopilotConfig.pill_corner.
_CORNER_OFFSETS: dict[str, tuple[float, float]] = {
    # (x_frac, y_frac) — fração do monitor a partir do canto top-left
    "top-center":   (0.50, 0.04),
    "top-right":    (0.85, 0.04),
    "top-left":     (0.15, 0.04),
    "bottom-center": (0.50, 0.93),
}

# Tempo que a pílula fica ociosa antes de esmaecer (override do config se existir)
_IDLE_FADE_OPACITY = 0.30


def _session_type_is_wayland() -> bool:
    """Heurística: o usuário está em Wayland? Em Wayland, Window.move() é no-op."""
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


class VoicePillWindow(Gtk.Window):
    """Janela flutuante compacta e elegante estilo Dynamic Island para interação por voz."""

    def __init__(
        self,
        application: Gtk.Application,
        live_client: GeminiLiveClient | LocalLiveVoiceClient,
        on_expand: Optional[Callable[[], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
    ):
        super().__init__(application=application)
        self.live_client = live_client
        self.on_expand_cb = on_expand
        self.on_close_cb = on_close

        self.set_title("Zorin Copilot Live")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(390, 58)

        # ---- Estado interno ----
        self.config = CopilotConfig.load()
        self._alive: bool = True

        # Ancoragem Wayland. Em layer-shell a superfície é posicionada por âncoras
        # e margens, e precisa ser configurada antes de a janela ser realizada —
        # daí estar aqui e não no place_smart(). Sem suporte, seguimos com move().
        self._layer_shell: bool = self._init_layer_shell()
        self._target_audio_level: float = 0.0
        self._smooth_audio_level: float = 0.0
        self._prev_target_level: float = 0.0
        self._anim_time: float = 0.0
        self._last_frame_time_us: int = 0
        self._tick_id: int = 0
        self._current_state = LiveVoiceState.CONNECTING
        self._palette: dict[LiveVoiceState, tuple[float, float, float]] = {}
        self._palette_resolved: bool = False

        # Sessão / chip / fade
        self._session_start_monotonic: float = 0.0
        self._session_timer_id: int = 0
        self._chip_restore_id: int = 0
        self._chip_active: bool = False
        self._chip_saved_text: str = ""
        self._chip_saved_classes: list[str] = []
        self._last_activity_monotonic: float = time.monotonic()
        self._is_idle_faded: bool = False

        # PTT (push-to-interrupt)
        self._ptt_active: bool = False
        self._ptt_was_active: bool = False

        # Indicadores
        self._video_streaming: bool = False
        self._privacy_shielded: bool = False

        self._build_ui()
        setup_glass_pill_window(self)
        self._connect_client_events()
        self._install_global_handlers()

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------
    @property
    def _pill_margin(self) -> int:
        try:
            return max(0, int(getattr(self.config, "pill_margin", 12)))
        except Exception:
            return 12

    def _init_layer_shell(self) -> bool:
        """Tenta ancorar via wlr-layer-shell. Falso em X11 ou sem a biblioteca."""
        if not _session_type_is_wayland():
            return False
        try:
            from .layer_shell import anchor_corner

            corner = str(getattr(self.config, "pill_corner", "top-center"))
            return anchor_corner(self, corner, self._pill_margin)
        except Exception as exc:
            logger.debug("layer-shell indisponível, usando posicionamento do WM: %s", exc)
            return False

    def _build_ui(self) -> None:
        handle = Gtk.WindowHandle()
        self.set_child(handle)

        # Overlay externo: recebe popover e REC dot sem alterar o footprint horizontal
        self._overlay = Gtk.Overlay()
        handle.set_child(self._overlay)

        # Container principal (vai ser alvo de classes de estado e opacidade)
        self.container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.container.add_css_class("voice-pill-container")
        self.container.set_valign(Gtk.Align.CENTER)
        self.container.set_halign(Gtk.Align.CENTER)
        self.container.set_margin_top(6)
        self.container.set_margin_bottom(6)
        self.container.set_margin_start(8)
        self.container.set_margin_end(8)
        self._overlay.set_child(self.container)

        # 1. Avatar / Status Orb do Agente
        self.avatar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.avatar_box.add_css_class("voice-pill-avatar")
        self.avatar_box.set_valign(Gtk.Align.CENTER)
        self.avatar_box.set_halign(Gtk.Align.CENTER)

        # Overlay sobre o avatar para o REC dot
        avatar_overlay = Gtk.Overlay()
        avatar_overlay.set_child(self.avatar_box)
        self.avatar_box.set_size_request(32, 32)
        self.rec_dot = Gtk.Label(label="●")
        self.rec_dot.add_css_class("pill-rec-dot")
        self.rec_dot.set_halign(Gtk.Align.END)
        self.rec_dot.set_valign(Gtk.Align.START)
        self.rec_dot.set_visible(False)
        avatar_overlay.add_overlay(self.rec_dot)
        self.container.append(avatar_overlay)

        self.avatar_icon = Gtk.Image.new_from_icon_name("starred-symbolic")
        self.avatar_icon.set_pixel_size(16)
        self.avatar_box.append(self.avatar_icon)

        # 2. Textos: título (com timer embutido) + status (com chip)
        text_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_vbox.set_valign(Gtk.Align.CENTER)
        text_vbox.set_hexpand(True)

        # Title row: "Copilot Live"  +  separador  +  "0:42" (timer)
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        title_row.set_valign(Gtk.Align.CENTER)
        self.title_lbl = Gtk.Label(label="<b>Copilot Live</b>", use_markup=True, xalign=0)
        self.title_lbl.add_css_class("caption")
        title_row.append(self.title_lbl)

        self.title_sep = Gtk.Label(label="·")
        self.title_sep.add_css_class("caption")
        self.title_sep.add_css_class("voice-pill-title-sep")
        title_row.append(self.title_sep)
        self.title_sep.set_visible(False)

        self.timer_lbl = Gtk.Label(label="0:00", xalign=0)
        self.timer_lbl.add_css_class("caption")
        self.timer_lbl.add_css_class("dim-label")
        title_row.append(self.timer_lbl)
        self.timer_lbl.set_visible(False)

        text_vbox.append(title_row)

        self.status_lbl = Gtk.Label(label="Iniciando...", xalign=0)
        self.status_lbl.add_css_class("caption")
        self.status_lbl.add_css_class("dim-label")
        self.status_lbl.add_css_class("voice-pill-status")
        self.status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_lbl.set_max_width_chars(28)
        text_vbox.append(self.status_lbl)
        self.container.append(text_vbox)

        # 3. Mini Visualizador de Áudio
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_content_width(80)
        self.drawing_area.set_content_height(26)
        self.drawing_area.set_valign(Gtk.Align.CENTER)
        self.drawing_area.set_draw_func(self._draw_wave_func)
        self._tick_id = self.drawing_area.add_tick_callback(self._on_visualizer_tick)
        self.container.append(self.drawing_area)

        # 4. Separador sutil
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_top(6)
        sep.set_margin_bottom(6)
        self.container.append(sep)

        # 5. Controles Rápidos
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        controls_box.set_valign(Gtk.Align.CENTER)

        self.mute_btn = Gtk.Button.new_from_icon_name("audio-input-microphone-symbolic")
        self.mute_btn.add_css_class("flat")
        self.mute_btn.add_css_class("circular")
        self.mute_btn.add_css_class("voice-pill-btn")
        self.mute_btn.set_tooltip_text("Mutar microfone (Ctrl+M)")
        self.mute_btn.connect("clicked", self._on_toggle_mute)
        controls_box.append(self.mute_btn)

        self.expand_btn = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic")
        self.expand_btn.add_css_class("flat")
        self.expand_btn.add_css_class("circular")
        self.expand_btn.add_css_class("voice-pill-btn")
        self.expand_btn.set_tooltip_text("Expandir para janela completa (?)")
        self.expand_btn.connect("clicked", self._on_expand_clicked)
        controls_box.append(self.expand_btn)

        self.close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.close_btn.add_css_class("flat")
        self.close_btn.add_css_class("circular")
        self.close_btn.add_css_class("voice-pill-btn")
        self.close_btn.set_tooltip_text("Encerrar conversa de voz (Esc)")
        self.close_btn.connect("clicked", self._on_close_clicked)
        controls_box.append(self.close_btn)

        self.container.append(controls_box)

        # ---- Probe labels invisíveis (5) — fonte de cor por estado via get_color() ----
        self._probes: dict[LiveVoiceState, Gtk.Widget] = {}
        for state, css_suffix in (
            (LiveVoiceState.LISTENING, "listening"),
            (LiveVoiceState.SPEAKING, "speaking"),
            (LiveVoiceState.THINKING, "thinking"),
            (LiveVoiceState.EXECUTING, "executing"),
            (LiveVoiceState.ERROR, "error"),
        ):
            probe = Gtk.Label(label="·")
            probe.add_css_class("pill-probe")
            probe.add_css_class(f"pill-probe-{css_suffix}")
            # Não usar set_visible(False): zera o estilo resolvido. set_opacity(0) preserva.
            probe.set_opacity(0.0)
            probe.set_size_request(1, 1)
            self._overlay.add_overlay(probe)
            self._probes[state] = probe

        # ---- Atalhos de teclado ----
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        key_ctrl.connect("key-released", self._on_key_released)
        self.add_controller(key_ctrl)

        # Movimento do mouse sobre a pílula restaura opacidade total
        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", self._on_motion_enter)
        self.add_controller(motion)

        # Reset de PTT se a janela perder foco durante o hold
        self.connect("notify::is-active", self._on_notify_is_active)

        # Clique direito abre o menu de descoberta
        right_click = Gtk.GestureClick.new()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_right_click)
        self.add_controller(right_click)

    # ------------------------------------------------------------------
    # Conexão de callbacks do cliente
    # ------------------------------------------------------------------
    def _connect_client_events(self) -> None:
        self.live_client.on_state_change = lambda state, msg: GLib.idle_add(
            self._ui_on_state_change, state, msg
        )
        self.live_client.on_audio_level = lambda lvl: GLib.idle_add(self._ui_on_audio_level, lvl)
        existing_error_cb = self.live_client.on_error

        def _forward_error_pill(err: str) -> None:
            if existing_error_cb:
                existing_error_cb(err)
            GLib.idle_add(self._ui_on_error, err)

        self.live_client.on_error = _forward_error_pill
        # Sinais novos (antes descartados):
        if callable(getattr(self.live_client, "on_tool_executed", None)):
            self.live_client.on_tool_executed = (
                lambda name, msg, ok: GLib.idle_add(self._ui_on_tool_executed, name, msg, ok)
            )
        if callable(getattr(self.live_client, "on_video_state_change", None)):
            self.live_client.on_video_state_change = (
                lambda on: GLib.idle_add(self._ui_on_video_state_change, on)
            )
        if callable(getattr(self.live_client, "on_privacy_state_change", None)):
            self.live_client.on_privacy_state_change = (
                lambda shielded, label: GLib.idle_add(
                    self._ui_on_privacy_state_change, shielded, label
                )
            )
        if callable(getattr(self.live_client, "on_window_focus_change", None)):
            self.live_client.on_window_focus_change = (
                lambda app, title: GLib.idle_add(self._ui_on_window_focus_change, app, title)
            )

    def _install_global_handlers(self) -> None:
        """Handlers ligados ao ciclo de vida da janela (independem do live_client)."""
        # best-effort: pin/unpin com base na config
        try:
            self.set_keep_above(bool(getattr(self.config, "pill_pinned", True)))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers de estado
    # ------------------------------------------------------------------
    def _set_flag(self, css_class: str, on: bool) -> None:
        """Adiciona ou remove uma classe do container principal."""
        if on:
            self.container.add_css_class(css_class)
        else:
            self.container.remove_css_class(css_class)

    def _safe_remove_class(self, widget: Gtk.Widget, cls: str) -> None:
        try:
            widget.remove_css_class(cls)
        except Exception:
            pass

    def _bump_activity(self) -> None:
        self._last_activity_monotonic = time.monotonic()
        if self._is_idle_faded:
            self._set_idle(False)

    def _set_idle(self, idle: bool) -> None:
        if idle and not self._is_idle_faded:
            self.container.set_opacity(_IDLE_FADE_OPACITY)
            self._is_idle_faded = True
        elif not idle and self._is_idle_faded:
            self.container.set_opacity(1.0)
            self._is_idle_faded = False

    # ------------------------------------------------------------------
    # Resolução de paleta (CSS → Python)
    # ------------------------------------------------------------------
    def _resolve_theme_colors(self) -> None:
        """Lê as cores das probe labels (resolvidas pelo CSS) e popula self._palette.

        Falha silenciosa → fallback _FALLBACK_PALETTE. Idempotente.
        """
        if self._palette_resolved:
            return
        self._palette_resolved = True
        for state, probe in self._probes.items():
            try:
                color = probe.get_color()
                r = max(0.0, min(1.0, color.red))
                g = max(0.0, min(1.0, color.green))
                b = max(0.0, min(1.0, color.blue))
                # se o tema resolver tudo para preto, ainda é "válido" — mas queremos
                # sinalizar fallback. Só aceitamos o valor se não for idêntico ao anterior.
                if (r, g, b) == (0.0, 0.0, 0.0):
                    raise ValueError("resolved color is pure black — likely unresolved")
                self._palette[state] = (r, g, b)
            except Exception:
                # fallback (incl. testes sem display, GTK não-inicializado)
                self._palette[state] = _FALLBACK_PALETTE.get(
                    state, _FALLBACK_PALETTE[LiveVoiceState.LISTENING]
                )
        # Conectando usa a cor de LISTENING como fallback universal
        self._palette.setdefault(LiveVoiceState.CONNECTING, self._palette[LiveVoiceState.LISTENING])
        self._palette.setdefault(LiveVoiceState.DISCONNECTED, self._palette[LiveVoiceState.LISTENING])

    def _color_for(self, state: LiveVoiceState) -> tuple[float, float, float]:
        if not self._palette_resolved:
            self._resolve_theme_colors()
        return self._palette.get(state, _FALLBACK_PALETTE[LiveVoiceState.LISTENING])

    # ------------------------------------------------------------------
    # Handlers de estado / sinal (chamados via GLib.idle_add)
    # ------------------------------------------------------------------
    def _ui_on_state_change(self, state: LiveVoiceState, msg: str) -> bool:
        if not self._alive:
            return GLib.SOURCE_REMOVE
        self._current_state = state
        self._bump_activity()

        # Map estado → (status, icon) — preserve EXACTAMENTE os textos que os testes pinam
        if state == LiveVoiceState.CONNECTING:
            status_text = "Conectando..."
            icon = "network-transmit-receive-symbolic"
        elif state == LiveVoiceState.LISTENING:
            status_text = "Ouvindo você..."
            icon = "starred-symbolic"
        elif state == LiveVoiceState.SPEAKING:
            status_text = "Falando..."
            icon = "audio-speakers-symbolic"
        elif state == LiveVoiceState.EXECUTING:
            status_text = "Executando..."
            icon = "system-run-symbolic"
        elif state == LiveVoiceState.THINKING:
            status_text = "Pensando..."
            icon = "system-search-symbolic"
        elif state == LiveVoiceState.ERROR:
            status_text = "Erro de áudio"
            icon = "dialog-error-symbolic"
        elif state == LiveVoiceState.DISCONNECTED:
            status_text = "Desconectado"
            icon = "starred-symbolic"
        else:
            status_text = ""
            icon = "starred-symbolic"

        if not self._chip_active:
            self.status_lbl.set_text(status_text)
        self.avatar_icon.set_from_icon_name(icon)

        # Timer de sessão
        if state in (LiveVoiceState.CONNECTED, LiveVoiceState.LISTENING):
            if self._session_start_monotonic == 0.0:
                self._session_start_monotonic = time.monotonic()
            if self._session_timer_id == 0:
                self._session_timer_id = GLib.timeout_add_seconds(1, self._update_session_timer)
                self.title_sep.set_visible(True)
                self.timer_lbl.set_visible(True)
        elif state in (LiveVoiceState.ERROR, LiveVoiceState.DISCONNECTED):
            if self._session_timer_id != 0:
                GLib.source_remove(self._session_timer_id)
                self._session_timer_id = 0
            self._session_start_monotonic = 0.0
            self.title_sep.set_visible(False)
            self.timer_lbl.set_visible(False)

        self.drawing_area.queue_draw()
        return GLib.SOURCE_REMOVE

    def _ui_on_audio_level(self, level: float) -> bool:
        if not self._alive:
            return GLib.SOURCE_REMOVE
        self._target_audio_level = max(0.0, min(1.0, level))
        if self._target_audio_level > 0.05:
            self._bump_activity()
        return GLib.SOURCE_REMOVE

    def _ui_on_transcript(self, role: str, text: str) -> bool:
        if not self._alive:
            return GLib.SOURCE_REMOVE
        self._bump_activity()
        if role == "model" and text:
            snippet = text.strip().replace("\n", " ")
            if len(snippet) > 22:
                snippet = snippet[:22] + "..."
            if not self._chip_active:
                self.status_lbl.set_text(snippet)
        elif role == "user":
            if not self._chip_active:
                self.status_lbl.set_text("Ouvindo você...")
        return GLib.SOURCE_REMOVE

    def _ui_on_error(self, err: str) -> bool:
        if not self._alive:
            return GLib.SOURCE_REMOVE
        logger.warning("VoicePillWindow recebeu erro: %s", err)
        if not self._chip_active:
            self.status_lbl.set_text("Erro de conexão")
        return GLib.SOURCE_REMOVE

    # --- Sinais novos ---

    def _ui_on_tool_executed(self, name: str, msg: str, success: bool) -> bool:
        if not self._alive:
            return GLib.SOURCE_REMOVE
        self._bump_activity()
        # Limpa nome: pega o que vier após último '.' ou usa o próprio nome
        short = name.split(".")[-1].replace("_", " ").strip()
        if msg:
            # msg pode ser longa; cap em ~22 chars igual ao snippet
            short_msg = msg.strip().replace("\n", " ")
            if len(short_msg) > 18:
                short_msg = short_msg[:18] + "..."
            label_text = f"{'✓' if success else '✗'} {short}: {short_msg}"
        else:
            label_text = f"{'✓' if success else '✗'} {short}"
        self._show_chip(label_text, ok=success)
        return GLib.SOURCE_REMOVE

    def _ui_on_video_state_change(self, on: bool) -> bool:
        if not self._alive:
            return GLib.SOURCE_REMOVE
        self._video_streaming = bool(on)
        self.rec_dot.set_visible(self._video_streaming)
        return GLib.SOURCE_REMOVE

    def _ui_on_privacy_state_change(self, shielded: bool, label: str) -> bool:
        if not self._alive:
            return GLib.SOURCE_REMOVE
        self._privacy_shielded = bool(shielded)
        self._set_flag("pill-privacy", self._privacy_shielded)
        if shielded and label:
            self.avatar_box.set_tooltip_text(f"Privacidade: {label}")
        else:
            self.avatar_box.set_tooltip_text(None)
        return GLib.SOURCE_REMOVE

    def _ui_on_window_focus_change(self, app: str, title: str) -> bool:
        if not self._alive:
            return GLib.SOURCE_REMOVE
        ctx = (app or "").strip()
        if title:
            ctx = f"{ctx} — {title.strip()}" if ctx else title.strip()
        if len(ctx) > 40:
            ctx = ctx[:39] + "…"
        if ctx:
            self.title_lbl.set_tooltip_text(f"Contexto: {ctx}")
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Chip de tool + timer de sessão
    # ------------------------------------------------------------------
    def _show_chip(self, text: str, ok: bool) -> None:
        """Mostra um chip transitório no status_lbl. Restaura após 3s."""
        if self._chip_restore_id:
            try:
                GLib.source_remove(self._chip_restore_id)
            except Exception:
                pass
            self._chip_restore_id = 0
        if not self._chip_active:
            # Primeiro chip: salvar estado atual
            self._chip_saved_text = self.status_lbl.get_text()
            self._chip_saved_classes = [
                c for c in self.container.get_css_classes() if c.startswith("pill-chip-")
            ]
        # Limpa classes antigas de chip
        for cls in self._chip_saved_classes:
            self._set_flag(cls, False)
        # Aplica nova classe + texto
        self._set_flag("pill-chip-ok" if ok else "pill-chip-fail", True)
        self.status_lbl.set_text(text)
        self._chip_active = True
        self._chip_restore_id = GLib.timeout_add_seconds(3, self._restore_status)

    def _restore_status(self) -> bool:
        self._chip_restore_id = 0
        for cls in ("pill-chip-ok", "pill-chip-fail"):
            self._set_flag(cls, False)
        for cls in self._chip_saved_classes:
            self._set_flag(cls, True)
        self.status_lbl.set_text(self._chip_saved_text or "")
        self._chip_active = False
        return GLib.SOURCE_REMOVE

    def _update_session_timer(self) -> bool:
        if not self._alive or self._session_start_monotonic == 0.0:
            return GLib.SOURCE_REMOVE
        elapsed = int(time.monotonic() - self._session_start_monotonic)
        mins, secs = divmod(max(0, elapsed), 60)
        self.timer_lbl.set_text(f"{mins}:{secs:02d}")
        return GLib.SOURCE_CONTINUE

    # ------------------------------------------------------------------
    # Ações dos botões / menu
    # ------------------------------------------------------------------
    def _on_toggle_mute(self, _btn: Gtk.Button) -> None:
        if hasattr(self.live_client, "toggle_mute"):
            is_muted = self.live_client.toggle_mute()
            if is_muted:
                self.mute_btn.set_icon_name("audio-input-microphone-muted-symbolic")
                self.mute_btn.add_css_class("destructive-action")
                self._set_flag("pill-muted", True)
                if not self._chip_active:
                    self.status_lbl.set_text("Microfone mudo")
            else:
                self.mute_btn.set_icon_name("audio-input-microphone-symbolic")
                self._safe_remove_class(self.mute_btn, "destructive-action")
                self._set_flag("pill-muted", False)
                if not self._chip_active:
                    self.status_lbl.set_text("Ouvindo você...")

    def _on_expand_clicked(self, _btn: Gtk.Button) -> None:
        self.set_visible(False)
        if self.on_expand_cb:
            self.on_expand_cb()

    def _on_close_clicked(self, _btn: Gtk.Button | None = None) -> None:
        self.set_visible(False)
        if self.on_close_cb:
            self.on_close_cb()

    def _on_interrupt_action(self, *_args) -> None:
        """Interrompe o agente (barge-in) — usado por menu, tecla 'i' e long-press."""
        if callable(getattr(self.live_client, "interrupt", None)):
            try:
                self.live_client.interrupt()
            except Exception as exc:  # pragma: no cover
                logger.warning("Falha ao interromper: %s", exc)
        elif callable(getattr(self.live_client, "stop", None)):
            # Fallback conservador: parar e deixar o caller reiniciar
            try:
                self.live_client.stop()
            except Exception:  # pragma: no cover
                pass
        # Visual: pulso rápido
        self._set_flag("pill-interrupting", True)
        GLib.timeout_add_seconds(1, lambda: (self._set_flag("pill-interrupting", False), GLib.SOURCE_REMOVE)[1])

    def _on_set_pinned(self, *_args) -> None:
        new_val = not bool(getattr(self.config, "pill_pinned", True))
        self.config.pill_pinned = new_val
        try:
            self.config.save()
        except Exception as exc:  # pragma: no cover
            logger.warning("Não foi possível salvar pill_pinned: %s", exc)
        try:
            self.set_keep_above(new_val)
        except Exception:
            pass

    def _on_set_corner(self, corner: str, *_args) -> None:
        if corner not in _CORNER_OFFSETS:
            return
        self.config.pill_corner = corner
        try:
            self.config.save()
        except Exception as exc:  # pragma: no cover
            logger.warning("Não foi possível salvar pill_corner: %s", exc)
        self.place_smart()

    # ------------------------------------------------------------------
    # Atalhos de teclado
    # ------------------------------------------------------------------
    def _on_key_pressed(
        self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType
    ) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if keyval == Gdk.KEY_Escape:
            self._on_close_clicked()
            return True
        if keyval == Gdk.KEY_question:
            # Shift+/ em layout US — mas Gdk já entrega KEY_question em ambos layouts
            self._on_expand_clicked(self.expand_btn)
            return True
        if ctrl and keyval in (Gdk.KEY_m, Gdk.KEY_M):
            self._on_toggle_mute(self.mute_btn)
            return True
        if keyval in (Gdk.KEY_i, Gdk.KEY_I):
            self._on_interrupt_action()
            return True
        if keyval in (Gdk.KEY_p, Gdk.KEY_P) and not ctrl:
            self._on_set_pinned()
            return True
        if keyval == Gdk.KEY_space:
            if not self._ptt_active:
                self._ptt_active = True
                self._ptt_was_active = True
                self._on_interrupt_action()
            return True
        return False

    def _on_key_released(
        self, _controller, keyval: int, _keycode: int, _state: Gdk.ModifierType
    ) -> bool:
        if keyval == Gdk.KEY_space and self._ptt_active:
            self._ptt_active = False
        return False

    def _on_notify_is_active(self, *_args) -> None:
        # Reset PTT se a janela perder foco durante o hold
        if not self.is_active() and self._ptt_active:
            self._ptt_active = False

    def _on_motion_enter(self, *_args) -> None:
        self._bump_activity()

    # ------------------------------------------------------------------
    # Menu de descoberta (clique direito)
    # ------------------------------------------------------------------
    def _build_popover_menu(self) -> Gtk.PopoverMenu:
        menu = Gio.Menu()

        menu.append("Interromper agente (i)", "pill.interrupt")
        menu.append("Pressionar-para-falar (segure Space)", "pill.ptt-info")
        menu.append("Mutar / Desmutar (Ctrl+M)", "pill.toggle-mute")

        # Submenu de posição
        corner_menu = Gio.Menu()
        for corner in _CORNER_OFFSETS:
            corner_menu.append(corner, f"pill.corner::{corner}")
        menu.append_submenu("Posição", corner_menu)

        pinned_label = (
            "Desfixar acima de tudo"
            if bool(getattr(self.config, "pill_pinned", True))
            else "Fixar acima de tudo"
        )
        menu.append(pinned_label, "pill.toggle-pinned")
        menu.append("Expandir (?)", "pill.expand")
        menu.append("Encerrar (Esc)", "pill.close")

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.add_css_class("pill-popover")

        action_group = Gio.SimpleActionGroup()
        for name, cb in (
            ("interrupt", self._on_interrupt_action),
            ("toggle-mute", lambda *_: self._on_toggle_mute(self.mute_btn)),
            ("toggle-pinned", self._on_set_pinned),
            ("expand", lambda *_: self._on_expand_clicked(self.expand_btn)),
            ("close", lambda *_: self._on_close_clicked()),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            action_group.add_action(act)
        # PTT info — ação no-op (apenas informativa)
        ptt_info = Gio.SimpleAction.new("ptt-info", None)
        ptt_info.connect("activate", lambda *_: None)
        action_group.add_action(ptt_info)
        # Cantos
        for corner in _CORNER_OFFSETS:
            act = Gio.SimpleAction.new(f"corner::{corner}", None)
            act.connect("activate", self._on_set_corner, corner)
            action_group.add_action(act)

        self.insert_action_group("pill", action_group)
        return popover

    def _on_right_click(self, _gesture, _n_press: int, x: float, y: float) -> None:
        popover = self._build_popover_menu()
        popover.set_parent(self)
        # Posicionar relativo à pílula
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    # ------------------------------------------------------------------
    # Visualizer tick + desenho
    # ------------------------------------------------------------------
    def _on_visualizer_tick(self, _area: Gtk.DrawingArea, frame_clock: Gdk.FrameClock) -> bool:
        if not self._alive:
            return GLib.SOURCE_REMOVE
        curr_time_us = frame_clock.get_frame_time()
        if self._last_frame_time_us > 0:
            dt = max(0.001, min(0.1, (curr_time_us - self._last_frame_time_us) / 1_000_000.0))
        else:
            dt = 0.016
        self._last_frame_time_us = curr_time_us

        alpha = min(1.0, dt * 14.0)
        self._smooth_audio_level += alpha * (self._target_audio_level - self._smooth_audio_level)
        self._anim_time += dt
        self._prev_target_level = self._target_audio_level

        # Pulse do REC dot (4s breath — presença sutil, não sirene)
        if self._video_streaming and self.rec_dot.get_visible():
            pulse = 0.85 + 0.15 * math.sin(self._anim_time * math.pi / 2.0)
            self.rec_dot.set_opacity(pulse)

        # Auto-hide ocioso
        self._tick_idle_check()

        self.drawing_area.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _tick_idle_check(self) -> None:
        try:
            timeout_sec = int(getattr(self.config, "pill_idle_timeout_sec", 8))
        except Exception:
            timeout_sec = 8
        if timeout_sec <= 0:
            self._set_idle(False)
            return
        # Estados "ocupados" não esmaecem
        if self._current_state in (LiveVoiceState.SPEAKING, LiveVoiceState.EXECUTING):
            self._set_idle(False)
            return
        idle_for = time.monotonic() - self._last_activity_monotonic
        if idle_for >= timeout_sec:
            self._set_idle(True)
        else:
            self._set_idle(False)

    # ------------------------------------------------------------------
    # Visualizer (dispatch por estilo)
    # ------------------------------------------------------------------
    def _draw_wave_func(self, _area: Gtk.DrawingArea, cr: Any, width: int, height: int) -> None:
        cr.save()
        cy = height / 2.0
        lvl = self._smooth_audio_level
        t = self._anim_time
        pad_x = 4.0
        eff_w = max(10.0, width - 2.0 * pad_x)

        # Estado mudo → linha estática vermelha
        if hasattr(self.live_client, "is_muted") and self.live_client.is_muted():
            cr.set_line_width(1.6)
            cr.set_source_rgba(0.88, 0.20, 0.20, 0.55)
            cr.move_to(pad_x, cy)
            cr.line_to(pad_x + eff_w, cy)
            cr.stroke()
            cr.restore()
            return

        # Cor via paleta resolvida do CSS
        r, g, b = self._color_for(self._current_state)

        style = getattr(self.config, "voice_visualizer_style", "waves") or "waves"
        if style == "bars":
            self._draw_bars(cr, pad_x, eff_w, cy, height, lvl, t, r, g, b)
        elif style == "matrix":
            self._draw_matrix(cr, pad_x, eff_w, cy, height, lvl, t, r, g, b)
        elif style == "orb":
            self._draw_orb(cr, pad_x, eff_w, cy, height, lvl, t, r, g, b)
        else:
            self._draw_waves(cr, pad_x, eff_w, cy, height, lvl, t, r, g, b)
        cr.restore()

    def _draw_waves(
        self, cr, pad_x, eff_w, cy, height, lvl, t, r, g, b
    ) -> None:
        num_layers = 3
        total_amp = max(2.8, (height * 0.44) * (0.22 + 0.78 * lvl))
        for k in range(num_layers):
            norm_k = (k - (num_layers - 1) / 2.0)
            phase = t * 4.2 + norm_k * 0.85
            layer_amp = total_amp * (1.0 - 0.22 * abs(norm_k))
            alpha = max(0.25, (0.5 + 0.5 * lvl) * (1.0 - abs(norm_k) * 0.32))
            cr.set_line_width(1.7 - 0.3 * abs(norm_k))
            cr.set_source_rgba(r, g, b, alpha)
            cr.move_to(pad_x, cy)
            step_px = 2
            for step_i in range(0, int(eff_w) + 1, step_px):
                x = pad_x + step_i
                norm_x = step_i / eff_w
                env = math.sin(math.pi * norm_x) ** 1.5
                w = 0.75 * math.sin(2.0 * math.pi * norm_x + phase) + 0.25 * math.sin(
                    4.0 * math.pi * norm_x - phase * 0.7
                )
                y = cy + env * layer_amp * w
                cr.line_to(x, y)
            cr.stroke()

    def _draw_bars(
        self, cr, pad_x, eff_w, cy, height, lvl, t, r, g, b
    ) -> None:
        n_bars = 16
        bar_w = max(1.5, eff_w / (n_bars * 1.6))
        gap = (eff_w - bar_w * n_bars) / max(1, n_bars - 1)
        max_amp = height * 0.42
        cr.set_source_rgba(r, g, b, 0.85)
        for i in range(n_bars):
            # fase por barra + envelope gaussiano no centro
            x = pad_x + i * (bar_w + gap)
            local_phase = t * 6.0 + i * 0.6
            wave = 0.5 + 0.5 * math.sin(local_phase)
            env = math.exp(-((i - n_bars / 2) ** 2) / (2 * (n_bars / 3) ** 2))
            amp = max(2.0, max_amp * (0.25 + 0.75 * lvl) * env * (0.4 + 0.6 * wave))
            cr.rectangle(x, cy - amp, bar_w, 2 * amp)
            cr.fill()

    def _draw_matrix(
        self, cr, pad_x, eff_w, cy, height, lvl, t, r, g, b
    ) -> None:
        n_cols = 12
        col_w = eff_w / n_cols
        for i in range(n_cols):
            x = pad_x + i * col_w
            speed = 0.4 + 0.6 * ((i * 13) % 7) / 7
            # falling dots per column, posições derivadas do tempo
            n_dots = 4
            for d in range(n_dots):
                offset = (t * speed + d / n_dots) % 1.0
                y = cy + (offset - 0.5) * height * 0.7
                alpha = max(0.05, 1.0 - offset) * (0.4 + 0.6 * lvl)
                cr.set_source_rgba(r, g, b, alpha)
                cr.rectangle(x + col_w * 0.25, y, col_w * 0.5, 1.5)
                cr.fill()

    def _draw_orb(
        self, cr, pad_x, eff_w, cy, height, lvl, t, r, g, b
    ) -> None:
        cx = pad_x + eff_w / 2
        radius = min(eff_w, height) * 0.30 * (0.6 + 0.4 * lvl)
        cr.set_source_rgba(r, g, b, 0.15)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(r, g, b, 0.85)
        cr.set_line_width(1.6)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        # 3 dots orbitando
        for i in range(3):
            ang = t * 2.5 + i * (2 * math.pi / 3)
            dx = cx + radius * math.cos(ang)
            dy = cy + radius * math.sin(ang)
            cr.set_source_rgba(r, g, b, 0.9)
            cr.arc(dx, dy, 1.8, 0, 2 * math.pi)
            cr.fill()

    # ------------------------------------------------------------------
    # Posicionamento inteligente
    # ------------------------------------------------------------------
    def place_smart(self) -> None:
        """Posiciona a pílula no canto configurado do monitor apropriado.

        Em Wayland, Gtk.Window.move() é no-op — o compositor posiciona. Nesse caso
        a chamada é silenciosamente ignorada e o resultado prático é o que o WM
        decidir. Em X11 a posição é persistida entre sessões via save_geometry().
        """
        try:
            from ..core.fence import ScreenFenceManager  # import tardio p/ evitar ciclos
            monitors = ScreenFenceManager.detect_monitors()
        except Exception as exc:  # pragma: no cover
            logger.debug("place_smart: detect_monitors falhou: %s", exc)
            return

        if not monitors:
            return

        idx_cfg = int(getattr(self.config, "pill_monitor_idx", -1))
        if idx_cfg >= 0 and idx_cfg < len(monitors):
            monitor = monitors[idx_cfg]
        else:
            # Auto: monitor sob o cursor
            monitor = self._monitor_under_pointer(monitors)

        corner = str(getattr(self.config, "pill_corner", "top-center"))
        if corner not in _CORNER_OFFSETS:
            corner = "top-center"

        # Com layer-shell o compositor posiciona por âncora/margem: é o único
        # caminho que funciona em wlroots, e torna move() desnecessário.
        if self._layer_shell:
            try:
                from .layer_shell import reanchor

                reanchor(self, corner, self._pill_margin)
            except Exception as exc:  # pragma: no cover
                logger.debug("reanchor falhou: %s", exc)
            return

        xf, yf = _CORNER_OFFSETS[corner]

        # Geometria do monitor (tolerante a ausências)
        try:
            geom = monitor.geometry  # (x, y, width, height)
            mx, my, mw, mh = int(geom[0]), int(geom[1]), int(geom[2]), int(geom[3])
        except Exception:
            mx, my, mw, mh = 0, 0, 1920, 1080

        win_w, win_h = self.get_default_size()
        # Calcular coordenadas absolutas no monitor escolhido
        x = int(mx + (mw - win_w) * xf)
        y = int(my + (mh - win_h) * yf)
        # Limites (não sair do monitor)
        x = max(mx, min(mx + mw - win_w, x))
        y = max(my, min(my + mh - win_h, y))

        if _session_type_is_wayland():
            logger.debug("place_smart: Wayland detectado, move() é no-op")
            return

        try:
            self.move(x, y)
        except Exception as exc:  # pragma: no cover
            logger.debug("place_smart: move falhou: %s", exc)

    @staticmethod
    def _primary_monitor(monitors) -> Any:
        """Monitor primário declarado pelo GDK; na dúvida, o primeiro."""
        for m in monitors:
            try:
                if m.is_primary:
                    return m
            except Exception:
                continue
        return monitors[0]

    @staticmethod
    def _match_gdk_monitor(gdk_mon: Any, monitors) -> Any | None:
        """Cruza um GdkMonitor com os MonitorInfo vindos do ScreenFenceManager.

        Os dois lados não compartilham índice — o GDK reordena conforme os
        displays são conectados — então o casamento é por modelo/descrição e,
        em último caso, por geometria exata.
        """
        try:
            model = (gdk_mon.get_model() or "").strip()
            description = (gdk_mon.get_description() or "").strip()
            geom = gdk_mon.get_geometry()
            gx, gy = int(geom.x), int(geom.y)
            gw, gh = int(geom.width), int(geom.height)
        except Exception:
            return None

        for m in monitors:
            if model and m.model and model == m.model.strip():
                return m
            if description and m.name and description == m.name.strip():
                return m

        for m in monitors:
            if (m.x, m.y, m.width, m.height) == (gx, gy, gw, gh):
                return m
        return None

    def _monitor_under_pointer(self, monitors) -> Any:
        """Identifica o monitor sob o cursor. Fallback: monitor primário.

        No Wayland não existe perguntar a posição *global* do ponteiro — o
        protocolo não expõe isso de propósito. O que dá é pedir ao seat sobre
        qual superfície o ponteiro está e, a partir dela, pedir o monitor ao
        display. Em headless (sem seat) a resposta é None e caímos no primário,
        que é o comportamento correto.
        """
        try:
            display = Gdk.Display.get_default()
            if not display:
                return self._primary_monitor(monitors)
            seat = display.get_default_seat()
            if not seat:
                return self._primary_monitor(monitors)
            pointer = seat.get_pointer()
            if not pointer:
                return self._primary_monitor(monitors)

            surface, _x, _y = pointer.get_surface_at_position()
            if surface is None:
                return self._primary_monitor(monitors)
            gdk_mon = display.get_monitor_at_surface(surface)
            if gdk_mon is None:
                return self._primary_monitor(monitors)

            match = self._match_gdk_monitor(gdk_mon, monitors)
            if match is not None:
                return match
        except AttributeError:
            # get_surface_at_position não existe em GDK mais antigo (ex.: 4.6).
            logger.debug("_monitor_under_pointer: GDK sem get_surface_at_position")
        except Exception as exc:
            logger.debug("_monitor_under_pointer: %s", exc)

        return self._primary_monitor(monitors)

    def save_geometry(self) -> None:
        """Persiste a posição atual (X11) e o canto escolhido. No-op em Wayland."""
        if _session_type_is_wayland():
            return
        # Em X11 poderíamos ler self.get_position() e persistir — mas a posição pós-drag
        # já é relativa ao canto escolhido. O canto já está salvo. Nada mais a fazer.
        return
