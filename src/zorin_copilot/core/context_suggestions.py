# Decisão de design: Motor de geração de sugestões contextuais proativas.
# Transforma o snapshot de contexto em ações imediatas de 1-clique adaptadas ao trabalho
# atual do usuário, mantendo opções de alto valor global quando o contexto for genérico.

"""Motor de sugestões contextuais e inteligentes do desktop."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import List

from .desktop_context import AppCategory, ClipboardCategory, DesktopContext

logger = logging.getLogger(__name__)


@dataclass
class ContextSuggestion:
    """Representa uma sugestão acionável renderizada como chip na interface."""
    id: str
    icon: str
    title: str
    description: str
    prompt_template: str
    action_type: str = "prompt"  # "prompt", "voz_ao_vivo", "recortar_area", "analisar_copiado"
    priority: int = 50
    is_contextual: bool = True


class ContextSuggestionEngine:
    """Gera sugestões de prompts e ações rápidas sob medida para a situação atual."""

    @classmethod
    def get_suggestions(
        cls,
        context: DesktopContext | None,
        limit: int = 4,
    ) -> list[ContextSuggestion]:
        """
        Calcula as melhores sugestões para o contexto atual, respeitando o limite numérico.

        Args:
            context: Snapshot do estado atual do desktop e clipboard.
            limit: Número máximo de sugestões a retornar (padrão 4).

        Returns:
            Lista ordenada de ContextSuggestion.
        """
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = 4

        suggestions: list[ContextSuggestion] = []

        if context:
            # 1. Prioridade máxima: Erro na área de transferência
            if context.clipboard_category == ClipboardCategory.ERROR_TRACEBACK:
                clip_snip = context.clipboard_text[:1200]
                suggestions.append(
                    ContextSuggestion(
                        id="diagnose_error",
                        icon="dialog-error-symbolic",
                        title="Diagnosticar erro copiado",
                        description="Explicar causa raiz e passos para resolver o erro",
                        prompt_template=(
                            "Analise este erro do terminal e explique a causa raiz e a solução passo a passo:\n\n"
                            f"```\n{clip_snip}\n```"
                        ),
                        priority=10,
                    )
                )

            # 2. Link na área de transferência
            elif context.clipboard_category == ClipboardCategory.URL:
                url = context.clipboard_text.strip()
                suggestions.append(
                    ContextSuggestion(
                        id="analyze_url",
                        icon="web-browser-symbolic",
                        title="Analisar link copiado",
                        description=f"Examinar conteúdo de {url[:40]}...",
                        prompt_template=f"Acesse e resuma as informações principais deste link: {url}",
                        priority=15,
                    )
                )

            # 2.5 Sugestões de Repositório Git
            if context.git_repo and context.git_has_diff:
                suggestions.append(
                    ContextSuggestion(
                        id="git_diff_commit",
                        icon="document-edit-symbolic",
                        title="Sugerir commit das alterações",
                        description="Gerar mensagem convencional para git diff",
                        prompt_template="Analise as alterações não commitadas no repositório git (git diff) e sugira uma mensagem de commit clara no padrão Conventional Commits com explicação concisa.",
                        priority=12,
                    )
                )

            # 3. Código na área de transferência (se não estiver em editor)
            if (
                context.clipboard_category == ClipboardCategory.CODE_SNIPPET
                and context.category != AppCategory.CODE_EDITOR
            ):
                code_snip = context.clipboard_text[:800]
                suggestions.append(
                    ContextSuggestion(
                        id="explain_copied_code",
                        icon="emblem-documents-symbolic",
                        title="Explicar código copiado",
                        description="Explicar lógica do trecho copiado",
                        prompt_template=f"Explique o que este código faz e sugira possíveis melhorias:\n\n```\n{code_snip}\n```",
                        priority=20,
                    )
                )

            # 4. Contexto por Categoria de Aplicativo
            target = context.extracted_target.strip()
            title = context.window_title.strip()

            if context.category == AppCategory.CODE_EDITOR:
                if target:
                    suggestions.append(
                        ContextSuggestion(
                            id="explain_code_file",
                            icon="system-search-symbolic",
                            title=f"Explicar {target}",
                            description=f"Analisar a arquitetura e fluxo de {target}",
                            prompt_template=f"Analise o arquivo '{target}' aberto no editor e explique sua estrutura, funções e responsabilidades.",
                            priority=25,
                        )
                    )
                    suggestions.append(
                        ContextSuggestion(
                            id="generate_unit_tests",
                            icon="emblem-documents-symbolic",
                            title=f"Gerar testes para {target}",
                            description=f"Criar suíte de testes automatizados para {target}",
                            prompt_template=f"Crie uma suíte completa de testes unitários para o arquivo '{target}'.",
                            priority=30,
                        )
                    )
                    suggestions.append(
                        ContextSuggestion(
                            id="refactor_code",
                            icon="document-edit-symbolic",
                            title="Revisar e refatorar",
                            description="Sugerir melhorias de performance e boas práticas",
                            prompt_template=f"Revise o código do arquivo '{target}' em foco, sugerindo otimizações e conformidade com boas práticas.",
                            priority=35,
                        )
                    )
                else:
                    suggestions.append(
                        ContextSuggestion(
                            id="explain_editor",
                            icon="system-search-symbolic",
                            title="Explicar código em foco",
                            description="Analisar projeto atualmente aberto no editor",
                            prompt_template="Analise o código atualmente aberto no meu editor e explique sua arquitetura.",
                            priority=25,
                        )
                    )

            elif context.category == AppCategory.BROWSER:
                page_label = target if target else "esta página"
                suggestions.append(
                    ContextSuggestion(
                        id="summarize_page",
                        icon="format-justify-left-symbolic",
                        title="Resumir esta página",
                        description=f"Extrair conceitos principais de '{page_label}'",
                        prompt_template=f"Resuma os pontos mais importantes da página web '{page_label}' que estou navegando.",
                        priority=25,
                    )
                )
                suggestions.append(
                    ContextSuggestion(
                        id="research_topic",
                        icon="system-search-symbolic",
                        title="Pesquisar mais sobre o tema",
                        description="Buscar referências e estudos complementares",
                        prompt_template=f"Faça uma pesquisa detalhada na web sobre o assunto abordado na página '{page_label}' e traga referências adicionais.",
                        priority=30,
                    )
                )

            elif context.category == AppCategory.TERMINAL:
                suggestions.append(
                    ContextSuggestion(
                        id="terminal_assistance",
                        icon="utilities-terminal-symbolic",
                        title="Ajuda de terminal / Bash",
                        description="Explicar comandos ou diagnósticos de sistema",
                        prompt_template="Preciso de ajuda com comandos de terminal e administração de sistema no Zorin OS.",
                        priority=25,
                    )
                )

            elif context.category == AppCategory.DOCUMENT:
                doc_name = target if target else "o documento"
                suggestions.append(
                    ContextSuggestion(
                        id="summarize_doc",
                        icon="format-justify-left-symbolic",
                        title="Resumir documento",
                        description=f"Pontos-chave e conclusões de '{doc_name}'",
                        prompt_template=f"Extraia um resumo executivo e os principais tópicos do documento '{doc_name}'.",
                        priority=25,
                    )
                )
                suggestions.append(
                    ContextSuggestion(
                        id="review_writing",
                        icon="accessories-dictionary-symbolic",
                        title="Revisar gramática e estilo",
                        description="Melhorar clareza, tom e concordância",
                        prompt_template=f"Revise a gramática, concordância e coesão textual do documento '{doc_name}'.",
                        priority=30,
                    )
                )

            elif context.category == AppCategory.FILE_MANAGER:
                folder_name = target if target else "esta pasta"
                suggestions.append(
                    ContextSuggestion(
                        id="organize_folder",
                        icon="folder-saved-search-symbolic",
                        title=f"Organizar {folder_name}",
                        description="Sugerir estrutura e agrupamento de arquivos",
                        prompt_template=f"Como posso organizar melhor os arquivos na pasta '{folder_name}'?",
                        priority=25,
                    )
                )

        # 5. Sugestões Globais / Fallbacks para completar até 'limit'
        global_fallbacks = [
            ContextSuggestion(
                id="global_live_voice",
                icon="audio-input-microphone-symbolic",
                title="Voz ao Vivo (Gemini Live)",
                description="Conversar em tempo real por voz e visão",
                prompt_template="voz_ao_vivo",
                action_type="voz_ao_vivo",
                priority=100,
                is_contextual=False,
            ),
            ContextSuggestion(
                id="global_crop_screen",
                icon="edit-cut-symbolic",
                title="Recortar Área da Tela",
                description="Selecionar região retangular para análise visual",
                prompt_template="recortar_area",
                action_type="recortar_area",
                priority=110,
                is_contextual=False,
            ),
            ContextSuggestion(
                id="global_analyze_clipboard",
                icon="edit-paste-symbolic",
                title="Analisar Copiado",
                description="Enviar conteúdo da área de transferência para a IA",
                prompt_template="analisar_copiado",
                action_type="analisar_copiado",
                priority=120,
                is_contextual=False,
            ),
            ContextSuggestion(
                id="global_toggle_dark",
                icon="weather-clear-night-symbolic",
                title="Alternar modo escuro",
                description="Mudar tema visual do desktop",
                prompt_template="ativar modo escuro",
                action_type="prompt",
                priority=130,
                is_contextual=False,
            ),
        ]

        # Adiciona sugestões globais evitando IDs já adicionados
        existing_ids = {s.id for s in suggestions}
        for fallback in global_fallbacks:
            if len(suggestions) >= limit:
                break
            if fallback.id not in existing_ids:
                suggestions.append(fallback)
                existing_ids.add(fallback.id)

        # Ordena pela prioridade definida e retorna até o limite desejado
        suggestions.sort(key=lambda s: s.priority)
        return suggestions[:limit]
