# Decisão de design: interface imersiva de voz ao vivo com Glassmorphism, visualizador dinâmico de áudio
# e registro rolável da sessão. Ações executadas e transcrição são acumuladas em lista (não
# substituídas), para que o usuário possa revisar o que foi feito durante a chamada.

"""Componente de interface gráfica em GTK4 para o chat de voz ao vivo (Gemini Live)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from ..ai.live import GeminiLiveClient, LiveVoiceState

LOG_MAX_HEIGHT = 170
TRANSCRIPT_ROLE_ICONS = {
    "user": "avatar-default-symbolic",
    "assistant": "system-help-symbolic",
    "model": "system-help-symbolic",
}


class LiveVoiceWidget(Gtk.Box):
    """Widget de conversação por voz ao vivo com visualizador de áudio e registro da sessão."""

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
        self._elapsed_sec: int = 0
        self._timer_id: int | None = None

        self._build_ui()
        self._connect_client_events()

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.add_css_class("card")
        card.add_css_class("glass-card")
        card.set_margin_top(4)
        card.set_margin_bottom(4)
        card.set_margin_start(8)
        card.set_margin_end(8)

        card.append(self._build_header())
        card.append(self._build_visualizer())
        card.append(self._build_session_log())

        self.subtitle_lbl = Gtk.Label(label="Fale naturalmente com o assistente...", xalign=0.5)
        self.subtitle_lbl.add_css_class("dim-label")
        self.subtitle_lbl.set_wrap(True)
        self.subtitle_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.subtitle_lbl.set_margin_start(20)
        self.subtitle_lbl.set_margin_end(20)
        self.subtitle_lbl.set_margin_top(4)
        self.subtitle_lbl.set_margin_bottom(8)
        card.append(self.subtitle_lbl)

        card.append(self._build_controls())
        self.append(card)

    def _build_header(self) -> Gtk.Box:
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_box.set_margin_top(14)
        header_box.set_margin_start(16)
        header_box.set_margin_end(16)

        self.status_dot = Gtk.Label(label="●", use_markup=True)
        self.status_dot.add_css_class("status-dot")
        header_box.append(self.status_dot)

        self.status_lbl = Gtk.Label(
            label="<b>Zorin Copilot Live</b> • Conectando...", use_markup=True, xalign=0
        )
        self.status_lbl.set_hexpand(True)
        header_box.append(self.status_lbl)

        # Cronômetro da chamada (só visível enquanto conectado)
        self.timer_lbl = Gtk.Label(label="00:00", xalign=0)
        self.timer_lbl.add_css_class("caption")
        self.timer_lbl.add_css_class("dim-label")
        self.timer_lbl.set_visible(False)
        header_box.append(self.timer_lbl)

        self.video_badge = Gtk.Label(
            label="<span foreground='#e01b24'><b>● TELA AO VIVO (1 FPS)</b></span>", use_markup=True
        )
        self.video_badge.add_css_class("caption")
        self.video_badge.set_visible(False)
        header_box.append(self.video_badge)

        close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close_btn.set_tooltip_text("Encerrar conversa de voz")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("circular")
        close_btn.connect("clicked", lambda _: self._on_end_call())
        header_box.append(close_btn)
        return header_box

    def _build_visualizer(self) -> Gtk.DrawingArea:
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_content_width(200)
        self.drawing_area.set_content_height(100)
        self.drawing_area.set_draw_func(self._draw_audio_visualizer)
        return self.drawing_area

    def _build_session_log(self) -> Gtk.ScrolledWindow:
        """Área rolável que acumula ações executadas e a transcrição da chamada."""
        self.log_scrolled = Gtk.ScrolledWindow()
        self.log_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.log_scrolled.set_min_content_height(90)
        self.log_scrolled.set_max_content_height(LOG_MAX_HEIGHT)
        self.log_scrolled.set_margin_start(12)
        self.log_scrolled.set_margin_end(12)
        self.log_scrolled.set_propagate_natural_height(True)
        self.log_scrolled.set_visible(False)

        self.log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.log_scrolled.set_child(self.log_box)
        return self.log_scrolled

    def _build_controls(self) -> Gtk.Box:
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

        # Botão Live Video (streaming contínuo de tela com consentimento)
        self.video_btn = Gtk.Button()
        self.video_btn.add_css_class("pill")
        self.video_btn.add_css_class("glass-pill")
        self.video_btn.set_tooltip_text(
            "Transmitir tela ao vivo continuamente (1 FPS) para o Copilot visualizar suas janelas "
            "enquanto conversam"
        )
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

        # Botão Enviar Snapshot da Tela (foto única)
        self.screen_btn = Gtk.Button()
        self.screen_btn.add_css_class("pill")
        self.screen_btn.add_css_class("glass-pill")
        self.screen_btn.set_tooltip_text(
            "Captura uma imagem instantânea da tela atual e envia para a IA"
        )
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
        return controls_box

    # ------------------------------------------------------------------
    # Registro da sessão
    # ------------------------------------------------------------------
    def _append_log_row(self, icon_name: str, markup: str) -> None:
        """Adiciona uma linha ao registro rolável e rola até o fim."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_start(4)
        row.set_margin_end(4)
        row.set_margin_top(2)
        row.set_margin_bottom(2)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(14)
        icon.set_valign(Gtk.Align.START)
        row.append(icon)

        lbl = Gtk.Label(xalign=0)
        lbl.set_hexpand(True)
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_selectable(True)
        lbl.set_use_markup(True)
        lbl.set_markup(markup)
        lbl.add_css_class("caption")
        row.append(lbl)

        self.log_box.append(row)
        self.log_scrolled.set_visible(True)
        self._scroll_log_to_bottom()

    def _scroll_log_to_bottom(self) -> None:
        def _do_scroll():
            adj = self.log_scrolled.get_vadjustment()
            if adj:
                adj.set_value(adj.get_upper() - adj.get_page_size())
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_do_scroll)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%H:%M:%S")

    # ------------------------------------------------------------------
    # Eventos do cliente de voz
    # ------------------------------------------------------------------
    def _connect_client_events(self) -> None:
        """Registra os callbacks do cliente de áudio para atualizar a UI de forma thread-safe."""
        self.live_client.on_state_change = lambda state, msg: GLib.idle_add(
            self._ui_on_state_change, state, msg
        )
        self.live_client.on_audio_level = lambda lvl: GLib.idle_add(self._ui_on_audio_level, lvl)
        self.live_client.on_tool_executed = lambda name, msg, ok: GLib.idle_add(
            self._ui_on_tool_executed, name, msg, ok
        )
        self.live_client.on_transcript = lambda role, text: GLib.idle_add(
            self._ui_on_transcript, role, text
        )
        self.live_client.on_error = lambda err: GLib.idle_add(self._ui_on_error, err)
        self.live_client.on_video_state_change = lambda active: GLib.idle_add(
            self._update_video_ui, active
        )

    def _ui_on_state_change(self, state: LiveVoiceState, msg: str) -> bool:
        if state == LiveVoiceState.CONNECTING:
            self.status_dot.set_markup("<span foreground='#e5a50a'>●</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Conectando...")
            self.subtitle_lbl.set_text("Estabelecendo conexão segura com Gemini 2.5 Live...")
            self._start_timer()
        elif state == LiveVoiceState.LISTENING:
            self.status_dot.set_markup("<span foreground='#3584e4'>●</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Ouvindo você")
            self.subtitle_lbl.set_text("Fale naturalmente... o Copilot está ouvindo.")
            self._start_timer()
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
            self._stop_timer()
        elif state == LiveVoiceState.DISCONNECTED:
            self.status_dot.set_markup("<span foreground='#77767b'>○</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Desconectado")
            self._stop_timer()
        self.drawing_area.queue_draw()
        return GLib.SOURCE_REMOVE

    def _ui_on_audio_level(self, level: float) -> bool:
        self._audio_level = level
        self.drawing_area.queue_draw()
        return GLib.SOURCE_REMOVE

    def _ui_on_tool_executed(self, name: str, message: str, success: bool) -> bool:
        """Registra a ação executada no histórico rolável (era um pill que sumia em 5s)."""
        status_color = "#2ec27e" if success else "#e5a50a"
        self._append_log_row(
            self._tool_icon(name),
            f"<span foreground='{status_color}'><b>⚡ {name}:</b> {message}</span>"
            f"  <span alpha='60%'>{self._timestamp()}</span>",
        )
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _tool_icon(name: str) -> str:
        """Escolhe um ícone semântico para a ferramenta executada."""
        if "click" in name or "mouse" in name:
            return "input-mouse-symbolic"
        if "keyboard" in name or "type" in name or "hotkey" in name:
            return "input-keyboard-symbolic"
        if "contact" in name:
            return "contact-new-symbolic"
        if "email" in name:
            return "mail-send-symbolic"
        if "calendar" in name:
            return "x-office-calendar-symbolic"
        if "fence" in name or "monitor" in name:
            return "video-display-symbolic"
        if "app" in name:
            return "application-x-executable-symbolic"
        if "volume" in name or "control" in name:
            return "audio-volume-high-symbolic"
        if "capture" in name:
            return "camera-photo-symbolic"
        if "url" in name or "search" in name:
            return "web-browser-symbolic"
        if "document" in name or "file" in name:
            return "text-x-generic-symbolic"
        return "emblem-ok-symbolic" if "ok" in name else "utilities-terminal-symbolic"

    def _ui_on_transcript(self, role: str, text: str) -> bool:
        """Acumula a transcrição em vez de substituir a fala anterior."""
        clean = text.strip()
        if not clean:
            return GLib.SOURCE_REMOVE

        role_low = (role or "").lower()
        if role_low in ("user", "você", "voce"):
            display_role = "Você"
            icon = TRANSCRIPT_ROLE_ICONS["user"]
        else:
            display_role = "Copilot"
            icon = TRANSCRIPT_ROLE_ICONS["assistant"]

        self._append_log_row(
            icon,
            f"<b>{display_role}:</b> {clean}  <span alpha='60%'>{self._timestamp()}</span>",
        )
        # Mantém a última fala visível também no subtítulo (contexto imediato)
        self.subtitle_lbl.set_text(clean)
        return GLib.SOURCE_REMOVE

    def _ui_on_error(self, err: str) -> bool:
        self.subtitle_lbl.set_text(f"⚠️ {err}")
        self._append_log_row("dialog-warning-symbolic", f"<b>Erro:</b> {err}")
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Cronômetro
    # ------------------------------------------------------------------
    def _start_timer(self) -> None:
        if self._timer_id is not None:
            return
        self.timer_lbl.set_visible(True)
        self._timer_id = GLib.timeout_add_seconds(1, self._on_timer_tick)

    def _stop_timer(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self.timer_lbl.set_visible(False)

    def _on_timer_tick(self) -> bool:
        # Se o widget saiu da árvore, encerra o timer para não vazar
        if self.get_root() is None:
            self._timer_id = None
            return GLib.SOURCE_REMOVE

        self._elapsed_sec += 1
        minutes, seconds = divmod(self._elapsed_sec, 60)
        self.timer_lbl.set_text(f"{minutes:02d}:{seconds:02d}")
        return GLib.SOURCE_CONTINUE

    @property
    def elapsed_seconds(self) -> int:
        """Tempo decorrido da chamada, em segundos."""
        return self._elapsed_sec

    # ------------------------------------------------------------------
    # Visualizador
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Ações do usuário
    # ------------------------------------------------------------------
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
        mon_name = active_mon.name if active_mon else 'AOC 27"'

        if is_active:
            self.video_lbl.set_text("Pausar Tela")
            self.video_btn.add_css_class("suggested-action")
            self.video_badge.set_markup(
                f"<span foreground='#2ec27e'><b>● TELA AO VIVO ({mon_name})</b></span>"
            )
            self.video_badge.set_visible(True)
            self.subtitle_lbl.set_text(
                f"\U0001f3a5 Compartilhamento de tela ativo no {mon_name} (1 FPS). "
                "O assistente pode ver suas janelas."
            )
        else:
            self.video_lbl.set_text("Transmitir Tela")
            self.video_btn.remove_css_class("suggested-action")
            self.video_badge.set_visible(False)
            self.subtitle_lbl.set_text("Fale naturalmente com o assistente...")
        return False

    def _on_send_screen(self, _btn: Gtk.Button) -> None:
        ok = self.live_client.send_screen_frame()
        if ok:
            self.subtitle_lbl.set_text("\U0001f4f8 Imagem da tela enviada para a conversa ao vivo!")

    def _on_end_call(self) -> None:
        self._stop_timer()
        self.live_client.stop()
        if self.on_close_cb:
            self.on_close_cb()
