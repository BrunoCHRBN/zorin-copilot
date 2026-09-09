# Decisão de design: camada de provedores de LLM desacoplada — suporta Google Gemini (nuvem com free tier), Ollama (local offline) e OpenAI compatível (Groq/OpenRouter), com retorno estruturado de ações para o desktop.

"""Provedores de Inteligência Artificial para o Zorin Copilot."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Final

import requests

from .actions import ActionType, DesktopAction
from ..core.config import CopilotConfig
from ..core.usage import TokenUsage, TokenUsageTracker, usage_from_gemini, usage_from_ollama, usage_from_openai

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Sua missão é atuar como um colega de bancada inteligente, ágil, empático e resolutivo — ajudando a operar o computador, solucionar problemas técnicos, resumir informações e realizar tarefas no desktop.

PERSONALIDADE & TOM DE VOZ (PARCEIRO DE DESKTOP):
1. Linguagem Natural e Descomplicada: Responda em português brasileiro de forma fluida, acolhedora, competente e direta ao ponto.
2. Fim do Formalismo Robótico: NUNCA inicie com clichês como "Certamente! Como seu assistente...", "Com base nas diretrizes...", ou "Aqui está a resposta:". Vá direto ao assunto como um colega de trabalho parceiro.
3. Tom Camaleônico:
   - Para pedidos operacionais rápidos (ex: "abre o terminal", "muta o som", "memória livre"): seja breve, ágil (1 frase) e forneça a ação correspondente.
   - Para perguntas conceituais, análises ou depuração de erros: seja didático, detalhado e estruturado, usando formatação Markdown elegante (negrito, listas e blocos de código).
4. Proatividade Leve (Micro-Hooks Situacionais):
   - Ao concluir uma ação, você pode incluir no final de "explanation" UMA frase opcional sugerindo o próximo passo natural mais provável (ex: "Já abri o documento de metas. Se quiser que eu resuma os pontos principais ou calcule as médias, só me avisar!").
   - Evite insistência ou prolixidade: a sugestão deve ser curta, situacional e não obstrutiva.
5. Memória Orgânica e Transparente:
   - Aplique as preferências do usuário (ex: navegador favorito, caminhos de projetos, estilo de código) de forma invisível e natural. NUNCA diga "de acordo com a Base de Conhecimento" ou "conforme memorizado". Apenas aplique a preferência diretamente como se fosse seu hábito natural.
6. Tratamento Humanizado de Incertezas e Falhas:
   - Se um documento, arquivo ou comando não for encontrado, nunca responda com mensagens frias de erro técnico. Explique com naturalidade e proponha caminhos alternativos úteis.

DIRETRIZES TÉCNICAS E DE RESPOSTA:
1. Respostas Fatuais Diretas (Sem Evasão):
   - Para perguntas sobre data, dia da semana, horário, hardware ou status do sistema, RESPONDA DIRETAMENTE E CONCLUSIVAMENTE no campo "explanation" com os dados do contexto em tempo real. Nunca mande o usuário olhar o relógio ou abrir outro app para algo que você mesmo pode responder.
2. Multimodalidade e Auto-Cura (Self-Healing):
   - Ao receber capturas de tela ou recortes de erros:
     a) Descreva a causa raiz do problema em linguagem clara e acessível.
     b) Smart OCR: Se houver código, logs ou textos visíveis relevantes, transcreva com fidelidade em "extracted_text" ("code" ou "text").
     c) Crie OBRIGATORIAMENTE a ação "fix_command" pronta para execução quando houver erro de pacote, dependência quebrada ou serviço inativo no Linux.
3. Consciência Situacional:
   - Utilize as informações de [Contexto Situacional do Desktop] (janela em foco, horário, mídia ativa) para compreender referências imediatas do usuário.
4. Fidelidade Temporal e de Calendário:
   - Use SEMPRE a "Data e Horário" fornecida no [Contexto Situacional do Desktop] como verdade factual absoluta. O ano corrente é o indicado nesse contexto. O Natal ocorre invariavelmente em 25 de Dezembro de cada ano, e o Ano Novo em 1º de Janeiro. NUNCA invente datas ou anos distantes (como 2029) para datas comemorativas anuais. Para contagem de dias ou feriados, apoie-se nas informações temporais e feriados de referência do contexto situacional.
5. Concisão Executiva em Ações e Busca Web:
   - Ao executar ações que abrem conteúdo no desktop (como "open_url" para páginas web, "launch_app" para programas ou "open_document"):
     * O campo "explanation" DEVE ser uma confirmação executiva e acolhedora de apenas 1 a 2 frases curtas (ex: "Abri a pesquisa no Mercado Livre no seu navegador para você.", "Abri a Calculadora para você.").
     * NUNCA copie ou despeje trechos brutos de busca web, rodapés promocionais, menus de navegação institucionais ("Lojas oficiais. Categorias. Ofertas do dia..."), termos de serviço ou números de citação como "[1]" em "explanation" quando estiver abrindo a página correspondente. A página já estará aberta e visível na tela para o usuário!
   - Em perguntas puramente informativas baseadas na busca web (sem abrir URLs):
     * Resuma os fatos apurados em 2 a 3 frases objetivas em linguagem própria, sem copiar textos de menus, rodapés ou citações como "[1]".

AÇÕES DISPONÍVEIS NO ARRAY "actions":
- "fix_command": comando bash para correção no terminal. target: "descrição curta", params: {"command": "...", "requires_sudo": bool, "terminal": true}.
- "smart_ocr": texto ou código extraído da tela para o clipboard. target: "conteúdo", params: {"kind": "code"|"text"}.
- "open_url": abrir link no navegador. Para serviços web (Gmail, Google Drive, YouTube, Maps), use deep links específicos com parâmetros de pesquisa ou criação para executar a ação diretamente dentro do serviço:
  * Gmail busca: "https://mail.google.com/mail/u/0/#search/<query>" (ex: "is:unread")
  * Gmail novo email: "https://mail.google.com/mail/u/0/?view=cm&fs=1&to=<email>&su=<assunto>&body=<corpo>"
  * Google Drive busca: "https://drive.google.com/drive/search?q=<query>"
  * Google Docs criar: "https://docs.google.com/document/create"
  * Google Sheets criar: "https://sheets.google.com/create"
  * YouTube busca: "https://www.youtube.com/results?search_query=<query>"
  * Google Maps busca: "https://www.google.com/maps/search/<query>"
- "launch_app": abrir aplicativo do desktop. target: "nome_app".
- "open_document": abrir arquivo de documento localizado no visualizador. target: "/caminho/arquivo", params: {"page_number": 1}.
- "system_control": ajustes do sistema (volume, tema). target: "ação", params: {"action": "...", "value": "..."}.
- "media_control": controle de música e Spotify. target: "play"|"pause"|"next"|"previous"|"search", params: {"action": "play"|"pause"|"search", "query": "nome da música ou artista", "player": "spotify"}.
- "type_text": digitar texto na aplicação ativa. target: "descrição do campo", params: {"text": "conteúdo a digitar"}.
- "write_file": gerar arquivo ou relatório em disco. target: "nome.md", params: {"filename": "...", "content": "...", "directory": "~/Documentos"}.
- "organize_files": organizar pastas em categorias. target: "caminho", params: {"directory": "...", "dry_run": false}.
- "notify": emitir notificação no sistema.

Você DEVE responder EXCLUSIVAMENTE em formato JSON com o seguinte esquema:
{
  "explanation": "Texto conversacional, acolhedor e direto ao ponto para o usuário.",
  "extracted_text": "Texto ou código puro extraído da tela/imagem (ou null se não aplicável)",
  "extracted_kind": "code" | "text",
  "actions": [
    {
      "type": "fix_command" | "open_url" | "launch_app" | "open_document" | "system_control" | "media_control" | "type_text" | "write_file" | "organize_files" | "notify",
      "target": "alvo da ação",
      "description": "descrição clara e amigável da ação em português",
      "params": {}
    }
  ]
}
"""


class BaseLLMProvider(ABC):
    # Preenchido pelo `IntentEngine` com o tracker da sessão. `None` significa
    # "não rastrear" — útil para chamadas avulsas fora da janela.
    usage_tracker: TokenUsageTracker | None = None

    def _record_usage(self, usage: TokenUsage | None, *, provider: str, model: str) -> None:
        """Acumula o consumo de uma resposta no tracker da sessão, se houver."""
        if self.usage_tracker is not None and usage is not None:
            self.usage_tracker.record(usage, provider=provider, model=model)

    @abstractmethod
    def is_configured(self) -> bool:
        """Indica se as credenciais ou endpoints necessários estão configurados."""
        pass

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """Testa conexão com o provedor e retorna (sucesso, mensagem)."""
        pass

    @abstractmethod
    def chat(
        self,
        prompt: str,
        app_list: list[str] | None = None,
        context_summary: str | None = None,
        history: list[dict[str, str]] | None = None,
        image_bytes: bytes | None = None,
        image_mime: str = "image/jpeg",
    ) -> tuple[str, list[DesktopAction]]:
        """Processa a solicitação do usuário e retorna (explicação, lista de ações propostas)."""
        pass

    @staticmethod
    def parse_response_payload(raw_text: str) -> tuple[str, list[DesktopAction]]:
        """Interpreta resposta do modelo, suportando JSON estrito ou blocos markdown de JSON."""
        cleaned = raw_text.strip()
        # Remove blocos de código ```json ... ``` se o modelo tiver envelopado
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
            explanation = data.get("explanation", raw_text)
            extracted_text = data.get("extracted_text")
            extracted_kind = data.get("extracted_kind", "text")
            raw_actions = data.get("actions", [])
            actions: list[DesktopAction] = []

            # 1. Se houver texto/código extraído via Smart OCR no JSON raiz
            if extracted_text and isinstance(extracted_text, str) and extracted_text.strip():
                clean_ocr = extracted_text.strip()
                is_code = extracted_kind == "code" or any(k in clean_ocr for k in ["def ", "import ", "class ", "function", "const ", "var ", "SELECT "])
                actions.append(
                    DesktopAction(
                        action_type=ActionType.SMART_OCR,
                        target=clean_ocr,
                        params={"kind": "código" if is_code else "texto"},
                        description=f"Copiar {'código' if is_code else 'texto'} extraído da imagem",
                    )
                )

            # 2. Se houver fix_command no nível da raiz
            if data.get("fix_command") and isinstance(data["fix_command"], str):
                cmd_root = data["fix_command"].strip()
                actions.append(
                    DesktopAction(
                        action_type=ActionType.FIX_COMMAND,
                        target=cmd_root,
                        params={"command": cmd_root, "requires_sudo": "sudo " in cmd_root, "terminal": True},
                        description=f"Executar correção: {cmd_root[:45]}",
                        requires_confirmation=True,
                    )
                )

            # 3. Processa lista de ações
            for act in raw_actions:
                act_type_str = str(act.get("type", "")).lower()
                target = str(act.get("target", "")).strip()
                desc = str(act.get("description", ""))
                params = act.get("params", {})

                if act_type_str in ("fix_command", "fix", "repair", "terminal_command"):
                    cmd = params.get("command") or target
                    if cmd:
                        actions.append(
                            DesktopAction(
                                action_type=ActionType.FIX_COMMAND,
                                target=target or f"Executar {cmd[:35]}...",
                                params={
                                    "command": cmd,
                                    "requires_sudo": params.get("requires_sudo", "sudo " in cmd),
                                    "terminal": params.get("terminal", True),
                                },
                                description=desc or f"Executar correção: {cmd[:45]}",
                                requires_confirmation=True,
                            )
                        )
                    continue

                if not target:
                    continue

                type_map = {
                    "open_url": ActionType.OPEN_URL,
                    "launch_app": ActionType.LAUNCH_APP,
                    "system_control": ActionType.SYSTEM_CONTROL,
                    "notify": ActionType.NOTIFY,
                    "click": ActionType.CLICK,
                    "smart_ocr": ActionType.SMART_OCR,
                    "media_control": ActionType.MEDIA_CONTROL,
                    "write_file": ActionType.WRITE_FILE,
                    "write_document": ActionType.WRITE_FILE,
                    "open_document": ActionType.OPEN_DOCUMENT,
                    "organize_files": ActionType.ORGANIZE_FILES,
                    "organize_directory": ActionType.ORGANIZE_FILES,
                }
                action_type = type_map.get(act_type_str)
                if action_type:
                    actions.append(
                        DesktopAction(
                            action_type=action_type,
                            target=target,
                            params=params,
                            description=desc,
                        )
                    )

            return explanation, actions
        except Exception:
            # Fallback inteligente se a IA respondeu em texto livre/Markdown
            fallback_actions: list[DesktopAction] = []
            
            # Smart OCR: extrai bloco de código mais relevante para cópia rápida
            code_blocks = re.findall(r"```(?:\w+)?\n([\s\S]+?)\n```", raw_text)
            if code_blocks:
                longest_code = max(code_blocks, key=len).strip()
                if len(longest_code) > 10:
                    fallback_actions.append(
                        DesktopAction(
                            action_type=ActionType.SMART_OCR,
                            target=longest_code,
                            params={"kind": "código"},
                            description="Copiar código transcrito da imagem",
                        )
                    )

            bash_matches = re.findall(r"```(?:bash|sh)?\n(sudo\s+[^\n]+|[a-zA-Z0-9_\-\./]+\s+[^\n]+)\n```", raw_text)
            for m in bash_matches:
                cmd = m.strip()
                if any(cmd.startswith(pfx) for pfx in ("sudo apt", "sudo dpkg", "pip install", "npm install", "systemctl", "kill")):
                    fallback_actions.append(
                        DesktopAction(
                            action_type=ActionType.FIX_COMMAND,
                            target=cmd,
                            params={"command": cmd, "requires_sudo": "sudo " in cmd, "terminal": True},
                            description=f"Executar correção: {cmd[:40]}",
                            requires_confirmation=True,
                        )
                    )
            return raw_text, fallback_actions


#: Aliases "flutuantes" mantidos pelo Google: apontam sempre para a versão
#: estável mais recente da família. São a escolha durável — continuam válidos
#: quando uma versão fixa é descontinuada (foi o caso do Gemini 2.0, desligado
#: em 2026), e por isso vêm primeiro.
GEMINI_ALIASES: Final[tuple[str, ...]] = (
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
)

#: Versões fixas (pinned), para quem precisa de comportamento reprodutível.
#: Listadas da mais nova para a mais antiga; nenhuma delas está descontinuada.
GEMINI_PINNED_MODELS: Final[tuple[str, ...]] = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
)

#: Fonte única de verdade dos modelos oferecidos na interface. Antes esta lista
#: era replicada em `preferences.py` (com o default apontando para outro modelo
#: que não o anunciado como recomendado) e as cadeias de fallback do provedor
#: citavam uma terceira combinação. Agora tudo deriva daqui.
GEMINI_MODEL_CHOICES: Final[list[str]] = [*GEMINI_ALIASES, *GEMINI_PINNED_MODELS]

#: Ordem de fallback: alias mais estável primeiro, depois versões fixas.
GEMINI_FALLBACK_MODELS: Final[tuple[str, ...]] = (
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
)

#: Alias em vez de versão fixa: o app roda instalado no desktop do usuário e não
#: recebe atualizações com frequência, então um modelo fixo viraria erro 404
#: silencioso daqui a alguns meses. O alias acompanha a versão estável atual.
DEFAULT_GEMINI_MODEL: Final[str] = "gemini-flash-latest"


def _with_fallbacks(model: str) -> list[str]:
    """Modelo escolhido primeiro, depois os fallbacks, sem repetição."""
    ordered = [model]
    for candidate in GEMINI_FALLBACK_MODELS:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


class GeminiProvider(BaseLLMProvider):
    """Provedor oficial Google Gemini via REST API."""

    def __init__(self, api_key: str, model: str = DEFAULT_GEMINI_MODEL):
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_GEMINI_MODEL

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "Chave de API do Gemini não informada."
        models_to_test = _with_fallbacks(self.model)
        last_error = ""
        for m in models_to_test:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={self.api_key}"
            payload = {
                "contents": [{"parts": [{"text": "Olá! Responda apenas 'OK'."}]}],
                "generationConfig": {"maxOutputTokens": 10},
            }
            try:
                resp = requests.post(url, json=payload, timeout=6)
                if resp.status_code == 200:
                    return True, f"Conexão com Gemini ({m}) bem-sucedida!"
                last_error = f"Status {resp.status_code}: {resp.text[:140]}"
                if resp.status_code in (429, 403):
                    break
            except Exception as exc:
                last_error = str(exc)
                break

        return False, f"Falha ao conectar com Gemini: {last_error}"

    def chat(
        self,
        prompt: str,
        app_list: list[str] | None = None,
        context_summary: str | None = None,
        history: list[dict[str, str]] | None = None,
        image_bytes: bytes | None = None,
        image_mime: str = "image/jpeg",
    ) -> tuple[str, list[DesktopAction]]:
        if not self.is_configured():
            return (
                "Chave de API do Google Gemini não configurada. "
                "Clique no ícone de engrenagem para inserir sua chave gratuita do Google AI Studio.",
                [],
            )

        sys_instruction = (
            "Você é o Zorin Copilot, assistente e parceiro de desktop nativo do usuário no "
            f"{CopilotConfig.detect_platform()}.\n" + SYSTEM_PROMPT
        )
        if context_summary:
            sys_instruction += f"\n\n{context_summary}"
        if app_list:
            sys_instruction += f"\n\nAplicativos atualmente instalados no desktop do usuário:\n{', '.join(app_list[:40])}"

        contents = []
        if history:
            # Janela deslizante de contexto: retém os últimos 10 turnos para manter coerência sem inflar tokens
            for turn in history[-10:]:
                role = "model" if turn.get("role") == "assistant" else "user"
                text = turn.get("content", "").strip()
                if text:
                    contents.append({"role": role, "parts": [{"text": text}]})

        user_parts: list[dict[str, Any]] = []
        if image_bytes:
            import base64
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            user_parts.append({
                "inline_data": {
                    "mime_type": image_mime,
                    "data": b64_img,
                }
            })
        user_parts.append({"text": prompt})
        contents.append({"role": "user", "parts": user_parts})

        payload = {
            "system_instruction": {
                "parts": [{"text": sys_instruction}]
            },
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
            },
        }

        # Modelos com fallback em caso de alta demanda temporária (503 / 429 / 404)
        models_to_try = _with_fallbacks(self.model)

        last_error = ""
        for current_model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={self.api_key}"
            try:
                resp = requests.post(url, json=payload, timeout=12)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        content_parts = candidates[0].get("content", {}).get("parts", [])
                        if content_parts:
                            raw_text = content_parts[0].get("text", "")
                            self._record_usage(
                                usage_from_gemini(data), provider="gemini", model=current_model
                            )
                            return self.parse_response_payload(raw_text)

                last_error = f"Erro no modelo {current_model} ({resp.status_code}): {resp.text[:180]}"
                if resp.status_code == 429:
                    logger.warning(f"Cota da chave do Gemini atingida (429). Encerrando tentativas para comutação imediata ao Ollama.")
                    break
                logger.warning(f"{last_error}. Tentando fallback se disponível...")
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_error = f"Erro de comunicação/timeout com Gemini: {exc}"
                logger.warning(f"{last_error}. Encerrando tentativas para comutação imediata ao Ollama.")
                break
            except Exception as exc:
                last_error = f"Erro de comunicação com {current_model}: {exc}"
                logger.warning(last_error)

        return f"Não foi possível obter resposta do Gemini: {last_error}", []


class OllamaProvider(BaseLLMProvider):
    """Provedor Ollama para modelos locais e 100% offline (Texto & Visão Multimodal)."""

    def __init__(
        self,
        host_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5:7b",
        vision_model: str = "minicpm-v",
    ):
        self.host_url = host_url.rstrip("/")
        self.model = model.strip() or "qwen2.5:7b"
        self.vision_model = vision_model.strip() or "minicpm-v"

    def is_configured(self) -> bool:
        return bool(self.host_url)

    def test_connection(self) -> tuple[bool, str]:
        try:
            resp = requests.get(f"{self.host_url}/api/tags", timeout=4)
            if resp.status_code == 200:
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                model_base = self.model.split(":")[0]
                has_text = self.model in models or any(m.startswith(model_base) for m in models)
                vision_base = self.vision_model.split(":")[0]
                has_vision = self.vision_model in models or any(m.startswith(vision_base) for m in models)

                if has_text and has_vision:
                    return True, f"Ollama conectado! '{self.model}' (Texto) e '{self.vision_model}' (Visão) prontos na GPU."
                if has_text:
                    return True, f"Ollama conectado! Modelo '{self.model}' pronto na GPU."
                if models:
                    return True, f"Ollama ativo! Modelos disponíveis: {', '.join(models[:4])}"
                return True, f"Ollama conectado, mas modelo '{self.model}' não instalado (execute: ollama pull {self.model})."
            return False, f"Ollama retornou status {resp.status_code}."
        except Exception as exc:
            return False, f"Não foi possível conectar ao Ollama em {self.host_url}: {exc}"

    def chat(
        self,
        prompt: str,
        app_list: list[str] | None = None,
        context_summary: str | None = None,
        history: list[dict[str, str]] | None = None,
        image_bytes: bytes | None = None,
        image_mime: str = "image/jpeg",
    ) -> tuple[str, list[DesktopAction]]:
        url = f"{self.host_url}/api/chat"
        sys_instruction = (
            "Você é o Zorin Copilot, assistente e parceiro de desktop nativo do usuário no "
            f"{CopilotConfig.detect_platform()}.\n" + SYSTEM_PROMPT
        )
        if context_summary:
            sys_instruction += f"\n\n{context_summary}"
        if app_list:
            sys_instruction += f"\n\nAplicativos instalados no computador:\n{', '.join(app_list[:30])}"

        messages = [{"role": "system", "content": sys_instruction}]
        if history:
            messages.extend(history)
        
        user_msg: dict[str, Any] = {"role": "user", "content": prompt}
        if image_bytes:
            import base64
            user_msg["images"] = [base64.b64encode(image_bytes).decode("utf-8")]
        messages.append(user_msg)

        # Seleciona dinamicamente o modelo adequado (Visão multimodal vs Texto puro)
        selected_model = self.vision_model if (image_bytes and self.vision_model) else self.model
        timeout_sec = 90 if image_bytes else 45

        payload = {
            "model": selected_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.2 if image_bytes else 0.3,
                "num_predict": 2048,
            },
            "keep_alive": "15m",
        }
        if not image_bytes:
            payload["format"] = "json"

        try:
            resp = requests.post(url, json=payload, timeout=timeout_sec)
            if resp.status_code != 200:
                err_text = resp.text
                if "does not support images" in err_text.lower():
                    return (
                        f"O modelo local '{selected_model}' não possui suporte a visão de imagens. "
                        "Ele responde comandos e perguntas em texto de forma nativa.",
                        [],
                    )
                return f"Erro no Ollama ({resp.status_code}): {err_text[:200]}", []
            data = resp.json()
            raw_text = data.get("message", {}).get("content", "")
            self._record_usage(usage_from_ollama(data), provider="ollama", model=self.model)
            return self.parse_response_payload(raw_text)
        except Exception as exc:
            return f"Erro ao consultar Ollama local ({selected_model}): {exc}", []


class OpenAICompatProvider(BaseLLMProvider):
    """Provedor para APIs compatíveis com OpenAI (OpenAI, Groq, DeepSeek, etc.)."""

    def __init__(self, api_url: str, api_key: str, model: str = "gpt-4o-mini"):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip() or "gpt-4o-mini"

    def is_configured(self) -> bool:
        return bool(self.api_url and self.api_key)

    def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "URL ou chave de API não configurada."
        url = f"{self.api_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                return True, f"Conexão com {self.model} bem-sucedida!"
            return False, f"Erro na API ({resp.status_code}): {resp.text[:160]}"
        except Exception as exc:
            return False, f"Falha de conexão: {exc}"

    def chat(
        self,
        prompt: str,
        app_list: list[str] | None = None,
        context_summary: str | None = None,
        history: list[dict[str, str]] | None = None,
        image_bytes: bytes | None = None,
        image_mime: str = "image/jpeg",
    ) -> tuple[str, list[DesktopAction]]:
        if not self.is_configured():
            return "Chave de API ou URL não configurada.", []

        url = f"{self.api_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        sys_instruction = (
            "Você é o Zorin Copilot, assistente e parceiro de desktop nativo do usuário no "
            f"{CopilotConfig.detect_platform()}.\n" + SYSTEM_PROMPT
        )
        if context_summary:
            sys_instruction += f"\n\n{context_summary}"
        if app_list:
            sys_instruction += f"\n\nAplicativos disponíveis:\n{', '.join(app_list[:30])}"

        messages = [{"role": "system", "content": sys_instruction}]
        if history:
            messages.extend(history)

        if image_bytes:
            import base64
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{image_mime};base64,{b64_img}"},
                    },
                ],
            })
        else:
            messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=25)
            if resp.status_code != 200:
                return f"Erro na API ({resp.status_code}): {resp.text[:200]}", []
            data = resp.json()
            raw_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            self._record_usage(usage_from_openai(data), provider="openai", model=self.model)
            return self.parse_response_payload(raw_text)
        except Exception as exc:
            return f"Erro na requisição: {exc}", []


class WorkBuddyProvider(BaseLLMProvider):
    """Provedor oficial WorkBuddy AI / Tencent Hunyuan (Tencent HY4 MoE)."""

    def __init__(
        self,
        api_key: str,
        model: str = "hy4-preview",
        api_url: str = "https://www.workbuddy.ai/v2",
    ):
        self.api_key = api_key.strip()
        self.model = model.strip() or "hy4-preview"
        self.api_url = api_url.rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.api_url and self.api_key)

    def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "Chave de API do WorkBuddy AI não informada."

        url = f"{self.api_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # O endpoint do WorkBuddy exige stream: true e primeiro item como system prompt
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Você é um verificador de conectividade. Responda OK."},
                {"role": "user", "content": "ping"},
            ],
            "stream": True,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=12)
            if resp.status_code == 200:
                return True, f"Conexão com WorkBuddy AI ({self.model}) bem-sucedida!"
            err_msg = resp.text[:180]
            return False, f"Erro no WorkBuddy AI ({resp.status_code}): {err_msg}"
        except Exception as exc:
            return False, f"Falha ao conectar com WorkBuddy AI: {exc}"

    def chat(
        self,
        prompt: str,
        app_list: list[str] | None = None,
        context_summary: str | None = None,
        history: list[dict[str, str]] | None = None,
        image_bytes: bytes | None = None,
        image_mime: str = "image/jpeg",
    ) -> tuple[str, list[DesktopAction]]:
        if not self.is_configured():
            return "Chave de API do WorkBuddy AI não configurada.", []

        url = f"{self.api_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        sys_instruction = (
            "Você é o Zorin Copilot, assistente e parceiro de desktop nativo do usuário no "
            f"{CopilotConfig.detect_platform()}.\n" + SYSTEM_PROMPT
        )
        if context_summary:
            sys_instruction += f"\n\n{context_summary}"
        if app_list:
            sys_instruction += f"\n\nAplicativos disponíveis no sistema:\n{', '.join(app_list[:30])}"

        # WorkBuddy exige estritamente que a primeira mensagem seja role: 'system'
        messages: list[dict[str, Any]] = [{"role": "system", "content": sys_instruction}]

        if history:
            for turn in history[-10:]:
                r = turn.get("role", "user")
                c = turn.get("content", "")
                if r in ("user", "assistant") and c:
                    messages.append({"role": r, "content": c})

        if image_bytes:
            import base64
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{image_mime};base64,{b64_img}"},
                    },
                ],
            })
        else:
            messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }

        timeout_sec = 120 if image_bytes else 90
        try:
            resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout_sec)
            if resp.status_code != 200:
                err_text = resp.text[:200]
                return f"Erro no WorkBuddy AI ({resp.status_code}): {err_text}", []

            collected_tokens: list[str] = []
            try:
                for line in resp.iter_lines():
                    if not line:
                        continue
                    txt = line.decode("utf-8", errors="replace")
                    if txt.startswith("data: "):
                        chunk_str = txt[6:].strip()
                        if chunk_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(chunk_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            part = delta.get("content", "")
                            if part:
                                collected_tokens.append(part)
                        except Exception:
                            pass
            except Exception as stream_err:
                logger.warning(f"Aviso na transmissão da stream do WorkBuddy: {stream_err}")

            raw_text = "".join(collected_tokens)
            if not raw_text.strip():
                return "O WorkBuddy AI não retornou conteúdo na resposta.", []

            return self.parse_response_payload(raw_text)
        except Exception as exc:
            return f"Erro na comunicação com WorkBuddy AI: {exc}", []


class HybridProvider(BaseLLMProvider):
    """Provedor híbrido inteligente: Google Gemini primário com auto-failover instantâneo para Ollama local."""

    def __init__(
        self,
        gemini_provider: GeminiProvider,
        ollama_provider: OllamaProvider,
        mode: str = "hybrid",
        fallback_enabled: bool = True,
    ):
        self.gemini = gemini_provider
        self.ollama = ollama_provider
        self.mode = mode
        self.fallback_enabled = fallback_enabled

    def is_configured(self) -> bool:
        if self.mode == "hybrid":
            return self.gemini.is_configured() or self.ollama.is_configured()
        return self.gemini.is_configured()

    def test_connection(self) -> tuple[bool, str]:
        results = []
        gemini_ok = False
        if self.gemini.is_configured():
            ok, msg = self.gemini.test_connection()
            gemini_ok = ok
            results.append(f"Gemini: {'✓ Conectado' if ok else f'✗ {msg}'}")
        else:
            results.append("Gemini: ⚠️ Chave de API não informada")

        ollama_ok, o_msg = self.ollama.test_connection()
        results.append(f"Ollama: {'✓ ' + o_msg if ollama_ok else '✗ ' + o_msg}")

        is_healthy = gemini_ok or ollama_ok
        return is_healthy, " | ".join(results)

    @staticmethod
    def _is_gemini_error(text: str) -> bool:
        if not text:
            return True
        if (
            text.startswith("Não foi possível obter resposta do Gemini:")
            or text.startswith("Chave de API do Google Gemini não configurada")
        ):
            return True
        return False

    def chat(
        self,
        prompt: str,
        app_list: list[str] | None = None,
        context_summary: str | None = None,
        history: list[dict[str, str]] | None = None,
        image_bytes: bytes | None = None,
        image_mime: str = "image/jpeg",
    ) -> tuple[str, list[DesktopAction]]:
        use_gemini = self.gemini.is_configured()
        if use_gemini:
            try:
                explanation, actions = self.gemini.chat(
                    prompt=prompt,
                    app_list=app_list,
                    context_summary=context_summary,
                    history=history,
                    image_bytes=image_bytes,
                    image_mime=image_mime,
                )
                if not self._is_gemini_error(explanation):
                    return explanation, actions

                logger.warning(
                    f"Gemini indisponível ou com cota excedida ({explanation[:100]}...). "
                    f"Ativando failover para Ollama local ({self.ollama.model})."
                )
            except Exception as exc:
                logger.warning(f"Exceção no GeminiProvider: {exc}. Ativando failover para Ollama...")

        # Executa failover para Ollama local (Texto com Qwen ou Visão com MiniCPM-V)
        if self.fallback_enabled and self.ollama.is_configured():
            local_expl, local_actions = self.ollama.chat(
                prompt=prompt,
                app_list=app_list,
                context_summary=context_summary,
                history=history,
                image_bytes=image_bytes,
                image_mime=image_mime,
            )

            if local_expl and not local_expl.startswith("Erro ao consultar Ollama"):
                if use_gemini:
                    badge = (
                        f"⚡ *[Visão Computacional Local - {self.ollama.vision_model} (GPU Offline)]*\n\n"
                        if image_bytes
                        else f"⚡ *[Modo Local Offline - {self.ollama.model}]*\n\n"
                    )
                    if badge not in local_expl:
                        local_expl = f"{badge}{local_expl}"
                return local_expl, local_actions

        return (
            "Não foi possível processar sua solicitação: o Gemini está indisponível/sem cota "
            f"e o modelo local Ollama não pôde ser alcançado em {self.ollama.host_url}.",
            [],
        )


def get_llm_provider(config: CopilotConfig) -> BaseLLMProvider:
    """Retorna a instância do provedor ativo com base na configuração."""
    ollama_prov = OllamaProvider(
        host_url=config.ollama_url,
        model=config.ollama_model,
        vision_model=getattr(config, "ollama_vision_model", "minicpm-v"),
    )
    gemini_prov = GeminiProvider(api_key=config.gemini_api_key, model=config.gemini_model)

    if config.provider == "ollama":
        return ollama_prov
    if config.provider == "workbuddy":
        return WorkBuddyProvider(
            api_key=config.workbuddy_api_key,
            model=getattr(config, "workbuddy_model", "hy4-preview"),
            api_url=getattr(config, "workbuddy_url", "https://www.workbuddy.ai/v2"),
        )
    if config.provider == "openai":
        return OpenAICompatProvider(
            api_url=config.openai_url,
            api_key=config.openai_api_key,
            model=config.openai_model,
        )
    if config.provider == "hybrid":
        return HybridProvider(
            gemini_provider=gemini_prov,
            ollama_provider=ollama_prov,
            mode="hybrid",
            fallback_enabled=True,
        )

    # Padrão: Gemini com fallback para Ollama se fallback_to_ollama estiver ativo
    if getattr(config, "fallback_to_ollama", True):
        return HybridProvider(
            gemini_provider=gemini_prov,
            ollama_provider=ollama_prov,
            mode="gemini_with_fallback",
            fallback_enabled=True,
        )

    return gemini_prov
