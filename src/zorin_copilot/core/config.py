# Decisão de design: armazenamento seguro em ~/.config/zorin-copilot/config.json com suporte a fallback em variáveis de ambiente e permissões restritas.

"""Gerenciamento de configurações do Zorin Copilot."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class CopilotConfig:
    provider: str = "gemini"  # "gemini", "ollama", "openai"
    
    # Configurações do Google Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    
    # Configurações do Ollama (Local)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:latest"
    
    # Configurações de API compatível com OpenAI (OpenAI, Groq, DeepSeek, OpenRouter)
    openai_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Pesquisa na Web em tempo real
    web_search_enabled: bool = True

    # Atalho Global do Sistema (HUD Summon)
    global_shortcut_enabled: bool = True
    global_shortcut_key: str = "<Super>c"
    
    # Prompt de sistema customizável
    system_prompt: str = (
        "Você é o Zorin Copilot, assistente inteligente do sistema operacional Zorin OS 18 Core "
        "(GNOME 46 no Wayland). Você ajuda o usuário a realizar tarefas, responder dúvidas sobre "
        "o computador e propor ações de sistema de forma clara, prestativa e objetiva em português."
    )

    @classmethod
    def config_dir(cls) -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        path = Path(base) / "zorin-copilot"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def config_file(cls) -> Path:
        return cls.config_dir() / "config.json"

    @classmethod
    def load(cls) -> CopilotConfig:
        config = cls()
        file_path = cls.config_file()
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data: dict[str, Any] = json.load(f)
                    for k, v in data.items():
                        if hasattr(config, k) and v is not None:
                            setattr(config, k, v)
            except Exception:
                pass

        # Fallback para variáveis de ambiente se campos estiverem vazios
        if not config.gemini_api_key:
            config.gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
        if not config.openai_api_key:
            config.openai_api_key = os.environ.get("OPENAI_API_KEY", "")

        return config

    def save(self) -> None:
        file_path = self.config_file()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        # Permissões 0600 para proteger chaves de API
        try:
            os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def is_configured(self) -> bool:
        if self.provider == "gemini":
            return bool(self.gemini_api_key.strip())
        if self.provider == "ollama":
            return bool(self.ollama_url.strip())
        if self.provider == "openai":
            return bool(self.openai_api_key.strip())
        return False
