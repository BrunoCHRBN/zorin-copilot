# Decisão de design: interface fluida com processamento assíncrono em threads (zero travamentos na UI), exibição rica de respostas textuais explicativas com suporte a cópia, e orquestração de ações concretas de desktop e web.

"""Interface gráfica do Zorin Copilot em GTK4 / Libadwaita."""

from __future__ import annotations

import html
import re
import threading
from datetime import datetime
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import __app_id__, __version__
from ..ai.actions import ActionPlan, ActionType, DesktopAction
from ..ai.engine import IntentEngine
from ..core.a11y import DesktopInspector
from ..core.apps import AppManager
from ..core.clipboard import ClipboardService
from ..core.config import CopilotConfig
import time
from ..core.session import TopicSession
from ..core.shortcuts import ShortcutManager
from ..core.vision import ScreenCaptureService
from ..shell.executor import ActionExecutor
from ..ai.live import GeminiLiveClient
from .live_view import LiveVoiceWidget
from .preferences import PreferencesDialog
from .style import setup_glass_window


def format_relative_timestamp(iso_str: str) -> str:
    """Formata timestamp ISO de forma amigável para exibição no histórico de tópicos."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.now()
        time_part = dt.strftime("%H:%M")
        if dt.date() == now.date():
            return f"Hoje às {time_part}"
        elif (now.date() - dt.date()).days == 1:
            return f"Ontem às {time_part}"
        elif dt.year == now.year:
            return dt.strftime("%d/%m") + f" às {time_part}"
        else:
            return dt.strftime("%d/%m/%Y")
    except Exception:
        return iso_str[:16].replace("T", " ")


def format_markdown_to_markup(text: str) -> str:
    """Converte markdown comum (negrito, itálico, código, links) em GTK/Pango markup válido."""
    if not text:
        return ""
    try:
        s = html.escape(text)

        # 1. Blocos de código multilinhas: ```lang\ncode\n```
        s = re.sub(
            r"```(?:[a-zA-Z0-9_-]+)?\n?(.*?)```",
            lambda m: f"\n<tt><b>{m.group(1).strip()}</b></tt>\n",
            s,
            flags=re.DOTALL,
        )

        # 2. Código inline / caminhos de diretório: `código` -> <tt><b>código</b></tt>
        s = re.sub(r"`([^`\n]+)`", r"<tt><b>\1</b></tt>", s)

        # 3. Links markdown: [texto](url) -> <a href="url">texto</a>
        s = re.sub(
            r"\[([^\]]+)\]\((https?://[^\s\)]+)\)",
            lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
            s,
        )

        # 4. Negrito: **texto** ou __texto__ -> <b>texto</b>
        s = re.sub(r"\*\*([^\*\n]+)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", s)

        # 5. Itálico: *texto* -> <i>texto</i>
        s = re.sub(r"(?<!\*)\*([^\*\n]+)\*(?!\*)", r"<i>\1</i>", s)

        # 6. Marcadores de lista
        s = re.sub(r"(?m)^[\t ]*[-*]\s+", "  • ", s)

        return s
    except Exception:
        return html.escape(text)


def get_action_icon(action: DesktopAction) -> str:
    """Retorna o ícone semântico padrão mais adequado para a ação proposta."""
    target_low = action.target.lower()
    if action.action_type == ActionType.LAUNCH_APP:
        if "terminal" in target_low:
            return "utilities-terminal-symbolic"
        if "calc" in target_low:
            return "accessories-calculator-symbolic"
        if "steam" in target_low or "jog" in target_low:
            return "applications-games-symbolic"
        if any(b in target_low for b in ("web", "browser", "firefox", "chrome", "edge", "brave")):
            return "web-browser-symbolic"
        if any(f in target_low for f in ("file", "arquiv", "pasta", "nautilus")):
            return "system-file-manager-symbolic"
        return "application-x-executable-symbolic"
    if action.action_type == ActionType.OPEN_URL:
        return "web-browser-symbolic"
    if action.action_type == ActionType.SYSTEM_CONTROL:
        return "preferences-system-symbolic"
    if action.action_type == ActionType.CLICK:
        return "input-mouse-symbolic"
    if action.action_type == ActionType.NOTIFY:
        return "dialog-information-symbolic"
    if action.action_type == ActionType.FIX_COMMAND:
        return "utilities-terminal-symbolic"
    if action.action_type == ActionType.SMART_OCR:
        return "edit-copy-symbolic"
    return "system-run-symbolic"


def get_app_subtitle(app: Gio.AppInfo) -> str:
    """Retorna uma descrição legível e amigável para o aplicativo."""
    desc = app.get_description()
    if desc and len(desc) < 65:
        return desc.strip()

    app_id = (app.get_id() or "").lower()
    if "chrome-" in app_id or "msedge-" in app_id or "brave-" in app_id:
        return "Aplicativo Web • Integrado ao desktop"

    exe = app.get_executable()
    if exe:
        exe_name = exe.split("/")[-1]
        return f"Comando '{exe_name}' • Aplicativo instalado"

    return "Aplicativo instalado no Zorin OS"


class CopilotWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_title("Zorin Copilot")
        self.set_default_size(720, 520)
        self.set_resizable(True)

        self.config = CopilotConfig.load()
        self.inspector = DesktopInspector()
        self.executor = ActionExecutor(self.inspector)
        self.engine = IntentEngine(self.inspector, self.config)
        self.session = TopicSession(auto_persist=True)
        self.current_plan: ActionPlan | None = None
        self._raw_answer_text: str = ""
        self._is_busy = False
        self._search_debounce_timer: int | None = None
        self._matched_preview_app: Gio.AppInfo | None = None
        self._last_captured_image_bytes: bytes | None = None

        # Pilar 3: Contexto de Visão Contínua (Retém recorte para conversas multiturn)
        self._active_image_bytes: bytes | None = None
        self._active_image_is_area: bool = False
        self._active_image_is_clipboard: bool = False

        # Voz ao Vivo (Gemini Multimodal Live)
        self.live_client: GeminiLiveClient | None = None
        self.live_voice_widget: LiveVoiceWidget | None = None

        self._build_ui()
        setup_glass_window(self)
        self._update_provider_badge()
        self.connect("close-request", self._on_close_request)

    def _on_close_request(self, _win) -> bool:
        """Em modo HUD, fecha a janela ocultando-a da tela sem matar o processo em segundo plano."""
        if self.live_client and self.live_client.is_active():
            self.stop_live_voice()
        self.set_visible(False)
        return True

    def summon_hud(self) -> None:
        """Apresenta a janela com foco imediato no campo de busca com zero latência."""
        self.set_visible(True)
        self.present()
        self.entry.grab_focus()

    def toggle_hud(self) -> None:
        """Alterna a visibilidade da janela em modo HUD."""
        if self.get_visible() and self.is_active():
            self.set_visible(False)
        else:
            self.summon_hud()

    def toggle_live_voice(self) -> None:
        """Alterna a ativação do modo de conversa de voz ao vivo (Gemini Live)."""
        if self.live_client and self.live_client.is_active():
            self.stop_live_voice()
        else:
            self.start_live_voice()

    def start_live_voice(self) -> None:
        """Inicia o chat de voz ao vivo com o Gemini Live."""
        if not self.config.gemini_api_key.strip():
            self.show_toast("Chave de API do Google Gemini necessária para voz ao vivo. Configure em ⚙️.")
            self._open_settings(self.voice_call_btn)
            return

        if not self.live_client:
            self.live_client = GeminiLiveClient(config=self.config, executor=self.executor)

        self.live_voice_widget = LiveVoiceWidget(
            live_client=self.live_client,
            on_close=self.stop_live_voice,
        )
        self.live_voice_revealer.set_child(self.live_voice_widget)
        self.live_voice_revealer.set_reveal_child(True)
        self.voice_call_btn.add_css_class("suggested-action")
        self.welcome_box.set_visible(False)
        self.live_client.start()
        self.show_toast("🎙️ Conversa ao vivo iniciada! Pode falar...")

    def stop_live_voice(self) -> None:
        """Encerra a chamada de voz ao vivo e consolida a interação no chat ativo."""
        summary = self.live_client.get_session_summary() if self.live_client else {}
        if self.live_client:
            self.live_client.stop()
        self.live_voice_revealer.set_reveal_child(False)
        self.voice_call_btn.remove_css_class("suggested-action")

        # Se houve atividade durante a chamada, registra na thread do chat ativo
        if summary.get("has_activity"):
            duration = summary.get("duration_sec", 0)
            actions = summary.get("actions_executed", [])
            lines = [f"**🎙️ Sessão de Voz ao Vivo (Gemini Live)** • {duration}s de chamada\n"]
            if actions:
                lines.append("**Ações executadas no sistema:**")
                for act in actions:
                    t_name = act.get("tool", "")
                    t_args = act.get("args", {})
                    if t_name == "launch_app":
                        lines.append(f"- 🚀 Abriu o aplicativo **{t_args.get('app_name', '')}**")
                    elif t_name == "system_control":
                        lines.append(f"- ⚙️ Controle do sistema: **{t_args.get('action', '')}** ({t_args.get('value', '')})")
                    elif t_name == "open_url":
                        lines.append(f"- 🌐 Abriu link: `{t_args.get('url', '')}`")
                    elif t_name == "capture_screen":
                        lines.append("- 📸 Capturou a tela para inspeção visual")
                    elif t_name == "web_search":
                        lines.append(f"- 🔍 Pesquisa na web: *{t_args.get('query', '')}*")
                    else:
                        lines.append(f"- ⚡ Executou ferramenta: `{t_name}`")
                lines.append("")
            else:
                lines.append("Conversa por voz e áudio bidirecional concluída.")

            summary_text = "\n".join(lines).strip()
            prompt_label = "🎙️ Conversa de Voz ao Vivo"

            self.session.record_turn(prompt=prompt_label, answer=summary_text)
            self._save_current_session()
            self._update_pin_ui()

            self.answer_label.set_markup(format_markdown_to_markup(summary_text))
            self._raw_answer_text = summary_text
            self.source_badge.set_text("🎙️ Gemini Live")
            self.source_badge.set_visible(True)
            self.welcome_box.set_visible(False)
            self.answer_group.set_visible(True)
        else:
            if not self.answer_group.get_visible():
                self.welcome_box.set_visible(True)

        self.entry.grab_focus()
        self.show_toast("Conversa de voz encerrada.")
        if self.get_visible() and self.is_active():
            self.set_visible(False)
        else:
            self.summon_hud()

    def trigger_direct_crop(self) -> None:
        """Dispara imediatamente o recorte de área da tela a partir de atalho global (Super+Shift+S)."""
        if self.get_visible():
            self.set_visible(False)
        self._start_screen_capture(interactive=True, direct_mode=True)

    def _clear_active_vision(self, _btn: Gtk.Button | None = None) -> None:
        """Descarta o contexto visual ativo, retornando o chat para modo puramente textual."""
        self._active_image_bytes = None
        self._active_image_is_area = False
        self._active_image_is_clipboard = False
        self._current_ocr_text = None
        self.vision_preview_box.set_visible(False)
        self.ocr_btn.set_visible(False)
        self.show_toast("Contexto visual descartado. As próximas perguntas serão apenas em texto.")

    def _render_active_vision_thumbnail(self, image_bytes: bytes, is_area: bool = True, is_clipboard: bool = False) -> None:
        """Renderiza a miniatura visual no card com título contextual e badge de visão contínua."""
        try:
            gbytes = GLib.Bytes.new(image_bytes)
            texture = Gdk.Texture.new_from_bytes(gbytes)
            self.vision_thumbnail.set_paintable(texture)
            if is_clipboard:
                header_label = "<b>📋 Imagem da Área de Transferência</b>"
            elif is_area:
                header_label = "<b>✂️ Recorte de Tela Ativo</b>"
            else:
                header_label = "<b>🖥️ Captura de Tela Inteira Ativa</b>"
            self.vision_hdr_lbl.set_markup(header_label)
            self.vision_active_badge.set_visible(True)
            self.vision_preview_box.set_visible(True)
        except Exception:
            self.vision_preview_box.set_visible(False)

    def _build_ui(self) -> None:
        self.toolbar_view = Adw.ToolbarView()

        # HeaderBar nativa do GNOME / Zorin OS com controles de janela integrados
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Zorin Copilot", subtitle="Assistente Inteligente"))

        # Botão de Novo Chat / Nova Demanda (Estilo Gemini / Ctrl+N)
        self.new_chat_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        self.new_chat_btn.set_tooltip_text("Novo Chat / Nova Demanda (Ctrl+N)")
        self.new_chat_btn.add_css_class("flat")
        self.new_chat_btn.add_css_class("circular")
        self.new_chat_btn.add_css_class("glass-icon-btn")
        self.new_chat_btn.connect("clicked", lambda _: self._on_new_topic())
        header.pack_start(self.new_chat_btn)

        # Botão de Configurações
        settings_btn = Gtk.Button.new_from_icon_name("preferences-system-symbolic")
        settings_btn.set_tooltip_text("Configurações do Assistente e Chaves de IA")
        settings_btn.add_css_class("flat")
        settings_btn.add_css_class("circular")
        settings_btn.add_css_class("glass-icon-btn")
        settings_btn.connect("clicked", self._open_settings)

        # Botão de Histórico de Tópicos
        self.history_btn = Gtk.MenuButton()
        self.history_btn.set_icon_name("document-open-recent-symbolic")
        self.history_btn.set_tooltip_text("Histórico de Tópicos Salvos (Ctrl+H)")
        self.history_btn.add_css_class("flat")
        self.history_btn.add_css_class("circular")
        self.history_btn.add_css_class("glass-icon-btn")
        self._build_history_popover()

        # Botão de Conversa por Voz ao Vivo (Gemini Live)
        self.voice_call_btn = Gtk.Button.new_from_icon_name("audio-input-microphone-symbolic")
        self.voice_call_btn.set_tooltip_text("Conversa por Voz ao Vivo (Gemini Live / Ctrl+M)")
        self.voice_call_btn.add_css_class("flat")
        self.voice_call_btn.add_css_class("circular")
        self.voice_call_btn.add_css_class("glass-icon-btn")
        self.voice_call_btn.connect("clicked", lambda _: self.toggle_live_voice())

        # Botão indicador de IA no HeaderBar (clicável para abrir preferências)
        self.status_badge_btn = Gtk.Button()
        self.status_badge_btn.add_css_class("flat")
        self.status_badge_btn.add_css_class("pill")
        self.status_badge_btn.add_css_class("glass-pill")
        self.status_badge_btn.set_tooltip_text("Clique para alterar modelo ou provedor de IA")
        self.status_badge_btn.connect("clicked", self._open_settings)

        self.status_badge = Gtk.Label()
        self.status_badge.add_css_class("caption")
        self.status_badge_btn.set_child(self.status_badge)

        # Ordem pack_end: settings (direita), history, voice_call, status badge (esquerda)
        header.pack_end(settings_btn)
        header.pack_end(self.history_btn)
        header.pack_end(self.voice_call_btn)
        header.pack_end(self.status_badge_btn)

        self.toolbar_view.add_top_bar(header)

        # Controlador de teclado para tecla Escape e atalhos (Ctrl+P, Ctrl+N, Ctrl+H, Ctrl+M, Ctrl+Q)
        key_ctrl = Gtk.EventControllerKey()
        def on_key_pressed(_ctrl, keyval, _keycode, state):
            is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
            if is_ctrl and keyval in (Gdk.KEY_q, Gdk.KEY_Q):
                app = self.get_application()
                if app:
                    app.quit()
                return True
            if is_ctrl and keyval in (Gdk.KEY_m, Gdk.KEY_M):
                self.toggle_live_voice()
                return True
            if is_ctrl and keyval in (Gdk.KEY_h, Gdk.KEY_H):
                if self.history_popover.get_visible():
                    self.history_popover.popdown()
                else:
                    self.history_popover.popup()
                return True
            if is_ctrl and keyval in (Gdk.KEY_p, Gdk.KEY_P):
                self._on_toggle_pin()
                return True
            if is_ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self._on_new_topic()
                return True
            if keyval == Gdk.KEY_Escape:
                if self.live_client and self.live_client.is_active():
                    self.stop_live_voice()
                    return True
                if self.history_popover.get_visible():
                    self.history_popover.popdown()
                    return True
                if self.entry.get_text():
                    self.entry.set_text("")
                    return True
                elif self.session.is_pinned:
                    self._on_new_topic()
                    return True
                else:
                    self.set_visible(False)
                    return True
            return False
        key_ctrl.connect("key-pressed", on_key_pressed)
        self.add_controller(key_ctrl)

        clamp = Adw.Clamp(maximum_size=720)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(16)
        main_box.set_margin_end(16)

        # ---------------------------------------------------------------------
        # Campo de Entrada (Prompt)
        # ---------------------------------------------------------------------
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        
        self.entry = Gtk.Entry()
        self.entry.add_css_class("glass-entry")
        self.entry.set_placeholder_text("Ex: 'abrir zorin look', 'como acessar o gmail', 'modo escuro'...")
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "system-search-symbolic")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self._on_submit)
        self.entry.connect("changed", self._on_entry_changed)
        input_box.append(self.entry)

        # Botão de Visão Computacional (Captura de tela e recorte de área)
        self.vision_btn = Gtk.MenuButton(valign=Gtk.Align.CENTER)
        self.vision_btn.set_icon_name("camera-photo-symbolic")
        self.vision_btn.set_tooltip_text("Visão Computacional: Ler ou recortar a tela com IA")
        self.vision_btn.add_css_class("flat")
        self.vision_btn.add_css_class("circular")
        self.vision_btn.add_css_class("glass-icon-btn")

        vision_popover = Gtk.Popover()
        vision_pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vision_pop_box.set_margin_top(6)
        vision_pop_box.set_margin_bottom(6)
        vision_pop_box.set_margin_start(6)
        vision_pop_box.set_margin_end(6)

        # 1. Recortar Área da Tela
        area_btn = Gtk.Button()
        area_btn.add_css_class("flat")
        area_btn.add_css_class("glass-menu-item")
        area_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        area_box.set_margin_start(8)
        area_box.set_margin_end(8)
        area_box.set_margin_top(4)
        area_box.set_margin_bottom(4)
        area_icon = Gtk.Image.new_from_icon_name("edit-cut-symbolic")
        area_icon.set_pixel_size(18)
        area_box.append(area_icon)
        area_lbl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        area_lbl_title = Gtk.Label(label="<b>Recortar Área da Tela</b>", use_markup=True, xalign=0)
        area_lbl_sub = Gtk.Label(label="Selecione com o mouse para leitura imediata", xalign=0)
        area_lbl_sub.add_css_class("dim-label")
        area_lbl_sub.add_css_class("caption")
        area_lbl_box.append(area_lbl_title)
        area_lbl_box.append(area_lbl_sub)
        area_box.append(area_lbl_box)
        area_btn.set_child(area_box)
        area_btn.connect("clicked", lambda _: (vision_popover.popdown(), self._start_screen_capture(interactive=True)))
        vision_pop_box.append(area_btn)

        vision_pop_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 2. Capturar Tela Inteira
        full_btn = Gtk.Button()
        full_btn.add_css_class("flat")
        full_btn.add_css_class("glass-menu-item")
        full_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        full_box.set_margin_start(8)
        full_box.set_margin_end(8)
        full_box.set_margin_top(4)
        full_box.set_margin_bottom(4)
        full_icon = Gtk.Image.new_from_icon_name("zoom-fit-best-symbolic")
        full_icon.set_pixel_size(18)
        full_box.append(full_icon)
        full_lbl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        full_lbl_title = Gtk.Label(label="<b>Capturar Tela Inteira</b>", use_markup=True, xalign=0)
        full_lbl_sub = Gtk.Label(label="Analisa toda a tela do desktop", xalign=0)
        full_lbl_sub.add_css_class("dim-label")
        full_lbl_sub.add_css_class("caption")
        full_lbl_box.append(full_lbl_title)
        full_lbl_box.append(full_lbl_sub)
        full_box.append(full_lbl_box)
        full_btn.set_child(full_box)
        full_btn.connect("clicked", lambda _: (vision_popover.popdown(), self._start_screen_capture(interactive=False)))
        vision_pop_box.append(full_btn)

        vision_popover.set_child(vision_pop_box)
        self.vision_btn.set_popover(vision_popover)
        input_box.append(self.vision_btn)

        # Botão de Clipboard Inteligente (Área de Transferência)
        self.clipboard_btn = Gtk.MenuButton(valign=Gtk.Align.CENTER)
        self.clipboard_btn.set_icon_name("edit-paste-symbolic")
        self.clipboard_btn.set_tooltip_text("Clipboard Inteligente: Analisar, traduzir ou explicar conteúdo copiado")
        self.clipboard_btn.add_css_class("flat")
        self.clipboard_btn.add_css_class("circular")
        self.clipboard_btn.add_css_class("glass-icon-btn")

        clipboard_popover = Gtk.Popover()
        clipboard_pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        clipboard_pop_box.set_margin_top(6)
        clipboard_pop_box.set_margin_bottom(6)
        clipboard_pop_box.set_margin_start(6)
        clipboard_pop_box.set_margin_end(6)

        # Prévia dinâmica do clipboard
        clip_preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        clip_preview_box.set_margin_start(8)
        clip_preview_box.set_margin_end(8)
        clip_preview_box.set_margin_top(4)
        clip_preview_box.set_margin_bottom(6)

        clip_preview_icon = Gtk.Image.new_from_icon_name("edit-paste-symbolic")
        clip_preview_icon.set_pixel_size(14)
        clip_preview_icon.add_css_class("dim-label")
        clip_preview_box.append(clip_preview_icon)

        self.clip_preview_lbl = Gtk.Label(xalign=0)
        self.clip_preview_lbl.add_css_class("caption")
        self.clip_preview_lbl.add_css_class("dim-label")
        self.clip_preview_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.clip_preview_lbl.set_max_width_chars(34)
        self.clip_preview_lbl.set_text(ClipboardService.get_preview(34))
        clip_preview_box.append(self.clip_preview_lbl)

        clipboard_pop_box.append(clip_preview_box)
        clipboard_pop_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        def _update_clipboard_popover_preview(*_):
            self.clip_preview_lbl.set_text(ClipboardService.get_preview(34))

        clipboard_popover.connect("show", _update_clipboard_popover_preview)

        def make_clip_item(title: str, sub: str, icon_name: str, prompt_text: str) -> Gtk.Button:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("glass-menu-item")
            item_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            item_box.set_margin_start(8)
            item_box.set_margin_end(8)
            item_box.set_margin_top(4)
            item_box.set_margin_bottom(4)

            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(18)
            item_box.append(icon)

            lbl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lbl_title = Gtk.Label(label=f"<b>{html.escape(title)}</b>", use_markup=True, xalign=0)
            lbl_sub = Gtk.Label(label=sub, xalign=0)
            lbl_sub.add_css_class("dim-label")
            lbl_sub.add_css_class("caption")
            lbl_box.append(lbl_title)
            lbl_box.append(lbl_sub)
            item_box.append(lbl_box)

            btn.set_child(item_box)
            def on_item_clicked(_):
                clipboard_popover.popdown()
                self._trigger_prompt(prompt_text)
            btn.connect("clicked", on_item_clicked)
            return btn

        # 1. Explicar Código Copiado
        clipboard_pop_box.append(
            make_clip_item(
                "Explicar Código Copiado",
                "Analisa lógica, bibliotecas e melhorias",
                "utilities-terminal-symbolic",
                "explique o código que acabei de copiar",
            )
        )

        # 2. Traduzir para Inglês
        clipboard_pop_box.append(
            make_clip_item(
                "Traduzir para Inglês",
                "Tradução contextual fluente do texto",
                "preferences-desktop-locale-symbolic",
                "traduza o texto selecionado para o inglês",
            )
        )

        # 3. Corrigir Gramática & Formalizar E-mail
        clipboard_pop_box.append(
            make_clip_item(
                "Corrigir & Formalizar E-mail",
                "Revisão gramatical e tom profissional",
                "mail-send-symbolic",
                "corrija a gramática e formalize este e-mail",
            )
        )

        # 4. Resumir Conteúdo Copiado
        clipboard_pop_box.append(
            make_clip_item(
                "Resumir Conteúdo Copiado",
                "Pontos essenciais e conclusões em tópicos",
                "view-list-bullet-symbolic",
                "resuma o que acabei de copiar",
            )
        )

        clipboard_pop_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 5. Analisar Conteúdo Copiado (Geral)
        clipboard_pop_box.append(
            make_clip_item(
                "Analisar Conteúdo Copiado",
                "Diagnóstico inteligente de texto ou imagem",
                "system-search-symbolic",
                "analisar copiado",
            )
        )

        clipboard_popover.set_child(clipboard_pop_box)
        self.clipboard_btn.set_popover(clipboard_popover)
        input_box.append(self.clipboard_btn)

        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        input_box.append(self.spinner)

        self.submit_btn = Gtk.Button(label="Pedir", valign=Gtk.Align.CENTER)
        self.submit_btn.add_css_class("suggested-action")
        self.submit_btn.add_css_class("pill")
        self.submit_btn.add_css_class("glass-submit-btn")
        self.submit_btn.connect("clicked", self._on_submit)
        input_box.append(self.submit_btn)

        main_box.append(input_box)

        # ---------------------------------------------------------------------
        # Barra Dinâmica de Detecção de Aplicativos Instalados (Revealer)
        # ---------------------------------------------------------------------
        self.app_preview_revealer = Gtk.Revealer()
        self.app_preview_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.app_preview_revealer.set_transition_duration(180)
        self.app_preview_revealer.set_reveal_child(False)

        self.app_preview_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.app_preview_card.add_css_class("card")
        self.app_preview_card.add_css_class("glass-card")
        self.app_preview_card.set_margin_top(0)
        self.app_preview_card.set_margin_bottom(2)
        self.app_preview_card.set_margin_start(2)
        self.app_preview_card.set_margin_end(2)

        self.app_preview_icon = Gtk.Image()
        self.app_preview_icon.set_pixel_size(26)
        self.app_preview_icon.set_margin_start(10)
        self.app_preview_icon.set_margin_top(8)
        self.app_preview_icon.set_margin_bottom(8)
        self.app_preview_card.append(self.app_preview_icon)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_hexpand(True)
        info_box.set_valign(Gtk.Align.CENTER)
        info_box.set_margin_end(8)

        # Linha superior: Nome da aplicação + Badge de status + Botão compacto ao lado
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_row.set_valign(Gtk.Align.CENTER)

        self.app_preview_title = Gtk.Label(xalign=0)
        self.app_preview_title.add_css_class("heading")
        title_row.append(self.app_preview_title)

        self.app_preview_badge = Gtk.Label(xalign=0)
        self.app_preview_badge.add_css_class("caption")
        self.app_preview_badge.set_valign(Gtk.Align.CENTER)
        title_row.append(self.app_preview_badge)

        # Botão encolhido e discreto ao lado da aplicação
        self.app_preview_launch_btn = Gtk.Button()
        self.app_preview_launch_btn.set_tooltip_text("Abrir este aplicativo agora")
        self.app_preview_launch_btn.add_css_class("flat")
        self.app_preview_launch_btn.add_css_class("pill")
        self.app_preview_launch_btn.add_css_class("glass-launch-btn")
        self.app_preview_launch_btn.set_valign(Gtk.Align.CENTER)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        btn_box.set_margin_start(6)
        btn_box.set_margin_end(6)
        btn_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        btn_icon.set_pixel_size(12)
        btn_box.append(btn_icon)

        btn_label = Gtk.Label(label="Abrir")
        btn_label.add_css_class("caption")
        btn_box.append(btn_label)

        self.app_preview_launch_btn.set_child(btn_box)
        self.app_preview_launch_btn.connect("clicked", self._on_quick_launch_app)
        title_row.append(self.app_preview_launch_btn)

        info_box.append(title_row)

        self.app_preview_subtitle = Gtk.Label(xalign=0)
        self.app_preview_subtitle.add_css_class("caption")
        self.app_preview_subtitle.add_css_class("dim-label")
        info_box.append(self.app_preview_subtitle)

        self.app_preview_card.append(info_box)

        self.app_preview_revealer.set_child(self.app_preview_card)
        main_box.append(self.app_preview_revealer)

        # ---------------------------------------------------------------------
        # Painel de Voz ao Vivo (Gemini Live)
        # ---------------------------------------------------------------------
        self.live_voice_revealer = Gtk.Revealer()
        self.live_voice_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.live_voice_revealer.set_transition_duration(250)
        self.live_voice_revealer.set_reveal_child(False)
        main_box.append(self.live_voice_revealer)

        # ---------------------------------------------------------------------
        # Área Rolável de Conteúdo (Respostas e Ações)
        # ---------------------------------------------------------------------
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        # 1. Tela de Boas-vindas com Sugestões Rápidas (Compacta e Elegante)
        self.welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.welcome_box.set_valign(Gtk.Align.CENTER)
        self.welcome_box.set_halign(Gtk.Align.CENTER)
        self.welcome_box.set_margin_top(20)
        self.welcome_box.set_margin_bottom(12)

        header_welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header_welcome.set_halign(Gtk.Align.CENTER)

        welcome_icon = Gtk.Image.new_from_icon_name("system-help-symbolic")
        welcome_icon.set_pixel_size(36)
        welcome_icon.add_css_class("welcome-icon")
        header_welcome.append(welcome_icon)

        welcome_title = Gtk.Label(label="<b>Como posso ajudar hoje?</b>", use_markup=True)
        welcome_title.add_css_class("title-3")
        welcome_title.add_css_class("welcome-title")
        header_welcome.append(welcome_title)

        welcome_desc = Gtk.Label(label="Peça tarefas no desktop, consulte seus projetos ou converse por voz")
        welcome_desc.add_css_class("caption")
        welcome_desc.add_css_class("welcome-subtitle")
        header_welcome.append(welcome_desc)

        self.welcome_box.append(header_welcome)

        # Grid 2x2 com chips rápidos e compactos (ícones simbólicos na cor unificada)
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_halign(Gtk.Align.CENTER)
        grid.set_margin_top(6)

        suggestions = [
            ("audio-input-microphone-symbolic", "Voz ao Vivo (Gemini Live)", "voz_ao_vivo", 0, 0),
            ("edit-cut-symbolic", "Recortar Área da Tela", "recortar_area", 1, 0),
            ("edit-paste-symbolic", "Analisar Copiado", "analisar_copiado", 0, 1),
            ("weather-clear-night-symbolic", "Alternar modo escuro", "ativar modo escuro", 1, 1),
        ]

        for icon_name, label_text, prompt_val, col, row in suggestions:
            btn = Gtk.Button()
            btn.add_css_class("card")
            btn.add_css_class("pill")
            btn.add_css_class("glass-chip")

            chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            chip_box.set_halign(Gtk.Align.CENTER)
            chip_icon = Gtk.Image.new_from_icon_name(icon_name)
            chip_icon.set_pixel_size(16)
            chip_box.append(chip_icon)

            chip_lbl = Gtk.Label(label=label_text)
            chip_box.append(chip_lbl)
            btn.set_child(chip_box)

            def make_chip_click(p=prompt_val):
                return lambda _: self._trigger_prompt(p)
            btn.connect("clicked", make_chip_click())
            grid.attach(btn, col, row, 1, 1)

        self.welcome_box.append(grid)
        content_box.append(self.welcome_box)

        # Banner dinâmico de Tópico Ativo / Fixado
        self.topic_revealer = Gtk.Revealer()
        self.topic_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.topic_revealer.set_transition_duration(150)
        self.topic_revealer.set_reveal_child(False)

        topic_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        topic_card.add_css_class("card")
        topic_card.add_css_class("glass-card")
        topic_card.set_margin_start(2)
        topic_card.set_margin_end(2)
        topic_card.set_margin_bottom(2)

        self.topic_info_lbl = Gtk.Label(xalign=0)
        self.topic_info_lbl.set_use_markup(True)
        self.topic_info_lbl.set_markup("<b>📌 Tópico Fixado</b> • As próximas perguntas manterão este contexto")
        self.topic_info_lbl.add_css_class("caption")
        self.topic_info_lbl.set_hexpand(True)
        self.topic_info_lbl.set_margin_start(12)
        self.topic_info_lbl.set_margin_top(6)
        self.topic_info_lbl.set_margin_bottom(6)
        topic_card.append(self.topic_info_lbl)

        self.new_topic_btn = Gtk.Button(label="+ Novo Chat")
        self.new_topic_btn.set_tooltip_text("Iniciar novo chat para outra demanda independente (Ctrl+N)")
        self.new_topic_btn.add_css_class("flat")
        self.new_topic_btn.add_css_class("pill")
        self.new_topic_btn.set_valign(Gtk.Align.CENTER)
        self.new_topic_btn.set_margin_end(8)
        self.new_topic_btn.connect("clicked", self._on_new_topic)
        topic_card.append(self.new_topic_btn)

        self.topic_revealer.set_child(topic_card)
        content_box.append(self.topic_revealer)

        # 2. Grupo: Resposta / Explicação em Card Nativo
        self.answer_group = Adw.PreferencesGroup(title="Resposta")
        self.answer_group.set_visible(False)

        self.answer_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.answer_card.add_css_class("card")
        self.answer_card.add_css_class("glass-card")
        self.answer_card.set_margin_top(2)
        self.answer_card.set_margin_bottom(2)
        self.answer_card.set_margin_start(2)
        self.answer_card.set_margin_end(2)

        # Barra de topo interna do card: Ícone + Título + Badge + Botão de Fixar + Botão de Copiar
        card_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card_header.set_margin_start(14)
        card_header.set_margin_end(10)
        card_header.set_margin_top(10)

        header_icon = Gtk.Image.new_from_icon_name("system-help-symbolic")
        card_header.append(header_icon)

        card_title = Gtk.Label(label="<b>Resposta do Assistente</b>", use_markup=True, xalign=0)
        card_title.add_css_class("heading")
        card_title.set_hexpand(True)
        card_header.append(card_title)

        self.source_badge = Gtk.Label(xalign=1)
        self.source_badge.add_css_class("caption")
        self.source_badge.add_css_class("dim-label")
        self.source_badge.set_visible(False)
        card_header.append(self.source_badge)

        # Botão de fixar tópico (Pin) para manter contexto
        self.pin_btn = Gtk.Button()
        self.pin_btn.set_tooltip_text("Fixar este tópico para manter o contexto em perguntas seguintes (Ctrl+P)")
        self.pin_btn.add_css_class("flat")
        self.pin_btn.add_css_class("pill")
        self.pin_btn.add_css_class("glass-pill")
        self.pin_btn.add_css_class("glass-pin-btn")
        self.pin_btn.connect("clicked", self._on_toggle_pin)

        pin_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        pin_box.set_margin_start(4)
        pin_box.set_margin_end(4)
        self.pin_btn_icon = Gtk.Image.new_from_icon_name("view-pin-symbolic")
        self.pin_btn_icon.set_pixel_size(13)
        pin_box.append(self.pin_btn_icon)

        self.pin_btn_label = Gtk.Label(label="Fixar Tópico")
        self.pin_btn_label.add_css_class("caption")
        pin_box.append(self.pin_btn_label)

        self.pin_btn.set_child(pin_box)
        card_header.append(self.pin_btn)

        self.copy_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        self.copy_btn.set_tooltip_text("Copiar Resposta")
        self.copy_btn.add_css_class("flat")
        self.copy_btn.add_css_class("circular")
        self.copy_btn.add_css_class("glass-icon-btn")
        self.copy_btn.connect("clicked", self._on_copy_answer)
        card_header.append(self.copy_btn)

        self.answer_card.append(card_header)

        # Separador interno sutil
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_start(14)
        sep.set_margin_end(14)
        sep.set_margin_top(2)
        sep.set_margin_bottom(4)
        self.answer_card.append(sep)

        # Miniatura da captura visual analisada (se houver imagem)
        self.vision_preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.vision_preview_box.set_margin_start(14)
        self.vision_preview_box.set_margin_end(14)
        self.vision_preview_box.set_margin_top(4)
        self.vision_preview_box.set_margin_bottom(6)
        self.vision_preview_box.set_visible(False)

        # Header com título e botão para descartar a imagem ativa
        vision_hdr_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.vision_hdr_lbl = Gtk.Label(label="<b>Recorte de Tela Ativo:</b>", use_markup=True, xalign=0)
        self.vision_hdr_lbl.add_css_class("caption")
        self.vision_hdr_lbl.add_css_class("dim-label")
        self.vision_hdr_lbl.set_hexpand(True)
        vision_hdr_box.append(self.vision_hdr_lbl)

        self.vision_dismiss_btn = Gtk.Button()
        self.vision_dismiss_btn.set_icon_name("window-close-symbolic")
        self.vision_dismiss_btn.set_tooltip_text("Descartar esta imagem e continuar perguntas apenas em texto")
        self.vision_dismiss_btn.add_css_class("flat")
        self.vision_dismiss_btn.add_css_class("circular")
        self.vision_dismiss_btn.add_css_class("glass-icon-btn")
        self.vision_dismiss_btn.connect("clicked", self._clear_active_vision)
        vision_hdr_box.append(self.vision_dismiss_btn)

        self.vision_preview_box.append(vision_hdr_box)

        self.vision_thumbnail = Gtk.Picture()
        self.vision_thumbnail.set_can_shrink(True)
        self.vision_thumbnail.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.vision_thumbnail.set_size_request(-1, 140)
        self.vision_thumbnail.add_css_class("card")
        self.vision_preview_box.append(self.vision_thumbnail)

        # Badge indicadora de Visão Contínua ativa
        self.vision_active_badge = Gtk.Label(
            label="👁️ <i>Visão contínua ativa • Próximas perguntas usarão esta imagem como contexto</i>",
            use_markup=True,
            xalign=0,
        )
        self.vision_active_badge.add_css_class("dim-label")
        self.vision_active_badge.add_css_class("caption")
        self.vision_active_badge.set_margin_top(2)
        self.vision_preview_box.append(self.vision_active_badge)

        # Botão Smart OCR para cópia direta do conteúdo identificado na imagem
        self.ocr_btn = Gtk.Button()
        self.ocr_btn.set_tooltip_text("Copiar texto ou código extraído da imagem")
        self.ocr_btn.add_css_class("flat")
        self.ocr_btn.add_css_class("pill")
        self.ocr_btn.add_css_class("glass-pill")
        self.ocr_btn.set_halign(Gtk.Align.START)
        self.ocr_btn.set_margin_top(4)
        self.ocr_btn.set_visible(False)
        self.ocr_btn.connect("clicked", self._on_copy_ocr_text)

        ocr_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ocr_icon = Gtk.Image.new_from_icon_name("edit-copy-symbolic")
        ocr_icon.set_pixel_size(14)
        ocr_btn_box.append(ocr_icon)
        self.ocr_btn_label = Gtk.Label(label="Copiar Conteúdo Extraído")
        self.ocr_btn_label.add_css_class("caption")
        ocr_btn_box.append(self.ocr_btn_label)
        self.ocr_btn.set_child(ocr_btn_box)

        self.vision_preview_box.append(self.ocr_btn)

        self.answer_card.append(self.vision_preview_box)

        # Texto formatado da resposta (Markdown / Pango Markup)
        self.answer_label = Gtk.Label(xalign=0, yalign=0)
        self.answer_label.set_wrap(True)
        self.answer_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.answer_label.set_selectable(True)
        self.answer_label.set_use_markup(True)
        self.answer_label.set_margin_start(14)
        self.answer_label.set_margin_end(14)
        self.answer_label.set_margin_top(4)
        self.answer_label.set_margin_bottom(12)
        self.answer_card.append(self.answer_label)

        # Banner informativo quando IA não configurada
        self.config_ai_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.config_ai_banner.set_margin_start(14)
        self.config_ai_banner.set_margin_end(14)
        self.config_ai_banner.set_margin_bottom(10)
        self.config_ai_banner.set_visible(False)

        banner_lbl = Gtk.Label(
            label="<i>💡 Conecte o Google Gemini para raciocínio ilimitado e respostas completas.</i>",
            use_markup=True,
            xalign=0,
        )
        banner_lbl.set_hexpand(True)
        banner_lbl.add_css_class("dim-label")
        self.config_ai_banner.append(banner_lbl)

        self.config_ai_btn = Gtk.Button(label="Configurar Chave ⚙️")
        self.config_ai_btn.add_css_class("pill")
        self.config_ai_btn.add_css_class("glass-pill")
        self.config_ai_btn.connect("clicked", self._open_settings)
        self.config_ai_banner.append(self.config_ai_btn)

        self.answer_card.append(self.config_ai_banner)

        self.answer_group.add(self.answer_card)
        content_box.append(self.answer_group)

        # 3. Grupo: Ações Propostas no Desktop
        self.actions_group = Adw.PreferencesGroup(title="Ações Propostas")
        self.actions_group.set_visible(False)
        self.actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.actions_group.add(self.actions_box)
        content_box.append(self.actions_group)

        # Linha inferior de execução em lote / status geral
        self.exec_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.exec_box.set_margin_top(2)

        self.exec_all_btn = Gtk.Button(label="Executar Todas as Ações", valign=Gtk.Align.CENTER)
        self.exec_all_btn.add_css_class("suggested-action")
        self.exec_all_btn.add_css_class("pill")
        self.exec_all_btn.set_visible(False)
        self.exec_all_btn.connect("clicked", self._on_execute_all)
        self.exec_box.append(self.exec_all_btn)

        self.exec_status = Gtk.Label(label="", xalign=0)
        self.exec_status.add_css_class("dim-label")
        self.exec_status.set_hexpand(True)
        self.exec_box.append(self.exec_status)

        content_box.append(self.exec_box)

        scrolled.set_child(content_box)
        main_box.append(scrolled)

        clamp.set_child(main_box)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(clamp)
        self.toolbar_view.set_content(self.toast_overlay)
        self.set_content(self.toolbar_view)

    def show_toast(self, message: str) -> None:
        """Exibe uma notificação flutuante elegante na janela."""
        toast = Adw.Toast.new(message)
        self.toast_overlay.add_toast(toast)

    def _update_provider_badge(self) -> None:
        if self.config.is_configured():
            prov_name = {
                "gemini": f"Gemini ({self.config.gemini_model})",
                "ollama": f"Ollama ({self.config.ollama_model})",
                "openai": f"API ({self.config.openai_model})",
            }.get(self.config.provider, "IA Ativa")
            self.status_badge.set_text(f"● {prov_name}")
        else:
            self.status_badge.set_text("○ IA não configurada (⚙️)")

    def _open_settings(self, _btn: Gtk.Button) -> None:
        dialog = PreferencesDialog(self, on_saved=self._on_config_saved)
        dialog.present(self)

    def _on_config_saved(self, new_config: CopilotConfig) -> None:
        self.config = new_config
        self.engine.reload_config(new_config)
        self._update_provider_badge()

    def _trigger_prompt(self, text: str) -> None:
        """Dispara um prompt ou ação a partir de um chip de sugestão rápida."""
        if text == "voz_ao_vivo":
            self.toggle_live_voice()
            return
        if text == "recortar_area":
            self._start_screen_capture(interactive=True)
            return
        if text == "capturar_tela":
            self._start_screen_capture(interactive=False)
            return
        if text == "analisar_copiado":
            self.entry.set_text("📋 Analisar conteúdo da área de transferência")
            self._on_submit(self.entry)
            return
        self.entry.set_text(text)
        self._on_submit(self.entry)

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        """Monitora a digitação em tempo real para verificar se o app está instalado."""
        if self._search_debounce_timer:
            GLib.source_remove(self._search_debounce_timer)
            self._search_debounce_timer = None

        text = entry.get_text().strip()
        if not text:
            self.app_preview_revealer.set_reveal_child(False)
            self._matched_preview_app = None
            if not self.answer_group.get_visible():
                self.welcome_box.set_visible(True)
            return

        # Esconde imediatamente a tela de sugestões ao começar a digitar
        self.welcome_box.set_visible(False)

        if len(text) < 2:
            self.app_preview_revealer.set_reveal_child(False)
            self._matched_preview_app = None
            return

        def check_app():
            self._search_debounce_timer = None
            self._update_app_preview(text)
            return GLib.SOURCE_REMOVE

        self._search_debounce_timer = GLib.timeout_add(120, check_app)

    def _update_app_preview(self, text: str) -> None:
        """Verifica se há um app correspondente e atualiza a barra de prévia dinâmica."""
        is_launch, target_name = AppManager.is_app_launch_intent(text)
        if not is_launch or not target_name:
            self.app_preview_revealer.set_reveal_child(False)
            self._matched_preview_app = None
            return

        app, friendly_name = AppManager.find_app(target_name)
        if app:
            self._matched_preview_app = app
            if app.get_icon():
                self.app_preview_icon.set_from_gicon(app.get_icon())
            else:
                self.app_preview_icon.set_from_icon_name("application-x-executable-symbolic")

            self.app_preview_title.set_markup(f"<b>{html.escape(friendly_name)}</b>")
            subtitle = get_app_subtitle(app)
            self.app_preview_subtitle.set_text(subtitle)

            self.app_preview_badge.set_markup("<span foreground='#2ec27e'><b>✓ Instalado</b></span>")
            self.app_preview_launch_btn.set_visible(True)
            self.app_preview_launch_btn.set_tooltip_text(f"Abrir {friendly_name} agora")
            self.app_preview_revealer.set_reveal_child(True)
        else:
            self._matched_preview_app = None
            # Se for pedido explícito de abertura (ex: "abrir discord") e não estiver instalado:
            if any(text.lower().startswith(p) for p in ("abrir ", "abre ", "iniciar ", "inicia ", "rodar ", "executar ", "open ")):
                self.app_preview_icon.set_from_icon_name("dialog-warning-symbolic")
                self.app_preview_title.set_markup(f"<b>{html.escape(target_name)}</b> não encontrado")
                self.app_preview_subtitle.set_text("Nenhum aplicativo com este nome foi detectado no sistema.")
                self.app_preview_badge.set_markup("<span foreground='#e5a50a'><b>⚠️ Não instalado</b></span>")
                self.app_preview_launch_btn.set_visible(False)
                self.app_preview_revealer.set_reveal_child(True)
            else:
                self.app_preview_revealer.set_reveal_child(False)

    def _on_quick_launch_app(self, _btn: Gtk.Button) -> None:
        """Executa imediatamente o app detectado na barra de prévia sem precisar da IA."""
        if not self._matched_preview_app:
            return

        app = self._matched_preview_app
        ok, msg = AppManager.launch(app)
        self.exec_status.set_text(f"{'✓' if ok else '✗'} {msg}")
        self.engine.memory.log_action(
            prompt=self.entry.get_text().strip(),
            action_type=ActionType.LAUNCH_APP.value,
            target=app.get_name(),
            params={"app_id": app.get_id(), "executable": app.get_executable()},
            success=ok,
            message=msg,
        )
        self.app_preview_revealer.set_reveal_child(False)

    def _start_screen_capture(self, interactive: bool = True, direct_mode: bool = False) -> None:
        """Inicia a captura de tela (recorte ou tela cheia) ocultando temporariamente o Copilot."""
        if self._is_busy:
            return

        # Oculta a janela para não obstruir o que o usuário quer recortar/analisar
        self.set_visible(False)

        prompt_typed = self.entry.get_text().strip() if not direct_mode else ""

        def capture_worker():
            # Aguarda 200ms para que o compositor Wayland conclua a remoção visual da janela
            time.sleep(0.2)
            success, img_bytes, mode = ScreenCaptureService.capture(interactive=interactive)
            GLib.idle_add(self._on_capture_finished, success, img_bytes, mode, prompt_typed, interactive, direct_mode)

        threading.Thread(target=capture_worker, daemon=True).start()

    def _on_capture_finished(
        self,
        success: bool,
        image_bytes: bytes | None,
        mode: str,
        prompt_typed: str,
        is_area: bool,
        direct_mode: bool = False,
    ) -> bool:
        """Restaura a janela e dispara a análise multimodal na thread de IA."""
        if not success or not image_bytes:
            if direct_mode:
                # Cancelado via ESC pelo usuário de fora do app: mantém a janela fechada sem perturbar
                return False
            self.set_visible(True)
            self.present()
            self.show_toast("Captura de tela cancelada.")
            return False

        self.set_visible(True)
        self.present()
        self.entry.grab_focus()

        self._is_busy = True
        self.spinner.start()
        self.entry.set_sensitive(False)
        self.submit_btn.set_sensitive(False)
        self.vision_btn.set_sensitive(False)
        self.clipboard_btn.set_sensitive(False)
        self.welcome_box.set_visible(False)

        mode_name = "recorte selecionado" if is_area else "tela inteira"
        self.exec_status.set_text(f"📸 Lendo {mode_name} com IA...")

        if not prompt_typed:
            self.entry.set_text("✂️ Analisando recorte de tela..." if is_area else "🖥️ Analisando tela cheia...")

        # Guarda a imagem no contexto ativo para Visão Contínua (turnos subsequentes)
        self._active_image_bytes = image_bytes
        self._active_image_is_area = is_area
        self._active_image_is_clipboard = False

        # Renderiza a miniatura visual imediatamente
        self._render_active_vision_thumbnail(image_bytes, is_area=is_area, is_clipboard=False)

        def parse_thread():
            history = self.session.get_history_for_llm()
            plan = self.engine.parse(
                prompt_typed,
                history=history,
                image_bytes=image_bytes,
                is_area_capture=is_area,
            )
            prompt_label = prompt_typed or ("Recorte de tela" if is_area else "Captura de tela")
            GLib.idle_add(self._on_plan_ready, plan, prompt_label)

        threading.Thread(target=parse_thread, daemon=True).start()
        return False

    def _on_submit(self, _widget: Gtk.Widget) -> None:
        if self._search_debounce_timer:
            GLib.source_remove(self._search_debounce_timer)
            self._search_debounce_timer = None
        self.app_preview_revealer.set_reveal_child(False)

        text = self.entry.get_text().strip()
        if not text or self._is_busy:
            return

        low = text.lower()
        if any(w in low for w in ["copiad", "copiei", "clipboard", "área de transferência", "area de transferencia"]):
            kind, img_data = ClipboardService.get_content()
            if kind == "image" and isinstance(img_data, bytes):
                self._active_image_bytes = img_data
                self._active_image_is_area = False
                self._active_image_is_clipboard = True
                self._render_active_vision_thumbnail(img_data, is_area=False, is_clipboard=True)

        self._is_busy = True
        self.spinner.start()
        self.entry.set_sensitive(False)
        self.submit_btn.set_sensitive(False)
        self.vision_btn.set_sensitive(False)
        self.clipboard_btn.set_sensitive(False)
        self.welcome_box.set_visible(False)
        self.exec_status.set_text("Pensando...")

        # Visão contínua: reutiliza a imagem ativa para perguntas complementares
        active_img = getattr(self, "_active_image_bytes", None)
        active_is_area = getattr(self, "_active_image_is_area", False)

        # Se há imagem ativa e a sessão ainda não está fixada mas já possui turnos em memória, fixa para manter contexto
        if active_img and not self.session.is_pinned and self.session.turn_count == 0 and self.session._last_unpinned_turn:
            self.session.pin(title="Análise Visual")
            self._update_pin_ui()

        def parse_thread():
            history = self.session.get_history_for_llm()
            plan = self.engine.parse(
                text,
                history=history,
                image_bytes=active_img,
                is_area_capture=active_is_area,
            )
            GLib.idle_add(self._on_plan_ready, plan, text)

        threading.Thread(target=parse_thread, daemon=True).start()

    def _on_plan_ready(self, plan: ActionPlan, prompt_text: str = "") -> bool:
        self._is_busy = False
        self.spinner.stop()
        self.entry.set_sensitive(True)
        self.submit_btn.set_sensitive(True)
        self.vision_btn.set_sensitive(True)
        self.clipboard_btn.set_sensitive(True)
        self.current_plan = plan
        self.welcome_box.set_visible(False)

        # Se há imagem ativa no contexto, mantém a miniatura e badge de visão contínua visíveis
        if getattr(self, "_active_image_bytes", None):
            self._render_active_vision_thumbnail(
                self._active_image_bytes,
                is_area=getattr(self, "_active_image_is_area", False),
                is_clipboard=getattr(self, "_active_image_is_clipboard", False),
            )
        else:
            self.vision_preview_box.set_visible(False)

        # Smart OCR: Se houver texto ou código identificado na imagem
        ocr_text = plan.extracted_text or next((a.target for a in plan.actions if a.action_type == ActionType.SMART_OCR), None)
        if ocr_text and self.vision_preview_box.get_visible():
            self._current_ocr_text = ocr_text
            is_code = plan.extracted_kind in ("code", "código") or any(k in ocr_text for k in ["def ", "import ", "class ", "sudo ", "function", "const ", "let "])
            self.ocr_btn_label.set_text("📋 Copiar Código Extraído" if is_code else "📋 Copiar Texto da Imagem")
            self.ocr_btn.set_visible(True)
        else:
            self._current_ocr_text = None
            self.ocr_btn.set_visible(False)

        # 1. Renderiza a Resposta / Pensamento com Pango Markup
        explanation_text = plan.thought.strip()
        self._raw_answer_text = explanation_text
        if explanation_text:
            markup = format_markdown_to_markup(explanation_text)
            self.answer_label.set_markup(markup)
            self.answer_group.set_visible(True)

            # Registra o turno na sessão de tópicos e auto-persiste no SQLite (estilo Gemini)
            if prompt_text:
                self.session.record_turn(prompt=prompt_text, answer=explanation_text)
                self._save_current_session()
                self._update_pin_ui()

            # Badge da fonte: Visão, Web ou Memória
            if self.vision_preview_box.get_visible():
                self.source_badge.set_text("📸 Visão IA")
                self.source_badge.set_visible(True)
            elif "[Resultados da Pesquisa" in explanation_text or any(a.action_type == ActionType.OPEN_URL for a in plan.actions):
                self.source_badge.set_text("🌐 Web")
                self.source_badge.set_visible(True)
            elif "base de conhecimento" in explanation_text.lower() or "memorizado" in explanation_text.lower():
                self.source_badge.set_text("🧠 Memória")
                self.source_badge.set_visible(True)
            else:
                self.source_badge.set_visible(False)

            self.config_ai_banner.set_visible(not self.config.is_configured())
        else:
            self.answer_group.set_visible(False)

        # 2. Renderiza as Ações Executáveis com Botões Diretos e Ícones
        while child := self.actions_box.get_first_child():
            self.actions_box.remove(child)

        executable_actions = [a for a in plan.actions if a.action_type != ActionType.ANSWER]
        
        if executable_actions:
            self.actions_group.set_visible(True)
            for action in executable_actions:
                badge_desc = {
                    ActionType.LAUNCH_APP: "abrir aplicativo",
                    ActionType.OPEN_URL: "abrir link web",
                    ActionType.SYSTEM_CONTROL: "configuração do sistema",
                    ActionType.CLICK: "interação acessível",
                    ActionType.NOTIFY: "notificação",
                    ActionType.CAPTURE_SCREEN: "visão da tela",
                    ActionType.FIX_COMMAND: "auto-cura do sistema",
                    ActionType.SMART_OCR: "smart ocr",
                }.get(action.action_type, action.action_type.value)

                # Customização visual especializada para FIX_COMMAND e SMART_OCR
                if action.action_type == ActionType.FIX_COMMAND:
                    cmd_show = action.params.get("command") or action.target
                    row = Adw.ActionRow(
                        title=f"<b>⚡ Auto-Cura: {html.escape(action.target)}</b>",
                        subtitle=f"Comando: <tt><b>{html.escape(cmd_show)}</b></tt>",
                    )
                    row.set_use_markup(True)
                    exec_label = "Executar Correção"
                elif action.action_type == ActionType.SMART_OCR:
                    preview_txt = (action.target[:42] + "...") if len(action.target) > 42 else action.target
                    row = Adw.ActionRow(
                        title=f"<b>📋 Smart OCR: {html.escape(action.describe())}</b>",
                        subtitle=f"Texto: {html.escape(preview_txt)}",
                    )
                    row.set_use_markup(True)
                    exec_label = "Copiar Conteúdo"
                else:
                    row = Adw.ActionRow(
                        title=action.describe(),
                        subtitle=f"Tipo: {badge_desc}",
                    )
                    exec_label = "Recortar Agora" if (action.action_type == ActionType.CAPTURE_SCREEN and action.target == "area") else "Executar"

                row.add_css_class("card")
                row.add_css_class("glass-row")

                # Ícone semântico do desktop
                icon_name = "camera-photo-symbolic" if action.action_type == ActionType.CAPTURE_SCREEN else get_action_icon(action)
                prefix_icon = Gtk.Image.new_from_icon_name(icon_name)
                prefix_icon.set_pixel_size(22)
                row.add_prefix(prefix_icon)

                # Botão direto de execução na linha da ação
                exec_btn = Gtk.Button(label=exec_label)
                exec_btn.add_css_class("suggested-action")
                exec_btn.add_css_class("pill")
                exec_btn.set_valign(Gtk.Align.CENTER)

                if action.action_type == ActionType.CAPTURE_SCREEN:
                    is_area_target = (action.target == "area")
                    exec_btn.connect("clicked", lambda _, a=is_area_target: self._start_screen_capture(interactive=a))
                else:
                    def make_exec_handler(act: DesktopAction, btn: Gtk.Button):
                        def handler(_):
                            btn.set_sensitive(False)
                            btn.set_label("Executando...")
                            single_plan = ActionPlan(
                                thought=self.current_plan.thought if self.current_plan else "",
                                actions=[act],
                            )
                            reports = self.executor.execute_plan(single_plan, dry_run=False)
                            rep = reports[0] if reports else None
                            prompt_text = self.entry.get_text().strip()
                            if rep and rep.success:
                                btn.set_label("Executado ✓")
                                btn.remove_css_class("suggested-action")
                                btn.add_css_class("flat")
                                self.exec_status.set_text(f"✓ {rep.message}")
                            else:
                                err = rep.message if rep else "Erro"
                                btn.set_label("Falha ✗")
                                self.exec_status.set_text(f"✗ {err}")

                            if rep:
                                self.engine.memory.log_action(
                                    prompt=prompt_text,
                                    action_type=act.action_type.value,
                                    target=act.target,
                                    params=act.params,
                                    success=rep.success,
                                    message=rep.message,
                                )
                        return handler

                    exec_btn.connect("clicked", make_exec_handler(action, exec_btn))

                row.add_suffix(exec_btn)
                self.actions_box.append(row)

            if len(executable_actions) > 1:
                self.exec_all_btn.set_label(f"Executar Todas as {len(executable_actions)} Ações")
                self.exec_all_btn.set_visible(True)
                self.exec_all_btn.set_sensitive(True)
            else:
                self.exec_all_btn.set_visible(False)

            self.exec_status.set_text(f"{len(executable_actions)} ação(ões) disponível(is).")
        else:
            self.actions_group.set_visible(False)
            self.exec_all_btn.set_visible(False)
            self.exec_status.set_text("")

        return GLib.SOURCE_REMOVE

    def _on_execute_all(self, _widget: Gtk.Widget) -> None:
        if not self.current_plan:
            return

        prompt_text = self.entry.get_text().strip()
        reports = self.executor.execute_plan(self.current_plan, dry_run=False)
        for r in reports:
            self.engine.memory.log_action(
                prompt=prompt_text,
                action_type=r.action.action_type.value,
                target=r.action.target,
                params=r.action.params,
                success=r.success,
                message=r.message,
            )

        msgs = [r.message for r in reports]
        self.exec_status.set_text(" • ".join(msgs))
        self.exec_all_btn.set_sensitive(False)
        self.exec_all_btn.set_label("Todas Executadas ✓")

    def _on_copy_answer(self, _btn: Gtk.Button) -> None:
        text = self._raw_answer_text or self.answer_label.get_text()
        if not text:
            return
        display = Gdk.Display.get_default()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(text)
            self.copy_btn.set_icon_name("emblem-ok-symbolic")
            self.copy_btn.set_tooltip_text("Copiado com sucesso!")
            self.exec_status.set_text("✓ Resposta copiada para a área de transferência!")

            def reset_copy():
                self.copy_btn.set_icon_name("edit-copy-symbolic")
                self.copy_btn.set_tooltip_text("Copiar Resposta")
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(2000, reset_copy)

    def _on_copy_ocr_text(self, _btn: Gtk.Button) -> None:
        ocr_text = getattr(self, "_current_ocr_text", None)
        if not ocr_text:
            return
        ok = ClipboardService.set_text(ocr_text)
        if ok:
            old_label = self.ocr_btn_label.get_text()
            self.ocr_btn_label.set_text("✓ Conteúdo Copiado!")
            self.show_toast("Texto da imagem copiado para a área de transferência!")

            def reset_ocr():
                self.ocr_btn_label.set_text(old_label)
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(2200, reset_ocr)

    def _build_history_popover(self) -> None:
        """Constrói o popover de histórico de tópicos associado ao botão do HeaderBar."""
        self.history_popover = Gtk.Popover()
        self.history_popover.set_size_request(360, 420)
        self.history_btn.set_popover(self.history_popover)

        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        popover_box.set_margin_top(12)
        popover_box.set_margin_bottom(12)
        popover_box.set_margin_start(12)
        popover_box.set_margin_end(12)

        # Cabeçalho do Popover
        hdr_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_lbl = Gtk.Label(label="<b>Histórico de Tópicos</b>", use_markup=True, xalign=0)
        title_lbl.set_hexpand(True)
        hdr_box.append(title_lbl)

        new_btn = Gtk.Button(label="+ Novo Chat")
        new_btn.set_tooltip_text("Iniciar um novo chat pontual limpo (Ctrl+N)")
        new_btn.add_css_class("flat")
        new_btn.add_css_class("suggested-action")
        new_btn.connect("clicked", self._on_popover_new_chat)
        hdr_box.append(new_btn)

        popover_box.append(hdr_box)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        popover_box.append(sep)

        # Lista rolável de tópicos
        self.history_scrolled = Gtk.ScrolledWindow()
        self.history_scrolled.set_vexpand(True)
        self.history_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.history_listbox = Gtk.ListBox()
        self.history_listbox.add_css_class("boxed-list")
        self.history_listbox.connect("row-activated", self._on_history_row_activated)
        self.history_scrolled.set_child(self.history_listbox)
        popover_box.append(self.history_scrolled)

        # Rodapé com botão para limpar tudo
        self.clear_history_btn = Gtk.Button(label="Limpar Todo o Histórico")
        self.clear_history_btn.add_css_class("flat")
        self.clear_history_btn.add_css_class("destructive-action")
        self.clear_history_btn.connect("clicked", self._on_clear_all_history)
        popover_box.append(self.clear_history_btn)

        self.history_popover.set_child(popover_box)
        self.history_popover.connect("show", lambda _p: self._populate_history_list())

    def _on_popover_new_chat(self, _btn: Gtk.Button) -> None:
        self.history_popover.popdown()
        self._on_new_topic()

    def _populate_history_list(self) -> None:
        """Preenche o ListBox com os tópicos históricos recuperados do SQLite."""
        while row := self.history_listbox.get_first_child():
            self.history_listbox.remove(row)

        topics = self.engine.memory.list_chat_topics(limit=50)
        if not topics:
            empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            empty_box.set_valign(Gtk.Align.CENTER)
            empty_box.set_margin_top(40)
            empty_box.set_margin_bottom(40)

            icon = Gtk.Image.new_from_icon_name("document-open-recent-symbolic")
            icon.set_pixel_size(36)
            icon.add_css_class("dim-label")
            empty_box.append(icon)

            lbl = Gtk.Label(
                label="<b>Nenhum tópico salvo ainda</b>\n<span size='small' alpha='70%'>Fixe um tópico (📌) para mantê-lo\ne continuar o raciocínio depois.</span>",
                use_markup=True,
                justify=Gtk.Justification.CENTER,
            )
            empty_box.append(lbl)
            self.history_listbox.append(empty_box)
            self.clear_history_btn.set_visible(False)
            return

        self.clear_history_btn.set_visible(True)

        for topic in topics:
            row = Adw.ActionRow()
            is_current = bool(self.session.is_pinned and self.session.id == topic["id"])
            bullet = "● " if is_current else ""
            clean_title = topic["title"] or "Tópico Sem Título"
            if len(clean_title) > 42:
                clean_title = clean_title[:39] + "..."
            row.set_title(f"{bullet}{html.escape(clean_title)}")

            turns_num = topic["turn_count"]
            msg_str = f"{turns_num} {'pergunta' if turns_num == 1 else 'perguntas'}"
            time_str = format_relative_timestamp(topic["updated_at"])
            row.set_subtitle(f"{msg_str} • {time_str}")

            row.set_activatable(True)
            row._topic_id = topic["id"]

            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.add_css_class("circular")
            del_btn.set_tooltip_text("Excluir este tópico")
            del_btn.set_valign(Gtk.Align.CENTER)
            tid = topic["id"]
            del_btn.connect("clicked", lambda _b, t_id=tid: self._on_delete_topic(t_id))
            row.add_suffix(del_btn)

            self.history_listbox.append(row)

    def _on_history_row_activated(self, _listbox, row) -> None:
        topic_id = getattr(row, "_topic_id", None)
        if not topic_id:
            return
        self.history_popover.popdown()
        self._resume_topic(topic_id)

    def _resume_topic(self, topic_id: str) -> None:
        """Carrega e retoma uma sessão histórica de tópicos."""
        # Se a sessão atual estiver fixada e possuir turnos, garante seu salvamento prévio
        if self.session.is_pinned and self.session.turns:
            self._save_current_session()

        topic_data = self.engine.memory.get_chat_topic(topic_id)
        if not topic_data:
            return

        self.session.load_from_dict(topic_data)
        self._update_pin_ui()

        if self.session.turns:
            last_turn = self.session.turns[-1]
            self.entry.set_text("")
            self._raw_answer_text = last_turn.answer
            self.answer_label.set_markup(format_markdown_to_markup(last_turn.answer))
            self.actions_group.set_visible(False)
            self.answer_group.set_visible(True)
            self.welcome_box.set_visible(False)
            self.exec_status.set_text("📌 Tópico retomado! Digite sua próxima pergunta para continuar o raciocínio.")
        else:
            self.welcome_box.set_visible(True)
            self.answer_group.set_visible(False)

        self.entry.grab_focus()
        self.show_toast(f'Tópico "{self.session.title}" retomado!')

    def _save_current_session(self) -> None:
        """Persiste a sessão atual no banco de dados de tópicos (auto-save estilo Gemini)."""
        if not self.session.turns:
            return
        self.engine.memory.save_chat_topic(
            topic_id=self.session.id,
            title=self.session.title or (self.session.turns[0].prompt[:50] if self.session.turns else "Nova Demanda"),
            turns=[t.to_dict() for t in self.session.turns],
            is_pinned=True,
            created_at=self.session.created_at,
        )

    def _on_delete_topic(self, topic_id: str) -> None:
        self.engine.memory.delete_chat_topic(topic_id)
        if self.session.id == topic_id:
            self._on_new_topic()
        self._populate_history_list()
        self.show_toast("Chat excluído do histórico.")

    def _on_clear_all_history(self, _btn: Gtk.Button) -> None:
        self.engine.memory.clear_all_chat_topics()
        self.session.reset_new()
        self._update_pin_ui()
        self._populate_history_list()
        self.show_toast("Histórico de chats completamente limpo.")

    def _update_pin_ui(self) -> None:
        """Atualiza a aparência do indicador de chat e o banner da demanda ativa."""
        turns = self.session.turn_count
        if turns > 0 or self.session.title:
            self.pin_btn.add_css_class("suggested-action")
            self.pin_btn.set_tooltip_text("Demanda salva automaticamente no histórico. Clique para alternar fixação.")
            self.pin_btn_label.set_text("Chat Salvo ✓")
            msg_str = f"{turns} {'mensagem' if turns == 1 else 'mensagens'}"
            title_display = f' "{html.escape(self.session.title)}"' if self.session.title else ""
            self.topic_info_lbl.set_markup(
                f"<b>💬 Chat:{title_display}</b> ({msg_str}) • Histórico salvo automaticamente"
            )
            self.topic_revealer.set_reveal_child(True)
        else:
            self.pin_btn.remove_css_class("suggested-action")
            self.pin_btn.set_tooltip_text("Chat ativo sob demanda.")
            self.pin_btn_label.set_text("Chat Ativo")
            self.topic_revealer.set_reveal_child(False)

    def _on_toggle_pin(self, _btn: Gtk.Button | None = None) -> None:
        """Alterna o estado de fixação do tópico ativo."""
        was_pinned = self.session.is_pinned
        self.session.toggle_pin()
        self._update_pin_ui()
        if not was_pinned and self.session.is_pinned:
            if self.session.turns:
                self._save_current_session()
            self.exec_status.set_text("📌 Chat fixado e mantido no topo do histórico.")
        else:
            self.exec_status.set_text("📌 Chat mantido no histórico normal.")

    def _on_new_topic(self, _btn: Gtk.Button | None = None) -> None:
        """Salva o chat atual e inicia uma nova thread limpa para outra demanda (Estilo Gemini)."""
        if self.session.turns:
            self._save_current_session()
        self.session.reset_new()
        self._clear_active_vision()
        self._update_pin_ui()
        self.entry.set_text("")
        self.answer_group.set_visible(False)
        self.actions_group.set_visible(False)
        self.welcome_box.set_visible(True)
        self.entry.grab_focus()
        self.exec_status.set_text("Novo chat iniciado! Faça sua pergunta ou comando.")
        self.show_toast("✨ Novo chat iniciado!")


class ZorinCopilotApp(Adw.Application):
    """Aplicação Zorin Copilot com suporte a comando de linha, modo HUD e atalhos globais."""

    def __init__(self):
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.add_main_option(
            "toggle",
            ord("t"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Alterna a visibilidade do HUD do Copilot",
            None,
        )
        self.add_main_option(
            "crop",
            ord("c"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Dispara a seleção interativa de área da tela e analisa com IA",
            None,
        )
        self.add_main_option(
            "voice",
            ord("v"),
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Inicia imediatamente a conversa de voz ao vivo (Gemini Live)",
            None,
        )

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Garante o registro dos atalhos de sistema configurados no GNOME (HUD e Recorte)
        try:
            cfg = CopilotConfig.load()
            if cfg.global_shortcut_enabled:
                ShortcutManager.register(cfg.global_shortcut_key)
            if getattr(cfg, "crop_shortcut_enabled", True):
                ShortcutManager.register_crop(getattr(cfg, "crop_shortcut_key", "<Super><Shift>s"))
        except Exception:
            pass

    def _get_or_create_window(self) -> CopilotWindow:
        for win in self.get_windows():
            if isinstance(win, CopilotWindow):
                return win
        return CopilotWindow(self)

    def do_activate(self):
        win = self._get_or_create_window()
        win.summon_hud()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict()
        is_toggle = options.contains("toggle")
        is_crop = options.contains("crop")
        is_voice = options.contains("voice")
        args = command_line.get_arguments()
        if "--toggle" in args or "-t" in args:
            is_toggle = True
        if "--crop" in args or "-c" in args or "--snippet" in args:
            is_crop = True
        if "--voice" in args or "-v" in args:
            is_voice = True

        win = self._get_or_create_window()
        if is_voice:
            win.summon_hud()
            win.start_live_voice()
        elif is_crop:
            win.trigger_direct_crop()
        elif is_toggle:
            win.toggle_hud()
        else:
            win.summon_hud()

        return 0


def main() -> int:
    import sys
    app = ZorinCopilotApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    main()
