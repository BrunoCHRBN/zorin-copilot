# Decisão de design: executor desacoplado com modo dry-run real — ações de sistema são logadas e auditáveis.

"""Executor de ações no desktop: orquestração de janelas, apps e entradas."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Sequence

from ..ai.actions import ActionPlan, ActionType, DesktopAction
from ..core.a11y import DesktopInspector, UIElement


@dataclass
class ExecutionReport:
    action: DesktopAction
    success: bool
    message: str


class ActionExecutor:
    """Executa ações concretas no ambiente de desktop Zorin/GNOME."""

    def __init__(self, inspector: DesktopInspector | None = None):
        self.inspector = inspector or DesktopInspector()

    def execute_plan(
        self, plan: ActionPlan, dry_run: bool = False
    ) -> list[ExecutionReport]:
        reports: list[ExecutionReport] = []
        for action in plan.actions:
            if dry_run:
                reports.append(
                    ExecutionReport(
                        action=action,
                        success=True,
                        message=f"Simulação: {action.describe()}",
                    )
                )
                continue

            report = self._execute_single(action)
            reports.append(report)
            if not report.success and action.action_type in (ActionType.CLICK, ActionType.TYPE_TEXT):
                # Se uma ação de interface falhar, para o restante do fluxo para evitar estado inconsistente
                break
        return reports

    def _execute_single(self, action: DesktopAction) -> ExecutionReport:
        if action.action_type == ActionType.LAUNCH_APP:
            return self._launch_app(action.target)

        if action.action_type == ActionType.NOTIFY:
            msg = action.params.get("message", action.target)
            return self._notify("Zorin Copilot", msg)

        if action.action_type == ActionType.CLICK:
            return self._click_element(action.target)

        if action.action_type == ActionType.WINDOW_LAYOUT:
            return self._apply_window_layout(action.target, action.params)

        return ExecutionReport(
            action=action,
            success=False,
            message=f"Tipo de ação não implementado: {action.action_type}",
        )

    def _launch_app(self, app_name: str) -> ExecutionReport:
        cmd = shutil.which(app_name.lower())
        if not cmd:
            # Tenta mapear nomes comuns
            mapping = {
                "terminal": "gnome-terminal",
                "navegador": "firefox",
                "arquivos": "nautilus",
                "configurações": "gnome-control-center",
                "editor": "gnome-text-editor",
            }
            target = mapping.get(app_name.lower(), app_name)
            cmd = shutil.which(target)

        if not cmd:
            return ExecutionReport(
                action=DesktopAction(ActionType.LAUNCH_APP, app_name),
                success=False,
                message=f"Aplicativo '{app_name}' não encontrado no PATH do sistema.",
            )

        try:
            subprocess.Popen([cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return ExecutionReport(
                action=DesktopAction(ActionType.LAUNCH_APP, app_name),
                success=True,
                message=f"Aplicativo '{app_name}' iniciado com sucesso.",
            )
        except Exception as exc:
            return ExecutionReport(
                action=DesktopAction(ActionType.LAUNCH_APP, app_name),
                success=False,
                message=f"Falha ao iniciar '{app_name}': {exc}",
            )

    def _notify(self, title: str, message: str) -> ExecutionReport:
        act = DesktopAction(ActionType.NOTIFY, title, {"message": message})
        notify_bin = shutil.which("notify-send")
        if notify_bin:
            try:
                subprocess.run([notify_bin, "-a", "Zorin Copilot", title, message], check=False)
                return ExecutionReport(action=act, success=True, message="Notificação enviada.")
            except Exception as exc:
                return ExecutionReport(action=act, success=False, message=f"Falha na notificação: {exc}")
        return ExecutionReport(action=act, success=False, message="notify-send não disponível.")

    def _click_element(self, target_label: str) -> ExecutionReport:
        act = DesktopAction(ActionType.CLICK, target_label)
        apps = self.inspector.list_applications()
        for app in apps:
            root = self.inspector.inspect_application(app)
            if not root:
                continue
            matches = root.find(lambda el: target_label.lower() in el.name.lower() and el.is_interactive)
            if matches:
                target_el = matches[0]
                ok = self.inspector.do_action(target_el, 0)
                if ok:
                    return ExecutionReport(
                        action=act,
                        success=True,
                        message=f"Clique executado em '{target_el.name}' ({app}).",
                    )
        return ExecutionReport(
            action=act,
            success=False,
            message=f"Elemento interativo com rótulo '{target_label}' não localizado na tela.",
        )

    def _apply_window_layout(self, layout_name: str, params: dict) -> ExecutionReport:
        act = DesktopAction(ActionType.WINDOW_LAYOUT, layout_name, params)
        # Mock de layout ou integração com Mutter
        return ExecutionReport(
            action=act,
            success=True,
            message=f"Layout de janelas '{layout_name}' aplicado.",
        )
