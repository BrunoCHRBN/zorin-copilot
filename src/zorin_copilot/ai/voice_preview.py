# Decisão de design: Serviço de demonstração e prévia em áudio das vozes do assistente de IA.
# Permite ao usuário ouvir previamente as 8 vozes do Gemini Live (Puck, Aoede, Charon, Kore,
# Fenrir, Zephyr, Callirrhoe, Sulafat) diretamente na interface (ModelSelectorDialog, VoicePill, Preferences).
#
# RESILIÊNCIA E ZERO-CRASH:
# 1. Armazena amostras em cache local (~/.cache/zorin-copilot/voice_previews/).
# 2. Gera amostras acústicas harmônicas (chimes) com timbre exclusivo para cada voz usando
#    módulos nativos (wave, struct, math), garantindo funcionamento instantâneo offline e em testes.
# 3. Suporta síntese natural via Gemini TTS quando online e configurado com API Key.
# 4. Reprodução não-bloqueante via PipeWire (pw-play) ou ALSA (aplay).

"""Serviço de prévia auditiva e demonstração das vozes do assistente."""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Fallback GLib
try:
    from gi.repository import GLib
except ImportError:
    GLib = None  # type: ignore

VOICE_SCRIPTS: dict[str, str] = {
    "Puck": "Olá! Eu sou o Puck, uma voz enérgica e animada para acompanhar seu fluxo de trabalho no Zorin Copilot.",
    "Aoede": "Olá! Eu sou a Aoede, uma voz clara, fluida e expressiva para suas conversas em tempo real.",
    "Charon": "Olá! Eu sou o Charon, uma voz profunda, calma e focada para te auxiliar nas suas tarefas.",
    "Kore": "Olá! Eu sou a Kore, uma voz suave e amigável para tornar seu dia mais leve e produtivo.",
    "Fenrir": "Olá! Eu sou o Fenrir, uma voz firme e determinada para decisões rápidas no desktop.",
    "Zephyr": "Olá! Eu sou a Zephyr, uma voz precisa e equilibrada para organizar suas ideias.",
    "Callirrhoe": "Olá! Eu sou a Callirrhoe, uma voz calorosa e refinada para qualquer ocasião.",
    "Sulafat": "Olá! Eu sou o Sulafat, uma voz profissional, confiante e direta ao ponto.",
}

# Assinaturas harmônicas para o chime acústico de cada voz (frequências em Hz)
VOICE_CHORD_FREQS: dict[str, list[float]] = {
    "Puck": [440.0, 554.37, 659.25],       # Lá maior brilhante (A4, C#5, E5)
    "Aoede": [349.23, 440.0, 523.25],      # Fá maior melódico (F4, A4, C5)
    "Charon": [146.83, 220.0, 293.66],     # Ré grave/ressonante (D3, A3, D4)
    "Kore": [329.63, 392.0, 493.88],       # Mi menor suave (E4, G4, B4)
    "Fenrir": [196.0, 293.66, 392.0],      # Sol firme (G3, D4, G4)
    "Zephyr": [261.63, 329.63, 392.0],     # Dó maior equilibrado (C4, E4, G4)
    "Callirrhoe": [293.66, 369.99, 440.0], # Ré maior lírico (D4, F#4, A4)
    "Sulafat": [220.0, 329.63, 440.0],     # Lá profissional (A3, E4, A4)
}


class VoicePreviewService:
    """Gerenciador de geração, cache e reprodução das prévias de voz."""

    _instance: VoicePreviewService | None = None
    _lock = threading.Lock()

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = (
            cache_dir
            if cache_dir is not None
            else Path.home() / ".cache" / "zorin-copilot" / "voice_previews"
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._current_proc: subprocess.Popen[Any] | None = None
        self._current_voice: str = ""
        self._on_finished_cb: Callable[[], None] | None = None
        self._playback_lock = threading.Lock()

    @classmethod
    def get_default(cls) -> VoicePreviewService:
        with cls._lock:
            if cls._instance is None:
                cls._instance = VoicePreviewService()
            return cls._instance

    def get_cache_path(self, voice_name: str) -> Path:
        """Retorna o caminho do arquivo de áudio WAV para a voz solicitada."""
        clean = voice_name.strip().capitalize()
        return self.cache_dir / f"{clean.lower()}.wav"

    def generate_acoustic_chime_wav(self, voice_name: str, target_path: Path) -> Path:
        """Gera um arquivo WAV harmônico puro (24kHz 16-bit mono) instantaneamente sem dependências externas."""
        clean = voice_name.strip().capitalize()
        freqs = VOICE_CHORD_FREQS.get(clean, [440.0, 554.37, 659.25])
        sample_rate = 24000
        duration = 1.6  # segundos
        total_samples = int(sample_rate * duration)

        raw_frames = bytearray()
        for n in range(total_samples):
            t = float(n) / sample_rate
            # Envelope ADSR suave (ataque rápido de 40ms, decaimento exponencial)
            if t < 0.04:
                env = t / 0.04
            else:
                env = math.exp(-2.2 * (t - 0.04))

            val = 0.0
            for i, f in enumerate(freqs):
                amp = 1.0 / (i + 1.2)
                val += amp * math.sin(2.0 * math.pi * f * t)

            # Normaliza e aplica envelope
            val = (val / len(freqs)) * env * 0.85
            int_val = int(max(-32768, min(32767, val * 32767.0)))
            raw_frames.extend(struct.pack("<h", int_val))

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(raw_frames)

        logger.debug("Amostra acústica gerada para '%s' em %s", clean, target_path)
        return target_path

    def synthesize_tts_gemini_async(self, voice_name: str, target_path: Path) -> None:
        """Tenta sintetizar a voz real via REST Gemini API em thread secundária."""
        from ..core.config import CopilotConfig

        cfg = CopilotConfig.load()
        api_key = cfg.get_active_gemini_key()
        if not api_key:
            return

        clean = voice_name.strip().capitalize()
        script = VOICE_SCRIPTS.get(clean, f"Olá, eu sou a voz {clean} no Zorin Copilot.")

        def _worker() -> None:
            import urllib.request
            import urllib.error

            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": (
                                    "Fale a seguinte frase com entonação expressiva e natural em português brasileiro: "
                                    f"\"{script}\""
                                )
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": clean
                            }
                        }
                    }
                }
            }

            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    cands = res_json.get("candidates", [])
                    if cands:
                        parts = cands[0].get("content", {}).get("parts", [])
                        for p in parts:
                            inline = p.get("inlineData") or {}
                            if "data" in inline:
                                audio_bytes = base64.b64decode(inline["data"])
                                if audio_bytes and len(audio_bytes) > 200:
                                    target_path.write_bytes(audio_bytes)
                                    logger.info("Amostra TTS de voz para '%s' baixada e salva com sucesso.", clean)
                                    return
            except Exception as exc:
                logger.debug("Tentativa de síntese remota de voz falhou (mantendo áudio harmônico): %s", exc)

        th = threading.Thread(target=_worker, daemon=True, name=f"tts-preview-{clean}")
        th.start()

    def get_or_create_preview_audio(self, voice_name: str) -> Path:
        """Garante que a amostra de áudio existe no cache local e dispara enriquecimento online se oportuno."""
        clean = voice_name.strip().capitalize()
        path = self.get_cache_path(clean)
        if not path.exists() or path.stat().st_size < 100:
            self.generate_acoustic_chime_wav(clean, path)
            # Tenta obter a síntese neural real em background se possível
            if not (
                os.environ.get("ZORIN_TEST_MODE")
                or os.environ.get("PYTEST_CURRENT_TEST")
                or "pytest" in sys.modules
            ):
                self.synthesize_tts_gemini_async(clean, path)
        return path

    def play_voice(
        self,
        voice_name: str,
        on_finished: Callable[[], None] | None = None,
    ) -> bool:
        """Inicia a reprodução não-bloqueante da amostra de voz indicada."""
        clean = voice_name.strip().capitalize()
        self.stop()

        audio_path = self.get_or_create_preview_audio(clean)
        if not audio_path.exists():
            return False

        with self._playback_lock:
            self._current_voice = clean
            self._on_finished_cb = on_finished

        # Modo de teste automatizado: conclui imediatamente sem tocar som de verdade
        if (
            os.environ.get("ZORIN_TEST_MODE") == "1"
            or os.environ.get("PYTEST_CURRENT_TEST")
            or "pytest" in sys.modules
        ):
            self._notify_finished()
            return True

        # Localiza reprodutor disponível no sistema operacional
        player_cmd: list[str] | None = None
        if shutil.which("pw-play"):
            player_cmd = ["pw-play", str(audio_path)]
        elif shutil.which("aplay"):
            player_cmd = ["aplay", "-q", str(audio_path)]
        elif shutil.which("mpv"):
            player_cmd = ["mpv", "--no-video", "--really-quiet", str(audio_path)]
        elif shutil.which("ffplay"):
            player_cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(audio_path)]

        if not player_cmd:
            logger.warning("Nenhum reprodutor de áudio (pw-play, aplay, mpv) disponível.")
            self._notify_finished()
            return False

        try:
            proc = subprocess.Popen(
                player_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_proc = proc

            def _monitor() -> None:
                try:
                    proc.wait(timeout=6.0)
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                self._notify_finished()

            th = threading.Thread(target=_monitor, daemon=True, name=f"preview-play-{clean}")
            th.start()
            return True
        except Exception as exc:
            logger.warning("Falha ao iniciar reprodução de voz '%s': %s", clean, exc)
            self._notify_finished()
            return False

    def stop(self) -> None:
        """Interrompe a reprodução atual se houver."""
        with self._playback_lock:
            proc = self._current_proc
            self._current_proc = None
            self._current_voice = ""

            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass

        self._notify_finished()

    def is_playing(self, voice_name: str | None = None) -> bool:
        """Verifica se alguma voz (ou uma voz específica) está sendo reproduzida."""
        with self._playback_lock:
            if not self._current_voice:
                return False
            if voice_name is not None and self._current_voice.lower() != voice_name.strip().lower():
                return False
            if self._current_proc is not None:
                return self._current_proc.poll() is None
            return False

    def get_current_voice(self) -> str:
        """Retorna o nome da voz em reprodução no momento."""
        with self._playback_lock:
            return self._current_voice

    def _notify_finished(self) -> None:
        """Notifica o callback de finalização de reprodução com segurança."""
        cb: Callable[[], None] | None = None
        with self._playback_lock:
            cb = self._on_finished_cb
            self._on_finished_cb = None
            self._current_voice = ""

        if cb:
            if (
                os.environ.get("ZORIN_TEST_MODE") == "1"
                or os.environ.get("PYTEST_CURRENT_TEST")
                or "pytest" in sys.modules
                or GLib is None
            ):
                try:
                    cb()
                except Exception:
                    pass
            else:
                GLib.idle_add(cb)
