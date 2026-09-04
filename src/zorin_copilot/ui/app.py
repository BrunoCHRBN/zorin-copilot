# Decisão de design: interface fluida com processamento assíncrono em threads (zero travamentos na UI), exibição rica de respostas textuais explicativas com suporte a cópia, e orquestração de ações concretas de desktop e web.

"""Interface gráfica do Zorin Copilot em GTK4 / Libadwaita."""

from __future__ import annotations

import threading
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import __app_id__, __version__
from ..ai.actions import ActionPlan, ActionType, DesktopAction
from ..ai.engine import IntentEngine
from ..core.a11y import DesktopInspector
from ..core.config import CopilotConfig
from ..shell.executor import ActionExecutor
from .preferences import PreferencesDialog


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
        self._is_busy = False

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
        self.entry.set_placeholder_text("Ex: 'como acessar o gmail', 'abrir steam', 'modo escuro', 'aumentar volume'...")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self._on_submit)
        input_box.append(self.entry)

        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        input_box.append(self.spinner)

        self.submit_btn = Gtk.Button(label="Pedir", valign=Gtk.Align.CENTER)
        self.submit_btn.add_css_class("suggested-action")
        self.submit_btn.connect("clicked", self._on_submit)
        input_box.append(self.submit_btn)

        main_box.append(input_box)

        # ---------------------------------------------------------------------
        # Área Rolável de Conteúdo (Respostas e Ações)
        # ---------------------------------------------------------------------
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        # Grupo: Resposta / Explicação
        self.answer_group = Adw.PreferencesGroup(title="Resposta")
        self.answer_group.set_visible(False)
        
        self.answer_label = Gtk.Label(xalign=0, yalign=0)
        self.answer_label.set_wrap(True)
        self.answer_label.set_selectable(True)
        self.answer_label.add_css_class("card")
        self.answer_label.set_margin_top(4)
        self.answer_label.set_margin_bottom(4)
        self.answer_label.set_margin_start(4)
        self.answer_label.set_margin_end(4)

        # Barra de utilitários da resposta (Copiar / Configurar IA)
        self.answer_actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.answer_actions_box.set_margin_top(6)
        
        self.copy_btn = Gtk.Button(label="Copiar Resposta", valign=Gtk.Align.CENTER)
        self.copy_btn.add_css_class("flat")
        self.copy_btn.connect("clicked", self._on_copy_answer)
        self.answer_actions_box.append(self.copy_btn)

        self.config_ai_btn = Gtk.Button(label="Configurar Chave de IA (⚙️)", valign=Gtk.Align.CENTER)
        self.config_ai_btn.add_css_class("flat")
        self.config_ai_btn.connect("clicked", self._open_settings)
        self.config_ai_btn.set_visible(False)
        self.answer_actions_box.append(self.config_ai_btn)

        self.answer_group.add(self.answer_label)
        self.answer_group.add(self.answer_actions_box)
        content_box.append(self.answer_group)

        # Grupo: Ações Propostas no Desktop
        self.actions_group = Adw.PreferencesGroup(title="Ações Propostas")
        self.actions_group.set_visible(False)
        self.actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.actions_group.add(self.actions_box)
        content_box.append(self.actions_group)

        # Linha do Botão de Execução
        self.exec_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.exec_btn = Gtk.Button(label="Executar Ação", valign=Gtk.Align.CENTER)
        self.exec_btn.add_css_class("suggested-action")
        self.exec_btn.set_sensitive(False)
        self.exec_btn.connect("clicked", self._on_execute)
        self.exec_box.append(self.exec_btn)

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

    def _on_submit(self, _widget: Gtk.Widget) -> None:
        text = self.entry.get_text().strip()
        if not text or self._is_busy:
            return

        self._is_busy = True
        self.spinner.start()
        self.entry.set_sensitive(False)
        self.submit_btn.set_sensitive(False)
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

        # 1. Renderiza a Resposta / Pensamento
        explanation_text = plan.thought.strip()
        if explanation_text:
            self.answer_label.set_text(explanation_text)
            self.answer_group.set_visible(True)
            # Mostra botão de configurar IA se chave não estiver configurada
            self.config_ai_btn.set_visible(not self.config.is_configured())
        else:
            self.answer_group.set_visible(False)

        # 2. Renderiza as Ações Executáveis
        while child := self.actions_box.get_first_child():
            self.actions_box.remove(child)

        executable_actions = [a for a in plan.actions if a.action_type != ActionType.ANSWER]
        
        if executable_actions:
            self.actions_group.set_visible(True)
            for action in executable_actions:
                badge_text = {
                    ActionType.LAUNCH_APP: "abrir aplicativo",
                    ActionType.OPEN_URL: "abrir link web",
                    ActionType.SYSTEM_CONTROL: "sistema",
                    ActionType.CLICK: "clique a11y",
                    ActionType.NOTIFY: "notificação",
                }.get(action.action_type, action.action_type.value)

                row = Adw.ActionRow(title=action.describe(), subtitle=f"Tipo: {badge_text}")
                badge = Gtk.Label(label="pronto para executar")
                badge.add_css_class("dim-label")
                row.add_suffix(badge)
                self.actions_box.append(row)

            self.exec_btn.set_sensitive(True)
            self.exec_status.set_text(f"{len(executable_actions)} ação(ões) pronta(s).")
        else:
            self.actions_group.set_visible(False)
            self.exec_btn.set_sensitive(False)
            self.exec_status.set_text("")

        return GLib.SOURCE_REMOVE

    def _on_execute(self, _widget: Gtk.Widget) -> None:
        if not self.current_plan:
            return

        reports = self.executor.execute_plan(self.current_plan, dry_run=False)
        msgs = [r.message for r in reports]
        self.exec_status.set_text(" • ".join(msgs))
        self.exec_btn.set_sensitive(False)

    def _on_copy_answer(self, _btn: Gtk.Button) -> None:
        text = self.answer_label.get_text()
        if not text:
            return
        display = Gdk.Display.get_default()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(text)
            self.exec_status.set_text("Resposta copiada para a área de transferência!")


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
