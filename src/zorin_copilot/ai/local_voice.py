# Decisão de design: Pipeline de voz 100% local, contínuo e soberano (offline) utilizando
# faster-whisper (STT na CPU via INT8) e Piper TTS (voz neural brasileira em CPU via AVX2),
# consumindo 0 MB de VRAM da GPU para preservar a AMD RX 7600 exclusivamente para o LLM
# local (Qwen 2.5 7B / MiniCPM-V) e o compositor gráfico GNOME Wayland.
#
# A voz **não** é fixa: a chave vem de `piper_voice_model` e o caminho e a URL são
# derivados dela por `core/identity.voice_urls()`. O padrão é `pt_BR-faber-medium`.

"""Módulo de processamento de voz local contínua (Whisper STT + Piper TTS) do Zorin Copilot."""

from __future__ import annotations

import collections
import importlib.util
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

from ..core.audio.capture import enrich_capture_cmd, get_processed_capture_source
from ..core.config import CopilotConfig
from ..core.identity import DEFAULT_LOCAL_VOICE, voice_urls
from ..core.transcript import ROLE_ASSISTANT, ROLE_USER, make_entry
from .actions import ActionPlan, ActionType, DesktopAction
from .engine import IntentEngine
from .live import LiveVoiceState

logger = logging.getLogger(__name__)

# Diretórios padrão para modelos de voz locais
MODELS_BASE_DIR = Path(os.path.expanduser("~/.local/share/zorin-copilot/models"))
PIPER_MODELS_DIR = MODELS_BASE_DIR / "piper"
WHISPER_MODELS_DIR = MODELS_BASE_DIR / "whisper"

# Voz padrão do Piper. O **nome** é a única fonte do caminho e da URL: a derivação
# mora em `core/identity.voice_urls()`.
#
# Antes havia aqui duas constantes (`PIPER_MODEL_URL` / `PIPER_CONFIG_URL`) com a URL
# do **faber** cravada, enquanto o caminho do arquivo saía de `voice_name`. Era isso
# que fazia pedir `pt_BR-cadu-medium` baixar o faber, salvar com o nome do cadu e
# registrar "carregado com sucesso: pt_BR-cadu-medium" — o log confirmando a voz
# errada. Caminho e URL agora saem do mesmo lugar, então não há como divergirem.
PIPER_VOICE_NAME = DEFAULT_LOCAL_VOICE


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


def piper_available() -> bool:
    """Diz se o motor de voz local (Piper) pode ser carregado.

    Checagem **barata**: pergunta pelo módulo, sem carregar o modelo ONNX (que é
    pesado e por isso é lazy no `speak()`). Existe para que a falta do motor seja
    **dita no arranque** em vez de se manifestar como silêncio a cada anúncio —
    falha silenciosa é o pior defeito desta família de recursos.

    O extra `voice` do `pyproject.toml` é opcional (piper-tts e faster-whisper
    puxam nativos pesados). Sem ele a voz local fica indisponível e o resto do
    app continua funcionando — o que é aceitável, desde que seja visível.
    """
    return importlib.util.find_spec("piper") is not None


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
        """Verifica se os arquivos do modelo estão disponíveis ou faz o download.

        **A URL é derivada do nome da voz** (`core/identity.voice_urls`), e é essa
        derivação que impede o defeito antigo: baixar o modelo de *outra* voz para
        dentro do arquivo com o nome da voz pedida. Uma voz que não existe agora dá
        **404 e uma linha de erro nomeando a URL** — falha visível — em vez de outra
        voz em silêncio.
        """
        if self.model_path.exists() and self.config_path.exists():
            return True

        urls = voice_urls(self.voice_name)
        if urls is None:
            # Recusar é a resposta certa: baixar qualquer coisa aqui seria entregar
            # uma voz diferente da pedida, que é o defeito que este código teve.
            logger.error(
                "Voz Piper '%s' não tem o formato '{locale}-{voz}-{qualidade}' "
                "(ex.: pt_BR-cadu-medium). Não vou baixar nada.",
                self.voice_name,
            )
            return False
        model_url, config_url = urls

        PIPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            import urllib.request
            logger.info(f"Baixando modelo de voz Piper '{self.voice_name}'...")
            if not self.model_path.exists():
                urllib.request.urlretrieve(model_url, str(self.model_path))
            if not self.config_path.exists():
                urllib.request.urlretrieve(config_url, str(self.config_path))
            logger.info("Modelo Piper baixado com sucesso.")
            return True
        except Exception as exc:
            logger.error(
                "Falha ao baixar o modelo de voz Piper '%s' de %s: %s",
                self.voice_name,
                model_url,
                exc,
            )
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

    def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16000,
        initial_prompt: Optional[str] = None,
    ) -> str:
        """Transcreve bytes PCM brutos (16-bit mono 16kHz) em texto em português com salvaguardas anti-alucinação."""
        if not pcm_bytes or len(pcm_bytes) < 3200:
            return ""

        if not self.load():
            return ""

        default_prompt = (
            "Zorin Copilot, assistente do Zorin OS. Linux, Python, Bash, terminal, GNOME, Wayland, "
            "systemctl, git, apt, Docker, kernel, sudo, GitHub, Neovim, VS Code, Flatpak, PipeWire."
        )
        prompt = initial_prompt or default_prompt

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
                initial_prompt=prompt,
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

        # Identidade do cliente para a UI (Entrega C / P1-5). Este modo é 100% offline:
        # não há captura de tela, então a pílula precisa saber disso para **rotular** os
        # recursos de tela como indisponíveis, em vez de mostrar controles mortos.
        # Os stubs de `:1090-1101` continuam existindo como contrato de compatibilidade.
        self.mode: str = "local"
        self.supports_video: bool = False

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

        # Gancho de UI injetado pela janela, com o **mesmo contrato** do caminho
        # Gemini Live (`(problema, grafo, foco) -> dict`). O caminho local não tem
        # `ToolRegistry` (só `ActionPlan`/`ActionType`), então o comando de voz do
        # Quadro Negro não caberia lá — ele entra por este gancho, disparado pelo
        # detector puro `core/study_intent`. Sem gancho, o comando é ignorado e a
        # fala segue o fluxo normal do IntentEngine.
        self.board_opener: Optional[Callable[[str, Optional[Any], str], dict]] = None

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
        # Encerra o gravador corrente; o laço de reconexão de `_mic_loop` vê
        # `_is_running == False` e sai sem tentar reabrir (inclusive durante o backoff).
        self._close_recorder()
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

    # ------------------------------------------------------------------
    # Captura de microfone (com reconexão automática — Entrega B / P0-4)
    # ------------------------------------------------------------------
    def _build_recorder_cmd(self) -> Optional[list[str]]:
        """Monta o comando do gravador (pw-record/arecord) com o source filtrado WS2.

        Retorna ``None`` se não houver gravador de áudio no sistema.
        """
        if shutil.which("pw-record"):
            cmd = ["pw-record", "--raw", "--rate", "16000", "--channels", "1", "--format", "s16", "-"]
        elif shutil.which("arecord"):
            cmd = ["arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-"]
        else:
            return None

        # WS2/B2: direciona a captura para um source PipeWire filtrado (NS + AGC) quando
        # disponível. Sem PipeWire/filtro, `get_processed_capture_source` retorna None e o
        # comando fica inalterado (microfone padrão). Best-effort, nunca levanta.
        ns_enabled = getattr(getattr(self, "config", None), "live_mic_ns_enabled", True)
        agc_enabled = getattr(getattr(self, "config", None), "live_mic_agc_enabled", True)
        filtered_source = get_processed_capture_source(ns_enabled, agc_enabled)
        if filtered_source:
            cmd = enrich_capture_cmd(cmd, filtered_source)
        return cmd

    def _open_recorder(self) -> Optional[subprocess.Popen]:
        """Abre o gravador de microfone. Retorna o ``Popen`` ou ``None`` em falha (não levanta)."""
        cmd = self._build_recorder_cmd()
        if cmd is None:
            return None
        try:
            return subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.error(f"Falha ao iniciar microfone: {exc}")
            return None

    def _close_recorder(self) -> None:
        """Encerra o gravador corrente (se houver) e limpa a referência. Idempotente."""
        proc = self._record_proc
        self._record_proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=0.3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _mic_loop(self) -> None:
        """Lê áudio do microfone com VAD e **reconexão automática** do gravador.

        Se o gravador morre (mic desconectado, PipeWire reiniciado) ou a leitura falha,
        reabre com backoff exponencial (1.5s→30s, até ``live_local_reconnect_max_attempts``)
        em vez de encerrar a thread em silêncio — espelhando o wrapper ``_live_session`` do
        ``GeminiLiveClient``. Só vai a ERROR ao esgotar as tentativas. A fala em curso
        (``_speech_buffer``/``_preroll_buffer``) é **preservada** entre reaberturas.
        """
        if not (shutil.which("pw-record") or shutil.which("arecord")):
            err = "Nenhum gravador de áudio (pw-record ou arecord) disponível no sistema."
            logger.error(err)
            self._set_state(LiveVoiceState.ERROR, err)
            if self.on_error:
                self.on_error(err)
            return

        reconnect_enabled = getattr(getattr(self, "config", None), "live_local_reconnect_enabled", True)
        configured_max = getattr(getattr(self, "config", None), "live_local_reconnect_max_attempts", 5)
        max_attempts = max(1, int(configured_max)) if reconnect_enabled else 1
        base_delay = 1.5
        max_delay = 30.0

        attempt = 0
        last_reason = ""
        while self._is_running:
            attempt += 1
            self._record_proc = self._open_recorder()
            if self._record_proc is None:
                last_reason = "Falha ao abrir o gravador de microfone"
            else:
                if attempt > 1:
                    # Recuperou: a reconexão deu certo, volta a ouvir.
                    self._set_state(LiveVoiceState.LISTENING, "Ouvindo microfone...")
                last_reason = self._capture_loop()
                self._close_recorder()

            if not self._is_running:
                return

            if attempt >= max_attempts:
                err = last_reason or f"Microfone indisponível após {max_attempts} tentativa(s)."
                logger.error(f"Captura de microfone encerrada: {err}")
                self._set_state(LiveVoiceState.ERROR, err)
                if self.on_error:
                    self.on_error(err)
                return

            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            self._set_state(
                LiveVoiceState.CONNECTING,
                f"Reconectando microfone (tentativa {attempt + 1}/{max_attempts}) em {delay:.1f}s...",
            )
            if not self._sleep_cancellable(delay):
                return

    def _sleep_cancellable(self, delay: float) -> bool:
        """Dorme ``delay`` segundos em passos curtos; retorna False se ``stop()`` foi chamado."""
        deadline = time.monotonic() + max(0.0, delay)
        while time.monotonic() < deadline:
            if not self._is_running:
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return self._is_running

    def _capture_loop(self) -> str:
        """Laço de leitura de áudio (RMS + VAD) até o gravador morrer ou a sessão parar.

        Retorna o motivo da saída (usado no log da reconexão).
        """
        chunk_size = 3200  # 100ms de áudio a 16kHz s16 mono (1600 amostras * 2 bytes)
        proc = self._record_proc
        while self._is_running and proc and proc.poll() is None:
            try:
                pcm_chunk = proc.stdout.read(chunk_size)
            except Exception as exc:
                return f"Erro de leitura do microfone: {exc}"

            if not pcm_chunk:
                time.sleep(0.02)
                continue

            self._process_chunk(pcm_chunk)

        if proc is not None:
            code = proc.poll()
            if code is not None and code != 0:
                return f"Gravador de microfone encerrou com código {code}"
        return "Gravador de microfone encerrou"

    def _process_chunk(self, pcm_chunk: bytes) -> None:
        """Processa um chunk de áudio: nível RMS, isolamento de eco e VAD."""
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
            return

        # Isolamento acústico e anti-eco: Enquanto o assistente fala ou processa,
        # ignoramos a entrada do microfone para evitar auto-interrupção e eco dos alto-falantes.
        if self.state in (LiveVoiceState.SPEAKING, LiveVoiceState.THINKING, LiveVoiceState.EXECUTING):
            self._speech_buffer.clear()
            self._preroll_buffer.clear()
            self._is_user_speaking = False
            self._silence_chunks = 0
            return

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
                                        return
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
            self._transcripts_log.append(make_entry(ROLE_USER, user_text))

            if self.on_transcript:
                try:
                    self.on_transcript(ROLE_USER, user_text)
                except Exception as exc:
                    logger.warning(f"Erro no callback on_transcript(user): {exc}")

            if self.on_user_message:
                try:
                    self.on_user_message(user_text)
                except Exception as exc:
                    logger.warning(f"Erro no callback on_user_message: {exc}")

            # 2. Atalho determinístico do Quadro Negro (navegação de UI).
            # "abre o quadro e mostra a fatoração" é navegação, não ação de
            # desktop — o IntentEngine local só produz ActionPlan/ActionType e
            # não tem como expressá-la. No caminho Gemini Live o mesmo comando é
            # uma ferramenta (`study_show_board`); aqui o detector puro faz o
            # papel dela. Casa -> fecha o turno aqui. Não casa -> segue para o
            # IntentEngine (custo zero: é só regex).
            if self._handle_board_command(user_text):
                return

            # 3. Raciocínio e Geração de Ações via IntentEngine
            self._set_state(LiveVoiceState.THINKING, "Processando solicitação...")
            try:
                plan: ActionPlan = self.engine.parse(user_text)
            except Exception as exc:
                logger.error(f"Erro no IntentEngine ao processar voz: {exc}")
                plan = ActionPlan(
                    thought="Desculpe, tive uma dificuldade momentânea ao processar sua solicitação.",
                    actions=[],
                )

            # 4. Execução de Ações do Desktop (apenas ações reais, sem ANSWER ou SMART_OCR)
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

            # 5. Resposta em Voz (Piper TTS)
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

            # Síntese de voz concisa e fluida via Piper TTS (sem ler rodapés, promoções ou citações técnicas)
            speech_text = build_spoken_response(plan.thought or plan.extracted_text, executable_actions=executable_actions)
            self._finish_turn(response_text, speech_text)

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

    def _finish_turn(self, response_text: str, speech_text: str) -> None:
        """Publica e fala a resposta final do turno (caminho comum).

        Extraído porque o atalho do Quadro Negro fecha o turno exatamente como o
        caminho do IntentEngine — duplicar isso significaria manter dois lugares
        em sincronia toda vez que um callback novo for acrescentado.
        """
        # Entrega G / P1-7: papel canônico (`assistant`), não o histórico `"agent"` — o
        # mesmo vocabulário do cliente de nuvem. `normalize_role` continua lendo `"agent"`
        # para dados antigos.
        self._transcripts_log.append(make_entry(ROLE_ASSISTANT, response_text))

        if self.on_transcript:
            try:
                self.on_transcript(ROLE_ASSISTANT, response_text)
            except Exception as exc:
                logger.warning(f"Erro no callback on_transcript(assistant): {exc}")

        if self.on_agent_message:
            try:
                self.on_agent_message(response_text)
            except Exception as exc:
                logger.warning(f"Erro no callback on_agent_message: {exc}")

        self._set_state(LiveVoiceState.SPEAKING, "Falando...")
        self.synthesizer.speak(speech_text)

    def _handle_board_command(self, user_text: str) -> bool:
        """Trata "abre o quadro e mostra X" fora do IntentEngine.

        Devolve ``True`` quando a fala era um comando do Quadro Negro e o turno
        já foi fechado aqui — o chamador então **não** deve seguir para o
        IntentEngine. Devolve ``False`` quando não é o caso (ou quando não há
        gancho de UI injetado), e a fala segue o fluxo normal.

        O trabalho pesado de decidir *o que* abrir é do detector puro
        (`core/study_intent`), e a resolução do problema continua no gancho da
        janela — que já sabe fazer álgebra pura e, se preciso, escalar ao modelo
        fora da main thread. Aqui só se liga um ao outro.
        """
        from ..core.board_outcome import (
            BOARD_FAILED,
            BoardOutcome,
            message_for,
            outcome_from_payload,
        )
        from ..core.study_intent import parse_board_command

        command = parse_board_command(user_text)
        if command is None or self.board_opener is None:
            return False

        try:
            result = self.board_opener(command.problem, None, command.focus)
        except Exception as exc:
            logger.warning(f"Falha ao abrir o Quadro Negro por voz: {exc}")
            # Exceção do gancho é FALHA, não desfecho desconhecido: o log da
            # sessão precisa dizer o que aconteceu, não ficar ambíguo.
            result = BoardOutcome(
                BOARD_FAILED, message_for(BOARD_FAILED), detail=str(exc)
            ).to_dict()

        # O desfecho vem do gancho. Antes, `success: True` e a frase "Quadro Negro
        # aberto" eram fixos — então uma abertura que não aconteceu (sem provedor
        # de IA, por exemplo) era falada e registrada como sucesso.
        outcome = outcome_from_payload(result)
        spoken = outcome.message or message_for(outcome.status)

        self._executed_actions_log.append({
            "type": "study_show_board",
            "description": spoken,
            "success": outcome.accepted,
            "status": outcome.status,
        })
        if self.on_tool_executed:
            try:
                self.on_tool_executed("study_show_board", spoken, outcome.accepted)
            except Exception:
                pass
        self._finish_turn(spoken, spoken)
        return True

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
