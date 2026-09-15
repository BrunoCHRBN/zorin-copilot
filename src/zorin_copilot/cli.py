# Decisão de design: CLI espelha todas as capacidades do núcleo para que qualquer fluxo possa ser testado, scriptado e auditado via terminal.

"""Interface de linha de comando para o Zorin Copilot."""

from __future__ import annotations

import argparse
import json
import os
import sys

from pathlib import Path

from . import __version__
from .ai.actions import ActionPlan, ActionType, DesktopAction
from .ai.agent import AgentLoop
from .ai.agent_router import AgentRouter, RouteMode, classify_objective
from .ai.agent_tools import ToolRegistry
from .ai.engine import IntentEngine
from .ai.providers import DEFAULT_GEMINI_MODEL, GEMINI_MODEL_CHOICES
from .core.a11y import DesktopInspector
from .core.browser import BrowserManager, WebPageReader
from .core.config import CopilotConfig
from .core.memory import MemoryManager
from .core.rag import LocalDocumentRAG
from .core.shortcuts import AutostartManager, ShortcutManager
from .core.web_search import DeepWebResearcher, WebSearchClient
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
    config_cmd.add_argument(
        "--set-gemini-model",
        help=f"define o modelo Gemini (padrão: {DEFAULT_GEMINI_MODEL}; opções: {', '.join(GEMINI_MODEL_CHOICES)})",
    )
    config_cmd.add_argument("--set-workbuddy-key", help="define a chave de API do WorkBuddy AI / Tencent HY4")
    config_cmd.add_argument("--set-workbuddy-model", help="define o modelo WorkBuddy (ex: hy4-preview)")
    config_cmd.add_argument("--set-provider", choices=["gemini", "ollama", "openai", "workbuddy"], help="define o provedor ativo")

    # memory
    memory_cmd = sub.add_parser("memory", help="gerencia a base de conhecimento e histórico de execuções")
    mem_sub = memory_cmd.add_subparsers(dest="mem_action", required=True)
    mem_sub.add_parser("list", help="lista fatos e preferências aprendidas")
    mem_sub.add_parser("history", help="exibe histórico recente de ações no desktop")
    mem_sub.add_parser("profile", help="exibe perfil do sistema detectado")
    mem_sub.add_parser("clear", help="limpa toda a base de memória")
    add_mem = mem_sub.add_parser("add", help="adiciona um fato manualmente à base")
    add_mem.add_argument("key", help="identificador único do fato (ex: 'navegador_preferido')")
    add_mem.add_argument("content", help="conteúdo descritivo do fato")
    del_mem = mem_sub.add_parser("remove", help="remove um fato da base pela chave")
    del_mem.add_argument("key", help="chave do fato a remover")

    # action
    action_cmd = sub.add_parser("action", help="executa uma ação direta no desktop")
    action_cmd.add_argument("action_type", choices=["launch", "notify", "click", "url"])
    action_cmd.add_argument("target", help="alvo da ação")
    action_cmd.add_argument("--param", default="", help="parâmetro adicional")
    action_cmd.add_argument("--dry-run", action="store_true")

    # search
    search_cmd = sub.add_parser("search", help="realiza pesquisa na web em tempo real")
    search_cmd.add_argument("query", help="termo a pesquisar na internet")
    search_cmd.add_argument("--limit", type=int, default=4, help="número máximo de resultados")
    search_cmd.add_argument("--academic", action="store_true", help="busca em fontes acadêmicas e órgãos oficiais")
    search_cmd.add_argument(
        "--source",
        default="all",
        choices=["all", "scielo", "ibge", "sebrae", "ipea", "scholar", "internacional"],
        help="fonte da busca acadêmica (padrão: all)",
    )

    # rag
    rag_cmd = sub.add_parser("rag", help="indexação local e busca em documentos pessoais (PDFs, contratos, planilhas)")
    rag_sub = rag_cmd.add_subparsers(dest="rag_action", required=True)
    rag_idx = rag_sub.add_parser("index", help="varre e indexa documentos em ~/Documentos e ~/Downloads")
    rag_idx.add_argument("--dir", help="diretório específico para indexar")
    rag_search = rag_sub.add_parser("search", help="busca termos nos documentos locais indexados")
    rag_search.add_argument("query", help="termo a pesquisar nos documentos")
    rag_search.add_argument("--limit", type=int, default=4, help="número de resultados")
    rag_ask = rag_sub.add_parser("ask", help="responde a dúvidas sobre documentos pessoais")
    rag_ask.add_argument("question", help="pergunta sobre contratos, planilhas ou PDFs")
    rag_sub.add_parser("stats", help="exibe estatísticas e tipos de documentos indexados")
    rag_tc = rag_sub.add_parser("trust-check", help="avalia o nível de confiança e elegibilidade de um arquivo para RAG")
    rag_tc.add_argument("file_path", help="caminho do documento a avaliar")
    rag_pii = rag_sub.add_parser("pii-test", help="testa a detecção e mascaramento de dados sensíveis")
    rag_pii.add_argument("text", help="texto de teste com dados a mascarar")

    # setup
    setup_cmd = sub.add_parser("setup", help="configura atalhos globais e inicialização com o sistema no Zorin OS")
    setup_cmd.add_argument("--shortcut", action="store_true", help="registra atalhos de teclado globais GNOME (Super+C e Super+Shift+S)")
    setup_cmd.add_argument("--autostart", action="store_true", help="habilita inicialização automática no boot/login")
    setup_cmd.add_argument("--disable-autostart", action="store_true", help="desabilita inicialização automática")
    setup_cmd.add_argument("--all", action="store_true", help="configura atalhos e autostart completos")
    setup_cmd.add_argument("--status", action="store_true", help="exibe o status dos atalhos e autostart")

    # web
    web_cmd = sub.add_parser("web", help="navegação web aprofundada e leitura de páginas")
    web_sub = web_cmd.add_subparsers(dest="web_action", required=True)
    web_read = web_sub.add_parser("read", help="lê e extrai o conteúdo da aba aberta ou de uma URL")
    web_read.add_argument("--url", help="URL específica para ler (opcional)")
    web_deep = web_sub.add_parser("deep-search", help="executa pesquisa aprofundada analisando múltiplos sites")
    web_deep.add_argument("query", help="tema da pesquisa aprofundada")
    web_deep.add_argument("--sources", type=int, default=3, help="número de fontes a analisar")

    # agent — uso autônomo supervisionado do desktop
    agent_cmd = sub.add_parser(
        "agent",
        help="executa um objetivo no desktop passo a passo (modo agente supervisionado)",
    )
    agent_cmd.add_argument("objective", help="objetivo em linguagem natural (ex: 'abrir o Firefox e pesquisar X')")
    agent_cmd.add_argument("--dry-run", action="store_true", help="mostra o plano sem tocar no desktop")
    agent_cmd.add_argument("--max-steps", type=int, default=12, help="número máximo de passos (padrão 12)")
    agent_cmd.add_argument("--max-seconds", type=float, default=180.0, help="tempo máximo em segundos (padrão 180)")
    agent_cmd.add_argument("--local-only", action="store_true", help="usa apenas o modelo local (Ollama)")
    agent_cmd.add_argument("--cloud", action="store_true", help="usa apenas o modelo em nuvem")
    agent_cmd.add_argument("--yes", action="store_true", help="aprova automaticamente ações de risco (use com cuidado)")
    agent_cmd.add_argument("--no-audit", action="store_true", help="não grava a execução na memória/auditoria")
    agent_cmd.add_argument("--verbose", action="store_true", help="mostra a observação completa de cada passo")
    agent_cmd.add_argument("--json", action="store_true", help="saída em JSON (para scriptar)")

    # study — captura e organização de material de estudo
    study_cmd = sub.add_parser("study", help="captura e organiza material de estudo a partir da tela")
    study_sub = study_cmd.add_subparsers(dest="study_action", required=True)
    study_cap = study_sub.add_parser(
        "capture",
        help="lê o conteúdo da aula renderizada (AT-SPI) em vez de baixar o HTML",
    )
    study_cap.add_argument("--app", help="nome do aplicativo alvo (padrão: app em foco)")
    study_cap.add_argument(
        "--strategy",
        choices=["auto", "atspi", "clipboard", "ocr"],
        default="auto",
        help="auto tenta AT-SPI, cai para a área de transferência e por fim para OCR da tela (padrão)",
    )
    study_cap.add_argument(
        "--discipline",
        help="nome da disciplina (ex: 'Contabilidade Gerencial'); vira a subpasta em ~/Documentos/Estudos",
    )
    study_cap.add_argument("--min-words", type=int, default=120, help="aviso de captura rala (padrão 120)")
    study_cap.add_argument("--out", help="arquivo .md de destino (padrão: ~/Documentos/Estudos/<titulo>.md)")
    study_cap.add_argument("--no-save", action="store_true", help="não grava arquivo, só mostra")
    study_cap.add_argument("--print", dest="show", action="store_true", help="imprime o texto capturado")
    study_cap.add_argument("--json", action="store_true", help="saída em JSON (para scriptar)")

    study_deck = study_sub.add_parser("deck", help="gera flashcards a partir de um material capturado")
    study_deck.add_argument("--from", dest="source", required=True, help="arquivo .md capturado")
    study_deck.add_argument(
        "--discipline",
        default=None,
        help="nome da disciplina (padrão: a que veio da captura, senão 'Gestão Comercial')",
    )
    study_deck.add_argument("--max-cards", type=int, default=10, help="máximo de cards (padrão 10)")
    study_deck.add_argument("--local-only", action="store_true", help="usa apenas o modelo local")
    study_deck.add_argument("--cloud", action="store_true", help="usa apenas o modelo em nuvem")
    study_deck.add_argument("--json", action="store_true", help="saída em JSON")

    study_sum = study_sub.add_parser(
        "summarize", help="resumo em tópicos e glossário a partir de um material capturado"
    )
    study_sum.add_argument("--from", dest="source", required=True, help="arquivo .md capturado")
    study_sum.add_argument(
        "--discipline",
        default=None,
        help="nome da disciplina (padrão: a que veio da captura, senão 'Gestão Comercial')",
    )
    study_sum.add_argument("--max-topics", type=int, default=8, help="máximo de tópicos (padrão 8)")
    study_sum.add_argument("--max-terms", type=int, default=12, help="máximo de termos (padrão 12)")
    study_sum.add_argument("--out", help="arquivo .md de destino (padrão: ao lado do material)")
    study_sum.add_argument("--local-only", action="store_true", help="usa apenas o modelo local")
    study_sum.add_argument("--cloud", action="store_true", help="usa apenas o modelo em nuvem")
    study_sum.add_argument("--no-save", action="store_true", help="não grava arquivo, só mostra")
    study_sum.add_argument("--json", action="store_true", help="saída em JSON")

    study_decks = study_sub.add_parser("decks", help="lista os baralhos e quantos cards estão vencidos")
    study_decks.add_argument("--discipline", help="mostra só os baralhos desta disciplina")

    study_rev = study_sub.add_parser("review", help="sessão de revisão espaçada (SM-2) no terminal")
    study_rev.add_argument("--deck", help="id do baralho (padrão: o com mais cards vencidos)")
    study_rev.add_argument("--limit", type=int, default=20, help="máximo de cards na sessão")
    study_rev.add_argument("--all", action="store_true", help="inclui cards ainda não vencidos")

    study_abnt = study_sub.add_parser("abnt", help="gera .docx no padrão ABNT a partir de um .md")
    study_abnt.add_argument("--from", dest="source", required=True, help="arquivo .md de origem")
    study_abnt.add_argument("--out", help="arquivo .docx de destino (padrão: mesmo nome)")

    return parser


def cmd_doctor(args: argparse.Namespace) -> int:
    from .core.desktop.env import current_environment
    from .core.desktop import screenshot as desktop_shot
    from .core.desktop import controls as desktop_controls

    env = current_environment()

    print("Zorin Copilot — Diagnóstico do Sistema\n")
    print(f"  Ambiente: {env.describe()}")
    print(f"  Gerenciador de pacotes: {env.package_manager or 'desconhecido'}")
    print(f"  Backend de atalhos: {ShortcutManager.backend_name()}")
    print(f"  Backend de captura: {desktop_shot.select_backend(env).name}")

    missing = desktop_shot.missing_dependencies(env)
    if missing:
        print(f"  ⚠ Captura: faltam {', '.join(missing)}")

    # Red zones: 80px travados sem barra é a reclamação clássica em Hyprland/Sway.
    try:
        from .core.fence import default_insets

        bottom, top = default_insets(env)
        if bottom or top:
            print(f"  Red zones: inferior {bottom}px, superior {top}px (detectado do ambiente)")
        else:
            print("  Red zones: desativadas (nenhum painel detectado neste ambiente)")
    except Exception:
        pass
    print()

    checks = []

    # 1. PyGObject, GTK4 e a versão mínima de runtime que o código realmente usa
    try:
        from .ui.gi_versions import require_gtk4, toolkit_report

        require_gtk4()
        report = toolkit_report()
        parts = []
        for namespace, info in report.get("details", {}).items():  # type: ignore[union-attr]
            parts.append(f"{namespace} {info['found']}")
        checks.append(("GTK4 + Libadwaita", True, " / ".join(parts)))
    except Exception as exc:
        # ToolkitTooOld já vem com a mensagem de "como resolver" pronta.
        checks.append(("GTK4 + Libadwaita", False, str(exc)))

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

    # 4. Atalhos Globais (backend escolhido pelo ambiente)
    backend = ShortcutManager.backend_name()
    has_shortcut = ShortcutManager.is_registered()
    checks.append((
        "Atalho Global HUD (Super+C)",
        has_shortcut,
        f"registrado via {backend}" if has_shortcut else f"não registrado via {backend} (use 'zorin-copilot-cli setup --shortcut')",
    ))

    has_crop = ShortcutManager.is_crop_registered()
    checks.append(("Atalho Recorte (Super+Shift+S)", has_crop, f"registrado via {backend}" if has_crop else "não registrado"))

    has_voice = ShortcutManager.is_voice_registered()
    checks.append(("Atalho Voz (Super+Shift+V)", has_voice, f"registrado via {backend}" if has_voice else "não registrado"))

    # 5. Motor Ollama Local & Modelos
    ollama_ok = False
    ollama_desc = "serviço inativo ou não instalado"
    try:
        import requests
        resp = requests.get(f"{cfg.ollama_url.rstrip('/')}/api/tags", timeout=1.5)
        if resp.status_code == 200:
            m_names = [m.get("name", "") for m in resp.json().get("models", [])]
            ollama_ok = True
            ollama_desc = f"ativo ({', '.join(m_names) if m_names else 'sem modelos'})"
    except Exception:
        pass
    checks.append(("Motor Ollama Local (GPU)", ollama_ok, ollama_desc))

    # 6. Síntese de Voz Offline (Piper TTS)
    from .ai.local_voice import PIPER_MODELS_DIR, PIPER_VOICE_NAME
    piper_model_file = PIPER_MODELS_DIR / f"{PIPER_VOICE_NAME}.onnx"
    piper_ok = False
    piper_desc = "piper-tts não instalado"
    try:
        import piper  # noqa: F401
        if piper_model_file.exists():
            piper_ok = True
            piper_desc = f"disponível ({PIPER_VOICE_NAME})"
        else:
            piper_desc = f"modelo ausente em {piper_model_file}"
    except ImportError:
        pass
    checks.append(("Síntese Piper TTS", piper_ok, piper_desc))

    # 7. Reconhecimento de Fala Offline (faster-whisper)
    whisper_ok = False
    whisper_desc = "faster-whisper não instalado"
    try:
        import faster_whisper  # noqa: F401
        whisper_ok = True
        whisper_desc = f"disponível (modelo {cfg.whisper_model})"
    except ImportError:
        pass
    checks.append(("Reconhecimento faster-whisper", whisper_ok, whisper_desc))

    # 8. Subsistema de Áudio
    import shutil
    has_audio = shutil.which("pw-record") is not None or shutil.which("arecord") is not None
    audio_desc = "PipeWire (pw-record / pw-play)" if shutil.which("pw-record") else "ALSA (arecord / aplay)"
    checks.append(("Subsistema de Áudio", has_audio, audio_desc if has_audio else "pw-record ou arecord não encontrado"))

    # 9. Autostart
    has_auto = AutostartManager.is_enabled()
    checks.append(("Autostart no Boot/Login", has_auto, "ativo em ~/.config/autostart" if has_auto else "inativo (use 'zorin-copilot-cli setup --autostart')"))

    # 10. Poppler Utils (pdftotext)
    has_poppler = shutil.which("pdftotext") is not None
    checks.append(("Poppler Utils (pdftotext)", has_poppler, "para extração de texto em PDFs" if has_poppler else "instale poppler-utils"))

    # 11. Leitor Evince
    has_evince = shutil.which("evince") is not None
    checks.append(("Leitor Evince", has_evince, "para abertura de PDFs na página exata" if has_evince else "opcional"))

    # 12. notify-send
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

    if args.set_gemini_model:
        cfg.gemini_model = args.set_gemini_model
        changed = True
        print(f"Modelo Gemini alterado para '{args.set_gemini_model}'.")

    if args.set_workbuddy_key:
        cfg.workbuddy_api_key = args.set_workbuddy_key
        changed = True
        print("Chave WorkBuddy atualizada.")

    if args.set_workbuddy_model:
        cfg.workbuddy_model = args.set_workbuddy_model
        changed = True
        print(f"Modelo WorkBuddy alterado para '{args.set_workbuddy_model}'.")

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
        print(f"  WorkBuddy Configurado: {'Sim' if bool(cfg.workbuddy_api_key) else 'Não'}")
        print(f"  WorkBuddy Modelo: {cfg.workbuddy_model}")
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


def cmd_memory(args: argparse.Namespace) -> int:
    mem = MemoryManager()

    if args.mem_action == "list":
        facts = mem.get_all_facts()
        if not facts:
            print("Nenhum fato memorizado na base de conhecimento.")
            return 0
        print(f"Base de Conhecimento ({len(facts)} fatos):")
        for f in facts:
            print(f"  • [{f['key']}] {f['content']} ({f['source']} - {f['updated_at'][:10]})")
        return 0

    if args.mem_action == "history":
        actions = mem.get_recent_actions(limit=15)
        if not actions:
            print("Nenhum histórico de ações registrado.")
            return 0
        print(f"Histórico de ações no desktop ({len(actions)}):")
        for a in actions:
            status = "✓" if a["success"] else "✗"
            print(f"  {status} [{a['action_type']}] {a['target']} — pedido: '{a['prompt']}' ({a['timestamp'][:19]})")
        return 0

    if args.mem_action == "profile":
        profile = mem.get_system_profile()
        print("Perfil do Sistema Detectado:")
        for k, v in profile.items():
            print(f"  • {k}: {v}")
        return 0

    if args.mem_action == "add":
        mem.save_fact(args.key, args.content, category="usuario", source="cli")
        print(f"Fato '{args.key}' salvo na base de conhecimento.")
        return 0

    if args.mem_action == "remove":
        ok = mem.delete_fact_by_key(args.key)
        if ok:
            print(f"Fato '{args.key}' removido.")
        else:
            print(f"Fato com chave '{args.key}' não encontrado.")
        return 0

    if args.mem_action == "clear":
        mem.clear_all()
        print("Base de memória e histórico limpos com sucesso.")
        return 0

    return 0


def cmd_search(args: argparse.Namespace) -> int:
    client = WebSearchClient()

    # Busca acadêmica era exclusiva do cliente de voz; aqui fica scriptável.
    if getattr(args, "academic", False):
        print(f"Pesquisando em fontes acadêmicas: '{args.query}'...\n")
        results = client.academic_search(args.query, source=args.source, max_results=args.limit)
        if not results:
            print("Nenhum resultado acadêmico.")
            return 1
        print(f"Resultados acadêmicos ({len(results)}):\n")
        for idx, res in enumerate(results, 1):
            print(f"{idx}. {res.title}")
            print(f"   URL: {res.url}")
            if res.snippet:
                print(f"   {res.snippet}")
            print()
        return 0

    print(f"Pesquisando na web: '{args.query}'...\n")
    results = client.search(args.query, max_results=args.limit)
    if not results:
        print("Nenhum resultado encontrado na web.")
        return 1

    print(f"Resultados encontrados ({len(results)}):\n")
    for idx, res in enumerate(results, 1):
        print(f"{idx}. {res.title}")
        print(f"   URL: {res.url}")
        if res.snippet:
            print(f"   {res.snippet}")
        print()
    return 0


def cmd_rag(args: argparse.Namespace) -> int:
    rag = LocalDocumentRAG()

    if args.rag_action == "index":
        if args.dir:
            target_dir = Path(args.dir).expanduser()
            if not target_dir.exists():
                print(f"Diretório não encontrado: {target_dir}")
                return 1
            print(f"Indexando documentos em: {target_dir}...")
            count = 0
            for ext in (".pdf", ".docx", ".odt", ".xlsx", ".ods", ".csv", ".tsv", ".txt", ".md"):
                for p in target_dir.rglob(f"*{ext}"):
                    if rag.index_file(p):
                        count += 1
                        print(f"  ✓ {p.name}")
            print(f"\nIndexação concluída: {count} documentos processados.")
            return 0

        print(f"Indexando diretórios monitorados: {', '.join(str(d) for d in rag.watched_dirs)}...")

        def progress_cb(current: int, total: int, file_name: str) -> None:
            print(f"  [{current}/{total}] {file_name}")

        indexed = rag.index_all(on_progress=progress_cb)
        print(f"\nIndexação concluída: {indexed} novos ou atualizados documentos.")
        return 0

    if args.rag_action == "search":
        print(f"Buscando por '{args.query}' na base de documentos locais...\n")
        results = rag.search(args.query, limit=args.limit)
        if not results:
            print("Nenhum trecho encontrado correspondente à busca.")
            return 0
        print(f"Resultados encontrados ({len(results)} trechos):\n")
        for idx, res in enumerate(results, 1):
            page_str = f" (Pág. {res['page_number']})" if res.get("page_number", 0) > 0 else ""
            print(f"{idx}. {res['file_name']}{page_str}")
            print(f"   Arquivo: {res['file_path']}")
            print(f"   Trecho: {res['chunk_text'][:200]}...")
            print()
        return 0

    if args.rag_action == "ask":
        print(f"Consultando documentos locais: '{args.question}'...\n")
        cfg = CopilotConfig.load()
        llm = None
        if cfg.is_configured():
            try:
                from .ai.gemini import GeminiClient
                llm = GeminiClient(cfg.gemini_api_key, model=cfg.gemini_model)
            except Exception:
                pass
        ans = rag.ask(args.question, llm_provider=llm)
        print("💡 Resposta:")
        print(ans["answer"])
        if ans.get("citations"):
            print("\n📚 Fontes consultadas:")
            for cit in ans["citations"]:
                page_str = f" - Pág. {cit['page']}" if cit.get("page", 0) > 0 else ""
                print(f"  • {cit['file']}{page_str} ({cit['path']})")
        return 0

    if args.rag_action == "stats":
        stats = rag.get_stats()
        print("Estatísticas da Base de Conhecimento Local (RAG):")
        print(f"  Total de documentos: {stats['total_documents']}")
        print(f"  Total de fragmentos (chunks): {stats['total_chunks']}")
        if stats.get("by_type"):
            print("  Por formato:")
            for ftype, cnt in stats["by_type"].items():
                print(f"    • {ftype}: {cnt}")
        print("  Diretórios monitorados:")
        for d in stats["watched_directories"]:
            print(f"    • {d}")
        return 0

    if args.rag_action == "trust-check":
        from .core.trust import DocumentTrustManager, TrustLevel

        tm = DocumentTrustManager()
        fpath = Path(args.file_path).expanduser()
        level, reason = tm.evaluate_file(fpath)
        is_ok = level != TrustLevel.BLOCKED
        icons = {
            TrustLevel.TRUSTED: "🟢 Confiável (Acesso Pleno)",
            TrustLevel.CAUTION: "🟡 Zona de Cautela (Quarentena / Download)",
            TrustLevel.BLOCKED: "🔴 Bloqueado / Restrito (Não Indexável)",
        }
        print(f"Avaliação de Confiança e Elegibilidade para RAG:")
        print(f"  • Arquivo: {fpath}")
        print(f"  • Classificação: {icons.get(level, level.value)}")
        print(f"  • Elegível para Leitura RAG: {'Sim' if is_ok else 'Não'}")
        print(f"  • Justificativa da Política: {reason}")
        return 0

    if args.rag_action == "pii-test":
        from .core.trust import PIISanitizer

        masked = PIISanitizer.mask_text(args.text)
        print("Teste de Detecção e Mascaramento de Dados Sensíveis (PII):")
        print("--- Texto Original ---")
        print(args.text)
        print("\n--- Texto Protegido (Pronto para envio) ---")
        print(masked)
        return 0

    return 0


#: Rótulos legíveis dos backends de atalho. O nome interno do backend é um
#: identificador ("gnome-media-keys", "hyprland"), não uma frase — e no Zorin
#: Copilot fora do GNOME dizer "atalhos GNOME" era simplesmente falso.
_SHORTCUT_BACKEND_LABELS: dict[str, str] = {
    "gnome-media-keys": "GNOME (media-keys)",
    "hyprland": "Hyprland (hyprland.conf)",
    "sway": "Sway (config)",
    "kde-kglobalaccel": "KDE Plasma (kglobalshortcutsrc)",
    "global-shortcuts-portal": "Portal XDG (GlobalShortcuts)",
    "none": "nenhum backend disponível",
}


def cmd_setup(args: argparse.Namespace) -> int:
    did_something = False

    if args.all or args.shortcut:
        did_something = True
        backend = ShortcutManager.backend_name()
        label = _SHORTCUT_BACKEND_LABELS.get(backend, backend)
        print(f"Configurando atalhos globais via {label} (<Super>c, <Super><Shift>s e <Super><Shift>v)...")
        ok1 = ShortcutManager.register()
        ok2 = ShortcutManager.register_crop()
        ok3 = ShortcutManager.register_voice()
        if ok1 and ok2 and ok3:
            print(f"  ✓ Atalhos registrados no backend {label}.")
        elif ok1 or ok2 or ok3:
            print("  ✓ Atalhos globais registrados com avisos.")
            print(f"   Detalhe: {ShortcutManager.last_message}")
        else:
            print(f"  ✗ Falha ao registrar atalhos no backend {label}.")
            print(f"   Detalhe: {ShortcutManager.last_message}")

    if args.all or args.autostart:
        did_something = True
        print("Configurando inicialização automática no boot/login...")
        ok = AutostartManager.enable()
        if ok:
            print(f"  ✓ Inicialização automática ativada em {AutostartManager.get_autostart_file()}")
        else:
            print("  ✗ Erro ao criar arquivo de autostart.")

    if args.disable_autostart:
        did_something = True
        print("Desabilitando inicialização automática...")
        ok = AutostartManager.disable()
        if ok:
            print("  ✓ Inicialização automática desativada.")
        else:
            print("  ✗ Erro ao remover arquivo de autostart.")

    if args.status or not did_something:
        print("Status dos Componentes do Sistema:")
        sc = ShortcutManager.is_registered()
        sc_crop = ShortcutManager.is_crop_registered()
        sc_voice = ShortcutManager.is_voice_registered()
        auto = AutostartManager.is_enabled()
        print(f"  • Atalho Global HUD (Super+C): {'✓ Ativo' if sc else '✗ Não registrado'}")
        print(f"  • Atalho Recorte (Super+Shift+S): {'✓ Ativo' if sc_crop else '✗ Não registrado'}")
        print(f"  • Atalho Conversa por Voz (Super+Shift+V): {'✓ Ativo' if sc_voice else '✗ Não registrado'}")
        print(f"  • Autostart no boot/login: {'✓ Ativo' if auto else '✗ Desativado'}")
        if not did_something:
            print("\nDica: use --shortcut, --autostart ou --all para configurar.")

    return 0


def cmd_web(args: argparse.Namespace) -> int:
    if args.web_action == "read":
        if args.url:
            print(f"Lendo URL: {args.url}...\n")
            res = WebPageReader.fetch_and_clean(args.url)
            if not res.get("success"):
                print(f"✗ Erro ao ler página: {res.get('error', 'Desconhecido')}")
                return 1
            print(f"Título: {res.get('title', 'Sem título')}")
            print(f"URL: {res.get('url')}")
            chars = res.get("length", len(res.get("text", "")))
            print(f"Caracteres: {chars}\n")
            print("--- Conteúdo Extraído ---")
            print(res.get("text", "")[:3000])
            if len(res.get("text", "")) > 3000:
                print("\n[... conteúdo truncado para exibição ...]")
            return 0

        print("Inspecionando aba ativa do navegador aberto...")
        res = WebPageReader.read_active_tab()
        if not res.get("success"):
            print(f"✗ Não foi possível ler a aba aberta: {res.get('error', 'Nenhum navegador com página acessível detectado')}")
            print("Dica: forneça uma URL com --url <link> ou mantenha o navegador visível na tela.")
            return 1
        print(f"Navegador: {res.get('browser')}")
        print(f"Título da Aba: {res.get('title', 'Sem título')}")
        if res.get("url"):
            print(f"URL detectada: {res.get('url')}")
        chars = res.get("length", len(res.get("text", "")))
        print(f"Caracteres extraídos: {chars}\n")
        print("--- Conteúdo da Página ---")
        print(res.get("text", "")[:3000])
        if len(res.get("text", "")) > 3000:
            print("\n[... conteúdo truncado para exibição ...]")
        return 0

    if args.web_action == "deep-search":
        print(f"Iniciando pesquisa aprofundada sobre: '{args.query}' (fontes: {args.sources})...\n")
        cfg = CopilotConfig.load()
        llm = None
        if cfg.is_configured():
            try:
                from .ai.gemini import GeminiClient
                llm = GeminiClient(cfg.gemini_api_key, model=cfg.gemini_model)
            except Exception:
                pass
        researcher = DeepWebResearcher()

        def show_progress(stage: str, msg: str) -> None:
            icons = {"search": "🔍", "download": "🌐", "synthesis": "🧠", "done": "✓", "failed": "✗"}
            print(f"  {icons.get(stage, '•')} {msg}")

        result = researcher.deep_search(
            args.query,
            max_sources=args.sources,
            llm_provider=llm,
            on_progress=show_progress,
        )
        if not result.get("success"):
            print(f"\n✗ Pesquisa aprofundada falhou: {result.get('error', 'Sem resultados')}")
            return 1

        print("📊 Relatório de Pesquisa Aprofundada:\n")
        print(result.get("report", ""))
        print("\n🌐 Fontes consultadas:")
        for s in result.get("sources", []):
            print(f"  • [{s.get('title', 'Fonte')}]({s.get('url')})")
        return 0

    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    """Modo agente: planeja e executa um objetivo no desktop sob supervisão."""
    mode = RouteMode.AUTO
    if args.local_only:
        mode = RouteMode.LOCAL
    elif args.cloud:
        mode = RouteMode.CLOUD

    cfg = CopilotConfig.load()
    router = AgentRouter(cfg)
    route = router.route(args.objective, mode)
    complexity, complexity_reason = classify_objective(args.objective)

    if route.planner is None:
        print(f"✗ Nenhum modelo disponível para o modo '{mode.value}': {route.reason}")
        print("  Dica: configure uma chave (--cloud) ou suba o Ollama (ollama serve).")
        return 1

    registry = ToolRegistry()
    memory = None if args.no_audit else MemoryManager()
    loop = AgentLoop(
        route.planner,
        registry,
        max_steps=args.max_steps,
        max_seconds=args.max_seconds,
        memory=memory,
    )

    if not args.json:
        print(f"🎯 Objetivo: {args.objective}")
        print(f"🧭 Roteamento: {route.mode} ({route.reason})")
        if args.dry_run:
            print("🧪 Modo simulação: nada será executado.\n")

    def on_step(step) -> None:
        if args.json:
            return
        icon = "✓" if step.ok else ("✗" if step.ok is False else "•")
        flag = " [requer aprovação]" if step.requires_approval else ""
        print(f"  {icon} {step.index + 1}. {step.tool}{flag}")
        if args.verbose or not step.ok:
            print(f"     {step.observation}")

    def on_approval(step) -> bool:
        if args.yes:
            return True
        level_desc = step.risk
        try:
            answer = input(
                f"  ⚠️  '{step.tool}' é uma ação sensível ({level_desc}). Aprovar? [s/N] "
            )
        except EOFError:
            return False  # sem terminal interativo: recusa é o padrão seguro
        return answer.strip().lower() in ("s", "sim", "y", "yes")

    try:
        if args.dry_run:
            result = loop.plan(args.objective, on_step=on_step)
        else:
            result = loop.run(args.objective, on_step=on_step, on_approval=on_approval)
    except KeyboardInterrupt:
        loop.abort()
        print("\n⏹ Interrompido — sinal de parada enviado.")
        return 130

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.success else 1

    print()
    if result.final_answer:
        print(f"💬 {result.final_answer}")
    print(f"🏁 Parada: {result.stop_reason} — {result.stop_message}")
    print(f"   Passos: {len(result.steps)} | Tempo: {result.elapsed:.1f}s | Modo: {route.mode}")
    if result.error:
        print(f"   Detalhe: {result.error}")
    if memory is not None:
        print(f"   Auditoria: run_id {result.run_id}")
    return 0 if result.success else 1


def cmd_study(args: argparse.Namespace) -> int:
    """Comandos de estudo: captura, flashcards, revisão e entrega ABNT."""
    if args.study_action == "capture":
        return _study_capture(args)
    if args.study_action == "deck":
        return _study_deck(args)
    if args.study_action == "summarize":
        return _study_summarize(args)
    if args.study_action == "decks":
        return _study_decks(args)
    if args.study_action == "review":
        return _study_review(args)
    if args.study_action == "abnt":
        return _study_abnt(args)
    return 0


def _study_capture(args: argparse.Namespace) -> int:
    """Captura material de estudo da tela (ou da área de transferência)."""
    from .core.study_capture import LessonCapture

    capture = LessonCapture(min_words=args.min_words)
    result = capture.capture(args.app, strategy=args.strategy, discipline=getattr(args, "discipline", None))

    if args.json:
        if not args.no_save and result.ok:
            capture.save(result, filename=os.path.basename(args.out) if args.out else None,
                         directory=os.path.dirname(args.out) or None if args.out else None)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1

    if not result.ok:
        print(f"✗ {result.warnings[0] if result.warnings else 'Nada capturado.'}")
        print("  Dicas, nesta ordem:")
        print("   1. deixe a aula visível e em foco (a janela não pode estar minimizada);")
        print("   2. selecione o texto com Ctrl+A, Ctrl+C e use --strategy clipboard;")
        print("   3. se o player não expuser a árvore, use --strategy ocr (precisa de tesseract + pacote 'por');")
        print("   4. rode `zorin-copilot-cli doctor` para ver se o AT-SPI2 está ativo.")
        return 1

    path = ""
    if not args.no_save:
        target_name = os.path.basename(args.out) if args.out else None
        target_dir = os.path.dirname(args.out) if args.out else None
        path = capture.save(result, filename=target_name, directory=target_dir)

    print(f"✓ Material capturado — {result.word_count} palavras via {result.strategy}")
    print(f"  Título: {result.title}")
    if result.url:
        print(f"  URL: {result.url}")
    if path:
        print(f"  Salvo em: {path}")
        print("  Próximo: zorin-copilot-cli rag index   (para poder perguntar sobre o material)")
    for warning in result.warnings:
        print(f"  ⚠️  {warning}")
    if args.show:
        print("\n--- Conteúdo ---\n")
        print(result.text[:5000])
        if len(result.text) > 5000:
            print("\n[... use --out para salvar o conteúdo completo ...]")
    return 0


def _study_deck(args: argparse.Namespace) -> int:
    """Gera flashcards a partir de um material capturado e salva o baralho."""
    from .core.study_cards import (
        DeckStore,
        StudyAI,
        generate_cards,
        merge_cards,
        new_deck,
        read_discipline,
        read_source,
    )

    source = os.path.expanduser(args.source)
    if not os.path.exists(source):
        print(f"✗ Arquivo não encontrado: {source}")
        print("  Dica: capture antes com `zorin-copilot-cli study capture`.")
        return 1

    # A disciplina foi gravada na captura: o baralho herda dela em vez de chutar.
    discipline = (args.discipline or "").strip() or read_discipline(source) or "Gestão Comercial"
    title, body = read_source(source)
    mode = "local" if args.local_only else ("cloud" if args.cloud else "auto")
    ai = StudyAI(mode=mode)
    cards, warnings = generate_cards(
        body,
        ai,
        title=title or os.path.basename(source),
        discipline=discipline,
        n_cards=args.max_cards,
    )

    if not cards:
        print("✗ Nenhum card gerado.")
        for warning in warnings:
            print(f"  ⚠️  {warning}")
        return 1

    store = DeckStore()
    deck = new_deck(
        title or os.path.basename(source),
        cards,
        source=source,
        discipline=discipline,
        provider=ai.used,
    )
    existing = store.load(deck.id)
    if existing is not None and existing.cards:
        deck.cards = merge_cards(existing.cards, cards)  # preserva histórico de revisão
        deck.created_at = existing.created_at
    path = store.save(deck)

    if args.json:
        print(json.dumps(deck.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"✓ Baralho '{deck.title}' — {len(deck.cards)} cards (modelo: {ai.used})")
    if existing is not None and existing.cards:
        print(f"  Histórico de revisão preservado: {len(existing.cards)} cards já existiam.")
    print(f"  Salvo em: {path}")
    print(f"  Próximo: zorin-copilot-cli study review --deck {deck.id}")
    for warning in warnings:
        print(f"  ⚠️  {warning}")
    return 0


def _study_summarize(args: argparse.Namespace) -> int:
    """Resumo em tópicos + glossário a partir do material capturado."""
    from .core.study_cards import read_discipline, read_source
    from .core.study_summary import build_default_ai, generate_summary, render_markdown
    from .core.study_summary import save as save_summary

    source = os.path.expanduser(args.source)
    if not os.path.exists(source):
        print(f"✗ Arquivo não encontrado: {source}")
        print("  Dica: capture antes com `zorin-copilot-cli study capture`.")
        return 1

    discipline = (args.discipline or "").strip() or read_discipline(source) or "Gestão Comercial"
    title, body = read_source(source)
    mode = "local" if args.local_only else ("cloud" if args.cloud else "auto")
    ai = build_default_ai(mode)

    summary, warnings = generate_summary(
        body,
        ai,
        title=title or os.path.basename(source),
        discipline=discipline,
        max_topics=args.max_topics,
        max_terms=args.max_terms,
    )
    summary.source = source

    if not summary.topics and not summary.glossary:
        print("✗ Nenhum conteúdo aproveitável para resumir.")
        for warning in warnings:
            print(f"  ⚠️  {warning}")
        return 1

    path = ""
    if not args.no_save:
        target = args.out or os.path.join(
            os.path.dirname(source), f"resumo-{os.path.basename(source)}"
        )
        path = save_summary(summary, target)

    if args.json:
        payload = summary.to_dict()
        payload["path"] = path
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"✓ Resumo de '{summary.title}' — {len(summary.topics)} tópicos, "
          f"{len(summary.glossary)} termos (modelo: {summary.provider or ai.used})")
    print()
    print(render_markdown(summary))
    if path:
        print(f"Salvo em: {path}")
    for warning in warnings:
        print(f"  ⚠️  {warning}")
    return 0


def _study_decks(args: argparse.Namespace) -> int:
    """Lista baralhos com o resumo de revisão."""
    from .core.study_cards import DeckStore, deck_stats

    decks = DeckStore().list()
    filtro = (getattr(args, "discipline", "") or "").strip().lower()
    if filtro:
        decks = [d for d in decks if (d.discipline or "").strip().lower() == filtro]
    if not decks:
        if filtro:
            print(f"Nenhum baralho de '{args.discipline}'.")
        else:
            print("Nenhum baralho ainda. Gere um com `study deck --from <material.md>`.")
        return 0

    print(f"{'ID':<12} {'VENCIDOS':>8} {'TOTAL':>6}  {'DISCIPLINA':<24} TÍTULO")
    for deck in decks:
        stats = deck_stats(deck)
        print(
            f"{deck.id:<12} {stats['due']:>8} {stats['total']:>6}  "
            f"{(deck.discipline or '-')[:24]:<24} {deck.title}"
        )
    total_due = sum(deck_stats(d)["due"] for d in decks)
    print(f"\nTotal vencido agora: {total_due}")
    return 0


def _study_review(args: argparse.Namespace) -> int:
    """Sessão interativa de revisão espaçada (SM-2)."""
    from .core.study_cards import GRADE_SCALE, DeckStore, deck_stats, due_cards

    store = DeckStore()
    deck = store.load(args.deck) if args.deck else None
    if deck is None:
        decks = store.list()
        if not decks:
            print("Nenhum baralho. Gere um com `study deck --from <material.md>`.")
            return 1
        deck = max(decks, key=lambda d: deck_stats(d)["due"])

    queue = list(deck.cards) if args.all else due_cards(deck, limit=args.limit)
    queue = queue[: args.limit] if not args.all else queue
    if not queue:
        print("Nada vencido agora. Use --all para revisar mesmo assim.")
        return 0

    print(f"Revisando '{deck.title}' — {len(queue)} card(s). Ctrl+C encerra e salva.\n")
    reviewed = 0
    try:
        for index, card in enumerate(queue, 1):
            print(f"[{index}/{len(queue)}] {card.front}")
            try:
                input("   ⏎ para ver a resposta")
            except EOFError:
                print()
                break
            print(f"   → {card.back}")
            grade = _ask_grade()
            if grade is None:
                break
            card.apply_grade(grade)
            store.save(deck)  # progresso salvo a cada card
            reviewed += 1
            print(f"   Próxima revisão em {card.interval_days} dia(s).\n")
    except KeyboardInterrupt:
        print("\n⏹ Encerrado.")

    store.save(deck)
    stats = deck_stats(deck)
    print(f"Revisados: {reviewed} | Vencidos restantes: {stats['due']} | Total: {stats['total']}")
    return 0


def _ask_grade() -> int | None:
    """Pede a nota SM-2 (0–5). Enter vazio encerra a sessão."""
    from .core.study_cards import GRADE_SCALE

    scale = " | ".join(f"{n}={desc}" for n, desc in GRADE_SCALE)
    try:
        answer = input(f"   Nota [{scale}]: ").strip()
    except EOFError:
        return None
    if not answer:
        return None
    try:
        grade = int(answer)
    except ValueError:
        print("   Nota inválida, use 0 a 5. Encerrando.")
        return None
    if not 0 <= grade <= 5:
        print("   Fora da escala (0–5). Encerrando.")
        return None
    return grade


def _study_abnt(args: argparse.Namespace) -> int:
    """Gera o .docx ABNT a partir de um material em Markdown."""
    from .core.document_generators import MissingOfficeDependencyError, generate_abnt_docx

    source = os.path.expanduser(args.source)
    if not os.path.exists(source):
        print(f"✗ Arquivo não encontrado: {source}")
        return 1

    with open(source, encoding="utf-8", errors="replace") as handle:
        markdown = handle.read()

    out = os.path.expanduser(args.out) if args.out else os.path.splitext(source)[0] + ".docx"
    try:
        generate_abnt_docx(out, markdown)
    except MissingOfficeDependencyError as exc:
        print(f"✗ Falta dependência para gerar .docx: {exc}")
        print("  Instale com: pip install python-docx")
        return 1
    except Exception as exc:
        print(f"✗ Falha ao gerar o documento: {exc}")
        return 1

    print(f"✓ Documento ABNT gerado: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "doctor": cmd_doctor,
        "inspect": cmd_inspect,
        "action": cmd_action,
        "ask": cmd_ask,
        "config": cmd_config,
        "memory": cmd_memory,
        "search": cmd_search,
        "rag": cmd_rag,
        "setup": cmd_setup,
        "web": cmd_web,
        "agent": cmd_agent,
        "study": cmd_study,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
