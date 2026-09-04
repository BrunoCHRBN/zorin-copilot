# Decisão de design: camada de provedores de LLM desacoplada — suporta Google Gemini (nuvem com free tier), Ollama (local offline) e OpenAI compatível (Groq/OpenRouter), com retorno estruturado de ações para o desktop.

"""Provedores de Inteligência Artificial para o Zorin Copilot."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import requests

from .actions import ActionType, DesktopAction
from ..core.config import CopilotConfig

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Você é o Zorin Copilot, assistente inteligente integrado ao sistema operacional Zorin OS 18 Core (Linux / GNOME 46 no Wayland).
Sua missão é ajudar o usuário a usar o computador, tirar dúvidas sobre o sistema e realizar ações no desktop.

Sempre responda em português brasileiro de forma amigável, clara, didática e objetiva.

Você DEVE responder EXCLUSIVAMENTE em formato JSON com o seguinte esquema:
{
  "explanation": "Texto explicativo detalhado ou resposta para a pergunta do usuário. Pode conter instruções passo a passo.",
  "actions": [
    {
      "type": "open_url" | "launch_app" | "system_control" | "notify",
      "target": "alvo da ação",
      "description": "descrição amigável da ação em português",
      "params": {}
    }
  ]
}

Regras para o array "actions":
- Se o usuário pediu para acessar um site/serviço online (ex: "acessar gmail", "abrir youtube", "ver previsão do tempo"), crie uma ação "open_url" com target sendo o endereço completo (ex: "https://mail.google.com").
- Se o usuário pediu para abrir um aplicativo instalado (ex: "abrir navegador", "abrir steam", "abrir terminal"), crie uma ação "launch_app" com o nome do aplicativo.
- Se o usuário pediu ajustes de sistema (tema escuro/claro, volume, luz noturna, captura de tela), crie "system_control" com target correspondente.
- Se a pergunta for apenas conceitual, informativa ou de suporte ("o que é wayland?", "como funciona o zorin?"), deixe "actions": [].
- Se o usuário perguntar como acessar ou fazer algo e for conveniente oferecer um atalho, explique no campo "explanation" E proponha a ação em "actions" para que ele possa executar com 1 clique!
"""


class BaseLLMProvider(ABC):
    @abstractmethod
    def is_configured(self) -> bool:
        """Indica se as credenciais ou endpoints necessários estão configurados."""
        pass

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """Testa conexão com o provedor e retorna (sucesso, mensagem)."""
        pass

    @abstractmethod
    def chat(self, prompt: str, app_list: list[str] | None = None) -> tuple[str, list[DesktopAction]]:
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
            raw_actions = data.get("actions", [])
            actions: list[DesktopAction] = []

            for act in raw_actions:
                act_type_str = str(act.get("type", "")).lower()
                target = str(act.get("target", "")).strip()
                desc = str(act.get("description", ""))
                params = act.get("params", {})

                if not target:
                    continue

                type_map = {
                    "open_url": ActionType.OPEN_URL,
                    "launch_app": ActionType.LAUNCH_APP,
                    "system_control": ActionType.SYSTEM_CONTROL,
                    "notify": ActionType.NOTIFY,
                    "click": ActionType.CLICK,
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
            # Fallback seguro: trata o texto cru como explicação
            return raw_text, []


class GeminiProvider(BaseLLMProvider):
    """Provedor oficial Google Gemini via REST API."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key.strip()
        self.model = model.strip() or "gemini-2.5-flash"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "Chave de API do Gemini não informada."
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": "Olá! Responda apenas 'OK'."}]}],
            "generationConfig": {"maxOutputTokens": 10},
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True, f"Conexão com Gemini ({self.model}) bem-sucedida!"
            return False, f"Erro Gemini ({resp.status_code}): {resp.text[:160]}"
        except Exception as exc:
            return False, f"Falha de conexão com Gemini: {exc}"

    def chat(self, prompt: str, app_list: list[str] | None = None) -> tuple[str, list[DesktopAction]]:
        if not self.is_configured():
            return (
                "Chave de API do Google Gemini não configurada. "
                "Clique no ícone de engrenagem para inserir sua chave gratuita do Google AI Studio.",
                [],
            )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        
        sys_instruction = SYSTEM_PROMPT
        if app_list:
            sys_instruction += f"\n\nAplicativos atualmente instalados no desktop do usuário:\n{', '.join(app_list[:40])}"

        payload = {
            "systemInstruction": {"parts": [{"text": sys_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code != 200:
                return f"Erro na API do Gemini ({resp.status_code}): {resp.text[:200]}", []

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return "O modelo não retornou candidatos de resposta.", []

            content_parts = candidates[0].get("content", {}).get("parts", [])
            if not content_parts:
                return "Resposta vazia do modelo.", []

            raw_text = content_parts[0].get("text", "")
            return self.parse_response_payload(raw_text)
        except Exception as exc:
            return f"Erro de comunicação com Gemini: {exc}", []


class OllamaProvider(BaseLLMProvider):
    """Provedor Ollama para modelos locais e 100% offline."""

    def __init__(self, host_url: str = "http://localhost:11434", model: str = "llama3.2:latest"):
        self.host_url = host_url.rstrip("/")
        self.model = model.strip() or "llama3.2:latest"

    def is_configured(self) -> bool:
        return bool(self.host_url)

    def test_connection(self) -> tuple[bool, str]:
        try:
            resp = requests.get(f"{self.host_url}/api/tags", timeout=4)
            if resp.status_code == 200:
                models = [m.get("name") for m in resp.json().get("models", [])]
                if self.model in models:
                    return True, f"Ollama conectado! Modelo '{self.model}' disponível."
                if models:
                    return True, f"Ollama ativo! Modelos disponíveis: {', '.join(models[:4])}"
                return True, "Ollama conectado, mas nenhum modelo instalado (execute: ollama pull llama3.2)."
            return False, f"Ollama retornou status {resp.status_code}."
        except Exception as exc:
            return False, f"Não foi possível conectar ao Ollama em {self.host_url}: {exc}"

    def chat(self, prompt: str, app_list: list[str] | None = None) -> tuple[str, list[DesktopAction]]:
        url = f"{self.host_url}/api/chat"
        sys_instruction = SYSTEM_PROMPT
        if app_list:
            sys_instruction += f"\n\nAplicativos instalados no computador:\n{', '.join(app_list[:30])}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_instruction},
                {"role": "user", "content": prompt},
            ],
            "format": "json",
            "stream": False,
        }

        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                return f"Erro no Ollama ({resp.status_code}): {resp.text[:200]}", []
            data = resp.json()
            raw_text = data.get("message", {}).get("content", "")
            return self.parse_response_payload(raw_text)
        except Exception as exc:
            return f"Erro ao consultar Ollama local: {exc}", []


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

    def chat(self, prompt: str, app_list: list[str] | None = None) -> tuple[str, list[DesktopAction]]:
        if not self.is_configured():
            return "Chave de API ou URL não configurada.", []

        url = f"{self.api_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        sys_instruction = SYSTEM_PROMPT
        if app_list:
            sys_instruction += f"\n\nAplicativos disponíveis:\n{', '.join(app_list[:30])}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_instruction},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=25)
            if resp.status_code != 200:
                return f"Erro na API ({resp.status_code}): {resp.text[:200]}", []
            data = resp.json()
            raw_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return self.parse_response_payload(raw_text)
        except Exception as exc:
            return f"Erro na requisição: {exc}", []


def get_llm_provider(config: CopilotConfig) -> BaseLLMProvider:
    """Retorna a instância do provedor ativo com base na configuração."""
    if config.provider == "ollama":
        return OllamaProvider(host_url=config.ollama_url, model=config.ollama_model)
    if config.provider == "openai":
        return OpenAICompatProvider(
            api_url=config.openai_url,
            api_key=config.openai_api_key,
            model=config.openai_model,
        )
    # Padrão: Gemini
    return GeminiProvider(api_key=config.gemini_api_key, model=config.gemini_model)
