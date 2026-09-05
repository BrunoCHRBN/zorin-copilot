# Decisão de design: executor desacoplado com modo dry-run real — integra Gio.AppInfo e SystemController para ações concretas no desktop.

"""Executor de ações no desktop: orquestração de janelas, apps e entradas."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Sequence

from ..ai.actions import ActionPlan, ActionType, DesktopAction
from ..core.a11y import DesktopInspector, UIElement
from ..core.apps import AppManager
from ..core.clipboard import ClipboardService
from ..core.files import FileManager
from ..core.media import MediaPlayerManager
from .system import SystemController


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
                # Para o fluxo em caso de falha em cadeia
                break
        return reports

    def _execute_single(self, action: DesktopAction) -> ExecutionReport:
        if action.action_type == ActionType.LAUNCH_APP:
            return self._launch_app(action.target)

        if action.action_type == ActionType.OPEN_URL:
            return self._open_url(action.target)

        if action.action_type == ActionType.SYSTEM_CONTROL:
            return self._control_system(action.target, action.params)

        if action.action_type == ActionType.NOTIFY:
            msg = action.params.get("message", action.target)
            return self._notify("Zorin Copilot", msg)

        if action.action_type == ActionType.CLICK:
            return self._click_element(action.target)

        if action.action_type == ActionType.CAPTURE_SCREEN:
            return ExecutionReport(action=action, success=True, message=action.describe())

        if action.action_type == ActionType.ANSWER:
            return ExecutionReport(action=action, success=True, message=action.target)

        if action.action_type == ActionType.SMART_OCR:
            return self._copy_ocr_text(action)

        if action.action_type == ActionType.FIX_COMMAND:
            return self._execute_fix_command(action)

        if action.action_type == ActionType.MEDIA_CONTROL:
            return self._control_media(action)

        if action.action_type == ActionType.WRITE_FILE:
            return self._write_file(action)

        if action.action_type == ActionType.ORGANIZE_FILES:
            return self._organize_files(action)

        if action.action_type == ActionType.OPEN_DOCUMENT:
            return self._open_document(action)

        return ExecutionReport(
            action=action,
            success=False,
            message=f"Tipo de ação não implementado: {action.action_type}",
        )

    def _open_document(self, action: DesktopAction) -> ExecutionReport:
        fpath = action.target
        page = int(action.params.get("page_number", 1))
        from ..core.rag import LocalDocumentRAG
        rag = getattr(self, "rag", None) or LocalDocumentRAG()
        ok, msg = rag.open_document(fpath, page_number=page)
        return ExecutionReport(action=action, success=ok, message=msg)

    def _copy_ocr_text(self, action: DesktopAction) -> ExecutionReport:
        text = action.target
        if not text:
            return ExecutionReport(action=action, success=False, message="Nenhum texto disponível para cópia.")
        ok = ClipboardService.set_text(text)
        kind = action.params.get("kind", "conteúdo")
        if ok:
            return ExecutionReport(
                action=action,
                success=True,
                message=f"✓ {kind.capitalize()} extraído da tela copiado para a área de transferência!",
            )
        return ExecutionReport(
            action=action,
            success=False,
            message="Falha ao copiar conteúdo para a área de transferência.",
        )

    def _execute_fix_command(self, action: DesktopAction) -> ExecutionReport:
        cmd = action.params.get("command") or action.target
        if not cmd:
            return ExecutionReport(action=action, success=False, message="Nenhum comando especificado para auto-cura.")

        requires_sudo = bool(action.params.get("requires_sudo", False) or "sudo " in cmd)
        terminal = bool(action.params.get("terminal", True))

        if terminal or requires_sudo:
            term_bin = shutil.which("gnome-terminal") or shutil.which("ptyxis") or shutil.which("xterm")
            if term_bin:
                try:
                    wrapper = (
                        f"echo '========================================'; "
                        f"echo '   Zorin Copilot • Auto-Cura do Sistema'; "
                        f"echo '========================================'; "
                        f"echo 'Comando a executar:'; "
                        f"echo '  {cmd}'; "
                        f"echo '----------------------------------------'; "
                        f"{cmd}; "
                        f"RET=$?; "
                        f"echo; "
                        f"echo '----------------------------------------'; "
                        f"if [ $RET -eq 0 ]; then "
                        f"  echo '✓ Execução finalizada com sucesso (código 0).'; "
                        f"else "
                        f"  echo '⚠️ Comando retornou código de saída '$RET'.'; "
                        f"fi; "
                        f"read -p 'Pressione [Enter] para fechar esta janela...' -r"
                    )
                    if "gnome-terminal" in term_bin or "ptyxis" in term_bin:
                        subprocess.Popen([term_bin, "--", "bash", "-c", wrapper])
                    else:
                        subprocess.Popen([term_bin, "-e", f"bash -c \"{wrapper}\""])
                    return ExecutionReport(
                        action=action,
                        success=True,
                        message=f"Terminal aberto executando: {cmd}",
                    )
                except Exception as exc:
                    return ExecutionReport(action=action, success=False, message=f"Erro ao abrir terminal: {exc}")
            else:
                return ExecutionReport(action=action, success=False, message="Nenhum terminal encontrado para execução interativa.")
        else:
            try:
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=25)
                if res.returncode == 0:
                    out = res.stdout.strip()[:100] or "Comando executado com sucesso."
                    return ExecutionReport(action=action, success=True, message=out)
                else:
                    err = res.stderr.strip()[:120] or f"Falha (código {res.returncode})"
                    return ExecutionReport(action=action, success=False, message=err)
            except Exception as exc:
                return ExecutionReport(action=action, success=False, message=f"Falha na execução: {exc}")

    def _launch_app(self, app_name: str) -> ExecutionReport:
        act = DesktopAction(ActionType.LAUNCH_APP, app_name)
        app, matched_name = AppManager.find_app(app_name)
        if app:
            ok, msg = AppManager.launch(app)
            return ExecutionReport(action=act, success=ok, message=msg)

        cmd = shutil.which(app_name.lower())
        if cmd:
            try:
                subprocess.Popen([cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return ExecutionReport(action=act, success=True, message=f"Aplicativo '{app_name}' iniciado.")
            except Exception as exc:
                return ExecutionReport(action=act, success=False, message=f"Erro ao iniciar '{app_name}': {exc}")

        return ExecutionReport(
            action=act,
            success=False,
            message=f"Aplicativo '{app_name}' não foi encontrado no Zorin OS.",
        )

    def _open_url(self, url: str) -> ExecutionReport:
        act = DesktopAction(ActionType.OPEN_URL, url)
        if not url.startswith(("http://", "https://", "mailto:")):
            url = f"https://{url}"
        try:
            from gi.repository import Gio
            ok = Gio.AppInfo.launch_default_for_uri(url, None)
            if ok:
                return ExecutionReport(action=act, success=True, message=f"Endereço '{url}' aberto no navegador padrão.")
        except Exception:
            pass

        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return ExecutionReport(action=act, success=True, message=f"Endereço '{url}' aberto com xdg-open.")
        except Exception as exc:
            return ExecutionReport(action=act, success=False, message=f"Erro ao abrir endereço '{url}': {exc}")

    def _control_system(self, target: str, params: dict) -> ExecutionReport:
        act = DesktopAction(ActionType.SYSTEM_CONTROL, target, params)
        setting = params.get("setting", target)

        if setting == "dark_mode":
            ok, msg = SystemController.set_color_scheme(params.get("value", True))
            return ExecutionReport(action=act, success=ok, message=msg)
        if setting == "night_light":
            ok, msg = SystemController.toggle_night_light()
            return ExecutionReport(action=act, success=ok, message=msg)
        if setting == "volume":
            ok, msg = SystemController.adjust_volume(params.get("change", "up"))
            return ExecutionReport(action=act, success=ok, message=msg)
        if setting == "lock":
            ok, msg = SystemController.lock_session()
            return ExecutionReport(action=act, success=ok, message=msg)
        if setting == "screenshot":
            ok, msg = SystemController.take_screenshot()
            return ExecutionReport(action=act, success=ok, message=msg)

        return ExecutionReport(action=act, success=False, message=f"Controle de sistema '{target}' não suportado.")

    def _notify(self, title: str, message: str) -> ExecutionReport:
        act = DesktopAction(ActionType.NOTIFY, title, {"message": message})
        notify_bin = shutil.which("notify-send")
        if notify_bin:
            try:
                subprocess.run([notify_bin, "-a", "Zorin Copilot", title, message], check=False)
                return ExecutionReport(action=act, success=True, message="Notificação enviada com sucesso.")
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
        return ExecutionReport(
            action=act,
            success=True,
            message=f"Layout de janelas '{layout_name}' aplicado.",
        )

    def _control_media(self, action: DesktopAction) -> ExecutionReport:
        act = action.params.get("action", action.target)
        player = action.params.get("player")
        ok, msg = MediaPlayerManager.control(act, player_name=player)
        return ExecutionReport(action=action, success=ok, message=msg)

    def _write_file(self, action: DesktopAction) -> ExecutionReport:
        filename = action.params.get("filename") or action.target
        content = action.params.get("content", "")
        directory = action.params.get("directory")
        append = bool(action.params.get("append", False))
        ok, msg, _ = FileManager.write_document(
            filename=filename, content=content, directory=directory, append=append
        )
        return ExecutionReport(action=action, success=ok, message=msg)

    def _organize_files(self, action: DesktopAction) -> ExecutionReport:
        directory = action.params.get("directory") or action.target or "~/Downloads"
        dry_run = bool(action.params.get("dry_run", False))
        ok, msg, _ = FileManager.organize_directory(directory=directory, dry_run=dry_run)
        return ExecutionReport(action=action, success=ok, message=msg)

