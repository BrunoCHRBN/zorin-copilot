# Decisão de design: barra de prompt estilo Gemini em formato de pílula flutuante.
# A detecção de aplicativos é feita com debounce para não consultar o AppManager a cada tecla.

"""Barra inferior de entrada: visão, clipboard, campo de texto, voz e envio."""

from __future__ import annotations

import html
import threading
from typing import TYPE_CHECKING

from ..gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from ...ai.actions import ActionType
from ...core.apps import AppManager
from ...core.attachments import DEFAULT_QUESTION, compose_prompt
from ...core.clipboard import ClipboardService
from ...core.desktop_context import DesktopContext
from ...core.session import ChatTurn

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow

APP_PREVIEW_DEBOUNCE_MS = 120

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

class PromptBar:
    """Barra de prompt com prévia de aplicativo, voz ao vivo e envio de mensagens."""

    def __init__(self, ctx: "CopilotWindow"):
        self.ctx = ctx
        self._search_debounce_timer: int | None = None
        self._matched_preview_app = None
        self._active_context: DesktopContext | None = None
        self._context_dismissed: bool = False

        self.container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.container.set_margin_start(16)
        self.container.set_margin_end(16)
        self.container.set_margin_bottom(10)
        self.container.set_margin_top(4)

        self.context_pill_revealer: Gtk.Revealer
        self.context_pill_icon: Gtk.Image
        self.context_pill_label: Gtk.Label
        self.context_pill_clear_btn: Gtk.Button

        self.app_preview_revealer: Gtk.Revealer
        self.app_preview_icon: Gtk.Image
        self.app_preview_title: Gtk.Label
        self.app_preview_badge: Gtk.Label
        self.app_preview_subtitle: Gtk.Label
        self.app_preview_launch_btn: Gtk.Button

        self.entry: Gtk.Entry
        self.vision_btn: Gtk.MenuButton
        self.clipboard_btn: Gtk.MenuButton
        self.bottom_voice_btn: Gtk.Button
        self.spinner: Gtk.Spinner
        self.submit_btn: Gtk.Button

        self._build_context_pill()
        self._build_app_preview()
        self.container.append(ctx.attachment_bar.box)
        self.container.append(ctx.vision.preview_box)
        self.container.append(self.context_pill_revealer)
        self._build_prompt_bar()
        self.container.append(self._build_disclaimer())

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------
    def _build_context_pill(self) -> None:
        self.context_pill_revealer = Gtk.Revealer()
        self.context_pill_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.context_pill_revealer.set_transition_duration(150)
        self.context_pill_revealer.set_reveal_child(False)

        pill_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pill_box.add_css_class("prompt-context-pill")
        pill_box.set_halign(Gtk.Align.START)
        pill_box.set_margin_bottom(2)

        self.context_pill_icon = Gtk.Image.new_from_icon_name("window-new-symbolic")
        self.context_pill_icon.set_pixel_size(14)
        pill_box.append(self.context_pill_icon)

        self.context_pill_label = Gtk.Label(xalign=0)
        self.context_pill_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.context_pill_label.set_max_width_chars(55)
        pill_box.append(self.context_pill_label)

        self.context_pill_clear_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.context_pill_clear_btn.set_tooltip_text("Descartar contexto ativo nesta conversa")
        self.context_pill_clear_btn.add_css_class("flat")
        self.context_pill_clear_btn.add_css_class("circular")
        self.context_pill_clear_btn.set_valign(Gtk.Align.CENTER)
        self.context_pill_clear_btn.connect("clicked", lambda _: self.clear_active_context())
        pill_box.append(self.context_pill_clear_btn)

        self.context_pill_revealer.set_child(pill_box)

    def set_active_context(self, ctx: DesktopContext | None) -> None:
        """Exibe uma pílula sutil com o aplicativo e contexto em foco."""
        self._context_dismissed = False
        if not ctx or not ctx.is_contextual:
            self.clear_active_context()
            return

        self._active_context = ctx

        icon_name = "window-new-symbolic"
        text_parts = []

        app_name = ctx.app_name or "Desktop"
        text_parts.append(app_name)

        if ctx.git_repo:
            icon_name = "vcs-branch-symbolic"
            diff_mark = "*" if ctx.git_has_diff else ""
            branch_info = f"({ctx.git_branch}{diff_mark})" if ctx.git_branch else ""
            text_parts.append(f"{ctx.git_repo} {branch_info}".strip())
            if ctx.extracted_target:
                text_parts.append(ctx.extracted_target)
        elif ctx.extracted_target:
            icon_name = "text-x-generic-symbolic"
            text_parts.append(ctx.extracted_target)
        elif ctx.window_title:
            win_title = ctx.window_title.strip()
            if win_title and win_title.lower() != app_name.lower():
                short_title = win_title if len(win_title) <= 40 else win_title[:37] + "..."
                text_parts.append(short_title)

        pill_text = " • ".join(text_parts)
        self.context_pill_icon.set_from_icon_name(icon_name)
        self.context_pill_label.set_text(pill_text)
        self.context_pill_revealer.set_reveal_child(True)

    def clear_active_context(self) -> None:
        """Descarta o contexto ativo e oculta a pílula."""
        self._active_context = None
        self._context_dismissed = True
        if hasattr(self.ctx, "current_context"):
            self.ctx.current_context = None
        self.context_pill_revealer.set_reveal_child(False)

    def get_active_context(self) -> DesktopContext | None:
        """Retorna o contexto desktop ativo ou contexto vazio caso descartado."""
        if self._context_dismissed:
            return DesktopContext()
        return self._active_context

    def _build_app_preview(self) -> None:
        self.app_preview_revealer = Gtk.Revealer()
        self.app_preview_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.app_preview_revealer.set_transition_duration(180)
        self.app_preview_revealer.set_reveal_child(False)

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        card.add_css_class("card")
        card.add_css_class("glass-card")
        card.set_margin_bottom(4)

        self.app_preview_icon = Gtk.Image()
        self.app_preview_icon.set_pixel_size(24)
        self.app_preview_icon.set_margin_start(10)
        self.app_preview_icon.set_margin_top(6)
        self.app_preview_icon.set_margin_bottom(6)
        card.append(self.app_preview_icon)

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

        card.append(app_info_box)
        self.app_preview_revealer.set_child(card)
        self.container.append(self.app_preview_revealer)

    def _build_prompt_bar(self) -> None:
        ctx = self.ctx
        self.prompt_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.prompt_bar_box.add_css_class("prompt-bar-card")
        self.prompt_bar_box.add_css_class("glass-floating")

        self.vision_btn = self._build_vision_button()
        self.prompt_bar_box.append(self.vision_btn)

        self.clipboard_btn = self._build_clipboard_button()
        self.prompt_bar_box.append(self.clipboard_btn)

        self.agent_btn = self._build_agent_button()
        self.prompt_bar_box.append(self.agent_btn)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Peça ao Zorin Copilot ou digite um comando...")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self.submit)
        self.entry.connect("changed", self._on_entry_changed)
        self.prompt_bar_box.append(self.entry)
        ctx.entry = self.entry

        self.bottom_voice_btn = Gtk.Button.new_from_icon_name("audio-input-microphone-symbolic")
        self.bottom_voice_btn.set_tooltip_text("Conversa por Voz ao Vivo (Gemini Live / Ctrl+M)")
        self.bottom_voice_btn.add_css_class("flat")
        self.bottom_voice_btn.add_css_class("circular")
        self.bottom_voice_btn.add_css_class("glass-icon-btn")
        self.bottom_voice_btn.connect("clicked", lambda _: ctx.toggle_live_voice())
        self.prompt_bar_box.append(self.bottom_voice_btn)

        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self.prompt_bar_box.append(self.spinner)

        self.submit_btn = Gtk.Button(valign=Gtk.Align.CENTER)
        self.submit_btn.add_css_class("suggested-action")
        self.submit_btn.add_css_class("circular")
        self.submit_btn.add_css_class("glass-submit-btn")
        self.submit_btn.set_tooltip_text("Enviar (Enter)")
        submit_icon = Gtk.Image.new_from_icon_name("pan-end-symbolic")
        submit_icon.set_pixel_size(16)
        self.submit_btn.set_child(submit_icon)
        self.submit_btn.connect("clicked", self.submit)
        self.prompt_bar_box.append(self.submit_btn)

        self.container.append(self.prompt_bar_box)

    def _build_vision_button(self) -> Gtk.MenuButton:
        vision_btn = Gtk.MenuButton(valign=Gtk.Align.CENTER)
        vision_btn.set_icon_name("camera-photo-symbolic")
        vision_btn.set_tooltip_text("Visão Computacional: Ler ou recortar a tela com IA")
        vision_btn.add_css_class("flat")
        vision_btn.add_css_class("circular")
        vision_btn.add_css_class("glass-icon-btn")

        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        area_btn = self._make_menu_item(
            "edit-cut-symbolic", "<b>Recortar Área da Tela</b>"
        )
        area_btn.connect(
            "clicked",
            lambda _: (popover.popdown(), self.ctx._start_screen_capture(interactive=True)),
        )
        box.append(area_btn)

        full_btn = self._make_menu_item(
            "zoom-fit-best-symbolic", "<b>Capturar Tela Inteira</b>"
        )
        full_btn.connect(
            "clicked",
            lambda _: (popover.popdown(), self.ctx._start_screen_capture(interactive=False)),
        )
        box.append(full_btn)

        popover.set_child(box)
        vision_btn.set_popover(popover)
        return vision_btn

    def _build_clipboard_button(self) -> Gtk.MenuButton:
        clipboard_btn = Gtk.MenuButton(valign=Gtk.Align.CENTER)
        clipboard_btn.set_icon_name("edit-paste-symbolic")
        clipboard_btn.set_tooltip_text("Clipboard Inteligente: Analisar texto ou imagem copiada")
        clipboard_btn.add_css_class("flat")
        clipboard_btn.add_css_class("circular")
        clipboard_btn.add_css_class("glass-icon-btn")

        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        entries = [
            ("Explicar Código Copiado", "explique o código que acabei de copiar", "utilities-terminal-symbolic"),
            ("Traduzir para Inglês", "traduza o texto selecionado para o inglês", "preferences-desktop-locale-symbolic"),
            ("Resumir Conteúdo Copiado", "resuma o que acabei de copiar", "view-list-bullet-symbolic"),
            ("Analisar Copiado Geral", "analisar copiado", "system-search-symbolic"),
        ]
        for title, prompt_text, icon_name in entries:
            btn = self._make_menu_item(icon_name, title)
            btn.connect(
                "clicked",
                lambda _, p=prompt_text: (popover.popdown(), self.ctx._trigger_prompt(p)),
            )
            box.append(btn)

        popover.set_child(box)
        clipboard_btn.set_popover(popover)
        return clipboard_btn

    def _build_agent_button(self) -> Gtk.ToggleButton:
        agent_btn = Gtk.ToggleButton(valign=Gtk.Align.CENTER)
        agent_btn.set_icon_name("system-run-symbolic")
        agent_btn.set_tooltip_text("Modo Agente: Executar tarefas autônomas no desktop")
        agent_btn.add_css_class("flat")
        agent_btn.add_css_class("circular")
        agent_btn.add_css_class("glass-icon-btn")
        agent_btn.connect("toggled", self._on_agent_mode_toggled)
        return agent_btn

    def _on_agent_mode_toggled(self, btn: Gtk.ToggleButton) -> None:
        active = btn.get_active()
        self.ctx.agent_mode_active = active
        if active:
            btn.add_css_class("suggested-action")
            self.entry.set_placeholder_text("Descreva o objetivo para o Agente (ex: abrir Firefox e pesquisar...)")
        else:
            btn.remove_css_class("suggested-action")
            self.entry.set_placeholder_text("Peça ao Zorin Copilot ou digite um comando...")

    @staticmethod
    def _make_menu_item(icon_name: str, label_markup: str) -> Gtk.Button:
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
        lbl = Gtk.Label(label=label_markup, use_markup=True, xalign=0)
        box.append(lbl)
        btn.set_child(box)
        return btn

    @staticmethod
    def _build_disclaimer() -> Gtk.Label:
        lbl = Gtk.Label(
            label="<span size='small' alpha='65%'>O Zorin Copilot é um assistente com IA e pode "
                  "cometer erros. Verifique informações importantes.</span>",
            use_markup=True,
            xalign=0.5,
        )
        lbl.add_css_class("disclaimer-caption")
        return lbl

    # ------------------------------------------------------------------
    # Prévia de aplicativo
    # ------------------------------------------------------------------
    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        """Monitora a digitação em tempo real para verificar se o app está instalado."""
        if self._search_debounce_timer:
            GLib.source_remove(self._search_debounce_timer)
            self._search_debounce_timer = None

        text = entry.get_text().strip()
        if not text:
            self.app_preview_revealer.set_reveal_child(False)
            self._matched_preview_app = None
            if not self.ctx.answer_group.get_visible():
                self.ctx.chat_stream.welcome_box.set_visible(True)
            return

        # Esconde a tela de sugestões ao começar a digitar
        self.ctx.chat_stream.welcome_box.set_visible(False)

        if len(text) < 2:
            self.app_preview_revealer.set_reveal_child(False)
            self._matched_preview_app = None
            return

        def check_app():
            self._search_debounce_timer = None
            self._update_app_preview(text)
            return GLib.SOURCE_REMOVE

        self._search_debounce_timer = GLib.timeout_add(APP_PREVIEW_DEBOUNCE_MS, check_app)

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
            self.app_preview_subtitle.set_text(get_app_subtitle(app))
            self.app_preview_badge.set_markup("<span foreground='#2ec27e'><b>Instalado</b></span>")
            self.app_preview_launch_btn.set_visible(True)
            self.app_preview_launch_btn.set_tooltip_text(f"Abrir {friendly_name} agora")
            self.app_preview_revealer.set_reveal_child(True)
        else:
            self._matched_preview_app = None
            explicit = ("abrir ", "abre ", "iniciar ", "inicia ", "rodar ", "executar ", "open ")
            if any(text.lower().startswith(p) for p in explicit):
                self.app_preview_icon.set_from_icon_name("dialog-warning-symbolic")
                self.app_preview_title.set_markup(f"<b>{html.escape(target_name)}</b> não encontrado")
                self.app_preview_subtitle.set_text(
                    "Nenhum aplicativo com este nome foi detectado no sistema."
                )
                self.app_preview_badge.set_markup("<span foreground='#e5a50a'><b>Não instalado</b></span>")
                self.app_preview_launch_btn.set_visible(False)
                self.app_preview_revealer.set_reveal_child(True)
            else:
                self.app_preview_revealer.set_reveal_child(False)

    def _on_quick_launch_app(self, _btn: Gtk.Button) -> None:
        """Executa imediatamente o app detectado na barra de prévia sem precisar da IA."""
        if not self._matched_preview_app:
            return

        ctx = self.ctx
        app = self._matched_preview_app
        ok, msg = AppManager.launch(app)
        ctx.exec_status.set_text(msg)
        ctx.engine.memory.log_action(
            prompt=ctx.entry.get_text().strip(),
            action_type=ActionType.LAUNCH_APP.value,
            target=app.get_name(),
            params={"app_id": app.get_id(), "executable": app.get_executable()},
            success=ok,
            message=msg,
        )
        self.app_preview_revealer.set_reveal_child(False)

    # ------------------------------------------------------------------
    # Envio
    # ------------------------------------------------------------------
    def submit(self, _widget: Gtk.Widget) -> None:
        """Processa a mensagem do usuário, adiciona ao fluxo e despacha para a IA."""
        ctx = self.ctx
        if self._search_debounce_timer:
            GLib.source_remove(self._search_debounce_timer)
            self._search_debounce_timer = None
        self.app_preview_revealer.set_reveal_child(False)

        text = ctx.entry.get_text().strip()
        active_img = getattr(ctx, "_active_image_bytes", None)
        active_is_area = getattr(ctx, "_active_image_is_area", False)
        attachments = list(getattr(ctx, "attachments", None) or [])

        if not text and not active_img and not attachments:
            return
        if ctx._is_busy:
            return

        if not text and active_img:
            text = (
                "Analise o recorte da tela e explique o que está visível."
                if active_is_area
                else "Analise esta captura de tela."
            )
        elif not text and attachments:
            # Soltar um arquivo e mandar sem digitar nada é um caso comum;
            # o prompt precisa de uma pergunta, mas a bolha do usuário também.
            text = DEFAULT_QUESTION

        low = text.lower()
        if any(w in low for w in ["copiad", "copiei", "clipboard", "área de transferência", "area de transferencia"]):
            kind, img_data = ClipboardService.get_content()
            if kind == "image" and isinstance(img_data, bytes):
                ctx._active_image_bytes = img_data
                ctx._active_image_is_area = False
                ctx._active_image_is_clipboard = True
                active_img = img_data
                ctx.vision.render_thumbnail(img_data, is_area=False, is_clipboard=True)

        ctx._is_busy = True
        self.spinner.start()
        ctx.entry.set_sensitive(False)
        self.submit_btn.set_sensitive(False)
        self.vision_btn.set_sensitive(False)
        self.clipboard_btn.set_sensitive(False)
        self.agent_btn.set_sensitive(False)
        self.bottom_voice_btn.set_sensitive(False)
        ctx.chat_stream.welcome_box.set_visible(False)

        ctx.entry.set_text("")
        ctx.vision.preview_box.set_visible(False)
        ctx._active_image_bytes = None

        # Detecta se é execução do Modo Agente (via toggle ativo ou comando prefixado)
        is_agent = bool(self.agent_btn.get_active() or getattr(ctx, "agent_mode_active", False))
        for pfx in ("/agent ", "/agente ", "@agent ", "@agente "):
            if text.lower().startswith(pfx):
                text = text[len(pfx):].strip()
                is_agent = True
                break

        if is_agent and text:
            agent_full_prompt = compose_prompt(text, attachments) if attachments else text
            ctx.start_agent_execution(text, active_img, full_prompt=agent_full_prompt)
            return

        temp_turn = ChatTurn(prompt=text, answer="")
        ctx._pending_turn_box = ctx.chat_stream.create_turn_widget(
            temp_turn, image_bytes=active_img, is_pending=True
        )
        ctx.chat_stream.stream_box.append(ctx._pending_turn_box)
        ctx.chat_stream.scroll_to_bottom()

        # O texto enviado à IA ganha o contexto dos anexos; o que fica no
        # histórico (e na bolha do usuário) é só o que ele digitou.
        engine_text = compose_prompt(text, attachments) if attachments else text
        active_ctx = self.get_active_context()

        def parse_thread():
            history = ctx.session.get_history_for_llm()
            plan = ctx.engine.parse(
                engine_text,
                history=history,
                image_bytes=active_img,
                is_area_capture=active_is_area,
                desktop_context=active_ctx,
            )
            GLib.idle_add(ctx._on_plan_ready, plan, text, active_img)

        threading.Thread(target=parse_thread, daemon=True).start()

    def set_busy(self, busy: bool) -> None:
        """Habilita/desabilita os controles durante o processamento de uma mensagem."""
        ctx = self.ctx
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        ctx.entry.set_sensitive(not busy)
        self.submit_btn.set_sensitive(not busy)
        self.vision_btn.set_sensitive(not busy)
        self.clipboard_btn.set_sensitive(not busy)
        self.agent_btn.set_sensitive(not busy)
        self.bottom_voice_btn.set_sensitive(not busy)

    @property
    def matched_preview_app(self):
        return self._matched_preview_app
