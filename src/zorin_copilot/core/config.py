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
    # Importante: a BidiGenerateContent (Live API) rejeita aliases como
    # "-latest" para os modelos de áudio nativo. Tem que ser o model code
    # exato com a data da preview. Última versão estável documentada:
    # gemini-2.5-flash-native-audio-preview-12-2025
    # (https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash-native-audio-preview-12-2025)
    gemini_live_model: str = "models/gemini-2.5-flash-native-audio-preview-12-2025"
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

    # Atalho Global de Voz ao Vivo (Fase 3, parte B)
    live_voice_hotkey_enabled: bool = True
    live_voice_hotkey: str = "<Super>v"

    # Wake word ("palavra de ativação") — detecção offline e hands-free
    wake_word_enabled: bool = False
    wake_phrases: list = field(default_factory=lambda: ["ok copilot", "olá copilot"])
    wake_word_model_path: str = ""  # caminho do modelo Vosk (ex.: pt-BR)

    # Configurações de Voz Local (Piper TTS + faster-whisper)
    piper_voice_model: str = "pt_BR-faber-medium"
    whisper_model: str = "small"
    voice_visualizer_style: str = "waves"  # "waves", "bars", "matrix", "orb"
    voice_overlay_mode: str = "pill"  # "pill" (Pílula Compacta Flutuante) ou "full" (Janela Completa)

    # Posicionamento e comportamento da pílula de voz (voice_overlay_mode="pill")
    pill_monitor_idx: int = -1  # -1 = auto (monitor sob o cursor), senão índice Gdk.Display.get_monitors()
    pill_corner: str = "top-center"  # "top-center", "top-right", "top-left", "bottom-center"
    pill_pinned: bool = True  # manter acima de outras janelas (best-effort; X11-only)
    pill_margin: int = 12  # folga das bordas quando ancorada via layer-shell (Wayland)
    pill_idle_timeout_sec: int = 8  # 0 = nunca esmaecer; fade ocioso para 30% de opacidade

    # Inicialização automática com o sistema (Autostart no boot/login)
    autostart_enabled: bool = False

    # Red zones (faixas bloqueadas para automação) em pixels.
    # -1 = automático: pergunta ao ambiente (GNOME/KDE têm painel; wlroots só
    # tem barra se o usuário rodar uma). 0 desliga a faixa.
    red_zone_bottom_px: int = -1
    red_zone_top_px: int = -1

    # Configurações de Confiança, Privacidade e RAG

    @staticmethod
    def detect_platform() -> str:
        """Descreve o sistema real onde o Copilot roda, para o prompt de sistema.

        Lê ``/etc/os-release`` e as variáveis de ambiente do compositor. Evita
        mentir para o modelo (ex.: dizer "Zorin OS 18" quando o usuário está no
        EndeavourOS + Hyprland) — a detecção correta dita as sugestões de pacotes
        e comandos que o agente faz.
        """
        pretty = "Linux"
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("PRETTY_NAME="):
                        pretty = line.split("=", 1)[1].strip().strip('"')
                        break
        except OSError:
            pass
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").strip() or "desconhecido"
        session = (os.environ.get("XDG_SESSION_TYPE") or "").strip()
        if session:
            return f"{pretty} ({desktop} no {session})"
        return f"{pretty} ({desktop})"
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
    # Prompt de sistema customizável. Valor vazio = "use o dinâmico detectado por detect_platform()".
    system_prompt: str = ""

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

        # Prompt de sistema: se o usuário não customizou, usa a descrição da
        # plataforma real (evita dizer "Zorin OS 18" num EndeavourOS/Hyprland).
        if not config.system_prompt:
            plat = CopilotConfig.detect_platform()
            config.system_prompt = (
                f"Você é o Zorin Copilot, assistente inteligente do sistema operacional {plat}. "
                "Você ajuda o usuário a realizar tarefas, responder dúvidas sobre o computador e "
                "propor ações de sistema de forma clara, prestativa e objetiva em português."
            )

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
