# Decisão de design: motor híbrido — comandos locais rápidos resolvem instantaneamente com zero latência, e consultas complexas podem usar LLM (Gemini ou Ollama).

"""Motor de interpretação semântica de intenções para o Zorin Copilot."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any

from .actions import ActionPlan, ActionType, DesktopAction
from ..core.apps import AppManager
from ..core.a11y import DesktopInspector


class IntentEngine:
    """Interpreta solicitações do usuário e gera planos de ação para o desktop."""

    def __init__(self, inspector: DesktopInspector | None = None):
        self.inspector = inspector or DesktopInspector()
        self.config_path = os.path.expanduser("~/.config/zorin-copilot/config.json")

    def parse(self, prompt: str) -> ActionPlan:
        prompt_clean = prompt.strip()
        low = prompt_clean.lower()

        # 1. Checagem de Sistema: Esquema de Cores (Modo Escuro / Claro)
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

        # 2. Checagem de Sistema: Luz Noturna
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

        # 3. Checagem de Sistema: Volume e Áudio
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

        # 4. Checagem de Sistema: Bloqueio e Captura de Tela
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

        # 5. Busca e Inicialização de Qualquer Aplicativo Instalado no Zorin OS
        # (ex: 'abrir steam', 'steam', 'iniciar brave', 'abre a calculadora', etc.)
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

        # 6. Interação com Elementos da Janela Ativa (AT-SPI2)
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

        # 7. Notificações explícitas
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

        # 8. Consulta Geral / Pergunta (LLM ou Resposta Guiada)
        llm_answer = self._try_llm_query(prompt_clean)
        if llm_answer:
            return ActionPlan(
                thought="Resposta gerada por IA",
                actions=[
                    DesktopAction(
                        ActionType.ANSWER,
                        llm_answer,
                        {"text": llm_answer},
                        description=llm_answer,
                    )
                ],
            )

        return ActionPlan(
            thought=f"Não encontrei um aplicativo ou ação direta para '{prompt_clean}'",
            actions=[
                DesktopAction(
                    ActionType.ANSWER,
                    f"Posso abrir qualquer app instalado (ex: 'abrir steam', 'abrir terminal'), controlar o sistema (ex: 'modo escuro', 'aumentar volume', 'luz noturna') ou interagir com botões (ex: 'clicar em Salvar').",
                    description="Ajuda de comandos disponíveis",
                )
            ],
        )

    def _try_llm_query(self, prompt: str) -> str | None:
        """Tenta consultar Ollama local caso esteja ativo."""
        try:
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=json.dumps({"model": "llama3", "prompt": prompt, "stream": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload.get("response")
        except Exception:
            return None
