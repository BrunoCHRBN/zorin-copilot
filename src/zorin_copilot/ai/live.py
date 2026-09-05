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

import websockets

from ..core.apps import AppManager
from ..core.config import CopilotConfig
from ..core.vision import ScreenCaptureService
from ..core.web_search import WebSearchClient
from ..shell.executor import ActionExecutor
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
        ]
    }
]


class GeminiLiveClient:
    """Cliente assíncrono para o Gemini Multimodal Live API com áudio full-duplex e tool calling."""

    def __init__(
        self,
        config: CopilotConfig | None = None,
        executor: ActionExecutor | None = None,
    ):
        self.config = config or CopilotConfig.load()
        self.executor = executor or ActionExecutor()
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

                # 1. Envia Handshake de Setup
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
                                    "text": (
                                        "Você é o Zorin Copilot, assistente nativo de voz e visão multimodal do sistema operacional Zorin OS 18 (Linux / GNOME / Wayland). "
                                        "Você conversa por áudio em tempo real com o usuário em português brasileiro. "
                                        "Seja conciso, natural, simpático e direto ao ponto. "
                                        "Você possui ferramentas para controlar mídia e música (Spotify), criar arquivos e relatórios, organizar pastas, abrir programas, ajustar volume, ver a tela e pesquisar na web. "
                                        "Quando o usuário compartilhar a tela ao vivo (Live Video) ou enviar snapshots, você tem visão multimodal direta do que está acontecendo na área de trabalho dele. Você pode ler janelas, erros, códigos, páginas e interagir em tempo real sobre o que está visível. "
                                        "Sempre que o usuário pedir para fazer algo no computador, use imediatamente as ferramentas disponíveis e comente o resultado brevemente."
                                    )
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

