# Decisão de design: Chat de voz bidirecional em tempo real (Full-Duplex) via Gemini Multimodal Live API
# (BidiGenerateContent WebSocket) com áudio nativo 16kHz PCM (PipeWire pw-record) e saída 24kHz PCM (pw-play),
# com suporte a Tool Calling (execução de comandos locais no Zorin OS durante a fala da IA) e interrupção fluida (barge-in).

"""Cliente de conversação por voz ao vivo com o Google Gemini e execução de ferramentas em tempo real."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import os
import shutil
import struct
import subprocess
import threading
import time
from enum import Enum
from typing import Any, Callable

try:
    import websockets
except ImportError:
    websockets = None

from ..core.apps import AppManager
from ..core.browser import BrowserManager
from ..core.calendar import CalendarManager
from ..core.config import CopilotConfig
from ..core.email import EmailManager
from ..core.fence import ScreenFenceManager
from ..core.memory import MemoryManager
from ..core.rag import LocalDocumentRAG
from ..core.vision import ScreenCaptureService
from ..core.web_search import WebSearchClient
from ..shell.executor import ActionExecutor
from ..shell.input_driver import VirtualInputDriver
from .actions import ActionPlan, ActionType, DesktopAction

logger = logging.getLogger(__name__)


class LiveVoiceState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    EXECUTING = "executing"
    ERROR = "error"


LIVE_TOOLS_DECLARATION = [
    {
        "functionDeclarations": [
            {
                "name": "launch_app",
                "description": "Abre ou inicia um aplicativo instalado no sistema operacional Zorin OS.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "app_name": {
                            "type": "STRING",
                            "description": "Nome ou identificador do aplicativo a abrir (ex: terminal, google-chrome, firefox, spotify, vscode, nautilus, calc, arquivos)",
                        }
                    },
                    "required": ["app_name"],
                },
            },
            {
                "name": "system_control",
                "description": "Controla configurações do sistema operacional (volume, modo escuro/claro, mudo).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {
                            "type": "STRING",
                            "enum": ["volume_set", "volume_up", "volume_down", "mute", "dark_mode", "light_mode"],
                            "description": "Ação de controle a ser executada",
                        },
                        "value": {
                            "type": "STRING",
                            "description": "Valor numérico opcional (ex: '80' para volume_set)",
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "capture_screen",
                "description": "Captura um screenshot da tela do usuário para você poder inspecionar e analisar visualmente o que o usuário está vendo.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "interactive": {
                            "type": "BOOLEAN",
                            "description": "Se true, solicita recorte; se false, tira da tela inteira imediatamente.",
                        }
                    },
                },
            },
            {
                "name": "open_url",
                "description": "Abre um endereço de site ou URL no navegador padrão da área de trabalho.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "url": {
                            "type": "STRING",
                            "description": "Endereço web completo (ex: https://github.com)",
                        }
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "get_system_info",
                "description": "Obtém métricas do computador em tempo real: uso de memória RAM, CPU, status de bateria e versão do Zorin OS.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {},
                },
            },
            {
                "name": "web_search",
                "description": "Pesquisa na web por informações em tempo real, notícias, cotações ou documentações técnicas.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {
                            "type": "STRING",
                            "description": "Termo de busca na internet",
                        }
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "media_control",
                "description": "Controla tocadores de música e reprodutores de mídia como Spotify, VLC e navegadores (tocar, pausar, avançar faixa, retroceder, ou consultar que música está tocando).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {
                            "type": "STRING",
                            "enum": ["play", "pause", "play_pause", "next", "previous", "get_status"],
                            "description": "Ação de controle de mídia",
                        },
                        "player": {
                            "type": "STRING",
                            "description": "Nome opcional do reprodutor (ex: 'spotify')",
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "write_document",
                "description": "Cria ou salva um documento, anotação ou relatório em formato Markdown ou texto no computador do usuário.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filename": {
                            "type": "STRING",
                            "description": "Nome do arquivo (ex: 'relatorio_pesquisa.md')",
                        },
                        "content": {
                            "type": "STRING",
                            "description": "Conteúdo textual ou Markdown completo do documento",
                        },
                        "directory": {
                            "type": "STRING",
                            "description": "Pasta de destino (opcional, padrão ~/Documentos/Relatorios)",
                        },
                    },
                    "required": ["filename", "content"],
                },
            },
            {
                "name": "organize_directory",
                "description": "Organiza arquivos de uma pasta (como Downloads) categorizando em subpastas seguras (Imagens, Documentos, Instaladores, etc.).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "directory": {
                            "type": "STRING",
                            "description": "Caminho da pasta a ser organizada (opcional, padrão ~/Downloads)",
                        },
                        "dry_run": {
                            "type": "BOOLEAN",
                            "description": "Se true, apenas simula sem mover arquivos; se false, organiza os arquivos",
                        },
                    },
                },
            },
            {
                "name": "screen_fence_control",
                "description": "Controla a cerca de segurança espacial e qual monitor físico está autorizado para receber cliques e automações (ex: monitor principal AOC 27, monitor secundário VIE 24, ou todas as telas).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "monitor": {
                            "type": "STRING",
                            "description": "Identificador do monitor ou modo ('principal', 'secundaria', 'aoc', 'vie', 'all')",
                        }
                    },
                    "required": ["monitor"],
                },
            },
            {
                "name": "mouse_click",
                "description": "Executa um clique de mouse virtual na tela. As coordenadas são estritamente validadas pela cerca espacial do monitor ativo.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "x": {
                            "type": "NUMBER",
                            "description": "Coordenada horizontal (em porcentagem relativa de 0.0 a 1.0 ou pixels absolutos)",
                        },
                        "y": {
                            "type": "NUMBER",
                            "description": "Coordenada vertical (em porcentagem relativa de 0.0 a 1.0 ou pixels absolutos)",
                        },
                        "is_relative": {
                            "type": "BOOLEAN",
                            "description": "Se true, x e y são porcentagens [0.0, 1.0] do frame visual do vídeo; se false, pixels absolutos",
                        },
                        "button": {
                            "type": "STRING",
                            "enum": ["left", "right", "middle"],
                            "description": "Botão do mouse a ser clicado",
                        },
                        "double": {
                            "type": "BOOLEAN",
                            "description": "Se true, realiza clique duplo",
                        },
                    },
                    "required": ["x", "y"],
                },
            },
            {
                "name": "keyboard_type",
                "description": "Digita texto diretamente no aplicativo ou campo ativo na tela através do teclado virtual de hardware do Zorin OS.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": "Texto completo a ser digitado",
                        },
                        "press_enter": {
                            "type": "BOOLEAN",
                            "description": "Se true, pressiona Enter após concluir a digitação",
                        },
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "keyboard_hotkey",
                "description": "Envia uma combinação de teclas de atalho para a janela ativa (ex: ['ctrl', 'v'], ['ctrl', 'c'], ['alt', 'tab'], ['super']).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "keys": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                            "description": "Lista de teclas que compõem o atalho (ex: ['ctrl', 'c'])",
                        }
                    },
                    "required": ["keys"],
                },
            },
            {
                "name": "contact_lookup",
                "description": "Consulta os contatos salvos pelo nome, apelido (ex: 'contador', 'rh', 'financeiro') ou e-mail na memória permanente do usuário para obter o endereço exato sem alucinações.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {
                            "type": "STRING",
                            "description": "Nome, apelido ou termo de busca do contato",
                        }
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "contact_save",
                "description": "Salva um novo contato ou atualiza informações na memória permanente do usuário.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {
                            "type": "STRING",
                            "description": "Nome completo da pessoa ou instituição",
                        },
                        "email": {
                            "type": "STRING",
                            "description": "Endereço de e-mail válido",
                        },
                        "aliases": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                            "description": "Apelidos ou funções para fácil localização futura (ex: ['contador', 'carlos'])",
                        },
                        "notes": {
                            "type": "STRING",
                            "description": "Informações ou contexto adicional",
                        },
                    },
                    "required": ["name", "email"],
                },
            },
            {
                "name": "email_compose",
                "description": "Abre o cliente de e-mail (Thunderbird, Evolution, ou Webmail Gmail/Outlook no navegador) com destinatário, assunto e rascunho preenchidos para revisão do usuário.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "recipient": {
                            "type": "STRING",
                            "description": "Endereço de e-mail de destino ou nome/apelido de contato salvo",
                        },
                        "subject": {
                            "type": "STRING",
                            "description": "Assunto da mensagem",
                        },
                        "body": {
                            "type": "STRING",
                            "description": "Corpo ou rascunho textual do e-mail",
                        },
                        "client": {
                            "type": "STRING",
                            "enum": ["auto", "gmail", "outlook", "native"],
                            "description": "Cliente de e-mail a utilizar (padrão 'auto')",
                        },
                    },
                    "required": ["recipient"],
                },
            },
            {
                "name": "calendar_event",
                "description": "Gerencia compromissos e lembretes na agenda/calendário do Zorin OS (cria arquivos .ics compatíveis com GNOME Calendar e lista compromissos).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {
                            "type": "STRING",
                            "enum": ["create", "list", "delete"],
                            "description": "Ação de calendário a realizar",
                        },
                        "title": {
                            "type": "STRING",
                            "description": "Título do compromisso (necessário para 'create')",
                        },
                        "datetime_str": {
                            "type": "STRING",
                            "description": "Data e hora em linguagem natural (ex: 'hoje às 15:30', 'amanhã às 10h') ou ISO",
                        },
                        "duration_minutes": {
                            "type": "INTEGER",
                            "description": "Duração em minutos (padrão 60)",
                        },
                        "description": {
                            "type": "STRING",
                            "description": "Anotações ou link da reunião",
                        },
                        "location": {
                            "type": "STRING",
                            "description": "Local ou link da reunião",
                        },
                        "event_id": {
                            "type": "STRING",
                            "description": "Identificador do evento (necessário para 'delete')",
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "browser_search",
                "description": "Abre o navegador padrão com uma pesquisa direcionada no Google, YouTube, GitHub, Maps ou Wikipedia.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {
                            "type": "STRING",
                            "description": "Termo de pesquisa",
                        },
                        "engine": {
                            "type": "STRING",
                            "enum": ["google", "youtube", "github", "maps", "wikipedia", "duckduckgo"],
                            "description": "Motor de busca a utilizar (padrão 'google')",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "search_documents",
                "description": "Pesquisa na base local de documentos do usuário (PDFs, anotações, contratos e planilhas em ~/Documentos e ~/Downloads) usando busca semântica em texto completo.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {
                            "type": "STRING",
                            "description": "Termo de pesquisa, assunto ou pergunta sobre os documentos pessoais",
                        },
                        "limit": {
                            "type": "INTEGER",
                            "description": "Número máximo de trechos relevantes a retornar (padrão 4)",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "read_document_page",
                "description": "Lê o conteúdo completo de uma página específica de um documento localizado pela busca.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_path": {
                            "type": "STRING",
                            "description": "Caminho absoluto do arquivo no computador",
                        },
                        "page_number": {
                            "type": "INTEGER",
                            "description": "Número da página a ser lida (padrão 1)",
                        },
                    },
                    "required": ["file_path"],
                },
            },
            {
                "name": "open_document_file",
                "description": "Abre um documento local no visualizador do desktop (abrindo o Evince na página exata se for PDF).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "file_path": {
                            "type": "STRING",
                            "description": "Caminho do arquivo a ser aberto",
                        },
                        "page_number": {
                            "type": "INTEGER",
                            "description": "Página específica para abrir (padrão 1)",
                        },
                    },
                    "required": ["file_path"],
                },
            },
        ]
    }
]


class GeminiLiveClient:
    """Cliente assíncrono para o Gemini Multimodal Live API com áudio full-duplex e tool calling."""

    def __init__(
        self,
        config: CopilotConfig | None = None,
        executor: ActionExecutor | None = None,
        memory: MemoryManager | None = None,
    ):
        self.config = config or CopilotConfig.load()
        self.executor = executor or ActionExecutor()
        if memory is not None:
            self.memory = memory
        elif hasattr(self.executor, "memory") and isinstance(self.executor.memory, MemoryManager):
            self.memory = self.executor.memory
        else:
            self.memory = MemoryManager()

        self.fence = ScreenFenceManager()
        self.input_driver = VirtualInputDriver(fence=self.fence)
        self.email_mgr = EmailManager(memory=self.memory)
        self.cal_mgr = CalendarManager(memory=self.memory)
        self.rag = LocalDocumentRAG(memory=self.memory)
        self.state: LiveVoiceState = LiveVoiceState.DISCONNECTED

        # Callbacks para interface gráfica (GTK4)
        self.on_state_change: Callable[[LiveVoiceState, str], None] | None = None
        self.on_audio_level: Callable[[float], None] | None = None
        self.on_tool_executed: Callable[[str, str, bool], None] | None = None
        self.on_transcript: Callable[[str, str], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.on_video_state_change: Callable[[bool], None] | None = None

        self._is_running = False
        self._is_muted = False
        self._is_video_streaming = False
        self._video_thread: threading.Thread | None = None
        self._video_frames_count: int = 0
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any = None

        # Processos de áudio PipeWire
        self._record_proc: subprocess.Popen | None = None
        self._play_proc: subprocess.Popen | None = None

        # Rastreamento de ações e transcrições para persistência no chat da demanda
        self._session_start_time: float = 0.0
        self._executed_actions_log: list[dict[str, Any]] = []
        self._transcripts_log: list[tuple[str, str]] = []

    def _set_state(self, state: LiveVoiceState, message: str = "") -> None:
        self.state = state
        if self.on_state_change:
            try:
                self.on_state_change(state, message)
            except Exception as e:
                logger.error(f"Erro no callback on_state_change: {e}")

    def is_active(self) -> bool:
        return self._is_running and self.state != LiveVoiceState.DISCONNECTED

    def is_muted(self) -> bool:
        return self._is_muted

    def toggle_mute(self) -> bool:
        self._is_muted = not self._is_muted
        new_state = LiveVoiceState.LISTENING if not self._is_muted else LiveVoiceState.CONNECTED
        self._set_state(new_state, "Microfone mutado" if self._is_muted else "Microfone ativo")
        return self._is_muted

    def start(self) -> None:
        """Inicia a sessão de voz em uma thread de background dedicada."""
        if self._is_running:
            return

        if websockets is None:
            self._set_state(LiveVoiceState.ERROR, "Módulo 'websockets' não instalado. Instale com: sudo apt install python3-websockets")
            if self.on_error:
                self.on_error("Módulo 'websockets' ausente. Instale com: sudo apt install python3-websockets")
            return

        if not self.config.gemini_api_key:
            self._set_state(LiveVoiceState.ERROR, "Chave de API do Gemini não configurada.")
            if self.on_error:
                self.on_error("Chave de API do Gemini não informada. Configure em ⚙️.")
            return

        self._is_running = True
        self._is_muted = False
        self._video_frames_count = 0
        self._session_start_time = time.time()
        self._executed_actions_log.clear()
        self._transcripts_log.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="GeminiLiveWorker")
        self._thread.start()

    def stop(self) -> None:
        """Finaliza a conexão de voz e interrompe os fluxos de áudio e vídeo."""
        self.stop_video_stream()
        self._is_running = False
        self._terminate_audio_processes()

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        self._set_state(LiveVoiceState.DISCONNECTED, "Chamada encerrada.")

    def get_session_summary(self) -> dict[str, Any]:
        """Retorna o resumo estruturado da chamada de voz e vídeo ao vivo para persistir no chat da demanda ativa."""
        duration = max(1, int(time.time() - self._session_start_time)) if self._session_start_time > 0 else 0
        return {
            "duration_sec": duration,
            "actions_executed": list(self._executed_actions_log),
            "transcripts": list(self._transcripts_log),
            "video_streamed": bool(self._video_frames_count > 0),
            "video_frames": self._video_frames_count,
            "has_activity": bool(
                self._executed_actions_log
                or self._transcripts_log
                or self._video_frames_count > 0
                or duration >= 3
            ),
        }

    def _terminate_audio_processes(self) -> None:
        """Fecha subprocessos de gravação e reprodução de áudio."""
        if self._record_proc:
            try:
                self._record_proc.terminate()
                self._record_proc.wait(timeout=0.5)
            except Exception:
                try:
                    self._record_proc.kill()
                except Exception:
                    pass
            self._record_proc = None

        if self._play_proc:
            try:
                self._play_proc.terminate()
                self._play_proc.wait(timeout=0.5)
            except Exception:
                try:
                    self._play_proc.kill()
                except Exception:
                    pass
            self._play_proc = None

    def _start_player(self) -> subprocess.Popen | None:
        """Inicia processo de reprodução PipeWire para PCM 24kHz 16-bit mono."""
        self._stop_player()
        if shutil.which("pw-play"):
            cmd = ["pw-play", "--rate", "24000", "--channels", "1", "--format", "s16", "-"]
        elif shutil.which("aplay"):
            cmd = ["aplay", "-r", "24000", "-f", "S16_LE", "-c", "1", "-"]
        else:
            logger.error("Nenhum player de áudio (pw-play ou aplay) encontrado.")
            return None

        try:
            self._play_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return self._play_proc
        except Exception as exc:
            logger.error(f"Erro ao iniciar player de áudio: {exc}")
            return None

    def _stop_player(self) -> None:
        """Interrompe a reprodução de áudio imediatamente (barge-in)."""
        if self._play_proc:
            try:
                self._play_proc.terminate()
                self._play_proc.wait(timeout=0.2)
            except Exception:
                try:
                    self._play_proc.kill()
                except Exception:
                    pass
            self._play_proc = None

    def _run_loop(self) -> None:
        """Worker loop asyncio executado na thread de background."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._live_session())
        except Exception as exc:
            logger.error(f"Exceção no loop live: {exc}")
            self._set_state(LiveVoiceState.ERROR, str(exc))
        finally:
            self._terminate_audio_processes()
            self._set_state(LiveVoiceState.DISCONNECTED, "Desconectado.")

    async def _live_session(self) -> None:
        """Gerencia conexão WebSocket, streaming de microfone e recebimento de áudio/tools."""
        self._set_state(LiveVoiceState.CONNECTING, "Conectando ao Gemini Live...")

        api_key = self.config.gemini_api_key.strip()
        model_name = getattr(self.config, "gemini_live_model", "models/gemini-2.5-flash-native-audio-latest")
        voice_name = getattr(self.config, "gemini_live_voice", "Puck")
        uri = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={api_key}"

        try:
            async with websockets.connect(uri, max_size=10_000_000, ping_interval=15, ping_timeout=20) as ws:
                self._ws = ws

                # 1. Prepara contexto dinâmico de memória e cercas espaciais
                context_summary = self.memory.get_context_summary()
                active_mon = self.fence.get_active_monitor()
                active_mon_name = active_mon.name if active_mon else "Principal (AOC 27\")"
                monitors_desc = ", ".join([f"Monitor {m.index}: {m.name}" for m in self.fence.monitors])

                system_prompt_text = (
                    "Você é o Zorin Copilot, assistente nativo de voz e visão multimodal do sistema operacional Zorin OS 18 (Linux / GNOME / Wayland). "
                    "Você conversa por áudio em tempo real com o usuário em português brasileiro de forma concisa, simpática e prestativa. "
                    "Você tem controle e visão do desktop em tempo real quando o usuário compartilha a tela (Live Video a 1 FPS). "
                    f"Telas conectadas no desktop do usuário: [{monitors_desc}]. A tela ativa autorizada para ações no momento é '{active_mon_name}'. "
                    "Para alternar a tela autorizada de trabalho, use a ferramenta 'screen_fence_control'. "
                    "Para clicar ou digitar no desktop, use 'mouse_click', 'keyboard_type' e 'keyboard_hotkey'. Suas coordenadas serão validadas pela cerca espacial. "
                    "Ao redigir ou iniciar e-mails, use 'email_compose'. NUNCA invente ou adivinhe endereços de e-mail; se não souber, use 'contact_lookup' ou pergunte ao usuário. "
                    "Para marcar compromissos ou consultar a agenda, use 'calendar_event'. "
                    "Para pesquisas na web, use 'browser_search' ou 'web_search'. "
                    f"\n\n{context_summary}\n\n"
                    "Sempre que o usuário pedir para fazer algo no computador, use imediatamente as ferramentas apropriadas e comente o resultado brevemente por voz."
                )

                setup_payload = {
                    "setup": {
                        "model": model_name,
                        "generationConfig": {
                            "responseModalities": ["AUDIO"],
                            "speechConfig": {
                                "voiceConfig": {
                                    "prebuiltVoiceConfig": {
                                        "voiceName": voice_name,
                                    }
                                }
                            },
                        },
                        "systemInstruction": {
                            "parts": [
                                {
                                    "text": system_prompt_text,
                                }
                            ]
                        },
                        "tools": LIVE_TOOLS_DECLARATION,
                    }
                }

                await ws.send(json.dumps(setup_payload))
                setup_resp_raw = await ws.recv()
                setup_resp = json.loads(setup_resp_raw.decode("utf-8") if isinstance(setup_resp_raw, bytes) else setup_resp_raw)

                if "setupComplete" not in setup_resp:
                    err_msg = f"Falha no setup: {setup_resp}"
                    logger.error(err_msg)
                    self._set_state(LiveVoiceState.ERROR, err_msg)
                    return

                self._set_state(LiveVoiceState.LISTENING, "Conectado! Pode falar...")
                logger.info("Sessão Gemini Live estabelecida com sucesso.")

                # Inicia tarefas concorrentes: Gravação do Mic e Leitura do Servidor
                mic_task = asyncio.create_task(self._mic_recorder_loop(ws))
                server_task = asyncio.create_task(self._server_receiver_loop(ws))

                # Aguarda até que uma das tarefas finalize ou o usuário chame stop()
                done, pending = await asyncio.wait([mic_task, server_task], return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()

        except Exception as exc:
            logger.error(f"Erro de conexão com Gemini Live WebSocket: {exc}")
            self._set_state(LiveVoiceState.ERROR, f"Erro de conexão: {exc}")
            if self.on_error:
                self.on_error(str(exc))

    async def _mic_recorder_loop(self, ws: Any) -> None:
        """Lê áudio em tempo real do microfone via pw-record e transmite para o Gemini."""
        if shutil.which("pw-record"):
            cmd = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "-"]
        elif shutil.which("arecord"):
            cmd = ["arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-"]
        else:
            logger.error("Nenhum gravador de áudio (pw-record ou arecord) disponível.")
            return

        try:
            self._record_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.error(f"Falha ao iniciar gravador de microfone: {exc}")
            return

        chunk_size = 3200  # 100ms de áudio a 16kHz 16-bit (1600 amostras * 2 bytes)
        loop = asyncio.get_running_loop()

        while self._is_running and self._record_proc and self._record_proc.poll() is None:
            # Lê chunk do stdout sem bloquear o loop de eventos
            pcm_bytes = await loop.run_in_executor(None, self._record_proc.stdout.read, chunk_size)
            if not pcm_bytes:
                await asyncio.sleep(0.02)
                continue

            # Calcula volume / nível de amplitude RMS para o visualizador de onda da interface
            if self.on_audio_level:
                try:
                    num_samples = len(pcm_bytes) // 2
                    if num_samples > 0:
                        samples = struct.unpack(f"<{num_samples}h", pcm_bytes)
                        rms = math.sqrt(sum(s * s for s in samples) / num_samples)
                        norm_level = min(1.0, rms / 15000.0)
                        self.on_audio_level(norm_level)
                except Exception:
                    pass

            # Se não estiver mutado, envia chunk de áudio em base64 para o WebSocket
            if not self._is_muted:
                b64_audio = base64.b64encode(pcm_bytes).decode("utf-8")
                msg = {
                    "realtimeInput": {
                        "mediaChunks": [
                            {
                                "mimeType": "audio/pcm;rate=16000",
                                "data": b64_audio,
                            }
                        ]
                    }
                }
                try:
                    await ws.send(json.dumps(msg))
                except Exception as exc:
                    logger.warning(f"Erro ao enviar chunk de áudio: {exc}")
                    break

    async def _server_receiver_loop(self, ws: Any) -> None:
        """Recebe pacotes de áudio, eventos de interrupção e chamadas de ferramentas do Gemini."""
        while self._is_running:
            try:
                raw_msg = await ws.recv()
            except Exception:
                break

            data = json.loads(raw_msg.decode("utf-8") if isinstance(raw_msg, bytes) else raw_msg)

            # 1. Trata Tool Call (Execução de ações no desktop)
            if "toolCall" in data:
                self._set_state(LiveVoiceState.EXECUTING, "Executando comando...")
                await self._execute_tool_call(ws, data["toolCall"])
                continue

            # 2. Trata Server Content (Áudio do assistente e transcrição)
            if "serverContent" in data:
                sc = data["serverContent"]

                # Detecção de interrupção (Barge-In)
                if sc.get("interrupted"):
                    logger.info("Usuário interrompeu a fala da IA. Interrompendo player.")
                    self._stop_player()
                    self._set_state(LiveVoiceState.LISTENING, "Ouvindo você...")
                    continue

                model_turn = sc.get("modelTurn", {})
                parts = model_turn.get("parts", [])

                for p in parts:
                    # Áudio recebido do Gemini (PCM 24kHz)
                    if "inlineData" in p:
                        b64_data = p["inlineData"].get("data", "")
                        if b64_data:
                            audio_bytes = base64.b64decode(b64_data)
                            self._play_audio_chunk(audio_bytes)
                            self._set_state(LiveVoiceState.SPEAKING, "Falando...")

                    # Transcrição textual (se enviada pelo modelo)
                    if "text" in p:
                        self._transcripts_log.append(("assistant", p["text"]))
                        if self.on_transcript:
                            self.on_transcript("assistant", p["text"])

                if sc.get("turnComplete"):
                    self._set_state(LiveVoiceState.LISTENING, "Ouvindo você...")

    def _play_audio_chunk(self, audio_bytes: bytes) -> None:
        """Escreve dados PCM no stdin do processo de reprodução PipeWire."""
        if not self._play_proc or self._play_proc.poll() is not None:
            self._start_player()

        if self._play_proc and self._play_proc.stdin:
            try:
                self._play_proc.stdin.write(audio_bytes)
                self._play_proc.stdin.flush()
            except Exception as exc:
                logger.debug(f"Erro ao escrever áudio no player: {exc}")

    async def _execute_tool_call(self, ws: Any, tool_call_data: dict[str, Any]) -> None:
        """Executa ferramenta local solicitada pela IA e devolve toolResponse."""
        function_calls = tool_call_data.get("functionCalls", [])
        responses: list[dict[str, Any]] = []

        loop = asyncio.get_running_loop()

        for fc in function_calls:
            call_id = fc.get("id", "")
            name = fc.get("name", "")
            args = fc.get("args", {})
            logger.info(f"Executando ferramenta em tempo real: {name}({args})")

            # Executa a ação no SO em thread separada para não bloquear o loop WebSocket
            output = await loop.run_in_executor(None, self._dispatch_tool, name, args)

            success = output.get("success", True)
            message = output.get("message", f"{name} executado com sucesso.")

            self._executed_actions_log.append({
                "tool": name,
                "args": args,
                "output": output,
                "success": success,
                "message": message,
                "timestamp": time.time(),
            })

            if self.on_tool_executed:
                try:
                    self.on_tool_executed(name, message, success)
                except Exception as e:
                    logger.error(f"Erro no callback on_tool_executed: {e}")

            responses.append({
                "id": call_id,
                "response": {
                    "output": output,
                }
            })

        # Devolve o resultado de todas as ferramentas para a IA continuar falando
        tool_response_msg = {
            "toolResponse": {
                "functionResponses": responses,
            }
        }
        await ws.send(json.dumps(tool_response_msg))

    def _dispatch_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Despacha a execução concreta para os subsistemas do Zorin Copilot."""
        try:
            if name == "launch_app":
                app_name = args.get("app_name", "").strip()
                app, friendly_name = AppManager.find_app(app_name)
                if app:
                    ok, msg = AppManager.launch(app)
                    return {"success": ok, "message": f"Aplicativo '{friendly_name}' aberto com sucesso." if ok else msg}
                return {"success": False, "message": f"Aplicativo '{app_name}' não encontrado no sistema."}

            elif name == "system_control":
                action = args.get("action", "")
                val = args.get("value", "")
                desktop_act = DesktopAction(
                    action_type=ActionType.SYSTEM_CONTROL,
                    target=action,
                    params={"value": val, "step": val},
                )
                plan = ActionPlan(thought="Controle de sistema via Live Voice", actions=[desktop_act])
                reports = self.executor.execute_plan(plan)
                rep = reports[0] if reports else None
                return {
                    "success": rep.success if rep else True,
                    "message": rep.message if rep else f"Controle {action} executado.",
                }

            elif name == "capture_screen":
                interactive = bool(args.get("interactive", False))
                ok, img_bytes, mode = ScreenCaptureService.capture(interactive=interactive)
                if ok and img_bytes:
                    return {
                        "success": True,
                        "message": f"Captura ({mode}) realizada com sucesso com {len(img_bytes)} bytes.",
                    }
                return {"success": False, "message": "Captura de tela cancelada pelo usuário."}

            elif name == "open_url":
                url = args.get("url", "").strip()
                desktop_act = DesktopAction(action_type=ActionType.OPEN_URL, target=url)
                plan = ActionPlan(thought="Abrir URL via Live Voice", actions=[desktop_act])
                reports = self.executor.execute_plan(plan)
                rep = reports[0] if reports else None
                return {"success": rep.success if rep else True, "message": rep.message if rep else f"URL {url} aberta."}

            elif name == "get_system_info":
                profile = self.executor.memory.get_system_profile() if hasattr(self.executor, "memory") else {}
                stats = self.executor.memory.get_action_stats() if hasattr(self.executor, "memory") else {}
                import psutil
                vm = psutil.virtual_memory()
                cpu = psutil.cpu_percent(interval=0.1)
                return {
                    "success": True,
                    "os": profile.get("os_name", "Zorin OS 18"),
                    "cpu_usage_percent": cpu,
                    "ram_used_gb": round((vm.total - vm.available) / (1024 ** 3), 1),
                    "ram_total_gb": round(vm.total / (1024 ** 3), 1),
                    "battery": profile.get("battery_status", "AC Conectado"),
                }

            elif name == "web_search":
                query = args.get("query", "").strip()
                client = WebSearchClient()
                results = client.search(query, max_results=3)
                formatted = [
                    {"title": r.title, "url": r.url, "snippet": r.snippet[:150]}
                    for r in results
                ]
                return {"success": True, "results": formatted}

            elif name == "media_control":
                from ..core.media import MediaPlayerManager
                act = args.get("action", "play_pause")
                player = args.get("player")
                ok, msg = MediaPlayerManager.control(act, player_name=player)
                return {"success": ok, "message": msg}

            elif name == "write_document":
                from ..core.files import FileManager
                filename = args.get("filename", "documento.md")
                content = args.get("content", "")
                directory = args.get("directory")
                ok, msg, path = FileManager.write_document(filename, content, directory=directory)
                return {"success": ok, "message": msg, "path": path}

            elif name == "organize_directory":
                from ..core.files import FileManager
                directory = args.get("directory", "~/Downloads")
                dry_run = bool(args.get("dry_run", False))
                ok, msg, stats = FileManager.organize_directory(directory=directory, dry_run=dry_run)
                return {"success": ok, "message": msg, "stats": stats}

            elif name == "screen_fence_control":
                target = args.get("monitor", "primary")
                ok = self.fence.set_active_monitor(target)
                m = self.fence.get_active_monitor()
                name_str = m.name if m else target
                return {
                    "success": ok,
                    "message": f"Cerca de tela definida para '{name_str}'." if ok else f"Monitor '{target}' não localizado.",
                }

            elif name == "mouse_click":
                x = float(args.get("x", 0.0))
                y = float(args.get("y", 0.0))
                is_rel = bool(args.get("is_relative", False))
                btn = args.get("button", "left")
                double = bool(args.get("double", False))

                # Se for relativo ou se os valores estiverem entre 0.0 e 1.0
                if is_rel or (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                    ok, msg = self.input_driver.click_relative(x, y, button=btn, double=double)
                else:
                    ok, msg = self.input_driver.click(int(x), int(y), button=btn, double=double)
                return {"success": ok, "message": msg}

            elif name == "keyboard_type":
                text = args.get("text", "")
                enter = bool(args.get("press_enter", False))
                ok, msg = self.input_driver.type_text(text, press_enter=enter)
                return {"success": ok, "message": msg}

            elif name == "keyboard_hotkey":
                keys = args.get("keys", [])
                ok, msg = self.input_driver.hotkey(*keys)
                return {"success": ok, "message": msg}

            elif name == "contact_lookup":
                q = args.get("query", "").strip()
                contacts = self.memory.find_contact(q)
                formatted = [
                    {"name": c["name"], "email": c["email"], "aliases": c.get("aliases", []), "notes": c.get("notes", "")}
                    for c in contacts
                ]
                if formatted:
                    return {"success": True, "contacts": formatted, "message": f"{len(formatted)} contato(s) localizado(s)."}
                return {
                    "success": False,
                    "contacts": [],
                    "message": f"Nenhum contato encontrado para '{q}'. Pergunte ao usuário o e-mail ou se deseja salvá-lo.",
                }

            elif name == "contact_save":
                c_name = args.get("name", "").strip()
                c_email = args.get("email", "").strip()
                c_aliases = args.get("aliases", [])
                c_notes = args.get("notes", "").strip()
                saved = self.memory.save_contact(name=c_name, email=c_email, aliases=c_aliases, notes=c_notes)
                return {"success": True, "contact": saved, "message": f"Contato '{c_name}' <{c_email}> salvo com sucesso."}

            elif name == "email_compose":
                recip = args.get("recipient", "").strip()
                subj = args.get("subject", "").strip()
                body = args.get("body", "").strip()
                client = args.get("client", "auto")
                ok, msg, data = self.email_mgr.compose(recip, subject=subj, body=body, client=client)
                return {"success": ok, "message": msg, "details": data}

            elif name == "calendar_event":
                act = args.get("action", "create")
                if act == "create":
                    t = args.get("title", "")
                    dt_str = args.get("datetime_str", "amanhã às 10h")
                    dur = int(args.get("duration_minutes", 60))
                    desc = args.get("description", "")
                    loc = args.get("location", "")
                    ok, msg, data = self.cal_mgr.create_event(t, dt_str, duration_minutes=dur, description=desc, location=loc)
                    return {"success": ok, "message": msg, "event": data}
                elif act == "list":
                    day = args.get("datetime_str", "today")
                    events = self.cal_mgr.list_events(day)
                    return {"success": True, "events": events, "count": len(events)}
                elif act == "delete":
                    eid = args.get("event_id", "")
                    ok = self.cal_mgr.delete_event(eid)
                    return {"success": ok, "message": f"Compromisso {eid} removido." if ok else "Evento não encontrado."}

            elif name == "browser_search":
                q = args.get("query", "").strip()
                eng = args.get("engine", "google")
                ok, msg, url = BrowserManager.search(q, engine=eng)
                return {"success": ok, "message": msg, "url": url}

            elif name == "search_documents":
                q = args.get("query", "").strip()
                lim = int(args.get("limit", 4))
                results = self.rag.search(q, limit=lim)
                formatted = [r.to_dict() for r in results]
                if formatted:
                    citations = "\n".join([r.format_citation() for r in results])
                    return {
                        "success": True,
                        "count": len(formatted),
                        "results": formatted,
                        "citations": citations,
                        "message": f"{len(formatted)} trecho(s) relevante(s) encontrado(s) nos seus documentos.",
                    }
                return {
                    "success": False,
                    "count": 0,
                    "results": [],
                    "message": f"Nenhum documento encontrado para a busca '{q}'.",
                }

            elif name == "read_document_page":
                fpath = args.get("file_path", "").strip()
                pnum = int(args.get("page_number", 1))
                page_text = self.rag.read_document_page(fpath, page_number=pnum)
                if page_text:
                    return {
                        "success": True,
                        "page_number": pnum,
                        "file_path": fpath,
                        "content": page_text,
                        "message": f"Página {pnum} lida com sucesso ({len(page_text)} caracteres).",
                    }
                return {
                    "success": False,
                    "message": f"Não foi possível ler a página {pnum} do arquivo '{fpath}'.",
                }

            elif name == "open_document_file":
                fpath = args.get("file_path", "").strip()
                pnum = int(args.get("page_number", 1))
                ok, msg = self.rag.open_document(fpath, page_number=pnum)
                return {"success": ok, "message": msg}

            return {"success": False, "message": f"Ferramenta desconhecida: {name}"}

        except Exception as exc:
            logger.error(f"Erro ao despachar ferramenta {name}: {exc}")
            return {"success": False, "message": f"Erro de execução: {exc}"}

    def send_screen_frame(self) -> bool:
        """Captura um snapshot da tela e injeta no fluxo visual do Gemini Live."""
        if not self._is_running or not self._ws or not self._loop:
            return False

        def capture_and_send():
            ok, img_bytes, _ = ScreenCaptureService.capture(interactive=False, max_size=1024, quality=75)
            if not ok or not img_bytes:
                return

            b64_img = base64.b64encode(img_bytes).decode("utf-8")
            frame_msg = {
                "realtimeInput": {
                    "mediaChunks": [
                        {
                            "mimeType": "image/jpeg",
                            "data": b64_img,
                        }
                    ]
                }
            }
            asyncio.run_coroutine_threadsafe(self._ws.send(json.dumps(frame_msg)), self._loop)

        threading.Thread(target=capture_and_send, daemon=True).start()
        return True

    def is_video_streaming(self) -> bool:
        """Verifica se o streaming contínuo da tela está ativo."""
        return self._is_video_streaming

    def start_video_stream(self, fps: float = 1.0) -> bool:
        """Inicia streaming contínuo da tela para o Gemini Live com consentimento explícito."""
        if not self._is_running:
            return False
        if self._is_video_streaming:
            return True

        self._is_video_streaming = True
        if self.on_video_state_change:
            try:
                self.on_video_state_change(True)
            except Exception as exc:
                logger.error(f"Erro no callback on_video_state_change: {exc}")

        self._video_thread = threading.Thread(
            target=self._video_stream_worker,
            args=(fps,),
            daemon=True,
            name="GeminiLiveVideoWorker",
        )
        self._video_thread.start()
        return True

    def stop_video_stream(self) -> None:
        """Pausa/interrompe o streaming contínuo da tela."""
        if not self._is_video_streaming:
            return
        self._is_video_streaming = False
        if self.on_video_state_change:
            try:
                self.on_video_state_change(False)
            except Exception as exc:
                logger.error(f"Erro no callback on_video_state_change: {exc}")

    def toggle_video_stream(self, fps: float = 1.0) -> bool:
        """Alterna o streaming contínuo da tela e retorna o novo estado (True = ativo)."""
        if self._is_video_streaming:
            self.stop_video_stream()
            return False
        else:
            return self.start_video_stream(fps=fps)

    def _video_stream_worker(self, fps: float = 1.0) -> None:
        """Loop em background que captura e transmite frames de tela para o WebSocket da Live API."""
        delay = max(0.5, 1.0 / max(0.2, fps))
        while self._is_video_streaming and self._is_running:
            if self._ws and self._loop and self._loop.is_running():
                try:
                    ok, img_bytes, _ = ScreenCaptureService.capture(
                        interactive=False, max_size=1024, quality=70
                    )
                    if ok and img_bytes and self._is_video_streaming and self._is_running:
                        b64_img = base64.b64encode(img_bytes).decode("utf-8")
                        frame_msg = {
                            "realtimeInput": {
                                "mediaChunks": [
                                    {
                                        "mimeType": "image/jpeg",
                                        "data": b64_img,
                                    }
                                ]
                            }
                        }
                        asyncio.run_coroutine_threadsafe(
                            self._ws.send(json.dumps(frame_msg)), self._loop
                        )
                        self._video_frames_count += 1
                except Exception as exc:
                    logger.debug(f"Erro no envio de frame de vídeo contínuo: {exc}")

            time.sleep(delay)

        self._is_video_streaming = False
        if self.on_video_state_change:
            try:
                self.on_video_state_change(False)
            except Exception:
                pass

