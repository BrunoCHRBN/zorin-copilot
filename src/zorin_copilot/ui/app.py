# Decisão de design: interface estilo Spotlight flutuante centralizada — sem bordas pesadas, rápida para invocar por atalho e com prévia transparente das ações antes de executar.

"""Interface gráfica do Zorin Copilot em GTK4 / Libadwaita."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import __app_id__, __version__
from ..ai.actions import ActionPlan, ActionType, DesktopAction
from ..ai.engine import IntentEngine
from ..core.a11y import DesktopInspector
from ..shell.executor import ActionExecutor


class CopilotWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_title("Zorin Copilot")
        self.set_default_size(680, 440)
        self.set_resizable(False)

        self.inspector = DesktopInspector()
        self.executor = ActionExecutor(self.inspector)
        self.engine = IntentEngine(self.inspector)
        self.current_plan: ActionPlan | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        clamp = Adw.Clamp(maximum_size=680)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        main_box.set_margin_top(18)
        main_box.set_margin_bottom(18)
        main_box.set_margin_start(18)
        main_box.set_margin_end(18)

        # Header / Barra de Título
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title_label = Gtk.Label(label="<b>Zorin Copilot</b>", use_markup=True, xalign=0)
        title_label.add_css_class("title-2")
        header_box.append(title_label)

        self.hint_badge = Gtk.Label(xalign=1)
        self.hint_badge.add_css_class("dim-label")
        self.hint_badge.set_hexpand(True)
        self._refresh_apps_count()
        header_box.append(self.hint_badge)

        main_box.append(header_box)

        # Campo de Entrada (Prompt)
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Ex: 'abrir steam', 'modo escuro', 'aumentar volume', 'tirar print'...")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self._on_submit)
        input_box.append(self.entry)

        submit_btn = Gtk.Button(label="Pedir", valign=Gtk.Align.CENTER)
        submit_btn.add_css_class("suggested-action")
        submit_btn.connect("clicked", self._on_submit)
        input_box.append(submit_btn)

        main_box.append(input_box)

        # Área de Resposta e Ações
        self.status_label = Gtk.Label(label="Pronto para comandos.", xalign=0)
        self.status_label.add_css_class("dim-label")
        self.status_label.set_wrap(True)
        main_box.append(self.status_label)

        self.actions_group = Adw.PreferencesGroup(title="Ações Propostas")
        self.actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.actions_group.add(self.actions_box)
        main_box.append(self.actions_group)

        # Botão de Executar Plano
        self.btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.exec_btn = Gtk.Button(label="Executar Ação", valign=Gtk.Align.CENTER)
        self.exec_btn.add_css_class("suggested-action")
        self.exec_btn.set_sensitive(False)
        self.exec_btn.connect("clicked", self._on_execute)
        self.btn_box.append(self.exec_btn)

        main_box.append(self.btn_box)

        clamp.set_child(main_box)
        self.set_content(clamp)

    def _refresh_apps_count(self) -> None:
        active_apps = self.inspector.list_applications()
        self.hint_badge.set_text(f"{len(active_apps)} apps detectados no desktop")

    def _on_submit(self, _widget: Gtk.Widget) -> None:
        text = self.entry.get_text().strip()
        if not text:
            return

        self.status_label.set_text(f"Analisando: '{text}'...")
        plan = self.engine.parse(text)
        self.current_plan = plan
        self._render_plan(plan)

    def _render_plan(self, plan: ActionPlan) -> None:
        while child := self.actions_box.get_first_child():
            self.actions_box.remove(child)

        for action in plan.actions:
            badge_text = {
                ActionType.LAUNCH_APP: "iniciar app",
                ActionType.SYSTEM_CONTROL: "sistema",
                ActionType.CLICK: "clique a11y",
                ActionType.NOTIFY: "notificação",
                ActionType.ANSWER: "informação",
            }.get(action.action_type, action.action_type.value)

            row = Adw.ActionRow(title=action.describe(), subtitle=f"Categoria: {badge_text}")
            badge = Gtk.Label(label="pronto")
            badge.add_css_class("dim-label")
            row.add_suffix(badge)
            self.actions_box.append(row)

        self.status_label.set_text(plan.thought)
        # Habilita executar se não for apenas uma resposta puramente textual
        can_exec = not plan.is_empty and any(a.action_type != ActionType.ANSWER for a in plan.actions)
        self.exec_btn.set_sensitive(can_exec)

    def _on_execute(self, _widget: Gtk.Widget) -> None:
        if not self.current_plan:
            return

        reports = self.executor.execute_plan(self.current_plan, dry_run=False)
        msgs = [r.message for r in reports]
        self.status_label.set_text(" • ".join(msgs))
        self.exec_btn.set_sensitive(False)
        self._refresh_apps_count()


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
