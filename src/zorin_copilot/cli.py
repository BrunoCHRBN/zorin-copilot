# Decisão de design: CLI espelha todas as capacidades do núcleo para que qualquer fluxo possa ser testado e auditado via terminal.

"""Interface de linha de comando para o Zorin Copilot."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .ai.actions import ActionPlan, ActionType, DesktopAction
from .core.a11y import DesktopInspector
from .shell.executor import ActionExecutor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zorin-copilot",
        description="Assistente de IA integrado ao desktop Zorin OS.",
    )
    parser.add_argument("--version", action="version", version=f"zorin-copilot {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="diagnostica os barramentos AT-SPI2, Wayland e GNOME")

    inspect_cmd = sub.add_parser("inspect", help="inspeciona elementos de acessibilidade na tela")
    inspect_cmd.add_argument("app_name", nargs="?", default="", help="nome da aplicação a inspecionar")

    action_cmd = sub.add_parser("action", help="executa uma ação direta no desktop")
    action_cmd.add_argument("action_type", choices=["launch", "notify", "click"])
    action_cmd.add_argument("target", help="alvo da ação")
    action_cmd.add_argument("--param", default="", help="parâmetro adicional")
    action_cmd.add_argument("--dry-run", action="store_true")

    return parser


def cmd_doctor(args: argparse.Namespace) -> int:
    print("Zorin Copilot — Diagnóstico do Sistema\n")
    checks = []

    # 1. PyGObject e GTK4
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
        checks.append(("GTK 4.0 + Libadwaita 1", True, "disponível"))
    except Exception as exc:
        checks.append(("GTK 4.0 + Libadwaita 1", False, str(exc)))

    # 2. AT-SPI2
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        Atspi.init()
        desktop = Atspi.get_desktop(0)
        app_count = desktop.get_child_count() if desktop else 0
        checks.append(("Barramento AT-SPI2 (A11y)", True, f"{app_count} aplicações registradas"))
    except Exception as exc:
        checks.append(("Barramento AT-SPI2 (A11y)", False, str(exc)))

    # 3. notify-send
    import shutil
    has_notify = shutil.which("notify-send") is not None
    checks.append(("Comando notify-send", has_notify, "para notificações de desktop"))

    width = max(len(name) for name, _, _ in checks)
    failed = 0
    for name, ok, detail in checks:
        if not ok:
            failed += 1
        print(f"{'✓' if ok else '✗'} {name:<{width}}  {detail}")

    print(f"\n{len(checks) - failed}/{len(checks)} verificações OK")
    return 1 if failed else 0


def cmd_inspect(args: argparse.Namespace) -> int:
    inspector = DesktopInspector()
    apps = inspector.list_applications()
    if not apps:
        print("Nenhuma aplicação acessível detectada via AT-SPI2.")
        return 1

    if not args.app_name:
        print(f"Aplicações registradas ({len(apps)}):")
        for app in apps:
            print(f"  • {app}")
        print("\nDica: use 'zorin-copilot-cli inspect <nome>' para ver a árvore de elementos.")
        return 0

    root = inspector.inspect_application(args.app_name)
    if not root:
        print(f"Aplicação '{args.app_name}' não encontrada ou sem janela acessível.")
        return 1

    print(f"Árvore semântica de '{args.app_name}':\n")
    print(root.to_summary())
    return 0


def cmd_action(args: argparse.Namespace) -> int:
    executor = ActionExecutor()
    type_map = {
        "launch": ActionType.LAUNCH_APP,
        "notify": ActionType.NOTIFY,
        "click": ActionType.CLICK,
    }
    action_type = type_map[args.action_type]
    params = {}
    if args.param:
        params["message"] = args.param
        params["text"] = args.param

    action = DesktopAction(action_type=action_type, target=args.target, params=params)
    plan = ActionPlan(thought="Comando via CLI", actions=[action])

    reports = executor.execute_plan(plan, dry_run=args.dry_run)
    for rep in reports:
        print(f"{'✓' if rep.success else '✗'} {rep.message}")
    return 0 if all(r.success for r in reports) else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "doctor": cmd_doctor,
        "inspect": cmd_inspect,
        "action": cmd_action,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
