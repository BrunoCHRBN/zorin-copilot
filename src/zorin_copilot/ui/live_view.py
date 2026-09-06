# Decisão de design: Interface imersiva de voz ao vivo com Glassmorphism, visualizador dinâmico de áudio
# e feedback instantâneo de comandos de sistema executados durante a conversação.

"""Componente de interface gráfica em GTK4 para o chat de voz ao vivo (Gemini Live)."""

from __future__ import annotations

import math
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ..ai.live import GeminiLiveClient, LiveVoiceState


class LiveVoiceWidget(Gtk.Box):
    """Widget de conversação por voz ao vivo com visualizador de áudio e feedback de ações."""

    def __init__(
        self,
        live_client: GeminiLiveClient,
        on_close: Callable[[], None] | None = None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.live_client = live_client
        self.on_close_cb = on_close

        self.set_margin_top(16)
        self.set_margin_bottom(16)
        self.set_margin_start(16)
        self.set_margin_end(16)

        self._audio_level: float = 0.0
        self._build_ui()
        self._connect_client_events()

    def _build_ui(self) -> None:
        # Card principal com Glassmorphism
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.add_css_class("card")
        card.add_css_class("glass-card")
        card.set_margin_top(4)
        card.set_margin_bottom(4)
        card.set_margin_start(8)
        card.set_margin_end(8)

        # 1. Header do Modo Ao Vivo
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_box.set_margin_top(14)
        header_box.set_margin_start(16)
        header_box.set_margin_end(16)

        self.status_dot = Gtk.Label(label="●", use_markup=True)
        self.status_dot.add_css_class("status-dot")
        header_box.append(self.status_dot)

        self.status_lbl = Gtk.Label(label="<b>Zorin Copilot Live</b> • Conectando...", use_markup=True, xalign=0)
        self.status_lbl.set_hexpand(True)
        header_box.append(self.status_lbl)

        self.video_badge = Gtk.Label(label="<span foreground='#e01b24'><b>● TELA AO VIVO (1 FPS)</b></span>", use_markup=True)
        self.video_badge.add_css_class("caption")
        self.video_badge.set_visible(False)
        header_box.append(self.video_badge)

        close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close_btn.set_tooltip_text("Encerrar conversa de voz")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("circular")
        close_btn.connect("clicked", lambda _: self._on_end_call())
        header_box.append(close_btn)

        card.append(header_box)

        # 2. Visualizador Dinâmico de Áudio (DrawingArea)
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_content_width(200)
        self.drawing_area.set_content_height(100)
        self.drawing_area.set_draw_func(self._draw_audio_visualizer)
        card.append(self.drawing_area)

        # 3. Banner de Ação em Tempo Real (Tool Call Pill)
        self.action_revealer = Gtk.Revealer()
        self.action_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.action_revealer.set_reveal_child(False)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_box.set_halign(Gtk.Align.CENTER)
        action_box.add_css_class("glass-pill")
        action_box.set_margin_start(16)
        action_box.set_margin_end(16)
        action_box.set_margin_top(2)
        action_box.set_margin_bottom(2)

        self.action_icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        self.action_icon.set_pixel_size(16)
        action_box.append(self.action_icon)

        self.action_label = Gtk.Label(label="", use_markup=True)
        self.action_label.add_css_class("caption")
        action_box.append(self.action_label)

        self.action_revealer.set_child(action_box)
        card.append(self.action_revealer)

        # 4. Transcrição / Subtítulo da conversa
        self.subtitle_lbl = Gtk.Label(label="Fale naturalmente com o assistente...", xalign=0.5)
        self.subtitle_lbl.add_css_class("dim-label")
        self.subtitle_lbl.set_wrap(True)
        self.subtitle_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.subtitle_lbl.set_margin_start(20)
        self.subtitle_lbl.set_margin_end(20)
        self.subtitle_lbl.set_margin_top(4)
        self.subtitle_lbl.set_margin_bottom(8)
        card.append(self.subtitle_lbl)

        # 5. Barra de Controles Inferior
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        controls_box.set_halign(Gtk.Align.CENTER)
        controls_box.set_margin_bottom(14)

        # Botão Mudo
        self.mute_btn = Gtk.Button()
        self.mute_btn.add_css_class("pill")
        self.mute_btn.add_css_class("glass-pill")
        self.mute_btn.set_tooltip_text("Mutar / Desmutar microfone")
        self.mute_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.mute_icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        self.mute_icon.set_pixel_size(14)
        self.mute_lbl = Gtk.Label(label="Mutar")
        self.mute_lbl.add_css_class("caption")
        self.mute_btn_box.append(self.mute_icon)
        self.mute_btn_box.append(self.mute_lbl)
        self.mute_btn.set_child(self.mute_btn_box)
        self.mute_btn.connect("clicked", self._on_toggle_mute)
        controls_box.append(self.mute_btn)

        # Botão Live Video (Streaming contínuo de tela com consentimento)
        self.video_btn = Gtk.Button()
        self.video_btn.add_css_class("pill")
        self.video_btn.add_css_class("glass-pill")
        self.video_btn.set_tooltip_text("Transmitir tela ao vivo continuamente (1 FPS) para o Copilot visualizar suas janelas enquanto conversam")
        video_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.video_icon = Gtk.Image.new_from_icon_name("video-display-symbolic")
        self.video_icon.set_pixel_size(14)
        self.video_lbl = Gtk.Label(label="Transmitir Tela")
        self.video_lbl.add_css_class("caption")
        video_btn_box.append(self.video_icon)
        video_btn_box.append(self.video_lbl)
        self.video_btn.set_child(video_btn_box)
        self.video_btn.connect("clicked", self._on_toggle_video)
        controls_box.append(self.video_btn)

        # Botão Enviar Snapshot da Tela (Foto única)
        self.screen_btn = Gtk.Button()
        self.screen_btn.add_css_class("pill")
        self.screen_btn.add_css_class("glass-pill")
        self.screen_btn.set_tooltip_text("Captura uma imagem instantânea da tela atual e envia para a IA")
        screen_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        screen_icon = Gtk.Image.new_from_icon_name("camera-photo-symbolic")
        screen_icon.set_pixel_size(14)
        screen_lbl = Gtk.Label(label="Snapshot")
        screen_lbl.add_css_class("caption")
        screen_btn_box.append(screen_icon)
        screen_btn_box.append(screen_lbl)
        self.screen_btn.set_child(screen_btn_box)
        self.screen_btn.connect("clicked", self._on_send_screen)
        controls_box.append(self.screen_btn)

        # Botão Encerrar
        end_btn = Gtk.Button(label="Encerrar")
        end_btn.add_css_class("destructive-action")
        end_btn.add_css_class("pill")
        end_btn.connect("clicked", lambda _: self._on_end_call())
        controls_box.append(end_btn)

        card.append(controls_box)
        self.append(card)

    def _connect_client_events(self) -> None:
        """Registra os callbacks do cliente de áudio para atualizar a UI de forma thread-safe."""
        self.live_client.on_state_change = lambda state, msg: GLib.idle_add(self._ui_on_state_change, state, msg)
        self.live_client.on_audio_level = lambda lvl: GLib.idle_add(self._ui_on_audio_level, lvl)
        self.live_client.on_tool_executed = lambda name, msg, ok: GLib.idle_add(self._ui_on_tool_executed, name, msg, ok)
        self.live_client.on_transcript = lambda role, text: GLib.idle_add(self._ui_on_transcript, role, text)
        self.live_client.on_error = lambda err: GLib.idle_add(self._ui_on_error, err)
        self.live_client.on_video_state_change = lambda active: GLib.idle_add(self._update_video_ui, active)

    def _ui_on_state_change(self, state: LiveVoiceState, msg: str) -> bool:
        if state == LiveVoiceState.CONNECTING:
            self.status_dot.set_markup("<span foreground='#e5a50a'>●</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Conectando...")
            self.subtitle_lbl.set_text("Estabelecendo conexão segura com Gemini 2.5 Live...")
        elif state == LiveVoiceState.LISTENING:
            self.status_dot.set_markup("<span foreground='#3584e4'>●</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Ouvindo você")
            self.subtitle_lbl.set_text("Fale naturalmente... o Copilot está ouvindo.")
        elif state == LiveVoiceState.SPEAKING:
            self.status_dot.set_markup("<span foreground='#9141ac'>●</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Falando...")
        elif state == LiveVoiceState.EXECUTING:
            self.status_dot.set_markup("<span foreground='#2ec27e'>●</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Executando no desktop...")
        elif state == LiveVoiceState.ERROR:
            self.status_dot.set_markup("<span foreground='#e01b24'>●</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Erro")
            self.subtitle_lbl.set_text(msg or "Erro de conexão de voz.")
        elif state == LiveVoiceState.DISCONNECTED:
            self.status_dot.set_markup("<span foreground='#77767b'>○</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Desconectado")
        self.drawing_area.queue_draw()
        return GLib.SOURCE_REMOVE

    def _ui_on_audio_level(self, level: float) -> bool:
        self._audio_level = level
        self.drawing_area.queue_draw()
        return GLib.SOURCE_REMOVE

    def _ui_on_tool_executed(self, name: str, message: str, success: bool) -> bool:
        icon_name = "emblem-ok-symbolic" if success else "dialog-warning-symbolic"
        if "click" in name or "mouse" in name:
            icon_name = "input-mouse-symbolic"
        elif "keyboard" in name or "type" in name or "hotkey" in name:
            icon_name = "input-keyboard-symbolic"
        elif "contact" in name:
            icon_name = "contact-new-symbolic"
        elif "email" in name:
            icon_name = "mail-send-symbolic"
        elif "calendar" in name:
            icon_name = "x-office-calendar-symbolic"
        elif "fence" in name or "monitor" in name:
            icon_name = "video-display-symbolic"
        elif "app" in name:
            icon_name = "application-x-executable-symbolic"
        elif "volume" in name or "control" in name:
            icon_name = "audio-volume-high-symbolic"
        elif "capture" in name:
            icon_name = "camera-photo-symbolic"
        elif "url" in name or "search" in name:
            icon_name = "web-browser-symbolic"
        elif "document" in name or "file" in name:
            icon_name = "text-x-generic-symbolic"

        self.action_icon.set_from_icon_name(icon_name)
        status_color = "#2ec27e" if success else "#e5a50a"
        self.action_label.set_markup(f"<span foreground='{status_color}'><b>⚡ {name}:</b> {message}</span>")
        self.action_revealer.set_reveal_child(True)

        # Esconde automaticamente a pill de ação após 5 segundos
        GLib.timeout_add_seconds(5, lambda: (self.action_revealer.set_reveal_child(False), GLib.SOURCE_REMOVE)[1])
        return GLib.SOURCE_REMOVE

    def _ui_on_transcript(self, role: str, text: str) -> bool:
        clean = text.strip()
        if clean:
            self.subtitle_lbl.set_text(clean)
        return GLib.SOURCE_REMOVE

    def _ui_on_error(self, err: str) -> bool:
        self.subtitle_lbl.set_text(f"⚠️ {err}")
        return GLib.SOURCE_REMOVE

    def _draw_audio_visualizer(self, _area: Gtk.DrawingArea, cr: Any, width: int, height: int) -> None:
        """Desenha uma esfera harmônica suave e ondas concêntricas proporcionais ao áudio."""
        cx = width / 2.0
        cy = height / 2.0
        base_radius = 28.0
        pulse = self._audio_level * 22.0
        radius = base_radius + pulse

        # Cor do orbe varia conforme o estado
        st = self.live_client.state
        if st == LiveVoiceState.SPEAKING:
            r, g, b = (0.57, 0.25, 0.67)  # Roxo vibrante
        elif st == LiveVoiceState.EXECUTING:
            r, g, b = (0.18, 0.76, 0.49)  # Verde esmeralda
        elif self.live_client.is_muted():
            r, g, b = (0.90, 0.65, 0.04)  # Âmbar
        else:
            r, g, b = (0.21, 0.52, 0.89)  # Azul celeste (escutando)

        # 1. Halo externo translúcido
        cr.arc(cx, cy, radius * 1.5, 0, 2 * math.pi)
        cr.set_source_rgba(r, g, b, 0.12 + self._audio_level * 0.15)
        cr.fill()

        # 2. Anel intermediário
        cr.arc(cx, cy, radius * 1.2, 0, 2 * math.pi)
        cr.set_source_rgba(r, g, b, 0.25 + self._audio_level * 0.20)
        cr.fill()

        # 3. Orbe central
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.set_source_rgba(r, g, b, 0.85)
        cr.fill()

    def _on_toggle_mute(self, _btn: Gtk.Button) -> None:
        is_muted = self.live_client.toggle_mute()
        if is_muted:
            self.mute_icon.set_from_icon_name("audio-input-microphone-muted-symbolic")
            self.mute_lbl.set_text("Desmutar")
            self.mute_btn.add_css_class("destructive-action")
        else:
            self.mute_icon.set_from_icon_name("audio-input-microphone-symbolic")
            self.mute_lbl.set_text("Mutar")
            self.mute_btn.remove_css_class("destructive-action")

    def _on_toggle_video(self, _btn: Gtk.Button) -> None:
        is_active = self.live_client.toggle_video_stream(fps=1.0)
        self._update_video_ui(is_active)

    def _update_video_ui(self, is_active: bool) -> bool:
        active_fence = getattr(self.live_client, "fence", None)
        active_mon = active_fence.get_active_monitor() if active_fence else None
        mon_name = active_mon.name if active_mon else "AOC 27\""

        if is_active:
            self.video_lbl.set_text("Pausar Tela")
            self.video_btn.add_css_class("suggested-action")
            self.video_badge.set_markup(f"<span foreground='#2ec27e'><b>● TELA AO VIVO ({mon_name})</b></span>")
            self.video_badge.set_visible(True)
            self.subtitle_lbl.set_text(f"🎥 Compartilhamento de tela ativo no {mon_name} (1 FPS). O assistente pode ver suas janelas.")
        else:
            self.video_lbl.set_text("Transmitir Tela")
            self.video_btn.remove_css_class("suggested-action")
            self.video_badge.set_visible(False)
            self.subtitle_lbl.set_text("Fale naturalmente com o assistente...")
        return False

    def _on_send_screen(self, _btn: Gtk.Button) -> None:
        ok = self.live_client.send_screen_frame()
        if ok:
            self.subtitle_lbl.set_text("📸 Imagem da tela enviada para a conversa ao vivo!")

    def _on_end_call(self) -> None:
        self.live_client.stop()
        if self.on_close_cb:
            self.on_close_cb()
