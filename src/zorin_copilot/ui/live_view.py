# Decisão de design: Interface imersiva de voz ao vivo com Glassmorphism, visualizador dinâmico de áudio
# e feedback instantâneo de comandos de sistema executados durante a conversação.

"""Componente de interface gráfica em GTK4 para o chat de voz ao vivo (Gemini Live)."""

from __future__ import annotations

import html
import logging
import math
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from ..ai.live import GeminiLiveClient, LiveVoiceState
from ..core.config import CopilotConfig

logger = logging.getLogger("zorin_copilot.ui.live_view")


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

        # Configuração e estilo visual
        self.config = CopilotConfig.load()
        self.visualizer_style = getattr(self.config, "voice_visualizer_style", "waves")
        self._target_audio_level: float = 0.0
        self._smooth_audio_level: float = 0.0
        self._anim_time: float = 0.0
        self._last_frame_time_us: int = 0
        self._tick_id: int = 0

        self._build_ui()
        self._connect_client_events()

    def _get_style_label(self) -> str:
        labels = {
            "waves": "Ondas Fluidas (Siri)",
            "bars": "Barras de Equalizador",
            "matrix": "Matriz de Pontos (Grid)",
            "orb": "Orbe Pulsante (Clássico)",
        }
        return labels.get(self.visualizer_style, "Ondas Fluidas")

    def _cycle_visualizer_style(self) -> None:
        styles = ["waves", "bars", "matrix", "orb"]
        icons = {
            "waves": "audio-speakers-symbolic",
            "bars": "media-playlist-shuffle-symbolic",
            "matrix": "view-grid-symbolic",
            "orb": "media-record-symbolic",
        }
        try:
            curr_idx = styles.index(self.visualizer_style)
            next_idx = (curr_idx + 1) % len(styles)
        except ValueError:
            next_idx = 0

        self.visualizer_style = styles[next_idx]
        new_label = self._get_style_label()

        # Salva na configuração persistente do Copilot
        try:
            cfg = CopilotConfig.load()
            cfg.voice_visualizer_style = self.visualizer_style
            cfg.save()
        except Exception as exc:
            logger.debug(f"Erro ao salvar estilo do visualizador: {exc}")

        tip = f"Visualizador: {new_label} (clique para alternar)"
        if hasattr(self, "style_btn"):
            self.style_btn.set_tooltip_text(tip)
            self.style_btn.set_icon_name(icons.get(self.visualizer_style, "audio-speakers-symbolic"))
        self.drawing_area.set_tooltip_text(tip)
        self.drawing_area.queue_draw()

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

        icons_map = {
            "waves": "audio-speakers-symbolic",
            "bars": "media-playlist-shuffle-symbolic",
            "matrix": "view-grid-symbolic",
            "orb": "media-record-symbolic",
        }
        self.style_btn = Gtk.Button.new_from_icon_name(icons_map.get(self.visualizer_style, "audio-speakers-symbolic"))
        self.style_btn.set_tooltip_text(f"Visualizador: {self._get_style_label()} (clique para alternar)")
        self.style_btn.add_css_class("flat")
        self.style_btn.add_css_class("circular")
        self.style_btn.connect("clicked", lambda _: self._cycle_visualizer_style())
        header_box.append(self.style_btn)

        close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close_btn.set_tooltip_text("Encerrar conversa de voz")
        close_btn.add_css_class("flat")
        close_btn.add_css_class("circular")
        close_btn.connect("clicked", lambda _: self._on_end_call())
        header_box.append(close_btn)

        card.append(header_box)

        # 2. Visualizador Dinâmico de Áudio (DrawingArea a 60 FPS com Cairo)
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_content_width(280)
        self.drawing_area.set_content_height(105)
        self.drawing_area.set_draw_func(self._draw_audio_visualizer)
        self.drawing_area.set_tooltip_text(f"Visualizador: {self._get_style_label()} (clique para alternar)")

        # Alternar estilo por clique na área
        click_gesture = Gtk.GestureClick.new()
        click_gesture.connect("pressed", lambda *_: self._cycle_visualizer_style())
        self.drawing_area.add_controller(click_gesture)

        # Tick callback de animação contínua e suave
        self._tick_id = self.drawing_area.add_tick_callback(self._on_visualizer_tick)
        card.append(self.drawing_area)

        # 2.1 Miniatura Picture-in-Picture (PiP)
        self.pip_revealer = Gtk.Revealer()
        self.pip_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.pip_revealer.set_reveal_child(False)

        pip_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pip_card.set_halign(Gtk.Align.CENTER)
        pip_card.add_css_class("pip-preview-card")
        pip_card.set_margin_start(16)
        pip_card.set_margin_end(16)
        pip_card.set_margin_top(2)
        pip_card.set_margin_bottom(2)

        self.pip_picture = Gtk.Picture()
        self.pip_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.pip_picture.set_size_request(200, 112)
        self.pip_picture.set_can_shrink(True)
        pip_card.append(self.pip_picture)

        self.pip_caption = Gtk.Label(label="🪟 Janela Ativa", use_markup=True)
        self.pip_caption.add_css_class("caption")
        self.pip_caption.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.pip_caption.set_max_width_chars(35)
        pip_card.append(self.pip_caption)

        self.pip_revealer.set_child(pip_card)
        card.append(self.pip_revealer)

        # 2.2 Banner do Privacy Shield
        self.privacy_banner_revealer = Gtk.Revealer()
        self.privacy_banner_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.privacy_banner_revealer.set_reveal_child(False)

        privacy_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        privacy_box.set_halign(Gtk.Align.CENTER)
        privacy_box.add_css_class("privacy-shield-banner")
        privacy_box.set_margin_start(16)
        privacy_box.set_margin_end(16)
        privacy_box.set_margin_top(2)
        privacy_box.set_margin_bottom(2)

        privacy_icon = Gtk.Image.new_from_icon_name("security-high-symbolic")
        privacy_icon.set_pixel_size(16)
        privacy_box.append(privacy_icon)

        self.privacy_lbl = Gtk.Label(
            label="<span foreground='#e5a50a'><b>🛡️ Privacy Shield Ativo:</b> Transmissão oculta por segurança</span>",
            use_markup=True,
        )
        self.privacy_lbl.add_css_class("caption")
        self.privacy_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.privacy_lbl.set_max_width_chars(45)
        privacy_box.append(self.privacy_lbl)

        self.privacy_banner_revealer.set_child(privacy_box)
        card.append(self.privacy_banner_revealer)

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

        # Botão Seletor de Modo de Transmissão (Janela Ativa / Tela Inteira)
        self.mode_btn = Gtk.Button()
        self.mode_btn.add_css_class("pill")
        self.mode_btn.add_css_class("glass-pill")
        self.mode_btn.set_tooltip_text("Alternar modo de transmissão: Janela Ativa ou Tela Inteira")
        mode_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.mode_icon = Gtk.Image.new_from_icon_name("window-restore-symbolic")
        self.mode_icon.set_pixel_size(14)
        self.mode_lbl = Gtk.Label(label="🪟 Janela")
        self.mode_lbl.add_css_class("caption")
        mode_btn_box.append(self.mode_icon)
        mode_btn_box.append(self.mode_lbl)
        self.mode_btn.set_child(mode_btn_box)
        self.mode_btn.connect("clicked", self._on_toggle_mode)
        controls_box.append(self.mode_btn)

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

        # Atalho de Teclado: Escape corta o vídeo instantaneamente (Botão de Pânico)
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _connect_client_events(self) -> None:
        """Registra os callbacks do cliente de áudio para atualizar a UI de forma thread-safe."""
        self.live_client.on_state_change = lambda state, msg: GLib.idle_add(self._ui_on_state_change, state, msg)
        self.live_client.on_audio_level = lambda lvl: GLib.idle_add(self._ui_on_audio_level, lvl)
        self.live_client.on_tool_executed = lambda name, msg, ok: GLib.idle_add(self._ui_on_tool_executed, name, msg, ok)
        self.live_client.on_transcript = lambda role, text: GLib.idle_add(self._ui_on_transcript, role, text)
        self.live_client.on_error = lambda err: GLib.idle_add(self._ui_on_error, err)
        self.live_client.on_video_state_change = lambda active: GLib.idle_add(self._update_video_ui, active)
        self.live_client.on_video_frame_preview = lambda img_bytes: GLib.idle_add(self._ui_on_video_frame, img_bytes)
        self.live_client.on_privacy_state_change = lambda shielded, reason: GLib.idle_add(self._ui_on_privacy_change, shielded, reason)
        self.live_client.on_window_focus_change = lambda app, title: GLib.idle_add(self._ui_on_focus_change, app, title)

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
        elif state == LiveVoiceState.THINKING:
            self.status_dot.set_markup("<span foreground='#f5c211'>●</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Pensando...")
            self.subtitle_lbl.set_text(msg or "Processando sua solicitação...")
        elif state == LiveVoiceState.DISCONNECTED:
            self.status_dot.set_markup("<span foreground='#77767b'>○</span>")
            self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Desconectado")
        self.drawing_area.queue_draw()
        return GLib.SOURCE_REMOVE

    def _ui_on_audio_level(self, level: float) -> bool:
        self._target_audio_level = max(0.0, min(1.0, level))
        return GLib.SOURCE_REMOVE

    def _ui_on_transcript(self, role: str, text: str) -> bool:
        """Exibe a transcrição do que foi falado (usuário ou assistente) na legenda."""
        if not text:
            return GLib.SOURCE_REMOVE
        clean_text = text.strip()
        if role in ("user", "human"):
            self.subtitle_lbl.set_markup(f"<b>Você:</b> {html.escape(clean_text[:140])}")
        else:
            self.subtitle_lbl.set_markup(f"<b>Copilot:</b> {html.escape(clean_text[:140])}")
        return GLib.SOURCE_REMOVE

    def _ui_on_tool_executed(self, name: str, msg: str, ok: bool) -> bool:
        """Atualiza a legenda ao executar comandos no sistema operacional."""
        status_icon = "✓" if ok else "⚠️"
        self.subtitle_lbl.set_markup(f"<b>{status_icon}</b> {html.escape(msg or name)}")
        return GLib.SOURCE_REMOVE

    def _ui_on_error(self, err: str) -> bool:
        """Exibe erros de áudio ou IA na legenda."""
        self.status_dot.set_markup("<span foreground='#e01b24'>●</span>")
        self.status_lbl.set_markup("<b>Zorin Copilot Live</b> • Erro")
        self.subtitle_lbl.set_text(str(err) if err else "Erro momentâneo de áudio.")
        return GLib.SOURCE_REMOVE

    def _on_visualizer_tick(self, _widget: Gtk.DrawingArea, frame_clock: Gdk.FrameClock) -> bool:
        if not self.get_mapped() or not self.get_visible():
            return GLib.SOURCE_CONTINUE

        now_us = frame_clock.get_frame_time()
        if self._last_frame_time_us == 0:
            dt = 0.016
        else:
            dt = min(0.08, max(0.001, (now_us - self._last_frame_time_us) / 1_000_000.0))
        self._last_frame_time_us = now_us

        self._anim_time += dt

        # Interpolação suave do volume com ataque rápido e decaimento gradual
        rate = 18.0 if self._target_audio_level > self._smooth_audio_level else 7.0
        self._smooth_audio_level += (self._target_audio_level - self._smooth_audio_level) * min(1.0, dt * rate)

        self.drawing_area.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw_audio_visualizer(self, _area: Gtk.DrawingArea, cr: Any, width: int, height: int) -> None:
        """Renderiza a representação visual interativa do áudio (Ondas, Barras, Matriz ou Orbe)."""
        cx = width / 2.0
        cy = height / 2.0
        t = self._anim_time

        # Se a IA estiver falando e o nível direto não estiver ativo, simula cadência de fala
        lvl = self._smooth_audio_level
        st = self.live_client.state
        if st == LiveVoiceState.SPEAKING and lvl < 0.15:
            speech_cadence = 0.50 + 0.35 * (
                0.6 * math.sin(t * 7.5) * math.cos(t * 3.2) +
                0.4 * math.sin(t * 13.0)
            )
            lvl = max(0.25, min(0.95, speech_cadence))

        # Cores contextuais com base no estado da conversação
        if st == LiveVoiceState.SPEAKING:
            r, g, b = (0.64, 0.35, 0.94)  # Roxo vibrante (IA falando)
        elif st == LiveVoiceState.EXECUTING:
            r, g, b = (0.18, 0.76, 0.49)  # Verde esmeralda (Ação do sistema)
        elif st == LiveVoiceState.THINKING:
            r, g, b = (0.96, 0.76, 0.07)  # Dourado/Âmbar (Pensando/Processando)
            if lvl < 0.2:
                lvl = max(lvl, 0.22 + 0.10 * math.sin(t * 4.5))
        elif self.live_client.is_muted():
            r, g, b = (0.95, 0.65, 0.12)  # Âmbar (Mudo)
        else:
            r, g, b = (0.21, 0.52, 0.89)  # Azul celeste Zorin (Escutando usuário)

        if self.visualizer_style == "waves":
            self._draw_style_waves(cr, width, height, cx, cy, t, lvl, r, g, b)
        elif self.visualizer_style == "bars":
            self._draw_style_bars(cr, width, height, cx, cy, t, lvl, r, g, b)
        elif self.visualizer_style == "matrix":
            self._draw_style_matrix(cr, width, height, cx, cy, t, lvl, r, g, b)
        else:
            self._draw_style_orb(cr, width, height, cx, cy, lvl, r, g, b)

    def _draw_style_waves(
        self, cr: Any, width: int, height: int, cx: float, cy: float,
        t: float, lvl: float, r: float, g: float, b: float
    ) -> None:
        """Desenha ondas fluidas senoidais multicamadas inspiradas em fitas harmônicas (Imagem 1)."""
        num_layers = 16
        pad_x = 16.0
        eff_w = max(10.0, width - 2.0 * pad_x)

        base_amp = 4.5 + math.sin(t * 1.8) * 1.5
        voice_amp = lvl * (height * 0.42)
        total_amp = base_amp + voice_amp

        # Brilho de fundo sutil (aurora difusa)
        if lvl > 0.05:
            cr.save()
            cr.new_sub_path()
            cr.move_to(pad_x, cy)
            for step_i in range(0, int(eff_w) + 1, 6):
                x = pad_x + step_i
                norm_x = step_i / eff_w
                env = (math.sin(math.pi * norm_x)) ** 1.8
                y = cy + env * total_amp * 0.7 * math.sin(2.4 * math.pi * norm_x + t * 2.5)
                cr.line_to(x, y)
            for step_i in range(int(eff_w), -1, -6):
                x = pad_x + step_i
                norm_x = step_i / eff_w
                env = (math.sin(math.pi * norm_x)) ** 1.8
                y = cy - env * total_amp * 0.7 * math.sin(2.4 * math.pi * norm_x + t * 2.5)
                cr.line_to(x, y)
            cr.close_path()
            cr.set_source_rgba(r, g, b, 0.08 * lvl)
            cr.fill()
            cr.restore()

        # Renderização do feixe de fitas vetoriais
        cr.save()
        for k in range(num_layers):
            norm_k = (k - (num_layers - 1) / 2.0) / ((num_layers - 1) / 2.0)  # -1.0 a 1.0
            phase = t * (2.2 + 0.3 * (k % 3)) + norm_k * 0.55
            layer_amp = total_amp * (1.0 - 0.22 * abs(norm_k))

            alpha = max(0.12, (1.0 - abs(norm_k) * 0.65) * (0.35 + 0.65 * (0.3 + 0.7 * lvl)))
            cr.set_line_width(1.1 + 0.4 * (1.0 - abs(norm_k)))
            cr.set_source_rgba(
                min(1.0, r + 0.15 * (1.0 - abs(norm_k))),
                min(1.0, g + 0.15 * (1.0 - abs(norm_k))),
                min(1.0, b + 0.15 * (1.0 - abs(norm_k))),
                alpha,
            )

            cr.move_to(pad_x, cy)
            step_px = 3
            for step_i in range(0, int(eff_w) + 1, step_px):
                x = pad_x + step_i
                norm_x = step_i / eff_w
                env = (math.sin(math.pi * norm_x)) ** 1.6
                w1 = 0.72 * math.sin(2.5 * math.pi * norm_x + phase)
                w2 = 0.28 * math.sin(5.0 * math.pi * norm_x - phase * 0.85 + norm_k * 0.7)
                y = cy + env * layer_amp * (w1 + w2)
                cr.line_to(x, y)

            cr.stroke()
        cr.restore()

    def _draw_style_bars(
        self, cr: Any, width: int, height: int, cx: float, cy: float,
        t: float, lvl: float, r: float, g: float, b: float
    ) -> None:
        """Desenha barras verticais de equalizador simétricas com cantos arredondados (Imagem 2)."""
        num_bars = 36
        bar_w = 3.6
        gap = 3.4
        total_w = num_bars * bar_w + (num_bars - 1) * gap
        start_x = cx - total_w / 2.0
        max_half_h = (height * 0.44)

        cr.save()
        for i in range(num_bars):
            norm_i = (i - (num_bars - 1) / 2.0) / ((num_bars - 1) / 2.0)  # -1.0 a 1.0
            env = math.cos(norm_i * (math.pi * 0.48)) ** 1.6

            f1 = abs(math.sin(t * 5.2 + i * 0.58))
            f2 = abs(math.cos(t * 8.4 + i * 1.12))
            freq_factor = 0.55 * f1 + 0.45 * f2

            idle_h = 4.0 + 2.0 * math.sin(t * 2.8 + i * 0.35)
            voice_h = lvl * (max_half_h * 2.0) * env * (0.35 + 0.65 * freq_factor)
            bar_h = max(4.0, min(height * 0.88, idle_h + voice_h))

            bx = start_x + i * (bar_w + gap)
            top = cy - bar_h / 2.0
            bot = cy + bar_h / 2.0
            radius = min(bar_w / 2.0, bar_h / 2.0)

            cr.new_sub_path()
            cr.arc(bx + radius, top + radius, radius, math.pi, 0)
            cr.arc(bx + radius, bot - radius, radius, 0, math.pi)
            cr.close_path()

            intensity = min(1.0, 0.45 + 0.55 * (bar_h / max(1.0, max_half_h * 2.0)))
            cr.set_source_rgba(
                min(1.0, r + 0.15 * intensity),
                min(1.0, g + 0.15 * intensity),
                min(1.0, b + 0.15 * intensity),
                0.40 + 0.55 * intensity,
            )
            cr.fill()
        cr.restore()

    def _draw_style_matrix(
        self, cr: Any, width: int, height: int, cx: float, cy: float,
        t: float, lvl: float, r: float, g: float, b: float
    ) -> None:
        """Desenha matriz de pontos LED sobre grade sutil inspirada em analisadores de estúdio (Imagem 3)."""
        cols = 27
        rows = 9
        pad_x = 24.0
        pad_y = 14.0
        grid_w = max(10.0, width - 2.0 * pad_x)
        grid_h = max(10.0, height - 2.0 * pad_y)

        dx = grid_w / (cols - 1)
        dy = grid_h / (rows - 1)
        half_rows = rows // 2

        cr.save()

        # 1. Linhas de grade sutis (background grid)
        cr.set_line_width(0.7)
        cr.set_source_rgba(r, g, b, 0.08)
        for r_i in range(0, rows, 2):
            gy = pad_y + r_i * dy
            cr.move_to(pad_x, gy)
            cr.line_to(pad_x + grid_w, gy)
        for c_i in range(0, cols, 4):
            gx = pad_x + c_i * dx
            cr.move_to(gx, pad_y)
            cr.line_to(gx, pad_y + grid_h)
        cr.stroke()

        # 2. Matriz de pontos circulares
        for c_i in range(cols):
            norm_c = (c_i - (cols - 1) / 2.0) / ((cols - 1) / 2.0)
            env = math.cos(norm_c * (math.pi * 0.46)) ** 1.5

            f_wave = 0.6 * math.sin(t * 5.5 + c_i * 0.52) + 0.4 * math.cos(t * 8.0 + c_i * 1.1)
            active_span = env * half_rows * (0.25 + 0.75 * lvl * (0.6 + 0.4 * abs(f_wave)))

            px = pad_x + c_i * dx
            for r_i in range(rows):
                py = pad_y + r_i * dy
                dist = abs(r_i - half_rows)

                if dist <= active_span:
                    dot_radius = 2.4 + 1.2 * (1.0 - dist / max(1, half_rows))
                    alpha = 0.75 + 0.25 * (1.0 - dist / max(1, half_rows))
                    cr.arc(px, py, dot_radius, 0, 2 * math.pi)
                    cr.set_source_rgba(
                        min(1.0, r + 0.2),
                        min(1.0, g + 0.2),
                        min(1.0, b + 0.2),
                        alpha,
                    )
                    cr.fill()
                else:
                    cr.arc(px, py, 1.2, 0, 2 * math.pi)
                    cr.set_source_rgba(r, g, b, 0.12)
                    cr.fill()

        cr.restore()

    def _draw_style_orb(
        self, cr: Any, width: int, height: int, cx: float, cy: float,
        lvl: float, r: float, g: float, b: float
    ) -> None:
        """Desenha o orbe central clássico com anéis concêntricos pulsantes."""
        base_radius = 28.0
        pulse = lvl * 22.0
        radius = base_radius + pulse

        # 1. Halo externo translúcido
        cr.arc(cx, cy, radius * 1.5, 0, 2 * math.pi)
        cr.set_source_rgba(r, g, b, 0.12 + lvl * 0.15)
        cr.fill()

        # 2. Anel intermediário
        cr.arc(cx, cy, radius * 1.2, 0, 2 * math.pi)
        cr.set_source_rgba(r, g, b, 0.25 + lvl * 0.20)
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
        mode = getattr(self.live_client, "video_mode", "active_window")
        mode_label = "Janela Ativa" if mode == "active_window" else "Tela Inteira"

        if is_active:
            self.video_lbl.set_text("Pausar Tela")
            self.video_btn.add_css_class("suggested-action")
            self.video_badge.set_markup(f"<span foreground='#2ec27e'><b>● AO VIVO ({mode_label})</b></span>")
            self.video_badge.set_visible(True)
            self.pip_revealer.set_reveal_child(True)
            self.subtitle_lbl.set_text(f"🎥 Compartilhamento ativo ({mode_label} • 1 FPS). O assistente pode ver suas janelas.")
        else:
            self.video_lbl.set_text("Transmitir Tela")
            self.video_btn.remove_css_class("suggested-action")
            self.video_badge.set_visible(False)
            self.pip_revealer.set_reveal_child(False)
            self.privacy_banner_revealer.set_reveal_child(False)
            self.subtitle_lbl.set_text("Fale naturalmente com o assistente...")
        return False

    def _ui_on_video_frame(self, img_bytes: bytes) -> bool:
        try:
            gbytes = GLib.Bytes.new(img_bytes)
            texture = Gdk.Texture.new_from_bytes(gbytes)
            self.pip_picture.set_paintable(texture)
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    def _ui_on_privacy_change(self, is_shielded: bool, reason: str) -> bool:
        if is_shielded:
            escaped_reason = html.escape(reason) if reason else "Janela sensível detectada"
            self.privacy_lbl.set_markup(f"<span foreground='#e5a50a'><b>🛡️ Privacy Shield Ativo:</b> {escaped_reason}</span>")
            self.privacy_banner_revealer.set_reveal_child(True)
            self.video_badge.set_markup("<span foreground='#e5a50a'><b>🛡️ PRIVACY SHIELD ATIVO</b></span>")
        else:
            self.privacy_banner_revealer.set_reveal_child(False)
            mode = getattr(self.live_client, "video_mode", "active_window")
            mode_label = "Janela Ativa" if mode == "active_window" else "Tela Inteira"
            self.video_badge.set_markup(f"<span foreground='#2ec27e'><b>● AO VIVO ({mode_label})</b></span>")
        return GLib.SOURCE_REMOVE

    def _ui_on_focus_change(self, app_name: str, win_title: str) -> bool:
        display_title = f"{app_name}: {win_title}" if win_title else app_name
        if not display_title:
            display_title = "Janela Ativa"
        self.pip_caption.set_markup(f"<span foreground='#78aeed'>🪟 {html.escape(display_title[:35])}</span>")
        return GLib.SOURCE_REMOVE

    def _on_toggle_mode(self, _btn: Gtk.Button) -> None:
        curr_mode = getattr(self.live_client, "video_mode", "active_window")
        new_mode = "fullscreen" if curr_mode == "active_window" else "active_window"
        self.live_client.set_video_mode(new_mode)
        self._update_mode_ui(new_mode)
        if getattr(self.live_client, "video_streaming", False):
            self._update_video_ui(True)

    def _update_mode_ui(self, mode: str) -> None:
        if mode == "active_window":
            self.mode_icon.set_from_icon_name("window-restore-symbolic")
            self.mode_lbl.set_text("🪟 Janela")
            self.mode_btn.set_tooltip_text("Modo Janela Ativa ativo. Clique para alternar para Tela Inteira.")
        else:
            self.mode_icon.set_from_icon_name("video-display-symbolic")
            self.mode_lbl.set_text("🖥️ Tela")
            self.mode_btn.set_tooltip_text("Modo Tela Inteira ativo. Clique para alternar para Janela Ativa.")

    def _on_key_pressed(self, _ctrl: Gtk.EventControllerKey, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            if getattr(self.live_client, "video_streaming", False):
                self.live_client.panic_stop_video()
                self._update_video_ui(False)
                self.subtitle_lbl.set_text("🛑 Transmissão de vídeo interrompida (Modo Pânico - Esc).")
                return True
        return False

    def _on_send_screen(self, _btn: Gtk.Button) -> None:
        ok = self.live_client.send_screen_frame()
        if ok:
            self.subtitle_lbl.set_text("📸 Imagem da tela enviada para a conversa ao vivo!")

    def _on_end_call(self) -> None:
        if getattr(self, "_tick_id", 0) != 0:
            try:
                self.drawing_area.remove_tick_callback(self._tick_id)
            except Exception:
                pass
            self._tick_id = 0
        self.live_client.stop()
        if self.on_close_cb:
            self.on_close_cb()
