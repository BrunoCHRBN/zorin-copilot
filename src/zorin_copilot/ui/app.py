# Decisão de design: interface fluida com processamento assíncrono em threads (zero travamentos na UI), exibição rica de respostas textuais explicativas com suporte a cópia, e orquestração de ações concretas de desktop e web.

"""Interface gráfica do Zorin Copilot em GTK4 / Libadwaita."""

from __future__ import annotations

import html
import re
import threading
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
from ..core.config import CopilotConfig
from ..shell.executor import ActionExecutor
from .preferences import PreferencesDialog


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
    return "system-run-symbolic"


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
        self.current_plan: ActionPlan | None = None
        self._raw_answer_text: str = ""
        self._is_busy = False
        self._search_debounce_timer: int | None = None
        self._matched_preview_app: Gio.AppInfo | None = None

        self._build_ui()
        self._update_provider_badge()

    def _build_ui(self) -> None:
        clamp = Adw.Clamp(maximum_size=720)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        main_box.set_margin_top(16)
        main_box.set_margin_bottom(16)
        main_box.set_margin_start(18)
        main_box.set_margin_end(18)

        # ---------------------------------------------------------------------
        # Header / Barra Superior
        # ---------------------------------------------------------------------
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        title_label = Gtk.Label(label="<b>Zorin Copilot</b>", use_markup=True, xalign=0)
        title_label.add_css_class("title-2")
        header_box.append(title_label)

        self.status_badge = Gtk.Label(xalign=1)
        self.status_badge.add_css_class("dim-label")
        self.status_badge.set_hexpand(True)
        header_box.append(self.status_badge)

        # Botão de Configurações (⚙️)
        settings_btn = Gtk.Button.new_from_icon_name("preferences-system-symbolic")
        settings_btn.set_tooltip_text("Configurações do Assistente e Chaves de IA")
        settings_btn.add_css_class("flat")
        settings_btn.connect("clicked", self._open_settings)
        header_box.append(settings_btn)

        main_box.append(header_box)

        # ---------------------------------------------------------------------
        # Campo de Entrada (Prompt)
        # ---------------------------------------------------------------------
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Ex: 'abrir zorin look', 'como acessar o gmail', 'modo escuro'...")
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "system-search-symbolic")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self._on_submit)
        self.entry.connect("changed", self._on_entry_changed)
        input_box.append(self.entry)

        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        input_box.append(self.spinner)

        self.submit_btn = Gtk.Button(label="Pedir", valign=Gtk.Align.CENTER)
        self.submit_btn.add_css_class("suggested-action")
        self.submit_btn.add_css_class("pill")
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

        self.app_preview_title = Gtk.Label(xalign=0)
        self.app_preview_title.add_css_class("heading")
        info_box.append(self.app_preview_title)

        self.app_preview_subtitle = Gtk.Label(xalign=0)
        self.app_preview_subtitle.add_css_class("caption")
        self.app_preview_subtitle.add_css_class("dim-label")
        info_box.append(self.app_preview_subtitle)

        self.app_preview_card.append(info_box)

        self.app_preview_badge = Gtk.Label(xalign=1)
        self.app_preview_badge.add_css_class("caption")
        self.app_preview_badge.set_valign(Gtk.Align.CENTER)
        self.app_preview_card.append(self.app_preview_badge)

        self.app_preview_launch_btn = Gtk.Button(label="Abrir Agora ↵")
        self.app_preview_launch_btn.add_css_class("suggested-action")
        self.app_preview_launch_btn.add_css_class("pill")
        self.app_preview_launch_btn.set_valign(Gtk.Align.CENTER)
        self.app_preview_launch_btn.set_margin_end(10)
        self.app_preview_launch_btn.connect("clicked", self._on_quick_launch_app)
        self.app_preview_card.append(self.app_preview_launch_btn)

        self.app_preview_revealer.set_child(self.app_preview_card)
        main_box.append(self.app_preview_revealer)

        # ---------------------------------------------------------------------
        # Área Rolável de Conteúdo (Respostas e Ações)
        # ---------------------------------------------------------------------
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        # 1. Tela de Boas-vindas com Sugestões de 1-Clique (Empty State)
        self.welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.welcome_box.set_valign(Gtk.Align.CENTER)
        self.welcome_box.set_vexpand(True)
        self.welcome_box.set_margin_top(8)
        self.welcome_box.set_margin_bottom(8)

        welcome_status = Adw.StatusPage()
        welcome_status.set_icon_name("system-help-symbolic")
        welcome_status.set_title("Como posso ajudar?")
        welcome_status.set_description("Peça tarefas do desktop, consulte seus projetos ou pesquise na web em tempo real.")

        chips_flow = Gtk.FlowBox()
        chips_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        chips_flow.set_max_children_per_line(2)
        chips_flow.set_min_children_per_line(1)
        chips_flow.set_row_spacing(8)
        chips_flow.set_column_spacing(8)
        chips_flow.set_halign(Gtk.Align.CENTER)
        chips_flow.set_margin_top(10)

        suggestions = [
            ("📁 Onde fica meu projeto de trabalho?", "onde fica meu projeto de trabalho ?"),
            ("⚡ Ativar modo escuro", "ativar modo escuro"),
            ("🌐 Notícias recentes sobre Linux", "pesquise notícias recentes sobre Linux"),
            ("🖥️ Abrir o Terminal", "abrir terminal"),
        ]

        for label_text, prompt_val in suggestions:
            btn = Gtk.Button(label=label_text)
            btn.add_css_class("pill")
            btn.add_css_class("flat")
            def make_chip_click(p=prompt_val):
                return lambda _: self._trigger_prompt(p)
            btn.connect("clicked", make_chip_click())
            chips_flow.append(btn)

        welcome_status.set_child(chips_flow)
        self.welcome_box.append(welcome_status)
        content_box.append(self.welcome_box)

        # 2. Grupo: Resposta / Explicação em Card Nativo
        self.answer_group = Adw.PreferencesGroup(title="Resposta")
        self.answer_group.set_visible(False)

        self.answer_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.answer_card.add_css_class("card")
        self.answer_card.set_margin_top(2)
        self.answer_card.set_margin_bottom(2)
        self.answer_card.set_margin_start(2)
        self.answer_card.set_margin_end(2)

        # Barra de topo interna do card: Ícone + Título + Badge + Botão de Copiar
        card_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card_header.set_margin_start(14)
        card_header.set_margin_end(10)
        card_header.set_margin_top(10)

        header_icon = Gtk.Image.new_from_icon_name("system-help-symbolic")
        header_icon.add_css_class("accent")
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

        self.copy_btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        self.copy_btn.set_tooltip_text("Copiar Resposta")
        self.copy_btn.add_css_class("flat")
        self.copy_btn.add_css_class("circular")
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
        self.set_content(clamp)

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
        """Dispara um prompt a partir de um chip de sugestão rápida."""
        self.entry.set_text(text)
        self._on_submit(self.entry)

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        """Monitora a digitação em tempo real para verificar se o app está instalado."""
        if self._search_debounce_timer:
            GLib.source_remove(self._search_debounce_timer)
            self._search_debounce_timer = None

        text = entry.get_text().strip()
        if not text or len(text) < 2:
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
            exe_or_id = app.get_id() or app.get_executable() or "desktop"
            self.app_preview_subtitle.set_text(f"{exe_or_id} • Aplicativo instalado no Zorin OS")

            self.app_preview_badge.set_markup("<span foreground='#2ec27e'><b>✓ Instalado</b></span>")
            self.app_preview_launch_btn.set_visible(True)
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

    def _on_submit(self, _widget: Gtk.Widget) -> None:
        if self._search_debounce_timer:
            GLib.source_remove(self._search_debounce_timer)
            self._search_debounce_timer = None
        self.app_preview_revealer.set_reveal_child(False)

        text = self.entry.get_text().strip()
        if not text or self._is_busy:
            return

        self._is_busy = True
        self.spinner.start()
        self.entry.set_sensitive(False)
        self.submit_btn.set_sensitive(False)
        self.welcome_box.set_visible(False)
        self.exec_status.set_text("Pensando...")

        def parse_thread():
            plan = self.engine.parse(text)
            GLib.idle_add(self._on_plan_ready, plan)

        threading.Thread(target=parse_thread, daemon=True).start()

    def _on_plan_ready(self, plan: ActionPlan) -> bool:
        self._is_busy = False
        self.spinner.stop()
        self.entry.set_sensitive(True)
        self.submit_btn.set_sensitive(True)
        self.current_plan = plan
        self.welcome_box.set_visible(False)

        # 1. Renderiza a Resposta / Pensamento com Pango Markup
        explanation_text = plan.thought.strip()
        self._raw_answer_text = explanation_text
        if explanation_text:
            markup = format_markdown_to_markup(explanation_text)
            self.answer_label.set_markup(markup)
            self.answer_group.set_visible(True)

            # Badge da fonte: Web ou Memória
            if "[Resultados da Pesquisa" in explanation_text or any(a.action_type == ActionType.OPEN_URL for a in plan.actions):
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
                }.get(action.action_type, action.action_type.value)

                row = Adw.ActionRow(
                    title=action.describe(),
                    subtitle=f"Tipo: {badge_desc}",
                )

                # Ícone semântico do desktop
                icon_name = get_action_icon(action)
                prefix_icon = Gtk.Image.new_from_icon_name(icon_name)
                prefix_icon.set_pixel_size(22)
                prefix_icon.add_css_class("accent")
                row.add_prefix(prefix_icon)

                # Botão direto de execução na linha da ação
                exec_btn = Gtk.Button(label="Executar")
                exec_btn.add_css_class("suggested-action")
                exec_btn.add_css_class("pill")
                exec_btn.set_valign(Gtk.Align.CENTER)

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


class ZorinCopilotApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=__app_id__, flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = CopilotWindow(self)
        win.present()


def main() -> int:
    app = ZorinCopilotApp()
    return app.run(None)


if __name__ == "__main__":
    main()
