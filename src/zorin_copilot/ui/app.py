# Decisão de design: a janela principal atua apenas como orquestradora — monta os componentes
# de interface (cabeçalho, barra lateral, fluxo de conversa, barra de prompt e anexo visual),
# mantém o estado da sessão e coordena o ciclo pergunta -> IA -> renderização. Toda a
# construção de widgets foi movida para `zorin_copilot.ui.widgets`.

"""Interface gráfica do Zorin Copilot em GTK4 / Libadwaita."""

from __future__ import annotations

import logging
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import __app_id__
from ..ai.actions import ActionPlan
from ..ai.engine import IntentEngine
from ..ai.live import GeminiLiveClient
from ..core.a11y import DesktopInspector
from ..core.attachments import (
    Attachment,
    AttachmentKind,
    compose_prompt,
    load_attachments,
)
from ..core.config import CopilotConfig
from ..core.export import (
    exportable_turns,
    render_conversation_markdown,
    suggest_filename,
)
from ..core.fence import ScreenFenceManager
from ..core.rag import LocalDocumentRAG
from ..core.session import TopicSession
from ..core.shortcuts import APP_SHORTCUTS, ShortcutManager
from ..shell.executor import ActionExecutor
from .live_view import LiveVoiceWidget
from .preferences import PreferencesDialog
from .style import setup_glass_window
from .widgets.attachments import AttachmentBar
from .widgets.chat_stream import (
    ChatStreamView,
    format_markdown_to_markup,  # noqa: F401 - reexportado por compatibilidade
    get_action_icon,  # noqa: F401 - reexportado por compatibilidade
)
from .widgets.command_palette import CommandPalette, PaletteCommand
from .widgets.drop_zone import DropZone
from .widgets.header import HeaderBarWidget
from .widgets.prompt_bar import (
    PromptBar,
    get_app_subtitle,  # noqa: F401 - reexportado por compatibilidade
)
from .widgets.sidebar import (
    SidebarPanel,
    format_relative_timestamp,  # noqa: F401 - reexportado por compatibilidade
)
from .widgets.vision import VisionAttachment

logger = logging.getLogger(__name__)


def _is_dialog_dismissed(error: GLib.Error) -> bool:
    """Distingue "usuário cancelou" de uma falha real do Gtk.FileDialog.

    Cancelar é uma ação legítima e não deve gerar toast de erro; qualquer outra
    falha do diálogo (portal ausente, permissão negada) deve.
    """
    quark_fn = getattr(Gtk, "dialog_error_quark", None)
    dismissed = getattr(getattr(Gtk, "DialogError", None), "DISMISSED", None)
    if quark_fn is None or dismissed is None:
        return False
    try:
        return bool(error.matches(quark_fn(), int(dismissed)))
    except (TypeError, ValueError):  # bindings antigos sem Gtk.DialogError
        return False


class CopilotWindow(Adw.ApplicationWindow):
    """Janela principal do Copilot: compõe os widgets e orquestra a conversa."""

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
        self.attachments: list[Attachment] = []
        self._raw_answer_text: str = ""
        self._is_busy = False
        self._pending_turn_box: Gtk.Widget | None = None

        # Contexto de Visão Contínua (retém recorte para conversas multi-turn)
        self._active_image_bytes: bytes | None = None
        self._active_image_is_area: bool = False
        self._active_image_is_clipboard: bool = False
        self._current_ocr_text: str | None = None

        # Voz ao Vivo (Gemini Multimodal Live)
        self.live_client: GeminiLiveClient | None = None
        self.live_voice_widget: LiveVoiceWidget | None = None

        # Cerca de Proteção Espacial (isolamento de monitores no Wayland)
        self.fence = ScreenFenceManager()

        # Campos usados por componentes; criados antes da construção dos widgets
        self.entry: Gtk.Entry = Gtk.Entry()
        self.answer_label: Gtk.Label = Gtk.Label()
        self.exec_status: Gtk.Label = Gtk.Label()

        # RAG Local & Inteligência Documental (indexação incremental em segundo plano)
        self.rag = LocalDocumentRAG(memory=self.engine.memory)
        self.executor.rag = self.rag
        self.engine.rag = self.rag
        self.rag.start_background_indexing()

        self._build_ui()
        # Alvo de arrastar-e-soltar na janela inteira, não só no fluxo de chat.
        self.drop_zone.attach_to(self)
        setup_glass_window(self)
        self.header.update_provider_badge()
        self.chat_stream.rebuild()
        self.sidebar.populate()
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.toolbar_view = Adw.ToolbarView()

        self.vision = VisionAttachment(self)
        self.attachment_bar = AttachmentBar(self)
        self.drop_zone = DropZone(self)
        self.prompt_bar = PromptBar(self)

        self.header = HeaderBarWidget(self)
        self.toolbar_view.add_top_bar(self.header.header)

        # Atributos de compatibilidade mantidos por código legado/testes
        self.answer_group = Adw.PreferencesGroup()
        self.answer_group.set_visible(False)
        self.actions_group = Adw.PreferencesGroup()
        self.actions_group.set_visible(False)
        self.actions_box = Gtk.Box()
        self.history_btn = Gtk.MenuButton()
        self.history_popover = Gtk.Popover()
        self.topic_revealer = Gtk.Revealer()
        self.topic_info_lbl = Gtk.Label()
        self.pin_btn = Gtk.Button()
        self.pin_btn_label = Gtk.Label()
        self.pin_btn_icon = Gtk.Image()
        self.exec_all_btn = Gtk.Button()
        self.copy_btn = Gtk.Button()

        self._install_key_controller()

        self.split_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        self.sidebar = SidebarPanel(self)
        self.split_box.append(self.sidebar.revealer)

        chat_main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        chat_main_box.set_hexpand(True)

        self.live_voice_revealer = Gtk.Revealer()
        self.live_voice_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.live_voice_revealer.set_transition_duration(200)
        self.live_voice_revealer.set_reveal_child(False)
        chat_main_box.append(self.live_voice_revealer)

        self.chat_stream = ChatStreamView(self)
        chat_main_box.append(self.chat_stream.scrolled)

        clamp_bottom = Adw.Clamp(maximum_size=820)
        clamp_bottom.set_child(self.prompt_bar.container)
        chat_main_box.append(clamp_bottom)

        self.split_box.append(chat_main_box)

        # O painel de comandos fica num overlay acima do conteúdo, mas abaixo dos
        # toasts, para que avisos continuem visíveis enquanto ele está aberto.
        self.main_overlay = Gtk.Overlay()
        self.main_overlay.set_child(self.split_box)
        # Véu de "solte aqui" primeiro: o painel de comandos deve ficar por cima.
        self.main_overlay.add_overlay(self.drop_zone.overlay)
        self.command_palette = CommandPalette(self)
        self.main_overlay.add_overlay(self.command_palette)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.main_overlay)
        self.toolbar_view.set_content(self.toast_overlay)
        self.set_content(self.toolbar_view)

    def _install_key_controller(self) -> None:
        """Instala os atalhos declarativos da aplicação e o handler de Escape."""
        self._install_app_shortcuts()

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _install_app_shortcuts(self) -> None:
        """Registra os atalhos de `APP_SHORTCUTS` via Gtk.ShortcutController.

        Antes as combinações eram comparadas manualmente contra keyvals; agora vêm
        de um registro único em `core.shortcuts`, reutilizável e documentável.
        """
        handlers = {
            "app.quit": self._quit_application,
            "app.toggle-live-voice": self.toggle_live_voice,
            "app.toggle-sidebar": self.toggle_sidebar,
            "app.new-topic": self._on_new_topic,
            "app.toggle-pin": self._on_toggle_pin,
            "app.command-palette": self._open_command_palette,
            "app.export-conversation": self.export_conversation,
        }

        controller = Gtk.ShortcutController()
        # Escopo GLOBAL garante que os atalhos funcionem mesmo com o foco no campo de texto
        controller.set_scope(Gtk.ShortcutScope.GLOBAL)

        for shortcut in APP_SHORTCUTS:
            handler = handlers.get(shortcut.name)
            if handler is None:
                continue
            trigger = Gtk.ShortcutTrigger.parse_string(shortcut.accelerator)
            if trigger is None:
                logger.warning("Atalho inválido ignorado: %s", shortcut.accelerator)
                continue
            action = Gtk.CallbackAction.new(self._make_shortcut_callback(handler))
            controller.add_shortcut(Gtk.Shortcut.new(trigger, action))

        self.app_shortcut_controller = controller
        self.add_controller(controller)

    @staticmethod
    def _make_shortcut_callback(handler):
        """Envolve o handler para o CallbackAction.

        Se o handler devolver ``False``, o atalho é considerado não tratado e o
        evento segue para o widget focado — é o que permite ao Ctrl+K conviver com
        o atalho de edição de texto do GTK.
        """
        def invoke(_widget, _args):
            result = handler()
            return True if result is None else bool(result)
        return invoke

    @staticmethod
    def _quit_application() -> None:
        app = Adw.Application.get_default()
        if app:
            app.quit()

    def _on_key_pressed(self, _ctrl, keyval, _keycode, state) -> bool:
        """Mantém apenas o Escape no controlador de teclas, por sua lógica em cascata."""
        if keyval == Gdk.KEY_Escape:
            return self._handle_escape()
        return False

    def _handle_escape(self) -> bool:
        """Esc fecha primeiro o que estiver aberto; só esconde a janela por último.

        A versão anterior fechava a janela mesmo com um popover aberto, descartando
        menus sem intenção do usuário.
        """
        if self.command_palette.is_open:
            self.command_palette.close()
            return True

        if self.live_client and self.live_client.is_active():
            self.stop_live_voice()
            return True

        if self._close_open_popovers():
            return True

        if self.entry.get_text():
            self.entry.set_text("")
            return True

        if self.sidebar.search.get_text():
            self.sidebar.search.set_text("")
            return True

        self.set_visible(False)
        return True

    def _close_open_popovers(self) -> bool:
        """Fecha qualquer popover visível ancorado nos botões da interface."""
        closed = False
        for button in (
            self.prompt_bar.vision_btn,
            self.prompt_bar.clipboard_btn,
            self.header.fence_menu_btn,
            self.history_btn,
        ):
            popover = button.get_popover() if isinstance(button, Gtk.MenuButton) else None
            if popover and popover.get_visible():
                popover.popdown()
                closed = True
        return closed

    # ------------------------------------------------------------------
    # Painel de comandos (Ctrl+K)
    # ------------------------------------------------------------------
    def _open_command_palette(self) -> bool:
        """Abre (ou fecha) o painel de comandos.

        Devolve ``False`` quando o foco está num campo de texto: o GTK usa Ctrl+K
        para apagar até o fim da linha, e retornar False deixa o evento seguir
        para o widget focado em vez de roubar a combinação.
        """
        if self.command_palette.is_open:
            self.command_palette.close()
            return True

        focus = self.get_focus()
        if isinstance(focus, (Gtk.Editable, Gtk.TextView)):
            return False

        self.command_palette.open()
        return True

    def palette_commands(self) -> list[PaletteCommand]:
        """Comandos oferecidos no painel. Reconstruído a cada abertura."""
        accels = {s.name: s.accelerator for s in APP_SHORTCUTS}

        def acc(name: str) -> str:
            return accels.get(name, "")

        commands = [
            PaletteCommand(
                "app.new-topic", "Nova conversa", "Começar um tópico limpo",
                "list-add-symbolic", acc("app.new-topic"), ("limpar", "novo", "chat"),
            ),
            PaletteCommand(
                "app.toggle-sidebar", "Mostrar/ocultar conversas", "Alterna a barra lateral",
                "sidebar-show-symbolic", acc("app.toggle-sidebar"), ("histórico", "lateral"),
            ),
            PaletteCommand(
                "app.toggle-pin", "Fixar/desafixar conversa", "Mantém o tópico atual no topo",
                "view-pin-symbolic", acc("app.toggle-pin"), ("pin", "fixar"),
            ),
            PaletteCommand(
                "app.toggle-live-voice", "Conversa por voz ao vivo", "Inicia ou encerra o Gemini Live",
                "audio-input-microphone-symbolic", acc("app.toggle-live-voice"), ("voz", "microfone", "live"),
            ),
            PaletteCommand(
                "app.capture-area", "Recortar área da tela", "Analisa um recorte com a IA",
                "edit-cut-symbolic", "", ("screenshot", "print", "captura", "recorte"),
            ),
            PaletteCommand(
                "app.analyze-clipboard", "Analisar conteúdo copiado", "Usa o texto da área de transferência",
                "edit-paste-symbolic", "", ("colar", "clipboard", "cópia"),
            ),
            PaletteCommand(
                "app.toggle-dark-mode", "Alternar modo escuro", "Muda o tema do sistema",
                "weather-clear-night-symbolic", "", ("tema", "escuro", "claro", "dark"),
            ),
            PaletteCommand(
                "app.copy-answer", "Copiar última resposta", "Copia a resposta do assistente",
                "edit-copy-symbolic", "", ("copiar", "clipboard"),
            ),
            PaletteCommand(
                "app.export-conversation", "Exportar conversa (.md)", "Salva a conversa em Markdown",
                "document-save-symbolic", acc("app.export-conversation"),
                ("exportar", "salvar", "markdown", "md", "arquivo"),
            ),
            PaletteCommand(
                "app.open-settings", "Preferências", "Provedor de IA e chaves",
                "preferences-system-symbolic", "", ("configurações", "chave", "api", "modelo"),
            ),
            PaletteCommand(
                "app.clear-history", "Limpar todo o histórico", "Apaga as conversas salvas",
                "user-trash-symbolic", "", ("apagar", "excluir", "lixeira"),
            ),
            PaletteCommand(
                "app.quit", "Sair do Zorin Copilot", "",
                "application-exit-symbolic", acc("app.quit"), ("fechar", "encerrar"),
            ),
        ]

        # Só aparece quando faz sentido: comando que não tem efeito polui a busca.
        if self.attachments:
            commands.append(
                PaletteCommand(
                    "app.clear-attachments", "Remover anexos do chat",
                    f"{len(self.attachments)} arquivo(s) em contexto",
                    "edit-clear-symbolic", "", ("anexo", "arquivo", "remover", "limpar"),
                )
            )
        return commands

    def _palette_handlers(self) -> dict[str, object]:
        """Mapa nome -> callable. Separado para poder ser verificado por testes."""
        return {
            "app.new-topic": self._on_new_topic,
            "app.toggle-sidebar": self.toggle_sidebar,
            "app.toggle-pin": self._on_toggle_pin,
            "app.toggle-live-voice": self.toggle_live_voice,
            "app.capture-area": lambda: self._start_screen_capture(interactive=True),
            "app.analyze-clipboard": lambda: self._trigger_prompt("analisar_copiado"),
            "app.toggle-dark-mode": lambda: self._trigger_prompt("ativar modo escuro"),
            "app.copy-answer": self._on_copy_answer,
            "app.export-conversation": self.export_conversation,
            "app.clear-attachments": self.clear_attachments,
            "app.open-settings": self._open_settings,
            "app.clear-history": self.sidebar.clear_history,
            "app.quit": self._quit_application,
        }

    def run_palette_command(self, command: PaletteCommand) -> None:
        """Executa o comando escolhido no painel."""
        handler = self._palette_handlers().get(command.name)
        if handler is None:
            logger.warning("Comando do painel sem handler: %s", command.name)
            return
        handler()

    def palette_closed(self) -> None:
        """Devolve o foco ao campo de prompt depois de fechar o painel."""
        self.entry.grab_focus()

    # ------------------------------------------------------------------
    # Compatibilidade de API (usada por testes e código legado)
    # ------------------------------------------------------------------
    @property
    def window_title(self) -> Adw.WindowTitle:
        return self.header.window_title

    @property
    def sidebar_revealer(self) -> Gtk.Revealer:
        return self.sidebar.revealer

    @property
    def sidebar_search(self) -> Gtk.SearchEntry:
        return self.sidebar.search

    @property
    def history_listbox(self) -> Gtk.ListBox:
        return self.sidebar.history_listbox

    @property
    def clear_history_btn(self) -> Gtk.Button:
        return self.sidebar.clear_history_btn

    @property
    def sidebar_toggle_btn(self) -> Gtk.Button:
        return self.header.sidebar_toggle_btn

    @property
    def new_chat_btn(self) -> Gtk.Button:
        return self.header.new_chat_btn

    @property
    def voice_call_btn(self) -> Gtk.Button:
        return self.header.voice_call_btn

    @property
    def status_badge_btn(self) -> Gtk.Button:
        return self.header.status_badge_btn

    @property
    def status_badge(self) -> Gtk.Label:
        return self.header.status_badge

    @property
    def fence_menu_btn(self) -> Gtk.MenuButton:
        return self.header.fence_menu_btn

    @property
    def fence_lbl(self) -> Gtk.Label:
        return self.header.fence_lbl

    @property
    def chat_stream_box(self) -> Gtk.Box:
        return self.chat_stream.stream_box

    @property
    def chat_scrolled(self) -> Gtk.ScrolledWindow:
        return self.chat_stream.scrolled

    @property
    def welcome_box(self) -> Gtk.Box:
        return self.chat_stream.welcome_box

    @property
    def prompt_bar_box(self) -> Gtk.Box:
        return self.prompt_bar.prompt_bar_box

    @property
    def app_preview_revealer(self) -> Gtk.Revealer:
        return self.prompt_bar.app_preview_revealer

    @property
    def vision_preview_box(self) -> Gtk.Box:
        return self.vision.preview_box

    @property
    def vision_btn(self) -> Gtk.MenuButton:
        return self.prompt_bar.vision_btn

    @property
    def clipboard_btn(self) -> Gtk.MenuButton:
        return self.prompt_bar.clipboard_btn

    @property
    def bottom_voice_btn(self) -> Gtk.Button:
        return self.prompt_bar.bottom_voice_btn

    @property
    def submit_btn(self) -> Gtk.Button:
        return self.prompt_bar.submit_btn

    @property
    def spinner(self) -> Gtk.Spinner:
        return self.prompt_bar.spinner

    # Delegações de comportamento
    def toggle_sidebar(self, _btn: Gtk.Button | None = None) -> None:
        """Alterna a visibilidade da barra lateral de conversas."""
        self.sidebar.toggle()

    def _populate_sidebar_history(self, filter_query: str = "") -> None:
        self.sidebar.populate(filter_query=filter_query)

    def _on_sidebar_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self.sidebar._on_search_changed(entry)

    def _on_history_row_activated(self, _listbox, row) -> None:
        self.sidebar._on_row_activated(_listbox, row)

    def _on_delete_topic(self, topic_id: str) -> None:
        self.sidebar.on_delete_topic(topic_id)

    def _on_clear_all_history(self, _btn: Gtk.Button) -> None:
        self.sidebar._on_clear_all_history(_btn)

    def _rebuild_chat_stream(self) -> None:
        self.chat_stream.rebuild()

    def _create_turn_widget(self, turn, plan=None, image_bytes=None, is_pending=False):
        return self.chat_stream.create_turn_widget(
            turn, plan=plan, image_bytes=image_bytes, is_pending=is_pending
        )

    def _create_action_row(self, action, turn=None, index=0):
        return self.chat_stream.create_action_row(action, turn=turn, index=index)

    def _scroll_to_bottom(self) -> None:
        self.chat_stream.scroll_to_bottom()

    def _update_provider_badge(self) -> None:
        self.header.update_provider_badge()

    def _build_fence_popover(self) -> None:
        self.header.build_fence_popover()

    def _on_select_fence_monitor(self, monitor_idx: int, popover: Gtk.Popover) -> None:
        self.header.on_select_fence_monitor(monitor_idx, popover)

    def _on_select_all_monitors(self, popover: Gtk.Popover) -> None:
        self.header.on_select_all_monitors(popover)

    def _on_toggle_kill_switch(self, popover: Gtk.Popover) -> None:
        self.header.on_toggle_kill_switch(popover)

    def _update_app_preview(self, text: str) -> None:
        self.prompt_bar._update_app_preview(text)

    def _on_quick_launch_app(self, _btn: Gtk.Button) -> None:
        self.prompt_bar._on_quick_launch_app(_btn)

    def _on_submit(self, _widget: Gtk.Widget) -> None:
        self.prompt_bar.submit(_widget)

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        self.prompt_bar._on_entry_changed(entry)

    def _render_active_vision_thumbnail(
        self, image_bytes: bytes, is_area: bool = True, is_clipboard: bool = False
    ) -> None:
        self.vision.render_thumbnail(image_bytes, is_area=is_area, is_clipboard=is_clipboard)

    def _clear_active_vision(self, _btn: Gtk.Button | None = None) -> None:
        self.vision.clear(_btn)

    def _start_screen_capture(self, interactive: bool = True, direct_mode: bool = False) -> None:
        self.vision.start_capture(interactive=interactive, direct_mode=direct_mode)

    def _on_capture_finished(
        self, success, image_bytes, mode, prompt_typed, is_area, direct_mode=False
    ) -> bool:
        return self.vision._on_capture_finished(
            success, image_bytes, mode, prompt_typed, is_area, direct_mode
        )

    # ------------------------------------------------------------------
    # Ciclo de vida e HUD
    # ------------------------------------------------------------------
    def _on_close_request(self, _win) -> bool:
        """Em modo HUD, oculta a janela sem matar o processo em segundo plano."""
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

    def show_toast(self, message: str) -> None:
        """Exibe uma notificação flutuante elegante na janela."""
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    # ------------------------------------------------------------------
    # Configurações
    # ------------------------------------------------------------------
    def _open_settings(self, _btn: Gtk.Button | None = None) -> None:
        dialog = PreferencesDialog(self, on_saved=self._on_config_saved)
        dialog.present(self)

    def _on_config_saved(self, new_config: CopilotConfig) -> None:
        self.config = new_config
        self.engine.reload_config(new_config)
        self.header.update_provider_badge()

    # ------------------------------------------------------------------
    # Sugestões rápidas
    # ------------------------------------------------------------------
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
            self.entry.set_text("\U0001f4cb Analisar conteúdo da área de transferência")
            self.prompt_bar.submit(self.entry)
            return
        self.entry.set_text(text)
        self.prompt_bar.submit(self.entry)

    # ------------------------------------------------------------------
    # Ciclo de uma mensagem
    # ------------------------------------------------------------------
    def _on_plan_ready(
        self, plan: ActionPlan, prompt_text: str = "", attached_image: bytes | None = None
    ) -> bool:
        """Recebe o plano gerado pela IA e substitui o indicador de carregamento pelo resultado."""
        self._is_busy = False
        self.prompt_bar.set_busy(False)
        self.current_plan = plan
        self.chat_stream.welcome_box.set_visible(False)

        if self._pending_turn_box and self._pending_turn_box.get_parent() is self.chat_stream.stream_box:
            self.chat_stream.stream_box.remove(self._pending_turn_box)
            self._pending_turn_box = None

        explanation_text = plan.thought.strip()
        if not explanation_text and plan.actions:
            explanation_text = "Executei a ação solicitada no desktop."

        turn = self.session.record_turn(prompt=prompt_text, answer=explanation_text)
        self._save_current_session()
        self._update_pin_ui()

        self.chat_stream.stream_box.append(
            self.chat_stream.create_turn_widget(turn, plan=plan, image_bytes=attached_image)
        )

        if self.session.title:
            self.window_title.set_subtitle(self.session.title)

        self.sidebar.populate(filter_query=self.sidebar.search.get_text().strip())
        self.chat_stream.scroll_to_bottom()
        self.entry.grab_focus()
        return GLib.SOURCE_REMOVE

    def _on_copy_answer(self, _btn: Gtk.Button) -> None:
        text = self._raw_answer_text or self.answer_label.get_text()
        if not text:
            return
        display = Gdk.Display.get_default()
        if display:
            display.get_clipboard().set(text)
            self.show_toast("✓ Resposta copiada para a área de transferência!")

    def _on_copy_ocr_text(self, _btn: Gtk.Button) -> None:
        ocr_text = getattr(self, "_current_ocr_text", None)
        if not ocr_text:
            return
        from ..core.clipboard import ClipboardService
        if ClipboardService.set_text(ocr_text):
            self.show_toast("Texto copiado para a área de transferência!")

    # ------------------------------------------------------------------
    # Exportação da conversa (Markdown)
    # ------------------------------------------------------------------
    def _export_metadata(self) -> tuple[str, str]:
        """(provedor, modelo) ativos, para o frontmatter do arquivo exportado."""
        config = self.config
        provider = getattr(config, "provider", "") or ""
        model = {
            "gemini": getattr(config, "gemini_model", ""),
            "ollama": getattr(config, "ollama_model", ""),
            "openai": getattr(config, "openai_model", ""),
        }.get(provider, "")
        return provider, (model or "")

    def build_conversation_markdown(self) -> str:
        """Texto Markdown da conversa atual ("" se não houver nada a exportar)."""
        provider, model = self._export_metadata()
        return render_conversation_markdown(
            self.session, provider=provider, model=model
        )

    def export_conversation(self, *_args) -> None:
        """Abre o diálogo "salvar como" e grava a conversa em `.md`.

        O `Gtk.FileDialog` é assíncrono e não bloqueia a interface — diferente do
        antigo `Gtk.FileChooserDialog`, que travava o loop principal.
        """
        if not exportable_turns(self.session):
            self.show_toast("Nada para exportar: a conversa está vazia.")
            return

        dialog = Gtk.FileDialog()
        dialog.set_title("Exportar conversa como Markdown")
        dialog.set_initial_name(suggest_filename(self.session))

        md_filter = Gtk.FileFilter()
        md_filter.set_name("Markdown (*.md)")
        md_filter.add_pattern("*.md")
        dialog.set_default_filter(md_filter)

        dialog.save(self, None, self._on_export_target_chosen, None)

    def _on_export_target_chosen(
        self, dialog: Gtk.FileDialog, result: Gio.AsyncResult
    ) -> None:
        """Conclusão do diálogo de salvar: escreve o arquivo escolhido."""
        try:
            target = dialog.save_finish(result)
        except GLib.Error as err:
            if _is_dialog_dismissed(err):
                return
            logger.warning("Falha ao escolher destino da exportação: %s", err)
            self.show_toast("Não foi possível escolher onde salvar o arquivo.")
            return

        if target is None:
            return

        markdown = self.build_conversation_markdown()
        if not markdown:
            self.show_toast("Nada para exportar: a conversa está vazia.")
            return

        path = target.get_path()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(markdown)
        except OSError as err:
            logger.warning("Falha ao gravar a exportação em %s: %s", path, err)
            self.show_toast("Não foi possível gravar o arquivo.")
            return

        self.show_toast(f"✓ Conversa exportada para {os.path.basename(path)}")

    # ------------------------------------------------------------------
    # Anexos (arrastar e soltar)
    # ------------------------------------------------------------------
    def attach_files(self, paths: list[str]) -> None:
        """Anexa arquivos soltos na janela.

        Imagens vão para o canal multimodal (há um slot só, como na captura de
        tela); texto e PDF entram como contexto do prompt, com chip removível.
        """
        from dataclasses import replace as _replace

        resolved = load_attachments(paths)
        images = [a for a in resolved if a.ok and a.kind is AttachmentKind.IMAGE]
        docs = [a for a in resolved if a.ok and a.kind in (AttachmentKind.TEXT, AttachmentKind.PDF)]
        rejected = [a for a in resolved if not a.ok]

        # Soltar o mesmo arquivo duas vezes duplicaria o bloco de contexto — e o
        # usuário não teria como saber. Chips repetidos são o bug, não o recurso.
        known = {os.path.realpath(a.path) for a in self.attachments}
        fresh: list[Attachment] = []
        for att in docs:
            key = os.path.realpath(att.path)
            if key in known:
                rejected.append(_replace(att, error="já anexado"))
                continue
            known.add(key)
            fresh.append(att)

        if fresh:
            self.attachments.extend(fresh)

        image = images[0] if images else None
        if image is not None:
            self._attach_image_file(image)
            for extra in images[1:]:
                rejected.append(_replace(extra, error="só uma imagem por vez"))

        self.attachment_bar.refresh()
        self.show_toast(self._describe_attachments(fresh, image, rejected))

    def _attach_image_file(self, att: Attachment) -> None:
        """Coloca a imagem solta no mesmo slot multimodal da captura de tela."""
        if not att.data:
            return
        self._active_image_bytes = att.data
        self._active_image_is_area = False
        self._active_image_is_clipboard = False
        self.vision.render_thumbnail(
            att.data,
            is_area=False,
            is_clipboard=False,
            label=f"\U0001f5bc️ Imagem anexada: {att.name}",
        )
        self.entry.set_placeholder_text(
            "Faça uma pergunta sobre esta imagem ou pressione Enter..."
        )

    @staticmethod
    def _describe_attachments(
        docs: list[Attachment], image: Attachment | None, rejected: list[Attachment]
    ) -> str:
        """Frase do toast. Nomeia o primeiro problema: erro genérico não ajuda."""
        parts = []
        if image is not None:
            parts.append(f"imagem {image.name} anexada")
        if docs:
            n = len(docs)
            parts.append(f"{n} arquivo{'s' if n > 1 else ''} de texto anexado{'s' if n > 1 else ''}")
        if rejected:
            n = len(rejected)
            first = rejected[0]
            detail = f" ({first.error})" if n == 1 else ""
            parts.append(f"{n} ignorado{'s' if n > 1 else ''}: {first.name}{detail}")
        if not parts:
            return "Nada para anexar."
        return "✓ " + " · ".join(parts)

    def clear_attachments(self, *_args) -> None:
        """Remove todos os anexos de texto/PDF (não mexe na imagem ativa)."""
        if not self.attachments:
            self.show_toast("Nenhum anexo para remover.")
            return
        count = len(self.attachments)
        self.attachments.clear()
        self.attachment_bar.refresh()
        self.show_toast(f"{count} anexo{'s' if count > 1 else ''} removido{'s' if count > 1 else ''}.")

    # ------------------------------------------------------------------
    # Sessão e histórico
    # ------------------------------------------------------------------
    def _save_current_session(self) -> None:
        """Persiste a sessão atual no SQLite (auto-save estilo Gemini)."""
        if not self.session.turns:
            return
        self.engine.memory.save_chat_topic(
            topic_id=self.session.id,
            title=self.session.title
            or (self.session.turns[0].prompt[:50] if self.session.turns else "Nova Conversa"),
            turns=[t.to_dict() for t in self.session.turns],
            is_pinned=True,
            created_at=self.session.created_at,
        )

    def _resume_topic(self, topic_id: str) -> None:
        """Carrega e retoma uma conversa histórica com todo o seu fluxo de mensagens."""
        if self.session.turns:
            self._save_current_session()

        topic_data = self.engine.memory.get_chat_topic(topic_id)
        if not topic_data:
            return

        self.session.load_from_dict(topic_data)
        self.window_title.set_subtitle(self.session.title or "Conversa retomada")
        self.chat_stream.rebuild()
        self.sidebar.populate(filter_query=self.sidebar.search.get_text().strip())
        self.entry.grab_focus()
        self.show_toast(f'Conversa "{self.session.title}" aberta!')

    def _update_pin_ui(self) -> None:
        """Atualiza estado visual da fixação de tópicos."""

    def _on_toggle_pin(self, _btn: Gtk.Button | None = None) -> None:
        """Alterna a fixação da conversa."""
        self.session.toggle_pin()
        if self.session.turns:
            self._save_current_session()
        self.sidebar.populate(filter_query=self.sidebar.search.get_text().strip())
        self.show_toast("Conversa fixada no topo.")

    def _on_new_topic(self, _btn: Gtk.Button | None = None) -> None:
        """Inicia uma nova conversa limpa (Ctrl+N / Estilo Gemini)."""
        if self.session.turns:
            self._save_current_session()
        self.session.reset_new()
        self.vision.clear()
        self.window_title.set_subtitle("Assistente Inteligente")
        self.chat_stream.rebuild()
        self.sidebar.populate(filter_query=self.sidebar.search.get_text().strip())
        self.entry.set_text("")
        self.entry.grab_focus()
        self.show_toast("✨ Nova conversa iniciada!")

    def _build_history_popover(self) -> None:
        """Método de compatibilidade para histórico."""

    def _populate_history_list(self) -> None:
        """Método de compatibilidade."""
        self.sidebar.populate()

    # ------------------------------------------------------------------
    # Voz ao vivo (Gemini Live)
    # ------------------------------------------------------------------
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
            self._open_settings()
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
        self.header.voice_call_btn.add_css_class("suggested-action")
        self.prompt_bar.bottom_voice_btn.add_css_class("suggested-action")
        self.chat_stream.welcome_box.set_visible(False)
        self.live_client.start()
        self.show_toast("\U0001f399️ Conversa ao vivo iniciada! Pode falar...")

    def stop_live_voice(self) -> None:
        """Encerra a chamada de voz ao vivo e consolida a interação no chat ativo."""
        summary = self.live_client.get_session_summary() if self.live_client else {}
        if self.live_client:
            self.live_client.stop()
        self.live_voice_revealer.set_reveal_child(False)
        self.header.voice_call_btn.remove_css_class("suggested-action")
        self.prompt_bar.bottom_voice_btn.remove_css_class("suggested-action")

        if summary.get("has_activity"):
            duration = summary.get("duration_sec", 0)
            actions = summary.get("actions_executed", [])
            lines = [f"**\U0001f399️ Sessão de Voz ao Vivo (Gemini Live)** • {duration}s de chamada\n"]
            if actions:
                lines.append("**Ações executadas no sistema:**")
                for act in actions:
                    t_name = act.get("tool", "")
                    t_args = act.get("args", {})
                    if t_name == "launch_app":
                        lines.append(f"- \U0001f680 Abriu o aplicativo **{t_args.get('app_name', '')}**")
                    elif t_name == "system_control":
                        lines.append(
                            f"- ⚙️ Controle do sistema: **{t_args.get('action', '')}** ({t_args.get('value', '')})"
                        )
                    elif t_name == "open_url":
                        lines.append(f"- \U0001f310 Abriu link: `{t_args.get('url', '')}`")
                    elif t_name == "capture_screen":
                        lines.append("- \U0001f4f8 Capturou a tela para inspeção visual")
                    elif t_name == "web_search":
                        lines.append(f"- \U0001f50d Pesquisa na web: *{t_args.get('query', '')}*")
                    elif t_name == "media_control":
                        lines.append(f"- \U0001f3b5 Controle de mídia: **{t_args.get('action', '')}**")
                    elif t_name == "write_document":
                        lines.append(f"- \U0001f4dd Salvou documento: `{t_args.get('filename', '')}`")
                    elif t_name == "organize_directory":
                        lines.append(f"- \U0001f4c1 Organizou pasta: `{t_args.get('directory', 'Downloads')}`")
                    else:
                        lines.append(f"- ⚡ Executou ferramenta: `{t_name}`")
                lines.append("")
            else:
                lines.append("Conversa bidirecional em tempo real concluída.")

            if summary.get("video_streamed"):
                frames = summary.get("video_frames", 0)
                lines.append(
                    f"**\U0001f3a5 Live Video:** Compartilhamento de tela contínuo ativo "
                    f"({frames} frames analisados).\n"
                )

            prompt_label = (
                "\U0001f399️\U0001f3a5 Chamada de Voz e Tela ao Vivo"
                if summary.get("video_streamed")
                else "\U0001f399️ Conversa de Voz ao Vivo"
            )
            turn = self.session.record_turn(prompt=prompt_label, answer="\n".join(lines).strip())
            self._save_current_session()
            self._update_pin_ui()

            self.chat_stream.welcome_box.set_visible(False)
            self.chat_stream.stream_box.append(
                self.chat_stream.create_turn_widget(turn)
            )
            self.sidebar.populate()
            self.chat_stream.scroll_to_bottom()
        else:
            if not self.session.turns:
                self.chat_stream.welcome_box.set_visible(True)

        self.entry.grab_focus()
        self.show_toast("Conversa de voz encerrada.")
        if self.get_visible() and self.is_active():
            self.set_visible(False)
        else:
            self.summon_hud()

    def trigger_direct_crop(self) -> None:
        """Dispara o recorte de área da tela a partir do atalho global (Super+Shift+S)."""
        if self.get_visible():
            self.set_visible(False)
        self.vision.start_capture(interactive=True, direct_mode=True)


class ZorinCopilotApp(Adw.Application):
    """Aplicação Zorin Copilot com suporte a comando de linha, modo HUD e atalhos globais."""

    def __init__(self):
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.add_main_option(
            "toggle", ord("t"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Alterna a visibilidade do HUD do Copilot", None,
        )
        self.add_main_option(
            "crop", ord("c"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Dispara a seleção interativa de área da tela e analisa com IA", None,
        )
        self.add_main_option(
            "voice", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Inicia imediatamente a conversa de voz ao vivo (Gemini Live)", None,
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
