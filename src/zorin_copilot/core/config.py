# Decisão de design: armazenamento seguro em ~/.config/zorin-copilot/config.json com suporte a fallback em variáveis de ambiente e permissões restritas.

"""Gerenciamento de configurações do Zorin Copilot."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CopilotConfig:
    provider: str = "gemini"  # "gemini", "hybrid", "ollama", "openai"
    fallback_to_ollama: bool = True
    
    # Configurações do Google Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    gemini_live_model: str = "models/gemini-2.5-flash-native-audio-latest"
    gemini_live_voice: str = "Puck"  # "Puck", "Aoede", "Charon", "Fenrir", "Kore"
    
    # Configurações do Ollama (Local)
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_vision_model: str = "minicpm-v"
    
    # Configurações de API compatível com OpenAI (OpenAI, Groq, DeepSeek, OpenRouter)
    openai_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Configurações do WorkBuddy AI (Tencent Hunyuan / HY4)
    workbuddy_url: str = "https://www.workbuddy.ai/v2"
    workbuddy_api_key: str = ""
    workbuddy_model: str = "hy4-preview"

    # Pesquisa na Web em tempo real
    web_search_enabled: bool = True

    # Atalho Global do Sistema (HUD Summon)
    global_shortcut_enabled: bool = True
    global_shortcut_key: str = "<Super>c"
    
    # Atalho Global Direto de Recorte Inteligente (Pilar 3: Visão Instantânea)
    crop_shortcut_enabled: bool = True
    crop_shortcut_key: str = "<Super><Shift>s"

    # Atalho Global Direto de Conversa por Voz (Fase 4: Live Voice Local)
    voice_shortcut_enabled: bool = True
    voice_shortcut_key: str = "<Super><Shift>v"

    # Configurações de Voz Local (Piper TTS + faster-whisper)
    piper_voice_model: str = "pt_BR-faber-medium"
    whisper_model: str = "small"
    voice_visualizer_style: str = "waves"  # "waves", "bars", "matrix", "orb"
    
    # Inicialização automática com o sistema (Autostart no boot/login)
    autostart_enabled: bool = False

    # Configurações de Confiança, Privacidade e RAG
    trusted_directories: list[str] = field(default_factory=lambda: ["~/Documentos"])
    quarantine_directories: list[str] = field(default_factory=lambda: ["~/Downloads"])
    ignored_patterns: list[str] = field(
        default_factory=lambda: [
            "*.kdbx",
            "*.key",
            "*.pem",
            "*.env",
            "*IRPF*",
            "*senha*",
            "*extrato*",
            "*credentials*",
        ]
    )
    rag_local_only: bool = False
    mask_pii: bool = True
    max_file_size_mb: int = 40
    
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
        if not config.workbuddy_api_key:
            config.workbuddy_api_key = os.environ.get("WORKBUDDY_API_KEY", "")

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
        if self.provider == "hybrid":
            return bool(self.gemini_api_key.strip() or self.ollama_url.strip())
        if self.provider == "gemini":
            return bool(self.gemini_api_key.strip())
        if self.provider == "ollama":
            return bool(self.ollama_url.strip())
        if self.provider == "openai":
            return bool(self.openai_api_key.strip())
        if self.provider == "workbuddy":
            return bool(self.workbuddy_api_key.strip())
        return False
