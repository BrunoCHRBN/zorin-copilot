# Decisão de design: o Ditado Global (Voice Typing) opera em segundo plano sem roubar o foco
# da janela ativa (LibreOffice, navegador, terminal, editor). A captura de áudio via PipeWire (pw-record)
# é processada com VAD local e transcrita pelo faster-whisper (CPU INT8) ou fallback em nuvem.
# O texto resultante é injetado diretamente no cursor do aplicativo em foco via VirtualInputDriver (wtype/ydotool),
# com salvaguarda automática na área de transferência caso o backend de entrada não esteja disponível.

"""Serviço de Ditado Global / Voice Typing no aplicativo em foco."""

from __future__ import annotations

import enum
import logging
import math
import re
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ..ai.local_voice import LocalVoiceTranscriber
from ..core.clipboard import ClipboardService
from ..core.config import CopilotConfig
from ..shell.input_driver import VirtualInputDriver

logger = logging.getLogger(__name__)

TECHNICAL_VOCABULARY_PROMPT: str = (
    "Zorin OS, Linux, Python, Bash, terminal, GNOME, Wayland, systemctl, git, apt, Docker, "
    "kernel, sudo, GitHub, Neovim, VS Code, Flatpak, PipeWire, LibreOffice, Firefox, Chrome."
)


def play_sound_cue(event_id: str) -> None:
    """Reproduz um earcon sutil do sistema em segundo plano sem bloquear a thread."""
    def _play() -> None:
        # Prioridade 1: canberra-gtk-play (nativo do tema de som do GNOME/XDG)
        if shutil.which("canberra-gtk-play"):
            try:
                subprocess.run(
                    ["canberra-gtk-play", "-i", event_id],
                    capture_output=True,
                    timeout=1.2,
                    check=False,
                )
                return
            except Exception:
                pass

        # Prioridade 2: pw-play / paplay com arquivo OGA direto
        sound_file = Path(f"/usr/share/sounds/freedesktop/stereo/{event_id}.oga")
        if sound_file.exists():
            for player in ("pw-play", "paplay"):
                if shutil.which(player):
                    try:
                        subprocess.run(
                            [player, str(sound_file)],
                            capture_output=True,
                            timeout=1.2,
                            check=False,
                        )
                        return
                    except Exception:
                        pass

    threading.Thread(target=_play, daemon=True).start()


def parse_spoken_punctuation(text: str) -> str:
    """Converte comandos de pontuação falados em símbolos gráficos correspondentes em PT-BR."""
    if not text:
        return ""

    replacements = [
        # Comandos compostos primeiro
        (r"\b(?:ponto\s+e\s+vírgula)\b", ";"),
        (r"\b(?:ponto\s+final)\b", "."),
        (r"\b(?:ponto\s+de\s+interrogação)\b", "?"),
        (r"\b(?:ponto\s+de\s+exclamação)\b", "!"),
        (r"\b(?:dois\s+pontos)\b", ":"),
        (r"\b(?:novo\s+parágrafo|parágrafo)\b", "\n\n"),
        (r"\b(?:nova\s+linha)\b", "\n"),
        (r"\b(?:abrir\s+aspas|abre\s+aspas)\b", '"'),
        (r"\b(?:fechar\s+aspas|fecha\s+aspas)\b", '"'),
        (r"\b(?:abrir\s+parênteses|abre\s+parênteses)\b", "("),
        (r"\b(?:fechar\s+parênteses|fecha\s+parênteses)\b", ")"),
        (r"\b(?:reticências)\b", "..."),
        (r"\b(?:travessão)\b", "—"),
        (r"\b(?:vírgula)\b", ","),
        (r"\b(?:interrogação)\b", "?"),
        (r"\b(?:exclamação)\b", "!"),
        # "ponto" isolado no fim da frase ou antes de espaço, protegendo "ponto de vista", "ponto turístico", etc.
        (r"(?<!ponto\s)(?<!\bponto\sde\s)\bponto\b(?!\s+(?:de\s+|turístico|comercial|cardeal|crítico|fraco|forte))", "."),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    # 1. Remove espaço antes de pontuação de fechamento: " ," -> ","
    text = re.sub(r"\s+([,.;:!?…\)])", r"\1", text)
    # 2. Garante espaço após pontuação se seguida de letra/número: ",olá" -> ", olá"
    text = re.sub(r"([,;:!?…])([^\s\d\n])", r"\1 \2", text)
    # 3. Ponto seguido de letra: ".olá" -> ". Olá" (com espaço)
    text = re.sub(r"\.([^\s\d\n])", r". \1", text)
    # 4. Remove espaço depois de abrir aspas ou abrir parênteses: "( olá" -> "(olá"
    text = re.sub(r"([\(\"])\s+", r"\1", text)
    # 5. Remove espaço antes de fechar aspas ou fechar parênteses: "olá \"" -> "olá\""
    text = re.sub(r"\s+([\"\)])", r"\1", text)
    # 6. Remove espaços horizontais imediatamente antes e depois de quebras de linha
    text = re.sub(r"[ \t]*\n+[ \t]*", lambda m: "\n" * m.group(0).count("\n"), text)

    # Capitaliza letras após [. ! ?] seguidos de espaço
    def _cap_after_punct(match: re.Match) -> str:
        punct = match.group(1)
        ws = match.group(2)
        char = match.group(3)
        return f"{punct}{ws}{char.upper()}"

    text = re.sub(r"([.!?])(\s+)([a-zà-ú])", _cap_after_punct, text, flags=re.IGNORECASE)
    # Capitaliza letras logo após quebras de linha
    text = re.sub(r"(\n+)([a-zà-ú])", lambda m: f"{m.group(1)}{m.group(2).upper()}", text, flags=re.IGNORECASE)

    return text


def smart_polish_speech(text: str) -> str:
    """Aplica polimento inteligente offline: remove hesitações, gaguejos e repetições consecutivas."""
    if not text:
        return ""
    # Remove hesitações e vícios comuns no início da frase ou isolados: "ééé...", "tipo assim", "né", "hã", "hum"
    text = re.sub(r"^(?:é{2,}|hã+|hum+|tipo\s+assim|olha\s+só)\s*[,.]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:é{2,}|hã+|hum+)\b", "", text, flags=re.IGNORECASE)
    # Remove repetições consecutivas idênticas (ex: "que que", "para para", "o o")
    text = re.sub(r"\b(\w+)\s+\1\b", r"\1", text, flags=re.IGNORECASE)
    # Normaliza múltiplos espaços
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


class DictationState(str, enum.Enum):
    """Estados do ciclo de vida do Ditado Global."""

    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    TYPING = "typing"
    DONE = "done"
    ERROR = "error"


class DictationService:
    """Coordenador de captura de voz, transcrição Whisper e injeção de texto no app em foco."""

    def __init__(
        self,
        config: Optional[CopilotConfig] = None,
        input_driver: Optional[VirtualInputDriver] = None,
        transcriber: Optional[LocalVoiceTranscriber] = None,
    ) -> None:
        self.config = config or CopilotConfig.load()
        self.input_driver = input_driver or VirtualInputDriver()
        model_size = getattr(self.config, "whisper_model", "small")
        self.transcriber = transcriber or LocalVoiceTranscriber(model_size=model_size)

        self.state: DictationState = DictationState.IDLE
        self._is_recording: bool = False
        self._should_stop: bool = False
        self._cancelled: bool = False
        self._record_proc: Optional[subprocess.Popen] = None
        self._worker_thread: Optional[threading.Thread] = None

        # Parâmetros de calibração de áudio e VAD
        self.sample_rate: int = 16000
        self.chunk_size: int = 3200  # 100ms a 16kHz 16-bit mono
        self.speech_threshold: float = 0.045  # RMS normalizado
        self.silence_timeout_sec: float = getattr(self.config, "dictate_silence_timeout_sec", 0.9)
        self.min_speech_ms: int = 400
        self.max_record_sec: float = 30.0

        # Injetor customizado opcional (ex: campo de texto interno quando Copilot tem foco)
        self.custom_text_injector: Optional[Callable[[str], bool]] = None

        # Callbacks para observabilidade da UI
        self.on_state_change: Optional[Callable[[DictationState, str], None]] = None
        self.on_audio_level: Optional[Callable[[float], None]] = None
        self.on_text_transcribed: Optional[Callable[[str], None]] = None

    def _set_state(self, state: DictationState, message: str = "") -> None:
        self.state = state
        if getattr(self.config, "dictate_sound_effects", True):
            if state == DictationState.DONE and message.startswith("✓"):
                play_sound_cue("complete")
            elif state == DictationState.ERROR:
                play_sound_cue("dialog-warning")

        if self.on_state_change:
            try:
                self.on_state_change(state, message)
            except Exception as exc:
                logger.debug(f"Erro em callback on_state_change: {exc}")

    def is_active(self) -> bool:
        """Indica se há uma operação de ditado em andamento."""
        return self.state in (
            DictationState.LISTENING,
            DictationState.TRANSCRIBING,
            DictationState.TYPING,
        )

    def start(self) -> bool:
        """Inicia a gravação de áudio do microfone para ditado."""
        if self.is_active():
            logger.debug("Ditado já está ativo.")
            return False

        self._is_recording = True
        self._should_stop = False
        self._cancelled = False

        if getattr(self.config, "dictate_sound_effects", True):
            play_sound_cue("message-new-instant")

        self._set_state(DictationState.LISTENING, "Ouvindo... (fale agora)")
        self._worker_thread = threading.Thread(target=self._record_and_process, daemon=True)
        self._worker_thread.start()
        return True

    def stop(self, cancel: bool = False) -> None:
        """Sinaliza encerramento da captura e força processamento imediato do áudio gravado (ou cancela)."""
        if cancel:
            self.cancel()
            return
        if self._is_recording:
            self._should_stop = True

    def cancel(self) -> None:
        """Cancela o ditado imediatamente sem transcrever nem digitar."""
        if getattr(self.config, "dictate_sound_effects", True) and self._is_recording:
            play_sound_cue("dialog-warning")
        self._cancelled = True
        self._should_stop = True
        self._is_recording = False
        self._terminate_proc()
        self._set_state(DictationState.IDLE, "Ditado cancelado.")

    def toggle(self) -> None:
        """Alterna entre iniciar e parar o ditado (atalho push-to-toggle)."""
        if self.is_active():
            self.stop()
        else:
            self.start()

    def _terminate_proc(self) -> None:
        if self._record_proc:
            try:
                self._record_proc.terminate()
                self._record_proc.wait(timeout=0.2)
            except Exception:
                try:
                    self._record_proc.kill()
                except Exception:
                    pass
            self._record_proc = None

    def _get_record_command(self) -> list[str] | None:
        """Determina o executável disponível para gravação de áudio PCM limpo."""
        if shutil.which("pw-record"):
            return ["pw-record", "--raw", "--rate", "16000", "--channels", "1", "--format", "s16", "-"]
        if shutil.which("arecord"):
            return ["arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-"]
        return None

    def _record_and_process(self) -> None:
        """Worker thread: grava áudio com VAD, detecta pausas e transcreve."""
        cmd = self._get_record_command()
        if not cmd:
            err_msg = "Nenhum utilitário de áudio (pw-record ou arecord) disponível no sistema."
            logger.error(err_msg)
            self._set_state(DictationState.ERROR, err_msg)
            self._is_recording = False
            return

        try:
            self._record_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            err_msg = f"Falha ao iniciar captura de microfone: {exc}"
            logger.error(err_msg)
            self._set_state(DictationState.ERROR, err_msg)
            self._is_recording = False
            return

        audio_buffer: list[bytes] = []
        has_spoken: bool = False
        speech_chunks: int = 0
        silence_chunks: int = 0
        silence_timeout_chunks = max(3, int(self.silence_timeout_sec * 10))
        min_speech_chunks = max(1, int(self.min_speech_ms / 100))
        max_chunks = int(self.max_record_sec * 10)

        start_time = time.time()

        while self._is_recording and self._record_proc and self._record_proc.poll() is None:
            if self._should_stop or self._cancelled:
                break

            if len(audio_buffer) >= max_chunks:
                logger.info("Limite máximo de duração do ditado atingido (30s).")
                break

            try:
                pcm_chunk = self._record_proc.stdout.read(self.chunk_size)
            except Exception:
                break

            if not pcm_chunk:
                time.sleep(0.01)
                continue

            audio_buffer.append(pcm_chunk)

            # Cálculo de volume RMS normalizado
            num_samples = len(pcm_chunk) // 2
            norm_level = 0.0
            if num_samples > 0:
                try:
                    samples = struct.unpack(f"<{num_samples}h", pcm_chunk)
                    sum_sq = sum(s * s for s in samples)
                    rms = math.sqrt(sum_sq / num_samples)
                    norm_level = min(1.0, rms / 10000.0)
                except Exception:
                    norm_level = 0.0

            if self.on_audio_level:
                try:
                    self.on_audio_level(norm_level)
                except Exception:
                    pass

            # VAD (Detecção de Atividade de Voz)
            if norm_level >= self.speech_threshold:
                speech_chunks += 1
                silence_chunks = 0
                if speech_chunks >= min_speech_chunks:
                    has_spoken = True
            else:
                if has_spoken:
                    silence_chunks += 1
                    if silence_chunks >= silence_timeout_chunks:
                        logger.debug("Pausa na fala detectada pelo VAD. Finalizando gravação.")
                        break

        self._terminate_proc()
        self._is_recording = False

        if self.on_audio_level:
            try:
                self.on_audio_level(0.0)
            except Exception:
                pass

        if self._cancelled:
            self._set_state(DictationState.IDLE, "Ditado cancelado.")
            return

        full_pcm = b"".join(audio_buffer)
        if not full_pcm or not has_spoken or len(full_pcm) < (self.chunk_size * min_speech_chunks):
            self._set_state(DictationState.DONE, "Nenhuma fala detectada.")
            return

        # Etapa de Transcrição
        self._set_state(DictationState.TRANSCRIBING, "Transcrevendo fala...")
        text = self._transcribe(full_pcm)

        if not text:
            self._set_state(DictationState.DONE, "Nenhuma fala compreensível detectada.")
            return

        # Pós-processamento de texto (capitalização e pontuação)
        text = self._format_text(text)

        # Etapa de Injeção de Texto
        self._set_state(DictationState.TYPING, f"Digitando: {text}")
        self._inject_text(text)

        if self.on_text_transcribed:
            try:
                self.on_text_transcribed(text)
            except Exception as exc:
                logger.debug(f"Erro em callback on_text_transcribed: {exc}")

        self._set_state(DictationState.DONE, f"✓ {text}")

    def _transcribe(self, pcm_data: bytes) -> str:
        """Transcreve o áudio usando faster-whisper local (ou fallback na nuvem)."""
        # 1. Tenta transcrição local via faster-whisper com vocabulário técnico
        try:
            try:
                text = self.transcriber.transcribe_pcm(
                    pcm_data,
                    sample_rate=self.sample_rate,
                    initial_prompt=TECHNICAL_VOCABULARY_PROMPT,
                )
            except TypeError:
                text = self.transcriber.transcribe_pcm(pcm_data, sample_rate=self.sample_rate)
            if text:
                return text.strip()
        except Exception as exc:
            logger.warning(f"Transcrição local falhou: {exc}")

        # 2. Fallback na nuvem se Gemini API Key estiver configurada
        if self.config.gemini_api_key:
            try:
                cloud_text = self._transcribe_cloud_gemini(pcm_data)
                if cloud_text:
                    return cloud_text.strip()
            except Exception as exc:
                logger.warning(f"Fallback de transcrição em nuvem falhou: {exc}")

        return ""

    def _transcribe_cloud_gemini(self, pcm_data: bytes) -> str:
        """Fallback de transcrição de áudio usando Gemini 2.0 Flash."""
        import io
        import wave
        from google import genai
        from google.genai import types

        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm_data)
        wav_bytes = wav_io.getvalue()

        client = genai.Client(api_key=self.config.gemini_api_key)
        response = client.models.generate_content(
            model=getattr(self.config, "gemini_model", "gemini-2.0-flash"),
            contents=[
                types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
                "Transcreva exatamente o áudio falado em português. Não adicione comentários, explicações ou aspas.",
            ],
        )
        return (response.text or "").strip()

    def _format_text(self, text: str) -> str:
        """Normaliza e formata o texto para digitação fluida com pontuação verbal e polimento."""
        text = text.strip()
        if not text:
            return ""

        # Remove aspas externas adicionadas por modelos
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()

        # Polimento inteligente de fala (remove hesitações e repetições consecutivas)
        if getattr(self.config, "dictate_smart_polish", True):
            text = smart_polish_speech(text)

        # Parser de pontuação falada em PT-BR (vírgula, ponto, nova linha, etc.)
        text = parse_spoken_punctuation(text)

        # Garante primeira letra maiúscula
        if len(text) > 1:
            text = text[0].upper() + text[1:]
        elif len(text) == 1:
            text = text.upper()

        return text

    def _inject_text(self, text: str) -> bool:
        """Digita o texto no aplicativo em foco ou copia para a área de transferência."""
        # Se um injetor customizado foi fornecido (ex: campo interno com foco), usa-o
        if self.custom_text_injector is not None:
            try:
                if self.custom_text_injector(text):
                    return True
            except Exception as exc:
                logger.debug(f"Injetor customizado falhou: {exc}")

        # Injeta via VirtualInputDriver no aplicativo ativo do desktop
        ok, msg = self.input_driver.type_text(text)
        if ok:
            logger.info(f"Ditado injetado com sucesso no app em foco: '{text[:40]}'")
            return True

        # Fallback de segurança: copia para o clipboard
        logger.warning(f"Injeção direta falhou ({msg}). Copiando para a área de transferência como fallback.")
        ClipboardService.set_text(text)
        return False
