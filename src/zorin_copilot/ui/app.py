# Decisão de design: interface estilo Spotlight flutuante centralizada — sem bordas pesadas, rápida para invocar por atalho e com prévia transparente das ações antes de executar.

"""Interface gráfica do Zorin Copilot em GTK4 / Libadwaita."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import __app_id__, __version__
from ..ai.actions import ActionPlan, ActionType, DesktopAction
from ..core.a11y import DesktopInspector
from ..shell.executor import ActionExecutor


class CopilotWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_title("Zorin Copilot")
        self.set_default_size(680, 420)
        self.set_resizable(False)

        self.inspector = DesktopInspector()
        self.executor = ActionExecutor(self.inspector)
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

        active_apps = self.inspector.list_applications()
        active_hint = f"{len(active_apps)} apps detectados no desktop"
        hint_badge = Gtk.Label(label=active_hint, xalign=1)
        hint_badge.add_css_class("dim-label")
        hint_badge.set_hexpand(True)
        header_box.append(hint_badge)

        main_box.append(header_box)

        # Campo de Entrada (Prompt)
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Digite o que deseja fazer no desktop (ex: abrir terminal, notificar, inspecionar)...")
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

    def _on_submit(self, _widget: Gtk.Widget) -> None:
        text = self.entry.get_text().strip()
        if not text:
            return

        self.status_label.set_text(f"Analisando intenção: '{text}'...")
        # Parser heurístico para v0.1.0 inicial
        plan = self._parse_intent(text)
        self.current_plan = plan
        self._render_plan(plan)

    def _parse_intent(self, prompt: str) -> ActionPlan:
        low = prompt.lower()
        actions: list[DesktopAction] = []
        thought = f"Interpretação da intenção: {prompt}"

        if "terminal" in low or "bash" in low:
            actions.append(DesktopAction(ActionType.LAUNCH_APP, "gnome-terminal", description="Abrir o Terminal GNOME"))
        elif "navegador" in low or "firefox" in low or "web" in low or "chrome" in low:
            actions.append(DesktopAction(ActionType.LAUNCH_APP, "firefox", description="Abrir o navegador Web"))
        elif "arquivos" in low or "nautilus" in low:
            actions.append(DesktopAction(ActionType.LAUNCH_APP, "nautilus", description="Abrir o Gerenciador de Arquivos"))
        elif "notifi" in low or "lembr" in low:
            actions.append(DesktopAction(ActionType.NOTIFY, "Lembrete", {"message": prompt}, description="Exibir notificação"))
        elif "clique" in low or "clicar" in low:
            target = prompt.split("em")[-1].strip().strip("'\"")
            actions.append(DesktopAction(ActionType.CLICK, target, description=f"Clicar no elemento '{target}'"))
        else:
            actions.append(DesktopAction(ActionType.NOTIFY, "Zorin Copilot", {"message": prompt}, description=f"Responder: {prompt}"))

        return ActionPlan(thought=thought, actions=actions)

    def _render_plan(self, plan: ActionPlan) -> None:
        # Limpa itens anteriores
        while child := self.actions_box.get_first_child():
            self.actions_box.remove(child)

        for action in plan.actions:
            row = Adw.ActionRow(title=action.describe(), subtitle=f"Tipo: {action.action_type.value}")
            badge = Gtk.Label(label="pronto")
            badge.add_css_class("dim-label")
            row.add_suffix(badge)
            self.actions_box.append(row)

        self.status_label.set_text(plan.thought)
        self.exec_btn.set_sensitive(not plan.is_empty)

    def _on_execute(self, _widget: Gtk.Widget) -> None:
        if not self.current_plan:
            return

        reports = self.executor.execute_plan(self.current_plan, dry_run=False)
        msgs = [r.message for r in reports]
        self.status_label.set_text(" | ".join(msgs))
        self.exec_btn.set_sensitive(False)


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
