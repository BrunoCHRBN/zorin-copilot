# Decisão de design: motor híbrido em camadas — comandos de sistema e lançamento de apps resolvem instantaneamente (0ms); perguntas e comandos livres utilizam provedores de LLM configurados (Gemini, Ollama, OpenAI) com fallback guiado para serviços comuns da web.

"""Motor de interpretação semântica de intenções para o Zorin Copilot."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any

from .actions import ActionPlan, ActionType, DesktopAction
from .providers import BaseLLMProvider, get_llm_provider
from ..core.a11y import DesktopInspector
from ..core.apps import AppManager
from ..core.clipboard import ClipboardService
from ..core.config import CopilotConfig
from ..core.memory import MemoryManager
from ..core.web_search import WebSearchClient

logger = logging.getLogger(__name__)


class IntentEngine:
    """Interpreta solicitações do usuário e gera planos de ação para o desktop."""

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        config: CopilotConfig | None = None,
        memory: MemoryManager | None = None,
        search_client: WebSearchClient | None = None,
    ):
        self.inspector = inspector or DesktopInspector()
        self.config = config or CopilotConfig.load()
        self.memory = memory or MemoryManager()
        self.search_client = search_client or WebSearchClient()
        self.llm_provider: BaseLLMProvider = get_llm_provider(self.config)

    def reload_config(self, config: CopilotConfig | None = None) -> None:
        """Recarrega a configuração e reinicializa o provedor de LLM."""
        self.config = config or CopilotConfig.load()
        self.llm_provider = get_llm_provider(self.config)

    def parse(
        self,
        prompt: str,
        history: list[dict[str, str]] | None = None,
        image_bytes: bytes | None = None,
        is_area_capture: bool = False,
    ) -> ActionPlan:
        prompt_clean = prompt.strip()
        low = prompt_clean.lower()

        # =========================================================================
        # -1. MODO VISUAL MULTIMODAL (Análise de tela ou recorte de área)
        # =========================================================================
        if image_bytes:
            if not prompt_clean or any(
                low == w
                for w in [
                    "analise", "leia", "veja", "o que tem na tela", "analisar tela",
                    "analise minha tela", "analise a tela", "print", "screenshot",
                    "analise este recorte", "analise o recorte", "recorte",
                ]
            ):
                if is_area_capture:
                    prompt_clean = (
                        "Analise detalhadamente o conteúdo deste recorte de tela selecionado no Zorin OS. "
                        "Identifique e leia qualquer texto, código, diálogo, mensagem de erro ou dados visíveis. "
                        "Explique claramente o que significa e indique a solução ou próximos passos recomendados."
                    )
                else:
                    prompt_clean = (
                        "Analise esta captura de tela completa do desktop no Zorin OS. "
                        "Identifique as janelas ativas, mensagens de erro ou informações visíveis e explique o que está acontecendo e como proceder."
                    )

            if self.llm_provider.is_configured():
                try:
                    app_names = [a.get_name() for a in AppManager.get_all_apps() if a.get_name()]
                    context_summary = self.memory.get_context_summary()
                    explanation, actions = self.llm_provider.chat(
                        prompt_clean,
                        app_list=app_names,
                        context_summary=context_summary,
                        history=history,
                        image_bytes=image_bytes,
                    )
                    if not actions:
                        actions = [
                            DesktopAction(
                                ActionType.ANSWER,
                                explanation,
                                description="Análise visual do Zorin Copilot",
                            )
                        ]
                    ocr_act = next((a for a in actions if a.action_type == ActionType.SMART_OCR), None)
                    ext_text = ocr_act.target if ocr_act else None
                    ext_kind = ocr_act.params.get("kind", "texto") if ocr_act else "text"
                    return ActionPlan(
                        thought=explanation,
                        actions=actions,
                        raw_response=explanation,
                        extracted_text=ext_text,
                        extracted_kind=ext_kind,
                    )
                except Exception as exc:
                    logger.error(f"Erro na análise visual da imagem: {exc}")
                    return ActionPlan(
                        thought=f"Ocorreu um erro ao processar a imagem com a IA ({self.config.provider}):\n\n{exc}",
                        actions=[
                            DesktopAction(
                                ActionType.ANSWER,
                                f"Falha na visão: {exc}",
                                description="Erro de comunicação com o modelo",
                            )
                        ],
                    )
            else:
                return ActionPlan(
                    thought=(
                        "A captura da tela foi realizada com sucesso! Porém, para fazer a leitura inteligente "
                        "do conteúdo da imagem, é necessário conectar sua chave do Google Gemini nas Preferências (⚙️)."
                    ),
                    actions=[
                        DesktopAction(
                            ActionType.ANSWER,
                            "IA não configurada para visão computacional.",
                            description="Configurar chave de API para análise visual",
                        )
                    ],
                )

        # =========================================================================
        # 0. BASE DE CONHECIMENTO: Memorização e Aprendizado Explícito
        # =========================================================================
        learn_match = re.match(
            r"^(?:lembre-se que|lembre que|guarde que|salve que|anote que|grave que)\s+(.+)$",
            prompt_clean,
            flags=re.I,
        )
        if learn_match:
            fact_content = learn_match.group(1).strip()
            fact_key = f"fato_{int(time.time())}"
            self.memory.save_fact(fact_key, fact_content, category="usuario", source="usuario")
            return ActionPlan(
                thought=(
                    f"Entendido! Guardei na minha base de conhecimento:\n\n"
                    f"• \"{fact_content}\"\n\n"
                    "Eu levarei essa informação em conta em todas as próximas respostas e ações."
                ),
                actions=[
                    DesktopAction(
                        ActionType.ANSWER,
                        f"Conhecimento memorizado: '{fact_content}'",
                        description="Salvo na base de conhecimento permanente",
                    )
                ],
            )

        # =========================================================================
        # 0.5 ÁREA DE TRANSFERÊNCIA: Clipboard Inteligente (Texto, Código e Imagem)
        # =========================================================================
        is_clipboard_intent = (
            any(w in low for w in ["copiad", "copiei", "acabei de copiar", "clipboard", "área de transferência", "area de transferencia"])
            or low in [
                "traduza o texto selecionado para o inglês",
                "traduza o texto selecionado para o ingles",
                "traduza o texto selecionado",
                "traduzir o texto selecionado",
                "traduza a seleção",
                "traduza a selecao",
                "corrija a gramática e formalize este e-mail",
                "corrija a gramatica e formalize este e-mail",
                "formalize este e-mail",
                "formalize este email",
                "formalize este texto",
                "corrija este e-mail",
                "corrija este email",
                "analisar_copiado",
                "analisar copiado",
            ]
            or (
                any(q in low for q in ["traduza", "traduzir", "corrija", "corrigir", "formalize", "resuma", "resumir", "explique"])
                and any(t in low for t in ["selecionado", "seleção", "selecao", "este texto", "este e-mail", "este email", "este código", "este codigo"])
                and len(prompt_clean.split()) <= 12
            )
        )

        if is_clipboard_intent and not image_bytes:
            content_type, clip_content = ClipboardService.get_content()

            if content_type == "empty" or not clip_content:
                thought = (
                    "A área de transferência está vazia no momento.\n\n"
                    "**Como usar o Clipboard Inteligente:**\n"
                    "1. Selecione o código, texto, e-mail ou imagem em qualquer aplicativo;\n"
                    "2. Pressione **Ctrl+C** para copiar;\n"
                    "3. Volte aqui e clique em **📋 Analisar Copiado** ou peça para explicar, traduzir ou formalizar!"
                )
                return ActionPlan(
                    thought=thought,
                    actions=[
                        DesktopAction(
                            ActionType.ANSWER,
                            "Área de transferência vazia. Copie algum conteúdo (Ctrl+C) e tente novamente.",
                            description="Aguardando conteúdo copiado na área de transferência",
                        )
                    ],
                    raw_response=thought,
                )

            if content_type == "image" and isinstance(clip_content, bytes):
                # Processa imagem da área de transferência via visão computacional multimodal
                if self.llm_provider.is_configured():
                    try:
                        app_names = [a.get_name() for a in AppManager.get_all_apps() if a.get_name()]
                        context_summary = self.memory.get_context_summary()
                        vision_prompt = (
                            "Analise detalhadamente esta imagem copiada da área de transferência no Zorin OS. "
                            "Identifique qualquer texto, código, diálogo, diagrama ou mensagem visível e explique seu conteúdo com precisão."
                        )
                        explanation, actions = self.llm_provider.chat(
                            vision_prompt,
                            app_list=app_names,
                            context_summary=context_summary,
                            history=history,
                            image_bytes=clip_content,
                        )
                        if not actions:
                            actions = [
                                DesktopAction(
                                    ActionType.ANSWER,
                                    explanation,
                                    description="Análise da imagem copiada na área de transferência",
                                )
                            ]
                        ocr_act = next((a for a in actions if a.action_type == ActionType.SMART_OCR), None)
                        ext_text = ocr_act.target if ocr_act else None
                        ext_kind = ocr_act.params.get("kind", "texto") if ocr_act else "text"
                        return ActionPlan(
                            thought=explanation,
                            actions=actions,
                            raw_response=explanation,
                            extracted_text=ext_text,
                            extracted_kind=ext_kind,
                        )
                    except Exception as exc:
                        logger.error(f"Erro na análise visual do clipboard: {exc}")
                        return ActionPlan(
                            thought=f"Ocorreu um erro ao processar a imagem do clipboard com a IA ({self.config.provider}):\n\n{exc}",
                            actions=[DesktopAction(ActionType.ANSWER, str(exc), description="Erro de comunicação com a IA")],
                        )
                else:
                    thought = (
                        "Imagem detectada na área de transferência!\n\n"
                        "Para que a IA possa ler e analisar o conteúdo da imagem, "
                        "configure sua chave do Google Gemini nas Preferências (⚙️)."
                    )
                    return ActionPlan(
                        thought=thought,
                        actions=[
                            DesktopAction(
                                ActionType.ANSWER,
                                "Configure o Gemini para leitura visual do clipboard.",
                                description="IA não configurada para visão computacional",
                            )
                        ],
                        raw_response=thought,
                    )

            if content_type == "text" and isinstance(clip_content, str):
                # Formulação do prompt contextual enriquecido
                if any(w in low for w in ["expliq", "o que faz", "codigo", "código", "program", "funcao", "função", "script", "bug"]):
                    enriched_prompt = (
                        "O usuário copiou o seguinte trecho de código para a área de transferência:\n\n"
                        f"```\n{clip_content}\n```\n\n"
                        "Por favor, analise e explique detalhadamente:\n"
                        "1. **Finalidade:** O que este código faz;\n"
                        "2. **Lógica & Componentes:** Como funciona e que módulos ou sintaxes utiliza;\n"
                        "3. **Melhorias & Boas Práticas:** Sugestões de otimização, legibilidade ou potenciais bugs."
                    )
                elif any(w in low for w in ["traduz", "ingles", "inglês", "portugues", "português", "espanhol", "idioma"]):
                    enriched_prompt = (
                        "O usuário copiou o seguinte texto para a área de transferência:\n\n"
                        f"\"{clip_content}\"\n\n"
                        "Por favor, traduza este conteúdo para o inglês (ou para o idioma indicado), priorizando uma linguagem fluente, natural e precisa. Se houver expressões idiomáticas ou termos específicos, aponte breves notas de tradução."
                    )
                elif any(w in low for w in ["corrija", "corrigir", "formaliz", "gramatica", "gramática", "e-mail", "email", "melhor"]):
                    enriched_prompt = (
                        "O usuário copiou o seguinte texto/e-mail para a área de transferência:\n\n"
                        f"\"{clip_content}\"\n\n"
                        "Por favor, faça a revisão deste texto:\n"
                        "1. **Correção Gramatical:** Corrija concordância, pontuação, acentuação e clareza;\n"
                        "2. **Versão Formal:** Reescreva em formato profissional, cortês e polido;\n"
                        "3. **Texto Pronto:** Apresente o texto final pronto para cópia/envio."
                    )
                elif any(w in low for w in ["resum", "sintetiz", "topicos", "tópicos", "principais pontos"]):
                    enriched_prompt = (
                        "O usuário copiou o seguinte texto para a área de transferência:\n\n"
                        f"\"{clip_content}\"\n\n"
                        "Por favor, elabore um resumo conciso e estruturado em tópicos (bullet points), destacando as ideias centrais, conclusões e decisões mais importantes."
                    )
                else:
                    lines = clip_content.strip().splitlines()
                    looks_like_code = (
                        any(k in clip_content for k in ["def ", "import ", "class ", "function", "const ", "var ", "let ", "return ", "SELECT ", "FROM ", "<div>", "public static"])
                        or (len(lines) > 2 and any(l.startswith("    ") or l.startswith("\t") for l in lines))
                    )
                    if looks_like_code:
                        enriched_prompt = (
                            "O usuário copiou o seguinte trecho de código para a área de transferência:\n\n"
                            f"```\n{clip_content}\n```\n\n"
                            "Por favor, analise este código: explique o que ele faz, sua estrutura, lógica e aponte sugestões de otimização ou correções se aplicável."
                        )
                    else:
                        instruction = prompt_clean if low not in ("analisar copiado", "analisar_copiado", "analise o copiado", "analise o que copiei", "clipboard") else "Analise detalhadamente o conteúdo acima, explique seu propósito e forneça pontos de atenção ou ações recomendadas."
                        enriched_prompt = (
                            "O usuário copiou o seguinte conteúdo para a área de transferência:\n\n"
                            f"\"{clip_content}\"\n\n"
                            f"Instrução do usuário: {instruction}"
                        )

                if self.llm_provider.is_configured():
                    try:
                        app_names = [a.get_name() for a in AppManager.get_all_apps() if a.get_name()]
                        context_summary = self.memory.get_context_summary()
                        explanation, actions = self.llm_provider.chat(
                            enriched_prompt,
                            app_list=app_names,
                            context_summary=context_summary,
                            history=history,
                        )
                        if not actions:
                            actions = [
                                DesktopAction(
                                    ActionType.ANSWER,
                                    explanation,
                                    description="Análise do conteúdo da área de transferência",
                                )
                            ]
                        return ActionPlan(thought=explanation, actions=actions, raw_response=explanation)
                    except Exception as exc:
                        logger.error(f"Erro na análise de texto do clipboard: {exc}")
                        return ActionPlan(
                            thought=f"Ocorreu um erro ao comunicar com a IA ({self.config.provider}):\n\n{exc}",
                            actions=[DesktopAction(ActionType.ANSWER, str(exc), description="Erro de comunicação com a IA")],
                        )
                else:
                    preview = ClipboardService.get_preview(60)
                    thought = (
                        f"Conteúdo detectado na área de transferência ({len(clip_content)} caracteres):\n\n"
                        f"> {preview}\n\n"
                        "Para que a IA explique este código, traduza o texto, corrija o e-mail ou gere resumos, "
                        "configure sua chave do Google Gemini ou Ollama nas Preferências (⚙️)."
                    )
                    return ActionPlan(
                        thought=thought,
                        actions=[
                            DesktopAction(
                                ActionType.ANSWER,
                                "Configure um provedor de IA nas Preferências (⚙️) para habilitar a análise do clipboard.",
                                description="IA não configurada para análise de clipboard",
                            )
                        ],
                        raw_response=thought,
                    )

        # =========================================================================
        # 1. CAMADA RÁPIDA LOCAL: Relógio do Sistema e Configurações (0ms)
        # =========================================================================

        # Relógio, Data e Calendário Local Instantâneo (0ms)
        dias_sem = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
        meses_pt = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
        now = datetime.now()

        # Amanhã
        if any(q in low for q in ["que dia sera amanha", "que dia será amanhã", "data de amanha", "data de amanhã", "dia de amanha", "dia de amanhã"]):
            tom = now + timedelta(days=1)
            dia_str = dias_sem[tom.weekday()]
            mes_str = meses_pt[tom.month - 1]
            resp = f"Amanhã será **{dia_str}**, {tom.day:02d} de {mes_str} de {tom.year}."
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description="Data e dia da semana de amanhã")],
            )

        # Hoje
        if any(q in low for q in ["que dia é hoje", "que dia e hoje", "data de hoje", "qual a data de hoje", "em que dia estamos", "que dia da semana é hoje", "que dia da semana e hoje"]):
            dia_str = dias_sem[now.weekday()]
            mes_str = meses_pt[now.month - 1]
            resp = f"Hoje é **{dia_str}**, {now.day:02d} de {mes_str} de {now.year}."
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description="Data e dia da semana atual")],
            )

        # Horário
        if any(q in low for q in ["que horas são", "que horas sao", "qual a hora atual", "horario atual", "horário atual"]):
            resp = f"Agora são exatamente **{now.strftime('%H:%M')}**."
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description="Hora atual do sistema")],
            )

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
        # Análise Visual da Tela
        if any(w in low for w in ["analise minha tela", "analise a tela", "o que tem na minha tela", "o que está na minha tela", "leia minha tela", "ler minha tela", "analise esse erro", "analise o erro", "analisar tela", "ler tela"]):
            return ActionPlan(
                thought=(
                    "Para analisar a tela com precisão, você pode usar os botões de visão computacional:\n\n"
                    "• **✂️ Recortar Área da Tela:** Selecione com o mouse exatamente a área ou erro que deseja ler.\n"
                    "• **🖥️ Capturar Tela Inteira:** Lê toda a área de trabalho de uma vez só.\n\n"
                    "Ao selecionar, o Copilot faz a leitura e o diagnóstico automaticamente!"
                ),
                actions=[
                    DesktopAction(
                        ActionType.CAPTURE_SCREEN,
                        "area",
                        description="Recortar área da tela para leitura imediata",
                    ),
                    DesktopAction(
                        ActionType.CAPTURE_SCREEN,
                        "fullscreen",
                        description="Capturar tela inteira para análise",
                    ),
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
                context_parts = [self.memory.get_context_summary()]

                search_results = []
                if self.config.web_search_enabled and self.search_client.is_search_needed(prompt_clean):
                    clean_q = self.search_client.clean_search_query(prompt_clean)
                    search_results = self.search_client.search(clean_q, max_results=3)
                    if search_results:
                        context_parts.append(self.search_client.format_results_for_prompt(search_results))

                context_summary = "\n\n".join(p for p in context_parts if p)

                explanation, actions = self.llm_provider.chat(
                    prompt_clean,
                    app_list=app_names,
                    context_summary=context_summary,
                    history=history,
                )

                # Se houve busca na web e a IA não gerou ação de link, oferece a fonte primária
                if search_results and not any(a.action_type == ActionType.OPEN_URL for a in actions):
                    primary = search_results[0]
                    actions.append(
                        DesktopAction(
                            ActionType.OPEN_URL,
                            primary.url,
                            description=f"Abrir fonte: {primary.title[:45]}...",
                        )
                    )

                if not actions:
                    actions = [
                        DesktopAction(
                            ActionType.ANSWER,
                            explanation,
                            description="Resposta do Zorin Copilot",
                        )
                    ]

                ocr_act = next((a for a in actions if a.action_type == ActionType.SMART_OCR), None)
                ext_text = ocr_act.target if ocr_act else None
                ext_kind = ocr_act.params.get("kind", "texto") if ocr_act else "text"
                return ActionPlan(
                    thought=explanation,
                    actions=actions,
                    raw_response=explanation,
                    extracted_text=ext_text,
                    extracted_kind=ext_kind,
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
