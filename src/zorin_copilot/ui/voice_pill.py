# Decisão de design: Janela flutuante minimalista em formato de pílula (Dynamic Island)
# para conversação de voz ao vivo. Mantém a janela principal oculta, permitindo interação
# multimodal por voz sem obstruir a área de trabalho ou o fluxo de trabalho do usuário.

"""Janela flutuante compacta (Dynamic Island / Pílula) para voz ao vivo do Zorin Copilot."""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ..ai.live import GeminiLiveClient, LiveVoiceState
from ..ai.local_voice import LocalLiveVoiceClient
from ..core.config import CopilotConfig
from .style import setup_glass_pill_window

logger = logging.getLogger("zorin_copilot.ui.voice_pill")


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

        # Configuração visual e animação
        self.config = CopilotConfig.load()
        self._target_audio_level: float = 0.0
        self._smooth_audio_level: float = 0.0
        self._anim_time: float = 0.0
        self._last_frame_time_us: int = 0
        self._tick_id: int = 0
        self._current_state = LiveVoiceState.CONNECTING

        self._build_ui()
        setup_glass_pill_window(self)
        self._connect_client_events()

    def _build_ui(self) -> None:
        # WindowHandle permite arrastar a janela flutuante pela tela livremente
        handle = Gtk.WindowHandle()
        self.set_child(handle)

        container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        container.add_css_class("voice-pill-container")
        container.set_valign(Gtk.Align.CENTER)
        container.set_halign(Gtk.Align.CENTER)
        container.set_margin_top(6)
        container.set_margin_bottom(6)
        container.set_margin_start(8)
        container.set_margin_end(8)
        handle.set_child(container)

        # 1. Avatar / Status Orb do Agente (Identidade do Copilot)
        self.avatar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.avatar_box.add_css_class("voice-pill-avatar")
        self.avatar_box.set_valign(Gtk.Align.CENTER)
        self.avatar_box.set_halign(Gtk.Align.CENTER)

        self.avatar_icon = Gtk.Image.new_from_icon_name("system-help-symbolic")
        self.avatar_icon.set_pixel_size(16)
        self.avatar_box.append(self.avatar_icon)
        container.append(self.avatar_box)

        # 2. Textos de Status e Feedback
        text_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_vbox.set_valign(Gtk.Align.CENTER)
        text_vbox.set_hexpand(True)

        self.title_lbl = Gtk.Label(label="<b>Copilot Live</b>", use_markup=True, xalign=0)
        self.title_lbl.add_css_class("caption")
        text_vbox.append(self.title_lbl)

        self.status_lbl = Gtk.Label(label="Iniciando...", xalign=0)
        self.status_lbl.add_css_class("caption")
        self.status_lbl.add_css_class("dim-label")
        self.status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_lbl.set_max_width_chars(28)
        text_vbox.append(self.status_lbl)
        container.append(text_vbox)

        # 3. Mini Visualizador de Áudio (DrawingArea compacta com ondas fluidas)
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_content_width(80)
        self.drawing_area.set_content_height(26)
        self.drawing_area.set_valign(Gtk.Align.CENTER)
        self.drawing_area.set_draw_func(self._draw_wave_func)
        self._tick_id = self.drawing_area.add_tick_callback(self._on_visualizer_tick)
        container.append(self.drawing_area)

        # 4. Separador sutil
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_top(6)
        sep.set_margin_bottom(6)
        container.append(sep)

        # 5. Controles Rápidos (Mutar, Expandir, Encerrar)
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        controls_box.set_valign(Gtk.Align.CENTER)

        # Botão Mudo
        self.mute_btn = Gtk.Button.new_from_icon_name("audio-input-microphone-symbolic")
        self.mute_btn.add_css_class("flat")
        self.mute_btn.add_css_class("circular")
        self.mute_btn.add_css_class("voice-pill-btn")
        self.mute_btn.set_tooltip_text("Mutar microfone")
        self.mute_btn.connect("clicked", self._on_toggle_mute)
        controls_box.append(self.mute_btn)

        # Botão Expandir
        self.expand_btn = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic")
        self.expand_btn.add_css_class("flat")
        self.expand_btn.add_css_class("circular")
        self.expand_btn.add_css_class("voice-pill-btn")
        self.expand_btn.set_tooltip_text("Expandir para janela completa (chat e visão)")
        self.expand_btn.connect("clicked", self._on_expand_clicked)
        controls_box.append(self.expand_btn)

        # Botão Encerrar
        self.close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.close_btn.add_css_class("flat")
        self.close_btn.add_css_class("circular")
        self.close_btn.add_css_class("voice-pill-btn")
        self.close_btn.set_tooltip_text("Encerrar conversa de voz (Esc)")
        self.close_btn.connect("clicked", self._on_close_clicked)
        controls_box.append(self.close_btn)

        container.append(controls_box)

        # Atalho de Teclado: Escape fecha a pílula
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _connect_client_events(self) -> None:
        """Conecta os callbacks do cliente de voz de forma segura na thread da UI."""
        self.live_client.on_state_change = lambda state, msg: GLib.idle_add(self._ui_on_state_change, state, msg)
        self.live_client.on_audio_level = lambda lvl: GLib.idle_add(self._ui_on_audio_level, lvl)
        self.live_client.on_transcript = lambda role, text: GLib.idle_add(self._ui_on_transcript, role, text)
        self.live_client.on_error = lambda err: GLib.idle_add(self._ui_on_error, err)

    def _ui_on_state_change(self, state: LiveVoiceState, msg: str) -> bool:
        self._current_state = state
        if state == LiveVoiceState.CONNECTING:
            self.status_lbl.set_text("Conectando...")
            self.avatar_icon.set_from_icon_name("network-transmit-receive-symbolic")
        elif state == LiveVoiceState.LISTENING:
            self.status_lbl.set_text("Ouvindo você...")
            self.avatar_icon.set_from_icon_name("system-help-symbolic")
        elif state == LiveVoiceState.SPEAKING:
            self.status_lbl.set_text("Falando...")
            self.avatar_icon.set_from_icon_name("audio-speakers-symbolic")
        elif state == LiveVoiceState.EXECUTING:
            self.status_lbl.set_text("Executando...")
            self.avatar_icon.set_from_icon_name("system-run-symbolic")
        elif state == LiveVoiceState.THINKING:
            self.status_lbl.set_text("Pensando...")
            self.avatar_icon.set_from_icon_name("system-search-symbolic")
        elif state == LiveVoiceState.ERROR:
            self.status_lbl.set_text("Erro de áudio")
            self.avatar_icon.set_from_icon_name("dialog-error-symbolic")
        elif state == LiveVoiceState.DISCONNECTED:
            self.status_lbl.set_text("Desconectado")
        self.drawing_area.queue_draw()
        return GLib.SOURCE_REMOVE

    def _ui_on_audio_level(self, level: float) -> bool:
        self._target_audio_level = max(0.0, min(1.0, level))
        return GLib.SOURCE_REMOVE

    def _ui_on_transcript(self, role: str, text: str) -> bool:
        if role == "model" and text:
            snippet = text.strip().replace("\n", " ")
            if len(snippet) > 22:
                snippet = snippet[:22] + "..."
            self.status_lbl.set_text(snippet)
        elif role == "user":
            self.status_lbl.set_text("Ouvindo você...")
        return GLib.SOURCE_REMOVE

    def _ui_on_error(self, err: str) -> bool:
        logger.warning(f"VoicePillWindow recebeu erro: {err}")
        self.status_lbl.set_text("Erro de conexão")
        return GLib.SOURCE_REMOVE

    def _on_toggle_mute(self, _btn: Gtk.Button) -> None:
        if hasattr(self.live_client, "toggle_mute"):
            is_muted = self.live_client.toggle_mute()
            if is_muted:
                self.mute_btn.set_icon_name("audio-input-microphone-muted-symbolic")
                self.mute_btn.add_css_class("destructive-action")
                self.status_lbl.set_text("Microfone mudo")
            else:
                self.mute_btn.set_icon_name("audio-input-microphone-symbolic")
                self.mute_btn.remove_css_class("destructive-action")
                self.status_lbl.set_text("Ouvindo você...")

    def _on_expand_clicked(self, _btn: Gtk.Button) -> None:
        self.set_visible(False)
        if self.on_expand_cb:
            self.on_expand_cb()

    def _on_close_clicked(self, _btn: Gtk.Button | None = None) -> None:
        self.set_visible(False)
        if self.on_close_cb:
            self.on_close_cb()

    def _on_key_pressed(self, _controller, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._on_close_clicked()
            return True
        return False

    def _on_visualizer_tick(self, _area: Gtk.DrawingArea, frame_clock: Gdk.FrameClock) -> bool:
        curr_time_us = frame_clock.get_frame_time()
        if self._last_frame_time_us > 0:
            dt = max(0.001, min(0.1, (curr_time_us - self._last_frame_time_us) / 1_000_000.0))
        else:
            dt = 0.016
        self._last_frame_time_us = curr_time_us

        alpha = min(1.0, dt * 14.0)
        self._smooth_audio_level += alpha * (self._target_audio_level - self._smooth_audio_level)
        self._anim_time += dt

        self.drawing_area.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw_wave_func(self, _area: Gtk.DrawingArea, cr: Any, width: int, height: int) -> None:
        """Desenha ondas sonoras fluidas e compactas em tempo real com Cairo."""
        cr.save()
        cy = height / 2.0
        lvl = self._smooth_audio_level
        t = self._anim_time
        pad_x = 4.0
        eff_w = max(10.0, width - 2.0 * pad_x)

        # Se estiver mudo, exibe linha horizontal estática de repouso
        if hasattr(self.live_client, "is_muted") and self.live_client.is_muted():
            cr.set_line_width(1.6)
            cr.set_source_rgba(0.88, 0.20, 0.20, 0.55)
            cr.move_to(pad_x, cy)
            cr.line_to(pad_x + eff_w, cy)
            cr.stroke()
            cr.restore()
            return

        # Cor dinâmica sincronizada com o estado
        if self._current_state == LiveVoiceState.SPEAKING:
            r, g, b = 0.57, 0.25, 0.67  # Roxo
        elif self._current_state == LiveVoiceState.THINKING:
            r, g, b = 0.96, 0.76, 0.07  # Amarelo
        elif self._current_state == LiveVoiceState.EXECUTING:
            r, g, b = 0.18, 0.76, 0.49  # Verde
        else:
            r, g, b = 0.08, 0.65, 0.94  # Azul Zorin

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
                w = 0.75 * math.sin(2.0 * math.pi * norm_x + phase) + 0.25 * math.sin(4.0 * math.pi * norm_x - phase * 0.7)
                y = cy + env * layer_amp * w
                cr.line_to(x, y)

            cr.stroke()

        cr.restore()
