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
from ..core.session import TopicSession, ChatTurn
from ..core.shortcuts import ShortcutManager
from ..core.vision import ScreenCaptureService
from ..shell.executor import ActionExecutor
from ..ai.live import GeminiLiveClient
from ..core.fence import ScreenFenceManager, FenceMode
from ..core.rag import LocalDocumentRAG
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
    if action.action_type == ActionType.MEDIA_CONTROL:
        return "multimedia-player-symbolic"
    if action.action_type == ActionType.WRITE_FILE:
        return "document-save-symbolic"
    if action.action_type == ActionType.ORGANIZE_FILES:
        return "folder-symbolic"
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
        self.set_default_size(880, 620)
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

        # Contexto de Visão Contínua (Retém recorte para conversas multiturn)
        self._active_image_bytes: bytes | None = None
        self._active_image_is_area: bool = False
        self._active_image_is_clipboard: bool = False
        self._current_ocr_text: str | None = None

        # Widget pendente durante raciocínio do modelo
        self._pending_turn_box: Gtk.Widget | None = None

        # Voz ao Vivo (Gemini Multimodal Live)
        self.live_client: GeminiLiveClient | None = None
        self.live_voice_widget: LiveVoiceWidget | None = None

        # Cerca de Proteção Espacial (Isolamento de Monitores no Wayland)
        self.fence = ScreenFenceManager()

        # RAG Local & Inteligência Documental (Indexação incremental em segundo plano)
        self.rag = LocalDocumentRAG(memory=self.engine.memory)
        self.executor.rag = self.rag
        self.engine.rag = self.rag
        self.rag.start_background_indexing()

        self._build_ui()
        setup_glass_window(self)
        self._update_provider_badge()
        self._rebuild_chat_stream()
        self._populate_sidebar_history()
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

    def toggle_sidebar(self, _btn: Gtk.Button | None = None) -> None:
        """Alterna a visibilidade da barra lateral de conversas."""
        is_revealed = self.sidebar_revealer.get_reveal_child()
        self.sidebar_revealer.set_reveal_child(not is_revealed)
        if not is_revealed:
            self._populate_sidebar_history(filter_query=self.sidebar_search.get_text().strip())

    def _build_fence_popover(self) -> None:
        """Constrói o menu suspenso de seleção de telas e Kill Switch."""
        popover = Gtk.Popover()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_margin_top(8)
        vbox.set_margin_bottom(8)
        vbox.set_margin_start(8)
        vbox.set_margin_end(8)

        title = Gtk.Label(label="<b>Cerca de Proteção Espacial</b>", use_markup=True, xalign=0)
        title.add_css_class("caption")
        title.set_margin_bottom(4)
        vbox.append(title)

        for m in self.fence.monitors:
            suffix = " (Principal)" if m.is_primary else " (Secundária)"
            row_btn = Gtk.Button(label=f"🖥️ {m.name}{suffix}")
            row_btn.add_css_class("flat")
            idx = m.index
            row_btn.connect("clicked", lambda _, i=idx, pop=popover: self._on_select_fence_monitor(i, pop))
            vbox.append(row_btn)

        all_btn = Gtk.Button(label="🌐 Todas as Telas (Livre)")
        all_btn.add_css_class("flat")
        all_btn.connect("clicked", lambda _, pop=popover: self._on_select_all_monitors(pop))
        vbox.append(all_btn)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        vbox.append(sep)

        kill_btn = Gtk.Button(label="🛑 Parada de Emergência (Kill Switch)")
        kill_btn.add_css_class("destructive-action")
        kill_btn.add_css_class("pill")
        kill_btn.connect("clicked", lambda _, pop=popover: self._on_toggle_kill_switch(pop))
        vbox.append(kill_btn)

        popover.set_child(vbox)
        self.fence_menu_btn.set_popover(popover)

    def _on_select_fence_monitor(self, monitor_idx: int, popover: Gtk.Popover) -> None:
        popover.popdown()
        ok = self.fence.set_active_monitor(monitor_idx)
        if ok:
            mon = self.fence.get_active_monitor()
            name = mon.name if mon else f"Monitor {monitor_idx}"
            self.fence_lbl.set_text(name)
            if self.live_client:
                self.live_client.fence = self.fence
                self.live_client.input_driver.fence = self.fence
            self.show_toast(f"🖥️ Cerca espacial fixada em: {name}")

    def _on_select_all_monitors(self, popover: Gtk.Popover) -> None:
        popover.popdown()
        self.fence.set_all_monitors()
        self.fence_lbl.set_text("Todas as Telas")
        if self.live_client:
            self.live_client.fence = self.fence
            self.live_client.input_driver.fence = self.fence
        self.show_toast("🌐 Cerca espacial expandida para todas as telas.")

    def _on_toggle_kill_switch(self, popover: Gtk.Popover) -> None:
        popover.popdown()
        if self.fence.is_emergency_stopped:
            self.fence.reset_emergency_stop()
            mon = self.fence.get_active_monitor()
            self.fence_lbl.set_text(mon.name if mon else "AOC 27\"")
            self.show_toast("✓ Parada de emergência desativada.")
        else:
            self.fence.trigger_emergency_stop()
            self.fence_lbl.set_text("🛑 BLOQUEADO")
            self.show_toast("🛑 KILL SWITCH ATIVADO: Automações suspensas.")

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
            self.live_client = GeminiLiveClient(
                config=self.config,
                executor=self.executor,
                memory=self.engine.memory,
            )
        self.live_client.rag = self.rag
        self.live_client.fence = self.fence
        self.live_client.input_driver.fence = self.fence

        self.live_voice_widget = LiveVoiceWidget(
            live_client=self.live_client,
            on_close=self.stop_live_voice,
        )
        self.live_voice_revealer.set_child(self.live_voice_widget)
        self.live_voice_revealer.set_reveal_child(True)
        self.voice_call_btn.add_css_class("suggested-action")
        self.bottom_voice_btn.add_css_class("suggested-action")
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
        self.bottom_voice_btn.remove_css_class("suggested-action")

        # Se houve atividade durante a chamada, registra no fluxo da conversa ativa
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
                    elif t_name == "media_control":
                        lines.append(f"- 🎵 Controle de mídia: **{t_args.get('action', '')}**")
                    elif t_name == "write_document":
                        lines.append(f"- 📝 Salvou documento: `{t_args.get('filename', '')}`")
                    elif t_name == "organize_directory":
                        lines.append(f"- 📁 Organizou pasta: `{t_args.get('directory', 'Downloads')}`")
                    else:
                        lines.append(f"- ⚡ Executou ferramenta: `{t_name}`")
                lines.append("")
            else:
                lines.append("Conversa bidirecional em tempo real concluída.")

            if summary.get("video_streamed"):
                frames = summary.get("video_frames", 0)
                lines.append(f"**🎥 Live Video:** Compartilhamento de tela contínuo ativo ({frames} frames analisados).\n")

            summary_text = "\n".join(lines).strip()
            prompt_label = "🎙️🎥 Chamada de Voz e Tela ao Vivo" if summary.get("video_streamed") else "🎙️ Conversa de Voz ao Vivo"

            turn = self.session.record_turn(prompt=prompt_label, answer=summary_text)
            self._save_current_session()
            self._update_pin_ui()

            # Renderiza o turno no fluxo de mensagens
            self.welcome_box.set_visible(False)
            turn_widget = self._create_turn_widget(turn)
            self.chat_stream_box.append(turn_widget)
            self._populate_sidebar_history()
            self._scroll_to_bottom()
        else:
            if not self.session.turns:
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
        self.entry.set_placeholder_text("Peça ao Zorin Copilot ou digite um comando...")
        self.show_toast("Contexto visual descartado.")

    def _render_active_vision_thumbnail(self, image_bytes: bytes, is_area: bool = True, is_clipboard: bool = False) -> None:
        """Renderiza a miniatura visual no card de anexo acima da barra de entrada."""
        try:
            gbytes = GLib.Bytes.new(image_bytes)
            texture = Gdk.Texture.new_from_bytes(gbytes)
            self.vision_thumbnail.set_paintable(texture)
            if is_clipboard:
                header_label = "<b>📋 Imagem da Área de Transferência Anexada</b>"
            elif is_area:
                header_label = "<b>✂️ Recorte de Tela Anexado</b>"
            else:
                header_label = "<b>🖥️ Captura de Tela Inteira Anexada</b>"
            self.vision_hdr_lbl.set_markup(header_label)
            self.vision_active_badge.set_visible(True)
            self.vision_preview_box.set_visible(True)
        except Exception:
            self.vision_preview_box.set_visible(False)

    def _build_ui(self) -> None:
        self.toolbar_view = Adw.ToolbarView()

        # ---------------------------------------------------------------------
        # HeaderBar nativa do Zorin OS / GNOME
        # ---------------------------------------------------------------------
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="Zorin Copilot", subtitle="Assistente Inteligente")
        header.set_title_widget(self.window_title)

        # Botão para alternar a barra lateral (Ctrl+H)
        self.sidebar_toggle_btn = Gtk.Button.new_from_icon_name("sidebar-show-symbolic")
        self.sidebar_toggle_btn.set_tooltip_text("Alternar barra lateral de conversas (Ctrl+H)")
        self.sidebar_toggle_btn.add_css_class("flat")
        self.sidebar_toggle_btn.add_css_class("circular")
        self.sidebar_toggle_btn.add_css_class("glass-icon-btn")
        self.sidebar_toggle_btn.connect("clicked", self.toggle_sidebar)
        header.pack_start(self.sidebar_toggle_btn)

        # Botão de Nova Conversa (Ctrl+N)
        self.new_chat_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        self.new_chat_btn.set_tooltip_text("Nova Conversa (Ctrl+N)")
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

        # Botão de Conversa por Voz ao Vivo (Header)
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

        # Seletor de Cerca de Monitor / Tela Ativa (HeaderBar)
        self.fence_menu_btn = Gtk.MenuButton()
        self.fence_menu_btn.add_css_class("flat")
        self.fence_menu_btn.add_css_class("pill")
        self.fence_menu_btn.add_css_class("glass-pill")
        self.fence_menu_btn.set_tooltip_text("Cerca Espacial: Monitor ativo para automações")

        fence_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        fence_icon = Gtk.Image.new_from_icon_name("video-display-symbolic")
        fence_icon.set_pixel_size(14)
        active_mon = self.fence.get_active_monitor()
        mon_name_init = active_mon.name if active_mon else "AOC 27\""
        self.fence_lbl = Gtk.Label(label=mon_name_init)
        self.fence_lbl.add_css_class("caption")
        fence_btn_box.append(fence_icon)
        fence_btn_box.append(self.fence_lbl)
        self.fence_menu_btn.set_child(fence_btn_box)
        self._build_fence_popover()

        # Ordem pack_end
        header.pack_end(settings_btn)
        header.pack_end(self.voice_call_btn)
        header.pack_end(self.fence_menu_btn)
        header.pack_end(self.status_badge_btn)

        self.toolbar_view.add_top_bar(header)

        # Atributos de compatibilidade
        self.history_btn = Gtk.MenuButton()
        self.history_popover = Gtk.Popover()
        self.topic_revealer = Gtk.Revealer()
        self.topic_info_lbl = Gtk.Label()
        self.pin_btn = Gtk.Button()
        self.pin_btn_label = Gtk.Label()
        self.pin_btn_icon = Gtk.Image()
        self.answer_group = Adw.PreferencesGroup()
        self.answer_group.set_visible(False)
        self.actions_group = Adw.PreferencesGroup()
        self.actions_group.set_visible(False)
        self.actions_box = Gtk.Box()
        self.exec_status = Gtk.Label()
        self.exec_all_btn = Gtk.Button()
        self.copy_btn = Gtk.Button()
        self.answer_label = Gtk.Label()

        # Controlador de atalhos de teclado
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
                self.toggle_sidebar()
                return True
            if is_ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self._on_new_topic()
                return True
            if is_ctrl and keyval in (Gdk.KEY_p, Gdk.KEY_P):
                self._on_toggle_pin()
                return True
            if keyval == Gdk.KEY_Escape:
                if self.live_client and self.live_client.is_active():
                    self.stop_live_voice()
                    return True
                if self.entry.get_text():
                    self.entry.set_text("")
                    return True
                elif self.sidebar_search.get_text():
                    self.sidebar_search.set_text("")
                    return True
                else:
                    self.set_visible(False)
                    return True
            return False
        key_ctrl.connect("key-pressed", on_key_pressed)
        self.add_controller(key_ctrl)

        # ---------------------------------------------------------------------
        # Layout Principal Dividido: Barra Lateral (Esquerda) + Chat (Direita)
        # ---------------------------------------------------------------------
        self.split_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        # =====================================================================
        # 1. BARRA LATERAL ESTILO GEMINI (Collapsible Sidebar)
        # =====================================================================
        self.sidebar_revealer = Gtk.Revealer()
        self.sidebar_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self.sidebar_revealer.set_transition_duration(200)
        self.sidebar_revealer.set_reveal_child(True)

        sidebar_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sidebar_panel.add_css_class("sidebar-panel")
        sidebar_panel.set_size_request(260, -1)
        sidebar_panel.set_hexpand(False)

        # Topo da Sidebar: Título e Botão de Recolher
        s_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        s_title = Gtk.Label(label="<b>Conversas</b>", use_markup=True, xalign=0)
        s_title.add_css_class("heading")
        s_title.set_hexpand(True)
        s_top.append(s_title)

        s_close_btn = Gtk.Button.new_from_icon_name("pan-start-symbolic")
        s_close_btn.set_tooltip_text("Recolher barra lateral (Ctrl+H)")
        s_close_btn.add_css_class("flat")
        s_close_btn.add_css_class("circular")
        s_close_btn.add_css_class("glass-icon-btn")
        s_close_btn.connect("clicked", self.toggle_sidebar)
        s_top.append(s_close_btn)
        sidebar_panel.append(s_top)

        # Botão + Nova conversa com alto destaque
        sidebar_new_btn = Gtk.Button()
        sidebar_new_btn.add_css_class("card")
        sidebar_new_btn.add_css_class("pill")
        sidebar_new_btn.add_css_class("glass-card")
        sidebar_new_btn.set_tooltip_text("Iniciar nova conversa limpa (Ctrl+N)")
        sidebar_new_btn.connect("clicked", lambda _: self._on_new_topic())

        s_new_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        s_new_box.set_margin_start(10)
        s_new_box.set_margin_end(10)
        s_new_box.set_margin_top(6)
        s_new_box.set_margin_bottom(6)
        s_new_icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
        s_new_icon.set_pixel_size(16)
        s_new_box.append(s_new_icon)
        s_new_lbl = Gtk.Label(label="<b>Nova conversa</b>", use_markup=True, xalign=0)
        s_new_box.append(s_new_lbl)
        sidebar_new_btn.set_child(s_new_box)
        sidebar_panel.append(sidebar_new_btn)

        # Campo de busca para filtrar conversas salvas
        self.sidebar_search = Gtk.SearchEntry()
        self.sidebar_search.set_placeholder_text("Pesquisar conversas...")
        self.sidebar_search.connect("search-changed", self._on_sidebar_search_changed)
        sidebar_panel.append(self.sidebar_search)

        # Lista rolável de conversas
        self.history_scrolled = Gtk.ScrolledWindow()
        self.history_scrolled.set_vexpand(True)
        self.history_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.history_listbox = Gtk.ListBox()
        self.history_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.history_listbox.connect("row-activated", self._on_history_row_activated)
        self.history_scrolled.set_child(self.history_listbox)
        sidebar_panel.append(self.history_scrolled)

        # Rodapé da sidebar: Limpar histórico
        self.clear_history_btn = Gtk.Button()
        self.clear_history_btn.add_css_class("flat")
        self.clear_history_btn.add_css_class("destructive-action")
        self.clear_history_btn.set_tooltip_text("Limpar todas as conversas gravadas")
        self.clear_history_btn.connect("clicked", self._on_clear_all_history)
        cl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cl_box.set_halign(Gtk.Align.CENTER)
        cl_icon = Gtk.Image.new_from_icon_name("user-trash-symbolic")
        cl_icon.set_pixel_size(14)
        cl_box.append(cl_icon)
        cl_lbl = Gtk.Label(label="Limpar Histórico")
        cl_lbl.add_css_class("caption")
        cl_box.append(cl_lbl)
        self.clear_history_btn.set_child(cl_box)
        sidebar_panel.append(self.clear_history_btn)

        sidebar_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        sidebar_wrap.set_hexpand(False)
        sidebar_wrap.append(sidebar_panel)
        sidebar_wrap.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.sidebar_revealer.set_child(sidebar_wrap)
        self.sidebar_revealer.set_hexpand(False)
        self.split_box.append(self.sidebar_revealer)

        # =====================================================================
        # 2. ÁREA CENTRAL DO CHAT (Fluxo de Diálogo + Barra de Resposta Abaixo)
        # =====================================================================
        chat_main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        chat_main_box.set_hexpand(True)

        # Painel de Voz ao Vivo (Gemini Live) retrátil no topo do chat
        self.live_voice_revealer = Gtk.Revealer()
        self.live_voice_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.live_voice_revealer.set_transition_duration(200)
        self.live_voice_revealer.set_reveal_child(False)
        chat_main_box.append(self.live_voice_revealer)

        # ---------------------------------------------------------------------
        # 2.1 Fluxo de Conversa Rolável (Multi-turn Chat Stream)
        # ---------------------------------------------------------------------
        self.chat_scrolled = Gtk.ScrolledWindow()
        self.chat_scrolled.set_vexpand(True)
        self.chat_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        clamp_chat = Adw.Clamp(maximum_size=820)
        self.chat_stream_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.chat_stream_box.set_margin_start(16)
        self.chat_stream_box.set_margin_end(16)
        self.chat_stream_box.set_margin_top(16)
        self.chat_stream_box.set_margin_bottom(16)

        # Tela de Boas-vindas quando o chat está limpo (Sem mensagens)
        self.welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.welcome_box.set_valign(Gtk.Align.CENTER)
        self.welcome_box.set_halign(Gtk.Align.CENTER)
        self.welcome_box.set_margin_top(40)
        self.welcome_box.set_margin_bottom(20)

        header_welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header_welcome.set_halign(Gtk.Align.CENTER)

        welcome_icon = Gtk.Image.new_from_icon_name("system-help-symbolic")
        welcome_icon.set_pixel_size(44)
        welcome_icon.add_css_class("welcome-icon")
        header_welcome.append(welcome_icon)

        welcome_title = Gtk.Label(label="<b>Como posso ajudar hoje?</b>", use_markup=True)
        welcome_title.add_css_class("title-2")
        welcome_title.add_css_class("welcome-title")
        header_welcome.append(welcome_title)

        welcome_desc = Gtk.Label(label="Peça tarefas no desktop, consulte seus projetos ou converse por voz")
        welcome_desc.add_css_class("caption")
        welcome_desc.add_css_class("welcome-subtitle")
        header_welcome.append(welcome_desc)
        self.welcome_box.append(header_welcome)

        # Grid de sugestões rápidas
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(10)
        grid.set_halign(Gtk.Align.CENTER)
        grid.set_margin_top(10)

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
        self.chat_stream_box.append(self.welcome_box)

        clamp_chat.set_child(self.chat_stream_box)
        self.chat_scrolled.set_child(clamp_chat)
        chat_main_box.append(self.chat_scrolled)

        # ---------------------------------------------------------------------
        # 2.2 Barra de Resposta e Entrada Fixada Abaixo do Chat (Estilo Gemini)
        # ---------------------------------------------------------------------
        clamp_bottom = Adw.Clamp(maximum_size=820)
        bottom_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bottom_container.set_margin_start(16)
        bottom_container.set_margin_end(16)
        bottom_container.set_margin_bottom(10)
        bottom_container.set_margin_top(4)

        # Barra dinâmica de detecção de aplicativos instalados (abre acima da entrada)
        self.app_preview_revealer = Gtk.Revealer()
        self.app_preview_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.app_preview_revealer.set_transition_duration(180)
        self.app_preview_revealer.set_reveal_child(False)

        self.app_preview_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.app_preview_card.add_css_class("card")
        self.app_preview_card.add_css_class("glass-card")
        self.app_preview_card.set_margin_bottom(4)

        self.app_preview_icon = Gtk.Image()
        self.app_preview_icon.set_pixel_size(24)
        self.app_preview_icon.set_margin_start(10)
        self.app_preview_icon.set_margin_top(6)
        self.app_preview_icon.set_margin_bottom(6)
        self.app_preview_card.append(self.app_preview_icon)

        app_info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        app_info_box.set_hexpand(True)
        app_info_box.set_valign(Gtk.Align.CENTER)

        app_title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.app_preview_title = Gtk.Label(xalign=0)
        self.app_preview_title.add_css_class("heading")
        app_title_row.append(self.app_preview_title)

        self.app_preview_badge = Gtk.Label(xalign=0)
        self.app_preview_badge.add_css_class("caption")
        app_title_row.append(self.app_preview_badge)

        self.app_preview_launch_btn = Gtk.Button(label="Abrir")
        self.app_preview_launch_btn.add_css_class("suggested-action")
        self.app_preview_launch_btn.add_css_class("pill")
        self.app_preview_launch_btn.set_valign(Gtk.Align.CENTER)
        self.app_preview_launch_btn.connect("clicked", self._on_quick_launch_app)
        app_title_row.append(self.app_preview_launch_btn)

        app_info_box.append(app_title_row)

        self.app_preview_subtitle = Gtk.Label(xalign=0)
        self.app_preview_subtitle.add_css_class("caption")
        self.app_preview_subtitle.add_css_class("dim-label")
        app_info_box.append(self.app_preview_subtitle)

        self.app_preview_card.append(app_info_box)
        self.app_preview_revealer.set_child(self.app_preview_card)
        bottom_container.append(self.app_preview_revealer)

        # Card de prévia da imagem capturada/anexada (aparece acima da barra)
        self.vision_preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.vision_preview_box.add_css_class("card")
        self.vision_preview_box.add_css_class("glass-card")
        self.vision_preview_box.set_margin_bottom(4)
        self.vision_preview_box.set_margin_start(2)
        self.vision_preview_box.set_margin_end(2)
        self.vision_preview_box.set_visible(False)

        vision_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vision_hdr.set_margin_start(10)
        vision_hdr.set_margin_end(8)
        vision_hdr.set_margin_top(6)

        self.vision_hdr_lbl = Gtk.Label(label="<b>Recorte de Tela Ativo</b>", use_markup=True, xalign=0)
        self.vision_hdr_lbl.add_css_class("caption")
        self.vision_hdr_lbl.set_hexpand(True)
        vision_hdr.append(self.vision_hdr_lbl)

        vision_dismiss_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        vision_dismiss_btn.set_tooltip_text("Descartar anexo de imagem")
        vision_dismiss_btn.add_css_class("flat")
        vision_dismiss_btn.add_css_class("circular")
        vision_dismiss_btn.add_css_class("glass-icon-btn")
        vision_dismiss_btn.connect("clicked", self._clear_active_vision)
        vision_hdr.append(vision_dismiss_btn)
        self.vision_preview_box.append(vision_hdr)

        self.vision_thumbnail = Gtk.Picture()
        self.vision_thumbnail.set_can_shrink(True)
        self.vision_thumbnail.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.vision_thumbnail.set_size_request(-1, 100)
        self.vision_thumbnail.set_margin_start(10)
        self.vision_thumbnail.set_margin_end(10)
        self.vision_thumbnail.set_margin_bottom(6)
        self.vision_preview_box.append(self.vision_thumbnail)

        self.vision_active_badge = Gtk.Label(
            label="👁️ <i>Próxima mensagem usará esta imagem como contexto</i>",
            use_markup=True,
            xalign=0,
        )
        self.vision_active_badge.add_css_class("dim-label")
        self.vision_active_badge.add_css_class("caption")
        self.vision_active_badge.set_margin_start(10)
        self.vision_active_badge.set_margin_bottom(6)
        self.vision_preview_box.append(self.vision_active_badge)

        self.ocr_btn = Gtk.Button()
        self.ocr_btn_label = Gtk.Label(label="Copiar Texto da Imagem")
        self.ocr_btn.set_visible(False)
        self.vision_preview_box.append(self.ocr_btn)

        bottom_container.append(self.vision_preview_box)

        # O Card Principal da Barra de Prompt (Floating Pill)
        self.prompt_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.prompt_bar_box.add_css_class("prompt-bar-card")

        # Botão de Visão (Anexar / Recortar Tela)
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

        # Item 1: Recortar Área
        area_btn = Gtk.Button()
        area_btn.add_css_class("flat")
        area_btn.add_css_class("glass-menu-item")
        area_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        area_box.set_margin_start(6)
        area_box.set_margin_end(6)
        area_box.set_margin_top(4)
        area_box.set_margin_bottom(4)
        area_ic = Gtk.Image.new_from_icon_name("edit-cut-symbolic")
        area_ic.set_pixel_size(16)
        area_box.append(area_ic)
        area_l = Gtk.Label(label="<b>Recortar Área da Tela</b>", use_markup=True, xalign=0)
        area_box.append(area_l)
        area_btn.set_child(area_box)
        area_btn.connect("clicked", lambda _: (vision_popover.popdown(), self._start_screen_capture(interactive=True)))
        vision_pop_box.append(area_btn)

        # Item 2: Tela Inteira
        full_btn = Gtk.Button()
        full_btn.add_css_class("flat")
        full_btn.add_css_class("glass-menu-item")
        full_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        full_box.set_margin_start(6)
        full_box.set_margin_end(6)
        full_box.set_margin_top(4)
        full_box.set_margin_bottom(4)
        full_ic = Gtk.Image.new_from_icon_name("zoom-fit-best-symbolic")
        full_ic.set_pixel_size(16)
        full_box.append(full_ic)
        full_l = Gtk.Label(label="<b>Capturar Tela Inteira</b>", use_markup=True, xalign=0)
        full_box.append(full_l)
        full_btn.set_child(full_box)
        full_btn.connect("clicked", lambda _: (vision_popover.popdown(), self._start_screen_capture(interactive=False)))
        vision_pop_box.append(full_btn)

        vision_popover.set_child(vision_pop_box)
        self.vision_btn.set_popover(vision_popover)
        self.prompt_bar_box.append(self.vision_btn)

        # Botão de Clipboard Inteligente
        self.clipboard_btn = Gtk.MenuButton(valign=Gtk.Align.CENTER)
        self.clipboard_btn.set_icon_name("edit-paste-symbolic")
        self.clipboard_btn.set_tooltip_text("Clipboard Inteligente: Analisar texto ou imagem copiada")
        self.clipboard_btn.add_css_class("flat")
        self.clipboard_btn.add_css_class("circular")
        self.clipboard_btn.add_css_class("glass-icon-btn")

        clipboard_popover = Gtk.Popover()
        clip_pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        clip_pop_box.set_margin_top(6)
        clip_pop_box.set_margin_bottom(6)
        clip_pop_box.set_margin_start(6)
        clip_pop_box.set_margin_end(6)

        def make_clip_row(title: str, prompt_text: str, icon_name: str) -> Gtk.Button:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("glass-menu-item")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_start(6)
            box.set_margin_end(6)
            box.set_margin_top(4)
            box.set_margin_bottom(4)
            ic = Gtk.Image.new_from_icon_name(icon_name)
            ic.set_pixel_size(16)
            box.append(ic)
            lbl = Gtk.Label(label=title, xalign=0)
            box.append(lbl)
            btn.set_child(box)
            btn.connect("clicked", lambda _: (clipboard_popover.popdown(), self._trigger_prompt(prompt_text)))
            return btn

        clip_pop_box.append(make_clip_row("Explicar Código Copiado", "explique o código que acabei de copiar", "utilities-terminal-symbolic"))
        clip_pop_box.append(make_clip_row("Traduzir para Inglês", "traduza o texto selecionado para o inglês", "preferences-desktop-locale-symbolic"))
        clip_pop_box.append(make_clip_row("Resumir Conteúdo Copiado", "resuma o que acabei de copiar", "view-list-bullet-symbolic"))
        clip_pop_box.append(make_clip_row("Analisar Copiado Geral", "analisar copiado", "system-search-symbolic"))
        clipboard_popover.set_child(clip_pop_box)
        self.clipboard_btn.set_popover(clipboard_popover)
        self.prompt_bar_box.append(self.clipboard_btn)

        # Entrada de Texto Principal
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Peça ao Zorin Copilot ou digite um comando...")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self._on_submit)
        self.entry.connect("changed", self._on_entry_changed)
        self.prompt_bar_box.append(self.entry)

        # Botão de Microfone / Gemini Live
        self.bottom_voice_btn = Gtk.Button.new_from_icon_name("audio-input-microphone-symbolic")
        self.bottom_voice_btn.set_tooltip_text("Conversa por Voz ao Vivo (Gemini Live / Ctrl+M)")
        self.bottom_voice_btn.add_css_class("flat")
        self.bottom_voice_btn.add_css_class("circular")
        self.bottom_voice_btn.add_css_class("glass-icon-btn")
        self.bottom_voice_btn.connect("clicked", lambda _: self.toggle_live_voice())
        self.prompt_bar_box.append(self.bottom_voice_btn)

        # Spinner de carregamento
        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self.prompt_bar_box.append(self.spinner)

        # Botão de Envio (Pedir)
        self.submit_btn = Gtk.Button(valign=Gtk.Align.CENTER)
        self.submit_btn.add_css_class("suggested-action")
        self.submit_btn.add_css_class("circular")
        self.submit_btn.add_css_class("glass-submit-btn")
        self.submit_btn.set_tooltip_text("Enviar (Enter)")
        submit_icon = Gtk.Image.new_from_icon_name("pan-end-symbolic")
        submit_icon.set_pixel_size(16)
        self.submit_btn.set_child(submit_icon)
        self.submit_btn.connect("clicked", self._on_submit)
        self.prompt_bar_box.append(self.submit_btn)

        bottom_container.append(self.prompt_bar_box)

        # Legenda discreta abaixo da barra de resposta
        disclaimer_lbl = Gtk.Label(
            label="<span size='small' alpha='65%'>O Zorin Copilot é um assistente com IA e pode cometer erros. Verifique informações importantes.</span>",
            use_markup=True,
            xalign=0.5,
        )
        disclaimer_lbl.add_css_class("disclaimer-caption")
        bottom_container.append(disclaimer_lbl)

        clamp_bottom.set_child(bottom_container)
        chat_main_box.append(clamp_bottom)

        self.split_box.append(chat_main_box)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.split_box)
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
        """Restaura a janela e anexa a captura visual acima da barra de entrada."""
        if not success or not image_bytes:
            if direct_mode:
                return False
            self.set_visible(True)
            self.present()
            self.show_toast("Captura de tela cancelada.")
            return False

        self.set_visible(True)
        self.present()
        self.entry.grab_focus()

        # Guarda a imagem no contexto ativo para Visão Contínua (turnos subsequentes)
        self._active_image_bytes = image_bytes
        self._active_image_is_area = is_area
        self._active_image_is_clipboard = False

        # Renderiza a miniatura visual imediatamente no anexo flutuante
        self._render_active_vision_thumbnail(image_bytes, is_area=is_area, is_clipboard=False)

        if prompt_typed:
            self.entry.set_text(prompt_typed)
            self._on_submit(self.entry)
        else:
            self.entry.set_placeholder_text("Faça uma pergunta sobre esta imagem ou pressione Enter...")
            self.show_toast("📸 Imagem anexada! Digite sua pergunta e envie.")

        return False

    def _on_submit(self, _widget: Gtk.Widget) -> None:
        """Processa a mensagem do usuário, adiciona ao fluxo rolável e despacha para a IA."""
        if self._search_debounce_timer:
            GLib.source_remove(self._search_debounce_timer)
            self._search_debounce_timer = None
        self.app_preview_revealer.set_reveal_child(False)

        text = self.entry.get_text().strip()
        active_img = getattr(self, "_active_image_bytes", None)
        active_is_area = getattr(self, "_active_image_is_area", False)

        if not text and not active_img:
            return
        if self._is_busy:
            return

        if not text and active_img:
            text = "Analise o recorte da tela e explique o que está visível." if active_is_area else "Analise esta captura de tela."

        low = text.lower()
        if any(w in low for w in ["copiad", "copiei", "clipboard", "área de transferência", "area de transferencia"]):
            kind, img_data = ClipboardService.get_content()
            if kind == "image" and isinstance(img_data, bytes):
                self._active_image_bytes = img_data
                self._active_image_is_area = False
                self._active_image_is_clipboard = True
                active_img = img_data
                self._render_active_vision_thumbnail(img_data, is_area=False, is_clipboard=True)

        self._is_busy = True
        self.spinner.start()
        self.entry.set_sensitive(False)
        self.submit_btn.set_sensitive(False)
        self.vision_btn.set_sensitive(False)
        self.clipboard_btn.set_sensitive(False)
        self.bottom_voice_btn.set_sensitive(False)
        self.welcome_box.set_visible(False)

        # Limpa o texto da barra de entrada imediatamente (estilo Gemini)
        self.entry.set_text("")
        self.vision_preview_box.set_visible(False)

        # Adiciona o turno pendente imediatamente ao fluxo da conversa
        temp_turn = ChatTurn(prompt=text, answer="")
        self._pending_turn_box = self._create_turn_widget(
            temp_turn,
            image_bytes=active_img,
            is_pending=True,
        )
        self.chat_stream_box.append(self._pending_turn_box)
        self._scroll_to_bottom()

        def parse_thread():
            history = self.session.get_history_for_llm()
            plan = self.engine.parse(
                text,
                history=history,
                image_bytes=active_img,
                is_area_capture=active_is_area,
            )
            GLib.idle_add(self._on_plan_ready, plan, text, active_img)

        threading.Thread(target=parse_thread, daemon=True).start()

    def _on_plan_ready(self, plan: ActionPlan, prompt_text: str = "", attached_image: bytes | None = None) -> bool:
        """Recebe o plano gerado pela IA e substitui o indicador de carregamento pelo resultado final."""
        self._is_busy = False
        self.spinner.stop()
        self.entry.set_sensitive(True)
        self.submit_btn.set_sensitive(True)
        self.vision_btn.set_sensitive(True)
        self.clipboard_btn.set_sensitive(True)
        self.bottom_voice_btn.set_sensitive(True)
        self.current_plan = plan
        self.welcome_box.set_visible(False)

        # Remove o widget pendente
        if self._pending_turn_box and self._pending_turn_box.get_parent() == self.chat_stream_box:
            self.chat_stream_box.remove(self._pending_turn_box)
            self._pending_turn_box = None

        explanation_text = plan.thought.strip()
        if not explanation_text and plan.actions:
            explanation_text = "Executei a ação solicitada no desktop."

        # Registra no histórico do tópico e auto-salva no SQLite
        turn = self.session.record_turn(prompt=prompt_text, answer=explanation_text)
        self._save_current_session()
        self._update_pin_ui()

        # Renderiza o turno consolidado no fluxo de mensagens
        turn_widget = self._create_turn_widget(turn, plan=plan, image_bytes=attached_image)
        self.chat_stream_box.append(turn_widget)

        # Atualiza o subtítulo da janela com o título da conversa
        if self.session.title:
            self.window_title.set_subtitle(self.session.title)

        # Atualiza a lista lateral para colocar a conversa no topo
        self._populate_sidebar_history(filter_query=self.sidebar_search.get_text().strip())

        # Rola suavemente até o final
        self._scroll_to_bottom()
        self.entry.grab_focus()
        return GLib.SOURCE_REMOVE

    def _create_turn_widget(
        self,
        turn: ChatTurn,
        plan: ActionPlan | None = None,
        image_bytes: bytes | None = None,
        is_pending: bool = False,
    ) -> Gtk.Widget:
        """Constrói o widget de um turno completo de conversa (Pergunta do usuário + Resposta do assistente)."""
        turn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # ---------------------------------------------------------------------
        # 1. Mensagem do Usuário (Alinhada à direita em balão com avatar)
        # ---------------------------------------------------------------------
        user_wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        user_wrap.set_halign(Gtk.Align.END)
        user_wrap.set_hexpand(True)

        user_bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        user_bubble.add_css_class("user-chat-bubble")

        u_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        u_icon = Gtk.Image.new_from_icon_name("avatar-default-symbolic")
        u_icon.set_pixel_size(14)
        u_hdr.append(u_icon)

        u_name = Gtk.Label(label="<b>Você</b>", use_markup=True, xalign=0)
        u_name.add_css_class("caption")
        u_hdr.append(u_name)
        user_bubble.append(u_hdr)

        if image_bytes:
            try:
                pic = Gtk.Picture()
                pic.set_can_shrink(True)
                pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                pic.set_size_request(-1, 120)
                pic.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(image_bytes)))
                user_bubble.append(pic)
            except Exception:
                pass

        prompt_lbl = Gtk.Label(label=turn.prompt or "...", xalign=0)
        prompt_lbl.set_wrap(True)
        prompt_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        prompt_lbl.set_selectable(True)
        user_bubble.append(prompt_lbl)

        user_wrap.append(user_bubble)
        turn_box.append(user_wrap)

        # ---------------------------------------------------------------------
        # 2. Resposta do Assistente (Card Glassmorphic com Markdown e Ações)
        # ---------------------------------------------------------------------
        assistant_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        assistant_card.add_css_class("card")
        assistant_card.add_css_class("assistant-message-card")

        a_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        a_icon = Gtk.Image.new_from_icon_name("system-help-symbolic")
        a_icon.set_pixel_size(18)
        a_hdr.append(a_icon)

        a_name = Gtk.Label(label="<b>Zorin Copilot</b>", use_markup=True, xalign=0)
        a_name.add_css_class("heading")
        a_hdr.append(a_name)

        prov_str = self.config.gemini_model if self.config.provider == "gemini" else self.config.provider
        a_badge = Gtk.Label(label=f"● {prov_str}", xalign=0)
        a_badge.add_css_class("caption")
        a_badge.add_css_class("dim-label")
        a_badge.set_hexpand(True)
        a_hdr.append(a_badge)

        if not is_pending:
            copy_b = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            copy_b.set_tooltip_text("Copiar Resposta")
            copy_b.add_css_class("flat")
            copy_b.add_css_class("circular")
            copy_b.add_css_class("glass-icon-btn")
            txt_to_copy = turn.answer

            def on_copy(_b, t=txt_to_copy, btn=copy_b):
                disp = Gdk.Display.get_default()
                if disp:
                    disp.get_clipboard().set(t)
                    btn.set_icon_name("emblem-ok-symbolic")
                    self.show_toast("✓ Resposta copiada!")
                    GLib.timeout_add(2000, lambda: (btn.set_icon_name("edit-copy-symbolic"), GLib.SOURCE_REMOVE)[1])

            copy_b.connect("clicked", on_copy)
            a_hdr.append(copy_b)

        assistant_card.append(a_hdr)

        if is_pending:
            spin_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            spin_box.set_margin_top(8)
            spin_box.set_margin_bottom(8)
            sp = Gtk.Spinner()
            sp.start()
            spin_box.append(sp)
            spin_lbl = Gtk.Label(label="Pensando...", xalign=0)
            spin_lbl.add_css_class("dim-label")
            spin_box.append(spin_lbl)
            assistant_card.append(spin_box)
        else:
            markup = format_markdown_to_markup(turn.answer)
            ans_lbl = Gtk.Label(xalign=0, yalign=0)
            ans_lbl.set_wrap(True)
            ans_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            ans_lbl.set_selectable(True)
            ans_lbl.set_use_markup(True)
            ans_lbl.set_markup(markup)
            ans_lbl.set_margin_top(2)
            ans_lbl.set_margin_bottom(4)
            assistant_card.append(ans_lbl)

            self.answer_label = ans_lbl
            self._raw_answer_text = turn.answer

            # Renderiza ações executáveis se presentes no plano
            if plan and plan.actions:
                executable = [a for a in plan.actions if a.action_type != ActionType.ANSWER]
                if executable:
                    acts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                    acts_box.set_margin_top(8)

                    acts_title = Gtk.Label(label=f"<b>Ações Propostas ({len(executable)}):</b>", use_markup=True, xalign=0)
                    acts_title.add_css_class("caption")
                    acts_title.add_css_class("dim-label")
                    acts_box.append(acts_title)

                    for act in executable:
                        row = self._create_action_row(act)
                        acts_box.append(row)

                    if len(executable) > 1:
                        exec_all = Gtk.Button(label=f"Executar Todas as {len(executable)} Ações")
                        exec_all.add_css_class("suggested-action")
                        exec_all.add_css_class("pill")
                        exec_all.set_halign(Gtk.Align.START)
                        exec_all.set_margin_top(4)

                        def make_exec_all(p=plan, btn=exec_all, p_text=turn.prompt):
                            def on_exec_all(_):
                                btn.set_sensitive(False)
                                btn.set_label("Executando...")
                                reports = self.executor.execute_plan(p, dry_run=False)
                                for r in reports:
                                    self.engine.memory.log_action(
                                        prompt=p_text,
                                        action_type=r.action.action_type.value,
                                        target=r.action.target,
                                        params=r.action.params,
                                        success=r.success,
                                        message=r.message,
                                    )
                                btn.set_label("Todas Executadas ✓")
                                self.show_toast(f"✓ {len(reports)} ações executadas com sucesso!")
                            return on_exec_all

                        exec_all.connect("clicked", make_exec_all())
                        acts_box.append(exec_all)

                    assistant_card.append(acts_box)

            # Botão de cópia rápida para Smart OCR se houver texto ou código identificado
            ocr_text = (plan.extracted_text if plan else None) or getattr(self, "_current_ocr_text", None)
            if ocr_text:
                ocr_btn = Gtk.Button()
                ocr_btn.add_css_class("flat")
                ocr_btn.add_css_class("pill")
                ocr_btn.add_css_class("glass-pill")
                ocr_btn.set_halign(Gtk.Align.START)
                ocr_btn.set_margin_top(4)

                ocr_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                ocr_ic = Gtk.Image.new_from_icon_name("edit-copy-symbolic")
                ocr_ic.set_pixel_size(14)
                ocr_box.append(ocr_ic)
                ocr_lbl = Gtk.Label(label="Copiar Texto/Código Extraído")
                ocr_lbl.add_css_class("caption")
                ocr_box.append(ocr_lbl)
                ocr_btn.set_child(ocr_box)

                def on_copy_ocr(_b, ot=ocr_text, ol=ocr_lbl):
                    ClipboardService.set_text(ot)
                    ol.set_text("✓ Conteúdo Copiado!")
                    self.show_toast("Texto copiado para a área de transferência!")
                    GLib.timeout_add(2000, lambda: (ol.set_text("Copiar Texto/Código Extraído"), GLib.SOURCE_REMOVE)[1])

                ocr_btn.connect("clicked", on_copy_ocr)
                assistant_card.append(ocr_btn)

        turn_box.append(assistant_card)
        return turn_box

    def _create_action_row(self, action: DesktopAction) -> Gtk.Widget:
        """Renderiza uma linha de ação proposta com ícone semântico e botão direto de execução."""
        badge_desc = {
            ActionType.LAUNCH_APP: "abrir aplicativo",
            ActionType.OPEN_URL: "abrir link web",
            ActionType.SYSTEM_CONTROL: "configuração do sistema",
            ActionType.CLICK: "interação acessível",
            ActionType.NOTIFY: "notificação",
            ActionType.CAPTURE_SCREEN: "visão da tela",
            ActionType.FIX_COMMAND: "auto-cura do sistema",
            ActionType.SMART_OCR: "smart ocr",
            ActionType.MEDIA_CONTROL: "controle de mídia",
            ActionType.WRITE_FILE: "salvar documento",
            ActionType.ORGANIZE_FILES: "organização de arquivos",
        }.get(action.action_type, action.action_type.value)

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
        elif action.action_type == ActionType.WRITE_FILE:
            dest_dir = action.params.get("directory") or "~/Documentos/Relatorios"
            row = Adw.ActionRow(
                title=f"<b>📝 Salvar: {html.escape(action.describe())}</b>",
                subtitle=f"Destino: <tt>{html.escape(dest_dir)}</tt>",
            )
            row.set_use_markup(True)
            exec_label = "Salvar Arquivo"
        elif action.action_type == ActionType.ORGANIZE_FILES:
            target_dir = action.params.get("directory") or "~/Downloads"
            row = Adw.ActionRow(
                title=f"<b>📁 Organizar: {html.escape(action.describe())}</b>",
                subtitle=f"Pasta: <tt>{html.escape(target_dir)}</tt> (Lixeira reversível)",
            )
            row.set_use_markup(True)
            exec_label = "Organizar Agora"
        elif action.action_type == ActionType.MEDIA_CONTROL:
            row = Adw.ActionRow(
                title=f"<b>🎵 Mídia: {html.escape(action.describe())}</b>",
                subtitle="Spotify / Reprodutor ativo MPRIS2",
            )
            row.set_use_markup(True)
            exec_label = "Controlar"
        else:
            row = Adw.ActionRow(
                title=action.describe(),
                subtitle=f"Tipo: {badge_desc}",
            )
            exec_label = "Recortar Agora" if (action.action_type == ActionType.CAPTURE_SCREEN and action.target == "area") else "Executar"

        row.add_css_class("card")
        row.add_css_class("glass-row")

        icon_name = "camera-photo-symbolic" if action.action_type == ActionType.CAPTURE_SCREEN else get_action_icon(action)
        prefix_icon = Gtk.Image.new_from_icon_name(icon_name)
        prefix_icon.set_pixel_size(20)
        row.add_prefix(prefix_icon)

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
                        self.show_toast(f"✓ {rep.message}")
                    else:
                        err = rep.message if rep else "Erro"
                        btn.set_label("Falha ✗")
                        self.show_toast(f"✗ {err}")

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
        return row

    def _rebuild_chat_stream(self) -> None:
        """Reconstrói todo o fluxo de conversa a partir dos turnos da sessão ativa."""
        while child := self.chat_stream_box.get_first_child():
            self.chat_stream_box.remove(child)

        if not self.session.turns:
            self.chat_stream_box.append(self.welcome_box)
            self.welcome_box.set_visible(True)
            return

        self.welcome_box.set_visible(False)
        for i, turn in enumerate(self.session.turns):
            is_last = (i == len(self.session.turns) - 1)
            plan_to_use = self.current_plan if is_last else None
            turn_widget = self._create_turn_widget(turn, plan=plan_to_use)
            self.chat_stream_box.append(turn_widget)

        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        """Rola o fluxo rolável de mensagens até o fim com fluidez."""
        def _do_scroll():
            adj = self.chat_scrolled.get_vadjustment()
            if adj:
                adj.set_value(adj.get_upper() - adj.get_page_size())
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_do_scroll)
        GLib.timeout_add(60, _do_scroll)

    def _populate_sidebar_history(self, filter_query: str = "") -> None:
        """Preenche o ListBox lateral com os tópicos de chat recuperados do SQLite."""
        while row := self.history_listbox.get_first_child():
            self.history_listbox.remove(row)

        topics = self.engine.memory.list_chat_topics(limit=60)
        if filter_query:
            q = filter_query.lower().strip()
            topics = [
                t for t in topics
                if q in (t.get("title") or "").lower() or q in (t.get("preview") or "").lower()
            ]

        if not topics:
            empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            empty_box.set_valign(Gtk.Align.CENTER)
            empty_box.set_margin_top(30)
            empty_box.set_margin_bottom(30)

            empty_icon = Gtk.Image.new_from_icon_name("document-open-recent-symbolic")
            empty_icon.set_pixel_size(28)
            empty_icon.add_css_class("dim-label")
            empty_box.append(empty_icon)

            empty_lbl = Gtk.Label(
                label="<span size='small' alpha='70%'>Nenhuma conversa encontrada</span>" if filter_query else "<span size='small' alpha='70%'>Nenhuma conversa salva ainda</span>",
                use_markup=True,
                justify=Gtk.Justification.CENTER,
            )
            empty_box.append(empty_lbl)
            self.history_listbox.append(empty_box)
            self.clear_history_btn.set_visible(False)
            return

        self.clear_history_btn.set_visible(True)

        for topic in topics:
            row = Gtk.ListBoxRow()
            row.add_css_class("sidebar-chat-row")
            is_active = (self.session.id == topic["id"])
            if is_active:
                row.add_css_class("active")

            item_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            item_box.set_margin_top(4)
            item_box.set_margin_bottom(4)
            item_box.set_margin_start(4)
            item_box.set_margin_end(4)

            # Indicador / Ícone de conversa
            chat_icon = Gtk.Image.new_from_icon_name("user-available-symbolic" if is_active else "dialog-information-symbolic")
            chat_icon.set_pixel_size(14)
            item_box.append(chat_icon)

            # Informações de título e data
            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            info_box.set_hexpand(True)

            clean_title = topic["title"] or "Conversa Sem Título"
            title_lbl = Gtk.Label(label=clean_title, xalign=0)
            title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            title_lbl.set_max_width_chars(24)
            info_box.append(title_lbl)

            time_str = format_relative_timestamp(topic.get("updated_at", ""))
            turns_num = topic.get("turn_count", 0)
            sub_str = f"{time_str} • {turns_num} msg{'s' if turns_num != 1 else ''}"
            sub_lbl = Gtk.Label(label=sub_str, xalign=0)
            sub_lbl.add_css_class("caption")
            sub_lbl.add_css_class("dim-label")
            info_box.append(sub_lbl)

            item_box.append(info_box)

            # Botão de exclusão da conversa
            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.add_css_class("circular")
            del_btn.add_css_class("glass-icon-btn")
            del_btn.set_tooltip_text("Excluir conversa")
            del_btn.set_valign(Gtk.Align.CENTER)
            tid = topic["id"]
            del_btn.connect("clicked", lambda _b, t_id=tid: self._on_delete_topic(t_id))
            item_box.append(del_btn)

            row.set_child(item_box)
            row._topic_id = topic["id"]
            self.history_listbox.append(row)

    def _on_sidebar_search_changed(self, entry: Gtk.SearchEntry) -> None:
        """Filtra a lista lateral de conversas dinamicamente durante a digitação."""
        query = entry.get_text().strip()
        self._populate_sidebar_history(filter_query=query)

    def _on_history_row_activated(self, _listbox, row) -> None:
        """Abre uma conversa selecionada na barra lateral."""
        topic_id = getattr(row, "_topic_id", None)
        if not topic_id:
            return
        self._resume_topic(topic_id)

    def _resume_topic(self, topic_id: str) -> None:
        """Carrega e retoma uma conversa histórica com todo o seu fluxo de mensagens."""
        if self.session.turns:
            self._save_current_session()

        topic_data = self.engine.memory.get_chat_topic(topic_id)
        if not topic_data:
            return

        self.session.load_from_dict(topic_data)
        self.window_title.set_subtitle(self.session.title or "Conversa retomada")
        self._rebuild_chat_stream()
        self._populate_sidebar_history(filter_query=self.sidebar_search.get_text().strip())
        self.entry.grab_focus()
        self.show_toast(f'Conversa "{self.session.title}" aberta!')

    def _save_current_session(self) -> None:
        """Persiste a sessão atual no SQLite (auto-save estilo Gemini)."""
        if not self.session.turns:
            return
        self.engine.memory.save_chat_topic(
            topic_id=self.session.id,
            title=self.session.title or (self.session.turns[0].prompt[:50] if self.session.turns else "Nova Conversa"),
            turns=[t.to_dict() for t in self.session.turns],
            is_pinned=True,
            created_at=self.session.created_at,
        )

    def _on_delete_topic(self, topic_id: str) -> None:
        """Exclui uma conversa do histórico SQLite."""
        self.engine.memory.delete_chat_topic(topic_id)
        if self.session.id == topic_id:
            self._on_new_topic()
        else:
            self._populate_sidebar_history(filter_query=self.sidebar_search.get_text().strip())
        self.show_toast("Conversa excluída.")

    def _on_clear_all_history(self, _btn: Gtk.Button) -> None:
        """Limpa todas as conversas do histórico SQLite."""
        self.engine.memory.clear_all_chat_topics()
        self.session.reset_new()
        self.window_title.set_subtitle("Assistente Inteligente")
        self._rebuild_chat_stream()
        self._populate_sidebar_history()
        self.show_toast("Histórico de conversas completamente limpo.")

    def _update_pin_ui(self) -> None:
        """Atualiza estado visual da fixação de tópicos."""
        pass

    def _on_toggle_pin(self, _btn: Gtk.Button | None = None) -> None:
        """Alterna a fixação da conversa."""
        self.session.toggle_pin()
        if self.session.turns:
            self._save_current_session()
        self._populate_sidebar_history(filter_query=self.sidebar_search.get_text().strip())
        self.show_toast("Conversa fixada no topo.")

    def _on_new_topic(self, _btn: Gtk.Button | None = None) -> None:
        """Inicia uma nova conversa limpa (Ctrl+N / Estilo Gemini)."""
        if self.session.turns:
            self._save_current_session()
        self.session.reset_new()
        self._clear_active_vision()
        self.window_title.set_subtitle("Assistente Inteligente")
        self._rebuild_chat_stream()
        self._populate_sidebar_history(filter_query=self.sidebar_search.get_text().strip())
        self.entry.set_text("")
        self.entry.grab_focus()
        self.show_toast("✨ Nova conversa iniciada!")

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
        self.show_toast(" • ".join(msgs))

    def _on_copy_answer(self, _btn: Gtk.Button) -> None:
        text = self._raw_answer_text or self.answer_label.get_text()
        if not text:
            return
        display = Gdk.Display.get_default()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(text)
            self.show_toast("✓ Resposta copiada para a área de transferência!")

    def _on_copy_ocr_text(self, _btn: Gtk.Button) -> None:
        ocr_text = getattr(self, "_current_ocr_text", None)
        if not ocr_text:
            return
        ok = ClipboardService.set_text(ocr_text)
        if ok:
            self.show_toast("Texto copiado para a área de transferência!")

    def _build_history_popover(self) -> None:
        """Método de compatibilidade para histórico."""
        pass

    def _populate_history_list(self) -> None:
        """Método de compatibilidade."""
        self._populate_sidebar_history()


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
