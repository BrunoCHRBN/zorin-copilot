# Decisão de design: motor híbrido em camadas — comandos de sistema e lançamento de apps resolvem instantaneamente (0ms); perguntas e comandos livres utilizam provedores de LLM configurados (Gemini, Ollama, OpenAI) com fallback guiado para serviços comuns da web.

"""Motor de interpretação semântica de intenções para o Zorin Copilot."""

from __future__ import annotations

import logging
import re
from typing import Any

from .actions import ActionPlan, ActionType, DesktopAction
from .providers import BaseLLMProvider, get_llm_provider
from ..core.a11y import DesktopInspector
from ..core.apps import AppManager
from ..core.config import CopilotConfig

logger = logging.getLogger(__name__)


class IntentEngine:
    """Interpreta solicitações do usuário e gera planos de ação para o desktop."""

    def __init__(self, inspector: DesktopInspector | None = None, config: CopilotConfig | None = None):
        self.inspector = inspector or DesktopInspector()
        self.config = config or CopilotConfig.load()
        self.llm_provider: BaseLLMProvider = get_llm_provider(self.config)

    def reload_config(self, config: CopilotConfig | None = None) -> None:
        """Recarrega a configuração e reinicializa o provedor de LLM."""
        self.config = config or CopilotConfig.load()
        self.llm_provider = get_llm_provider(self.config)

    def parse(self, prompt: str) -> ActionPlan:
        prompt_clean = prompt.strip()
        low = prompt_clean.lower()

        # =========================================================================
        # 1. CAMADA RÁPIDA LOCAL: Configurações do Sistema Operacional (0ms)
        # =========================================================================

        # Esquema de Cores (Modo Escuro / Claro)
        if any(w in low for w in ["modo escuro", "tema escuro", "dark mode", "tema dark"]):
            return ActionPlan(
                thought="Ativação do modo escuro do sistema",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "modo_escuro",
                        {"setting": "dark_mode", "value": True},
                        description="Ativar Modo Escuro no Zorin OS",
                    )
                ],
            )
        if any(w in low for w in ["modo claro", "tema claro", "light mode", "tema light"]):
            return ActionPlan(
                thought="Ativação do modo claro do sistema",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "modo_claro",
                        {"setting": "dark_mode", "value": False},
                        description="Ativar Modo Claro no Zorin OS",
                    )
                ],
            )

        # Luz Noturna
        if any(w in low for w in ["luz noturna", "filtro azul", "night light"]):
            return ActionPlan(
                thought="Alternar estado da luz noturna",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "luz_noturna",
                        {"setting": "night_light"},
                        description="Alternar Luz Noturna",
                    )
                ],
            )

        # Volume e Áudio
        if any(w in low for w in ["aumentar volume", "aumenta o volume", "mais volume", "subir o som"]):
            return ActionPlan(
                thought="Aumentar o volume do áudio",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "volume_up",
                        {"setting": "volume", "change": "up"},
                        description="Aumentar volume em 5%",
                    )
                ],
            )
        if any(w in low for w in ["diminuir volume", "diminui o volume", "menos volume", "abaixar o som"]):
            return ActionPlan(
                thought="Reduzir o volume do áudio",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "volume_down",
                        {"setting": "volume", "change": "down"},
                        description="Diminuir volume em 5%",
                    )
                ],
            )
        if any(w in low for w in ["mutar", "mudo", "silenciar", "desmutar"]):
            return ActionPlan(
                thought="Mutar ou desmutar o áudio",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "volume_mute",
                        {"setting": "volume", "change": "mute"},
                        description="Mutar/Desmutar áudio do sistema",
                    )
                ],
            )

        # Bloqueio e Captura de Tela
        if any(w in low for w in ["bloquear tela", "bloquear computador", "bloqueie"]):
            return ActionPlan(
                thought="Bloquear a sessão do usuário",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "lock",
                        {"setting": "lock"},
                        description="Bloquear tela do computador",
                    )
                ],
            )
        if any(w in low for w in ["tirar print", "print da tela", "screenshot", "capturar tela"]):
            return ActionPlan(
                thought="Abrir ferramenta de captura de tela",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "screenshot",
                        {"setting": "screenshot"},
                        description="Capturar tela do desktop",
                    )
                ],
            )

        # Notificações explícitas
        if low.startswith(("notificar ", "lembrete ", "aviso ")):
            msg = re.sub(r"^(notificar|lembrete|aviso)\s+", "", prompt_clean, flags=re.I)
            return ActionPlan(
                thought="Criar notificação no desktop",
                actions=[
                    DesktopAction(
                        ActionType.NOTIFY,
                        msg,
                        {"message": msg},
                        description=f"Exibir notificação: '{msg}'",
                    )
                ],
            )

        # Interação com Elementos da Janela Ativa (AT-SPI2)
        if low.startswith(("clicar em ", "clique em ", "aperte ", "pressione ")):
            target = re.sub(r"^(clicar em|clique em|aperte|pressione)\s+", "", prompt_clean, flags=re.I).strip("'\"")
            return ActionPlan(
                thought=f"Localizar e clicar no elemento '{target}' na tela",
                actions=[
                    DesktopAction(
                        ActionType.CLICK,
                        target,
                        description=f"Clicar no botão ou elemento '{target}'",
                    )
                ],
            )

        # Comandos diretos de abertura de apps (ex: "abrir steam", "abrir calculadora", etc.)
        # Somente se não for uma pergunta explicativa ("como acessar", "me explique", "o que é", etc.)
        is_question = any(q in low for q in ["me explique", "como", "o que", "onde", "por que", "qual", "quem"])
        if not is_question:
            app, app_name = AppManager.find_app(prompt_clean)
            if app:
                return ActionPlan(
                    thought=f"Localizado aplicativo correspondente: {app_name}",
                    actions=[
                        DesktopAction(
                            ActionType.LAUNCH_APP,
                            app_name,
                            {"app_id": app.get_id(), "executable": app.get_executable()},
                            description=f"Abrir o aplicativo '{app_name}'",
                        )
                    ],
                )

        # =========================================================================
        # 2. CAMADA DE INTELIGÊNCIA ARTIFICIAL (LLM: Gemini / Ollama / OpenAI)
        # =========================================================================
        if self.llm_provider.is_configured():
            try:
                # Obtém nomes de alguns apps instalados para dar contexto ao LLM
                app_names = [a.get_name() for a in AppManager.get_all_apps() if a.get_name()]
                explanation, actions = self.llm_provider.chat(prompt_clean, app_list=app_names)

                if not actions:
                    actions = [
                        DesktopAction(
                            ActionType.ANSWER,
                            explanation,
                            description="Resposta do Zorin Copilot",
                        )
                    ]

                return ActionPlan(
                    thought=explanation,
                    actions=actions,
                    raw_response=explanation,
                )
            except Exception as exc:
                logger.error(f"Erro na consulta ao provedor LLM: {exc}")
                return ActionPlan(
                    thought=f"Ocorreu um erro ao comunicar com a IA ({self.config.provider}):\n\n{exc}",
                    actions=[
                        DesktopAction(
                            ActionType.ANSWER,
                            str(exc),
                            description="Erro na consulta de IA",
                        )
                    ],
                )

        # =========================================================================
        # 3. CAMADA DE FALLBACK INTELIGENTE (Sem Chave de IA configurada)
        # =========================================================================

        # Atalhos comuns de serviços da web quando o usuário pergunta sobre eles
        web_shortcuts = [
            (r"\bgmail\b", "Gmail", "https://mail.google.com", "Para acessar o Gmail no Zorin OS, você pode abrir o navegador web e acessar o site mail.google.com, ou usar um cliente de e-mail como Thunderbird ou Geary."),
            (r"\byoutube\b", "YouTube", "https://youtube.com", "O YouTube pode ser acessado pelo navegador web."),
            (r"\bwhatsapp\b", "WhatsApp Web", "https://web.whatsapp.com", "Você pode acessar o WhatsApp através do WhatsApp Web no navegador."),
            (r"\bgithub\b", "GitHub", "https://github.com", "Você pode acessar o GitHub diretamente pelo navegador."),
        ]

        for pattern, name, url, expl in web_shortcuts:
            if re.search(pattern, low):
                return ActionPlan(
                    thought=f"{expl}\n\n💡 Dica: Conecte o Google Gemini nas Configurações (⚙️) para respostas conversacionais completas.",
                    actions=[
                        DesktopAction(
                            ActionType.OPEN_URL,
                            url,
                            description=f"Abrir o {name} no navegador ({url})",
                        )
                    ],
                )

        # Se mesmo com a checagem de apps anterior não bateu, tenta busca de app
        app, app_name = AppManager.find_app(prompt_clean)
        if app:
            return ActionPlan(
                thought=f"Localizado aplicativo correspondente: {app_name}",
                actions=[
                    DesktopAction(
                        ActionType.LAUNCH_APP,
                        app_name,
                        {"app_id": app.get_id(), "executable": app.get_executable()},
                        description=f"Abrir o aplicativo '{app_name}'",
                    )
                ],
            )

        # Resposta orientativa padrão
        return ActionPlan(
            thought=(
                f"Para responder perguntas livres como '{prompt_clean}', configure um provedor de Inteligência Artificial:\n\n"
                "• Google Gemini: Gratuito e rápido (basta inserir sua chave do Google AI Studio)\n"
                "• Ollama: 100% local e offline (ex: llama3.2)\n\n"
                "Clique no ícone de engrenagem ⚙️ no canto superior para configurar."
            ),
            actions=[
                DesktopAction(
                    ActionType.ANSWER,
                    "Configure o Gemini ou Ollama no ícone de configurações ⚙️ para habilitar o raciocínio de IA.",
                    description="Provedor de IA não configurado",
                )
            ],
        )
