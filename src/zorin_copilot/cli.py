# Decisão de design: CLI espelha todas as capacidades do núcleo para que qualquer fluxo possa ser testado, scriptado e auditado via terminal.

"""Interface de linha de comando para o Zorin Copilot."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .ai.actions import ActionPlan, ActionType, DesktopAction
from .ai.engine import IntentEngine
from .core.a11y import DesktopInspector
from .core.config import CopilotConfig
from .shell.executor import ActionExecutor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zorin-copilot-cli",
        description="Assistente de IA integrado ao desktop Zorin OS.",
    )
    parser.add_argument("--version", action="version", version=f"zorin-copilot {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # doctor
    sub.add_parser("doctor", help="diagnostica os barramentos AT-SPI2, Wayland e GNOME")

    # inspect
    inspect_cmd = sub.add_parser("inspect", help="inspeciona elementos de acessibilidade na tela")
    inspect_cmd.add_argument("app_name", nargs="?", default="", help="nome da aplicação a inspecionar")

    # ask
    ask_cmd = sub.add_parser("ask", help="envia uma pergunta ou comando para a IA")
    ask_cmd.add_argument("prompt", help="texto da solicitação (ex: 'como acessar o gmail', 'abrir steam')")
    ask_cmd.add_argument("--execute", action="store_true", help="executa automaticamente as ações propostas")

    # config
    config_cmd = sub.add_parser("config", help="gerencia configurações de IA")
    config_cmd.add_argument("--show", action="store_true", help="exibe configuração atual")
    config_cmd.add_argument("--set-gemini-key", help="define a chave de API do Gemini")
    config_cmd.add_argument("--set-provider", choices=["gemini", "ollama", "openai"], help="define o provedor ativo")

    # action
    action_cmd = sub.add_parser("action", help="executa uma ação direta no desktop")
    action_cmd.add_argument("action_type", choices=["launch", "notify", "click", "url"])
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

    # 3. Provedor de IA
    cfg = CopilotConfig.load()
    configured = cfg.is_configured()
    checks.append((f"Provedor IA ({cfg.provider})", configured, "configurado" if configured else "chave não informada (use ⚙️ na UI)"))

    # 4. notify-send
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


def cmd_ask(args: argparse.Namespace) -> int:
    engine = IntentEngine()
    print(f"Analisando: '{args.prompt}'...\n")
    plan = engine.parse(args.prompt)

    print(f"💡 Resposta / Pensamento:\n{plan.thought}\n")

    if plan.actions:
        print("🎯 Ações Propostas:")
        for idx, act in enumerate(plan.actions, 1):
            print(f"  {idx}. [{act.action_type.value}] {act.describe()}")

        if args.execute:
            print("\nExecutando plano...")
            executor = ActionExecutor()
            reports = executor.execute_plan(plan)
            for r in reports:
                print(f"  {'✓' if r.success else '✗'} {r.message}")
    else:
        print("Nenhuma ação de desktop necessária.")

    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = CopilotConfig.load()
    changed = False

    if args.set_gemini_key:
        cfg.gemini_api_key = args.set_gemini_key
        changed = True
        print("Chave Gemini atualizada.")

    if args.set_provider:
        cfg.provider = args.set_provider
        changed = True
        print(f"Provedor alterado para '{args.set_provider}'.")

    if changed:
        cfg.save()
        print("Configuração salva com sucesso.")

    if args.show or not changed:
        print("Configuração do Zorin Copilot:")
        print(f"  Provedor ativo: {cfg.provider}")
        print(f"  Gemini Configurado: {'Sim' if bool(cfg.gemini_api_key) else 'Não'}")
        print(f"  Gemini Modelo: {cfg.gemini_model}")
        print(f"  Ollama URL: {cfg.ollama_url} (Modelo: {cfg.ollama_model})")
        print(f"  Arquivo: {cfg.config_file()}")

    return 0


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
        "url": ActionType.OPEN_URL,
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
        "ask": cmd_ask,
        "config": cmd_config,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
