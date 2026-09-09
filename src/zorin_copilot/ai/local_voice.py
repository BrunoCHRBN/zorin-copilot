# Decisão de design: Pipeline de voz 100% local, contínuo e soberano (offline) utilizando
# faster-whisper (STT na CPU via INT8) e Piper TTS (voz neural brasileira masculina pt_BR-faber-medium
# em CPU via AVX2), consumindo 0 MB de VRAM da GPU para preservar a AMD RX 7600 exclusivamente
# para o LLM local (Qwen 2.5 7B / MiniCPM-V) e o compositor gráfico GNOME Wayland.

"""Módulo de processamento de voz local contínua (Whisper STT + Piper TTS) do Zorin Copilot."""

from __future__ import annotations

import collections
import io
import json
import logging
import math
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import numpy as np
except ImportError:
    np = None

from ..core.config import CopilotConfig
from .actions import ActionPlan, ActionType, DesktopAction
from .engine import IntentEngine
from .live import LiveVoiceState

logger = logging.getLogger(__name__)

# Diretórios padrão para modelos de voz locais
MODELS_BASE_DIR = Path(os.path.expanduser("~/.local/share/zorin-copilot/models"))
PIPER_MODELS_DIR = MODELS_BASE_DIR / "piper"
WHISPER_MODELS_DIR = MODELS_BASE_DIR / "whisper"

# URLs oficiais para download automático do modelo Piper pt_BR-faber-medium
PIPER_VOICE_NAME = "pt_BR-faber-medium"
PIPER_MODEL_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/{PIPER_VOICE_NAME}.onnx"
PIPER_CONFIG_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/{PIPER_VOICE_NAME}.onnx.json"


def clean_text_for_speech(text: str) -> str:
    """Higieniza texto bruto do LLM para leitura natural pelo sintetizador de voz (TTS).
    
    Remove marcações markdown, blocos de código, URLs, citações técnicas, colchetes e emojis
    para evitar que o sintetizador soletre caracteres técnicos.
    """
    if not text:
        return ""

    # Remove blocos de código markdown (```bash ... ```)
    cleaned = re.sub(r"```[\s\S]*?```", " código de terminal executado no sistema. ", text)
    # Remove trechos em código inline (`comando`)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)

    # Remove badges do Copilot (ex: ⚡ *[Modo Local]*, ⚡ *[Visão Computacional Local - ...]* )
    cleaned = re.sub(r"⚡\s*\*\[.*?\]\*", "", cleaned)
    cleaned = re.sub(r"\[⚡.*?\]", "", cleaned)

    # Remove citações numéricas ou de fonte (ex: [1], [2], [fonte: ...], [link])
    cleaned = re.sub(r"\[\s*\d+\s*\]", "", cleaned)
    cleaned = re.sub(r"\[\s*(?:fonte|link|url|ref)[^\]]*\]", "", cleaned, flags=re.IGNORECASE)

    # Remove URLs e links markdown [texto](url)
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+", "o site indicado", cleaned)

    # Remove títulos markdown (#, ##, ###)
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)

    # Remove marcadores de lista (- , * , 1. )
    cleaned = re.sub(r"^\s*[-*•]\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*\d+\.\s*", "", cleaned, flags=re.MULTILINE)

    # Remove negrito e itálico (**texto**, *texto*, __texto__, _texto_)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = re.sub(r"_([^_]+)_", r"\1", cleaned)

    # Remove caracteres especiais ou emojis comuns
    cleaned = re.sub(r"[🎙️⚡⚙️💻🔍💡🛠️📦📁🚀✨🔴🟢🟡⚠️❌✅💬]", "", cleaned)

    # Remove colchetes ou caracteres residuais isolados
    cleaned = re.sub(r"[\[\]{}<>]", " ", cleaned)

    # Normaliza múltiplos espaços e quebras de linha
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def build_spoken_response(
    text: str,
    executable_actions: Optional[list[DesktopAction]] = None,
    max_sentences: int = 2,
    max_chars: int = 220,
) -> str:
    """Gera uma resposta vocal concisa, fluida e executiva para o sintetizador de voz (TTS).
    
    Se uma ação no desktop (como abrir URL, abrir app ou alterar configuração) foi executada,
    evita ler trechos crus de busca web, rodapés de sites ou menus de navegação,
    fornecendo uma confirmação vocal direta e natural (1 a 2 frases curtas).
    
    Para respostas puramente informativas sem ações, limita a fala aos primeiros enunciados
    essenciais (até 2 frases ou ~220 caracteres), preservando a agilidade e naturalidade da conversa.
    """
    if not text and not executable_actions:
        return "Comando processado com sucesso."

    # 1. Se ações foram executadas no sistema
    if executable_actions:
        primary_action = executable_actions[0]
        act_type = primary_action.action_type

        # Isola introdução limpa antes de citações ([1], [2]), URLs ou blocos brutos
        raw_intro = ""
        if text:
            first_chunk = re.split(r":\s*\[|\b\[\d+\]|\n", text)[0].strip()
            first_chunk = re.sub(r"[:\-–—\s]+$", "", first_chunk).strip()
            # Garante que não é um trecho bruto de snippet ou citação
            if len(first_chunk) >= 6 and not first_chunk.startswith(("[", "http")):
                raw_intro = clean_text_for_speech(first_chunk)

        already_mentions_open = bool(
            raw_intro and re.search(r"\b(abrir?|aberto|abriu|acessei|acessar|iniciei|iniciar|abrindo)\b", raw_intro, re.I)
        )

        if act_type == ActionType.OPEN_URL:
            if raw_intro and len(raw_intro) <= 120:
                if already_mentions_open:
                    return raw_intro if raw_intro.endswith((".", "!", "?")) else f"{raw_intro}."
                return f"{raw_intro}. Abri a página no seu navegador."
            return "Abri a página solicitada no seu navegador."

        if act_type == ActionType.LAUNCH_APP:
            if raw_intro and len(raw_intro) <= 120:
                return raw_intro if raw_intro.endswith((".", "!", "?")) else f"{raw_intro}."
            target_name = primary_action.target or "o aplicativo"
            return f"Abri {target_name} para você."

        if act_type == ActionType.OPEN_DOCUMENT:
            if raw_intro and len(raw_intro) <= 120:
                return raw_intro if raw_intro.endswith((".", "!", "?")) else f"{raw_intro}."
            return "Abri o documento para você."

        if act_type in (ActionType.SYSTEM_CONTROL, ActionType.MEDIA_CONTROL):
            if raw_intro and len(raw_intro) <= 120:
                return raw_intro if raw_intro.endswith((".", "!", "?")) else f"{raw_intro}."
            if primary_action.description:
                return clean_text_for_speech(primary_action.description)
            return "Configuração ajustada no sistema."

        if act_type == ActionType.FIX_COMMAND:
            if raw_intro and len(raw_intro) <= 140:
                return raw_intro if raw_intro.endswith((".", "!", "?")) else f"{raw_intro}."
            return "Comando de terminal preparado para execução."

        # Outras ações
        if raw_intro and len(raw_intro) <= 120:
            return raw_intro if raw_intro.endswith((".", "!", "?")) else f"{raw_intro}."
        if primary_action.description:
            return clean_text_for_speech(primary_action.description)
        return "Ação executada com sucesso no sistema."

    # 2. Respostas puramente informativas (sem ações no desktop)
    cleaned = clean_text_for_speech(text)
    if not cleaned:
        return "Comando concluído."

    if len(cleaned) <= max_chars:
        return cleaned

    # Divide em sentenças terminadas em pontuação (. ? !)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    if not sentences:
        truncated = cleaned[:max_chars].rsplit(" ", 1)[0]
        return f"{truncated}..."

    selected_sentences: list[str] = []
    current_len = 0
    for s in sentences:
        if len(selected_sentences) >= max_sentences:
            break
        if current_len + len(s) > max_chars and selected_sentences:
            break
        selected_sentences.append(s)
        current_len += len(s) + 1

    result = " ".join(selected_sentences).strip()
    if not result.endswith((".", "!", "?")):
        result += "."
    return result


class LocalVoiceSynthesizer:
    """Sintetizador neural de voz offline baseado no Piper TTS rodando na CPU via AVX2."""

    def __init__(self, voice_name: str = PIPER_VOICE_NAME) -> None:
        self.voice_name = voice_name
        self.model_path = PIPER_MODELS_DIR / f"{voice_name}.onnx"
        self.config_path = PIPER_MODELS_DIR / f"{voice_name}.onnx.json"
        self._voice: Any = None
        self._lock = threading.Lock()
        self._playback_proc: Optional[subprocess.Popen] = None
        self._is_speaking = False
        self._stop_event = threading.Event()

    def ensure_model_available(self) -> bool:
        """Verifica se os arquivos do modelo estão disponíveis ou faz o download."""
        if self.model_path.exists() and self.config_path.exists():
            return True

        PIPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            import urllib.request
            logger.info(f"Baixando modelo de voz Piper '{self.voice_name}'...")
            if not self.model_path.exists():
                urllib.request.urlretrieve(PIPER_MODEL_URL, str(self.model_path))
            if not self.config_path.exists():
                urllib.request.urlretrieve(PIPER_CONFIG_URL, str(self.config_path))
            logger.info("Modelo Piper baixado com sucesso.")
            return True
        except Exception as exc:
            logger.error(f"Falha ao baixar modelo de voz Piper: {exc}")
            return False

    def load(self) -> bool:
        """Carrega a rede neural do Piper na memória (se ainda não carregada)."""
        if self._voice is not None:
            return True

        if not self.ensure_model_available():
            return False

        try:
            from piper import PiperVoice
            with self._lock:
                self._voice = PiperVoice.load(str(self.model_path), config_path=str(self.config_path))
            logger.info(f"Piper TTS carregado com sucesso: {self.voice_name}")
            return True
        except Exception as exc:
            logger.error(f"Erro ao instanciar PiperVoice: {exc}")
            return False

    def speak(self, text: str, on_done: Optional[Callable[[], None]] = None) -> bool:
        """Sintetiza e reproduz o texto de forma síncrona/streaming no processo de playback.
        
        Pode ser interrompido imediatamente chamando interrupt().
        """
        cleaned = clean_text_for_speech(text)
        if not cleaned:
            if on_done:
                on_done()
            return False

        if not self.load():
            logger.warning("Piper não disponível para reproduzir fala.")
            if on_done:
                on_done()
            return False

        self._stop_event.clear()
        self._is_speaking = True

        # Determina o comando de reprodução de áudio (PipeWire pw-play ou ALSA aplay)
        if shutil.which("pw-play"):
            cmd = ["pw-play", "--raw", "--rate", "22050", "--channels", "1", "--format", "s16", "-"]
        elif shutil.which("aplay"):
            cmd = ["aplay", "-r", "22050", "-c", "1", "-f", "S16_LE", "-"]
        else:
            logger.error("Nenhum reprodutor de áudio (pw-play ou aplay) encontrado.")
            self._is_speaking = False
            if on_done:
                on_done()
            return False

        try:
            self._playback_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Sintetiza chunks do Piper em streaming e escreve diretamente no stdin do tocador
            for chunk in self._voice.synthesize(cleaned):
                if self._stop_event.is_set():
                    break
                if self._playback_proc and self._playback_proc.stdin:
                    try:
                        self._playback_proc.stdin.write(chunk.audio_int16_bytes)
                    except (BrokenPipeError, OSError):
                        break

            if self._playback_proc and self._playback_proc.stdin:
                try:
                    self._playback_proc.stdin.close()
                except Exception:
                    pass

            if self._playback_proc and not self._stop_event.is_set():
                self._playback_proc.wait()

        except Exception as exc:
            logger.warning(f"Erro durante síntese ou reprodução do Piper: {exc}")
        finally:
            self._cleanup_proc()
            self._is_speaking = False
            if on_done:
                on_done()

        return True

    def interrupt(self) -> None:
        """Barge-in: Interrompe a reprodução de áudio imediatamente."""
        self._stop_event.set()
        self._cleanup_proc()
        self._is_speaking = False

    def _cleanup_proc(self) -> None:
        with self._lock:
            if self._playback_proc:
                try:
                    if self._playback_proc.stdin:
                        self._playback_proc.stdin.close()
                    self._playback_proc.terminate()
                    self._playback_proc.wait(timeout=0.2)
                except Exception:
                    try:
                        self._playback_proc.kill()
                    except Exception:
                        pass
                self._playback_proc = None

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking


WHISPER_PHANTOM_HALLUCINATIONS: set[str] = {
    "muito obrigado", "muito obrigada",
    "obrigado", "obrigada",
    "obrigado por assistir", "obrigada por assistir",
    "muito obrigado por assistir", "muito obrigada por assistir",
    "obrigado por assistir a este video", "obrigada por assistir a este video",
    "obrigado por assistir ao video", "obrigada por assistir ao video",
    "inscreva se no canal", "inscreva se", "inscrevam se",
    "deixe seu like", "deixe o seu like", "deixa o like",
    "compartilhe", "compartilhe este video",
    "ate a proxima", "ate o proximo video",
    "tchau tchau", "tchau", "valeu", "valeu falou",
    "amem",
    "legendas pela comunidade", "subtitles by", "subtitles",
    "voce",
}


class LocalVoiceTranscriber:
    """Transcritor de fala (STT) offline baseado em faster-whisper rodando na CPU via INT8."""

    def __init__(self, model_size: str = "small") -> None:
        self.model_size = model_size
        self._model: Any = None
        self._lock = threading.Lock()

    def load(self) -> bool:
        """Carrega o modelo do Whisper."""
        if self._model is not None:
            return True

        WHISPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            from faster_whisper import WhisperModel
            with self._lock:
                self._model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(WHISPER_MODELS_DIR),
                )
            logger.info(f"faster-whisper '{self.model_size}' (INT8 CPU) carregado com sucesso.")
            return True
        except Exception as exc:
            logger.error(f"Erro ao carregar modelo faster-whisper: {exc}")
            return False

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcreve bytes PCM brutos (16-bit mono 16kHz) em texto em português com salvaguardas anti-alucinação."""
        if not pcm_bytes or len(pcm_bytes) < 3200:
            return ""

        if not self.load():
            return ""

        try:
            # Converte bytes int16 para array numpy float32 normalizado (-1.0 a 1.0)
            if np is not None:
                audio_f32 = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            else:
                import array
                audio_f32 = [x / 32768.0 for x in array.array("h", pcm_bytes)]

            segments, _info = self._model.transcribe(
                audio_f32,
                language="pt",
                beam_size=5,
                best_of=5,
                temperature=0.0,
                condition_on_previous_text=False,
                hallucination_silence_threshold=0.8,
                no_speech_threshold=0.45,
                log_prob_threshold=-1.0,
                compression_ratio_threshold=2.4,
                initial_prompt="Zorin Copilot. Assistente de inteligência artificial do sistema operacional Zorin OS.",
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300),
            )

            text_parts = []
            for seg in segments:
                t = seg.text.strip()
                if not t:
                    continue
                # Filtra alucinações de ruído estático ou silêncio com alta probabilidade de não-fala
                no_speech = getattr(seg, "no_speech_prob", None)
                if isinstance(no_speech, (int, float)) and no_speech > 0.35:
                    logger.debug(f"Segmento ignorado por no_speech_prob={no_speech:.2f}: '{t}'")
                    continue
                avg_logprob = getattr(seg, "avg_logprob", None)
                if isinstance(avg_logprob, (int, float)) and avg_logprob < -1.10:
                    logger.debug(f"Segmento ignorado por avg_logprob={avg_logprob:.2f}: '{t}'")
                    continue
                text_parts.append(t)

            full_text = " ".join(text_parts).strip()
            if not full_text or len(full_text) < 2:
                return ""

            # Filtro contra alucinações fantasmas conhecidas em português (remove acentos e símbolos)
            import unicodedata
            nfkd = unicodedata.normalize("NFKD", full_text.lower())
            ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
            normalized = " ".join(re.sub(r"[^\w\s]", " ", ascii_text).split())
            if normalized in WHISPER_PHANTOM_HALLUCINATIONS:
                logger.info(f"Filtro Anti-Alucinação: Descartada frase fantasma do Whisper: '{full_text}'")
                return ""

            return full_text
        except Exception as exc:
            logger.warning(f"Erro na transcrição local faster-whisper: {exc}")
            return ""


class LocalLiveVoiceClient:
    """Cliente de voz contínua 100% offline do Zorin Copilot.
    
    Implementa o mesmo protocolo e callbacks de interface que o GeminiLiveClient:
    - Escuta ativa contínua via microfone (PipeWire pw-record)
    - Voice Activity Detection (VAD) com histerese e detecção de pausas
    - Interrupção fluida (Barge-in) se o usuário falar durante a resposta do assistente
    - Roteamento para o IntentEngine local (Qwen 2.5 7B) e execução de ações do sistema
    - Síntese com voz neural brasileira masculina (Piper TTS)
    """

    def __init__(
        self,
        config: CopilotConfig,
        executor: Any,
        memory: Any = None,
        engine: Optional[IntentEngine] = None,
    ) -> None:
        self.config = config
        self.executor = executor
        self.memory = memory
        self.engine = engine or IntentEngine(config=config, memory=memory)

        # Componentes de áudio neural local
        self.synthesizer = LocalVoiceSynthesizer(voice_name=getattr(config, "piper_voice_model", PIPER_VOICE_NAME))
        self.transcriber = LocalVoiceTranscriber(model_size=getattr(config, "whisper_model", "small"))

        # Estado e ciclo de vida
        self.state = LiveVoiceState.DISCONNECTED
        self._is_running = False
        self._is_muted = False
        self._record_proc: Optional[subprocess.Popen] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._session_start_time = 0.0

        # Buffer circular de pré-fala (pre-roll de 400ms) para nunca cortar o início das palavras
        self._preroll_buffer: collections.deque[bytes] = collections.deque(maxlen=4)

        # VAD & Buffer de Fala
        self._speech_buffer: list[bytes] = []
        self._is_user_speaking = False
        self._silence_chunks = 0
        self._barge_in_chunks = 0
        self.silence_threshold = 0.08  # RMS normalizado sensível calibrado para voz humana
        self.silence_chunks_timeout = 8  # 8 * 100ms = 800ms de silêncio para fechar turno
        self.min_speech_chunks = 6  # Mínimo de 600ms de fala para evitar ruído de respiração ou clique

        # Histórico da sessão para persistência
        self._executed_actions_log: list[dict[str, Any]] = []
        self._transcripts_log: list[dict[str, str]] = []
        self._video_frames_count = 0

        # Atributos auxiliares esperados pela UI
        self.rag = None
        self.fence = None
        self.input_driver = None

        # Callbacks registrados pela interface (LiveVoiceWidget)
        self.on_state_change: Optional[Callable[[LiveVoiceState, str], None]] = None
        self.on_audio_level: Optional[Callable[[float], None]] = None
        self.on_tool_executed: Optional[Callable[[str, str, bool], None]] = None
        self.on_transcript: Optional[Callable[[str, str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_user_message: Optional[Callable[[str], None]] = None
        self.on_agent_message: Optional[Callable[[str], None]] = None
        self.on_video_state_change: Optional[Callable[[bool], None]] = None
        self.on_video_frame_preview: Optional[Callable[[bytes], None]] = None
        self.on_privacy_state_change: Optional[Callable[[bool, str], None]] = None
        self.on_window_focus_change: Optional[Callable[[str, str], None]] = None

    def _set_state(self, state: LiveVoiceState, message: str = "") -> None:
        self.state = state
        if self.on_state_change:
            try:
                self.on_state_change(state, message)
            except Exception as exc:
                logger.warning(f"Erro no callback on_state_change: {exc}")

    def start(self) -> None:
        """Inicia o ciclo de captura e conversação contínua de voz."""
        if self._is_running:
            return

        self._is_running = True
        self._session_start_time = time.time()
        self._set_state(LiveVoiceState.CONNECTING, "Iniciando motor de voz local offline...")

        # Pré-carrega Piper e Whisper em segundo plano para não congelar o thread principal
        threading.Thread(target=self._preload_models, daemon=True).start()

        # Inicia thread de escuta do microfone
        self._worker_thread = threading.Thread(target=self._mic_loop, daemon=True)
        self._worker_thread.start()

    def _preload_models(self) -> None:
        try:
            self.synthesizer.load()
            self.transcriber.load()
            self._set_state(LiveVoiceState.LISTENING, "Pronto! Pode falar...")
        except Exception as exc:
            logger.error(f"Erro ao inicializar modelos locais de voz: {exc}")
            self._set_state(LiveVoiceState.ERROR, f"Falha nos modelos de voz: {exc}")

    def stop(self) -> None:
        """Interrompe e encerra a sessão de voz."""
        self._is_running = False
        self.synthesizer.interrupt()

        if self._record_proc:
            try:
                self._record_proc.terminate()
                self._record_proc.wait(timeout=0.3)
            except Exception:
                try:
                    self._record_proc.kill()
                except Exception:
                    pass
            self._record_proc = None

        self._set_state(LiveVoiceState.DISCONNECTED, "Chamada de voz encerrada.")

    def is_active(self) -> bool:
        """Verifica se o cliente de voz está ativo."""
        return self._is_running and self.state != LiveVoiceState.DISCONNECTED

    def is_video_streaming(self) -> bool:
        return False

    def is_muted(self) -> bool:
        return self._is_muted

    def toggle_mute(self) -> bool:
        self._is_muted = not self._is_muted
        if self._is_muted:
            self.synthesizer.interrupt()
            self._speech_buffer.clear()
            self._set_state(LiveVoiceState.LISTENING, "Microfone mutado.")
        else:
            self._set_state(LiveVoiceState.LISTENING, "Ouvindo microfone...")
        return self._is_muted

    def interrupt(self) -> None:
        """Interrompe a fala atual da IA (barge-in)."""
        self.synthesizer.interrupt()
        if self.state == LiveVoiceState.SPEAKING:
            self._set_state(LiveVoiceState.LISTENING, "Ouvindo...")

    def _mic_loop(self) -> None:
        """Lê áudio contínuo do microfone via PipeWire (pw-record) com VAD e detecção de fim de fala."""
        if shutil.which("pw-record"):
            cmd = ["pw-record", "--raw", "--rate", "16000", "--channels", "1", "--format", "s16", "-"]
        elif shutil.which("arecord"):
            cmd = ["arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-"]
        else:
            err = "Nenhum gravador de áudio (pw-record ou arecord) disponível no sistema."
            logger.error(err)
            self._set_state(LiveVoiceState.ERROR, err)
            if self.on_error:
                self.on_error(err)
            return

        try:
            self._record_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            err = f"Falha ao iniciar microfone: {exc}"
            logger.error(err)
            self._set_state(LiveVoiceState.ERROR, err)
            if self.on_error:
                self.on_error(err)
            return

        chunk_size = 3200  # 100ms de áudio a 16kHz s16 mono (1600 amostras * 2 bytes)

        while self._is_running and self._record_proc and self._record_proc.poll() is None:
            try:
                pcm_chunk = self._record_proc.stdout.read(chunk_size)
            except Exception:
                break

            if not pcm_chunk:
                time.sleep(0.02)
                continue

            # Calcula volume RMS para o visualizador de onda e VAD
            num_samples = len(pcm_chunk) // 2
            norm_level = 0.0
            if num_samples > 0:
                try:
                    samples = struct.unpack(f"<{num_samples}h", pcm_chunk)
                    rms = math.sqrt(sum(s * s for s in samples) / num_samples)
                    if rms > 70.0:
                        norm_level = min(1.0, ((rms - 70.0) / 1800.0) ** 0.7)
                    else:
                        norm_level = 0.0
                except Exception:
                    norm_level = 0.0

            if self.on_audio_level:
                try:
                    self.on_audio_level(norm_level)
                except Exception:
                    pass

            if self._is_muted:
                continue

            # Isolamento acústico e anti-eco: Enquanto o assistente fala ou processa,
            # ignoramos a entrada do microfone para evitar auto-interrupção e eco dos alto-falantes.
            if self.state in (LiveVoiceState.SPEAKING, LiveVoiceState.THINKING, LiveVoiceState.EXECUTING):
                self._speech_buffer.clear()
                self._preroll_buffer.clear()
                self._is_user_speaking = False
                self._silence_chunks = 0
                continue

            # VAD no estado de escuta
            if self.state in (LiveVoiceState.LISTENING, LiveVoiceState.CONNECTED):
                if norm_level > self.silence_threshold:
                    if not self._is_user_speaking:
                        self._is_user_speaking = True
                        # Resgata os últimos 400ms gravados antes do limiar ser atingido (pre-roll)
                        self._speech_buffer = list(self._preroll_buffer)
                        self._silence_chunks = 0
                    self._speech_buffer.append(pcm_chunk)
                    self._silence_chunks = 0
                else:
                    self._preroll_buffer.append(pcm_chunk)
                    if self._is_user_speaking:
                        self._speech_buffer.append(pcm_chunk)
                        self._silence_chunks += 1
                        # Pausa prolongada detectada -> Fim de frase do usuário
                        if self._silence_chunks >= self.silence_chunks_timeout:
                            self._is_user_speaking = False
                            if len(self._speech_buffer) >= self.min_speech_chunks:
                                full_pcm = b"".join(self._speech_buffer)
                                self._speech_buffer = []
                                self._preroll_buffer.clear()

                                # Validação acústica de energia: descarta ruído de fundo/respiração
                                num_samples = len(full_pcm) // 2
                                if num_samples > 0:
                                    try:
                                        s_arr = struct.unpack(f"<{num_samples}h", full_pcm)
                                        buf_rms = math.sqrt(sum(s * s for s in s_arr) / num_samples)
                                        if buf_rms < 125.0:
                                            logger.debug(f"Buffer de voz descartado por baixa energia (RMS {buf_rms:.1f} < 125).")
                                            continue
                                    except Exception:
                                        pass

                                # Dispara processamento em thread separada para não bloquear leitura do mic
                                threading.Thread(target=self._process_utterance, args=(full_pcm,), daemon=True).start()
                            else:
                                self._speech_buffer = []

    def _process_utterance(self, pcm_data: bytes) -> None:
        """Processa a fala do usuário: Whisper STT -> Qwen 2.5 IntentEngine -> Piper TTS."""
        try:
            self._set_state(LiveVoiceState.THINKING, "Transcrevendo fala...")

            # 1. Transcrição com faster-whisper (modelo small INT8 com prompt conditioning)
            user_text = self.transcriber.transcribe_pcm(pcm_data)
            if not user_text or len(user_text.strip()) < 2:
                self._set_state(LiveVoiceState.LISTENING, "Ouvindo...")
                return

            logger.info(f"Voz transcrita: '{user_text}'")
            self._transcripts_log.append({"role": "user", "text": user_text})

            if self.on_transcript:
                try:
                    self.on_transcript("user", user_text)
                except Exception as exc:
                    logger.warning(f"Erro no callback on_transcript(user): {exc}")

            if self.on_user_message:
                try:
                    self.on_user_message(user_text)
                except Exception as exc:
                    logger.warning(f"Erro no callback on_user_message: {exc}")

            # 2. Raciocínio e Geração de Ações via IntentEngine
            self._set_state(LiveVoiceState.THINKING, "Processando solicitação...")
            try:
                plan: ActionPlan = self.engine.parse(user_text)
            except Exception as exc:
                logger.error(f"Erro no IntentEngine ao processar voz: {exc}")
                plan = ActionPlan(
                    thought="Desculpe, tive uma dificuldade momentânea ao processar sua solicitação.",
                    actions=[],
                )

            # 3. Execução de Ações do Desktop (apenas ações reais, sem ANSWER ou SMART_OCR)
            executable_actions = [
                a for a in plan.actions
                if a.action_type not in (ActionType.ANSWER, ActionType.SMART_OCR)
            ]
            # Salvaguarda: se o modelo propuser abrir mais de 3 aplicativos simultaneamente, trata como informativo
            if len(executable_actions) > 3 and all(a.action_type == ActionType.LAUNCH_APP for a in executable_actions):
                logger.info("Múltiplas ações de launch_app detectadas como lista informativa; ignorando execução em massa.")
                executable_actions = []

            if executable_actions and self.executor:
                self._set_state(LiveVoiceState.EXECUTING, f"Executando {len(executable_actions)} ação(ões)...")
                for action in executable_actions:
                    try:
                        result = self.executor.execute(action)
                        self._executed_actions_log.append({
                            "type": action.action_type.value,
                            "description": action.description,
                            "success": result.success,
                        })
                        if self.on_tool_executed:
                            try:
                                self.on_tool_executed(action.action_type.value, action.description, result.success)
                            except Exception:
                                pass
                    except Exception as exc:
                        logger.warning(f"Erro ao executar ação de desktop por voz: {exc}")

            # 4. Resposta em Voz (Piper TTS)
            response_text = plan.thought or plan.extracted_text or "Comando processado com sucesso."

            # Higienização da exibição em tela: se abriu URL e a resposta continha citações brutas [1], [2]
            # de raspagem web, condensa para uma confirmação amigável e executiva
            if any(a.action_type == ActionType.OPEN_URL for a in executable_actions):
                if re.search(r":\s*\[|\b\[\d+\]", response_text):
                    first_chunk = re.split(r":\s*\[|\b\[\d+\]|\n", response_text)[0].strip()
                    first_chunk = re.sub(r"[:\-–—\s]+$", "", first_chunk).strip()
                    if len(first_chunk) >= 8:
                        if not re.search(r"\b(abrir?|aberto|abriu|acessei|acessar|abrindo)\b", first_chunk, re.I):
                            response_text = f"{first_chunk}. Abri a página no seu navegador para você conferir as opções."
                        else:
                            response_text = f"{first_chunk}."
                    else:
                        response_text = "Abri a página solicitada no seu navegador para você conferir as opções."

            self._transcripts_log.append({"role": "agent", "text": response_text})

            if self.on_transcript:
                try:
                    self.on_transcript("agent", response_text)
                except Exception as exc:
                    logger.warning(f"Erro no callback on_transcript(agent): {exc}")

            if self.on_agent_message:
                try:
                    self.on_agent_message(response_text)
                except Exception as exc:
                    logger.warning(f"Erro no callback on_agent_message: {exc}")

            # Síntese de voz concisa e fluida via Piper TTS (sem ler rodapés, promoções ou citações técnicas)
            speech_text = build_spoken_response(plan.thought or plan.extracted_text, executable_actions=executable_actions)
            self._set_state(LiveVoiceState.SPEAKING, "Falando...")
            self.synthesizer.speak(speech_text)

        except Exception as exc:
            logger.exception(f"Erro inesperado no processamento de voz: {exc}")
        finally:
            # Echo cooldown: aguarda a reverberação acústica da sala dissipar antes de reabrir o microfone
            time.sleep(0.4)
            self._speech_buffer.clear()
            self._preroll_buffer.clear()
            self._is_user_speaking = False
            self._silence_chunks = 0
            if self._is_running:
                self._set_state(LiveVoiceState.LISTENING, "Ouvindo você...")

    def get_session_summary(self) -> dict[str, Any]:
        """Retorna o resumo estruturado da chamada de voz para consolidação no chat principal."""
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
                or duration >= 3
            ),
        }

    # Stubs de compatibilidade de vídeo para LiveVoiceWidget
    def toggle_video_stream(self, fps: float = 1.0) -> bool:
        return False

    def set_video_mode(self, mode: Any) -> None:
        pass

    def panic_stop_video(self) -> None:
        pass

    def send_screen_frame(self) -> bool:
        return False
