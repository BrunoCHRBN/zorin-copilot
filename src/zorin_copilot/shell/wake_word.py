"""Detecção de wake word ("palavra de ativação") para o Copilot Live Voice.

Filosofia de design (espelha VirtualInputDriver):

* **Backend injetável**: a detecção real de fala (speech-to-text) é delegada a
  um backend. O backend padrão (:class:`VoskWakeWordBackend`) usa o Vosk —
  reconhecimento de fala **offline** que aceita **frases arbitrárias** (diferente
  do Porcupine/Snowboy, que exigem keywords treinadas/compiladas). Por ser
  injetável, testes rodam headless com um backend fake (sem microfone).
* **Frases configuráveis**: a lista de wake phrases vem do config (editável nas
  preferências), não é hardcoded. Qualquer frase funciona (Vosk transcreve e
  comparamos por substring normalizada).
* **Loop em thread**: o engine consome transcripts do backend em uma thread
  dedicada e dispara o callback `on_wake` quando uma frase é detectada.
* **Pausa/resume**: expõe `pause()`/`resume()` para a UI suspender a detecção
  durante uma sessão de voz ativa (evita conflito de microfone com o Live).
* **Degradação graciosa**: sem backend/modelo/microfone, o engine simplesmente
  não inicia (is_available=False), sem quebrar o app.

Privacidade: todo o processamento é local (offline); nenhum áudio sai da máquina.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalização + matching (PURO — testável headless)
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Normaliza texto para comparação de wake word.

    Lowercase, remove pontuação, colapsa espaços. Assim "OK, Copilot!",
    "ok copilot" e "Ok   Copilot" casam. Mantém acentos (\\w unicode) para
    suportar português ("olá copilot").
    """
    if not text:
        return ""
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def contains_wake_phrase(transcript: str, phrases: Iterable[str]) -> Optional[str]:
    """Retorna a primeira wake phrase contida no transcript, ou None.

    Comparação por substring sobre o texto normalizado: se o usuário disser
    "...ok copilot abra o navegador", detecta "ok copilot".
    """
    norm = normalize_text(transcript)
    if not norm:
        return None
    for phrase in phrases:
        p = normalize_text(phrase)
        if p and p in norm:
            return phrase
    return None


def extract_command(transcript: str, phrase: str) -> str:
    """Extrai o comando dito APÓS a wake phrase (Fase 4A).

    "ok copilot abre o navegador" com phrase "ok copilot" -> "abre o navegador".
    Se só a frase foi dita ("ok copilot"), retorna "" — o chamador trata como
    invocação simples (abrir a sessão e aguardar o pedido por voz).

    A extração usa a primeira ocorrência da frase no texto normalizado
    (lowercase, sem pontuação) — formato compatível com a saída típica do Vosk.
    """
    norm = normalize_text(transcript)
    p = normalize_text(phrase)
    if not norm or not p:
        return ""
    idx = norm.find(p)
    if idx < 0:
        return ""
    return norm[idx + len(p):].strip()


# ---------------------------------------------------------------------------
# Backends de STT (injetáveis)
# ---------------------------------------------------------------------------


class NullWakeWordBackend:
    """Backend nulo: nunca produz transcript (detecção desativada)."""

    name = "null"

    def is_available(self) -> bool:  # pragma: no cover - trivial
        return False

    def stream_transcripts(self):  # pragma: no cover - trivial
        return iter(())


class VoskWakeWordBackend:
    """Backend Vosk (STT offline) para wake phrases arbitrárias.

    Requer o pacote ``vosk`` (pip) e um modelo de linguagem (ex.: pt-BR).
    O microfone é lido via ``sounddevice`` se disponível; caso contrário via
    ``pw-record`` (PipeWire). Se nada disso existir, ``is_available()`` é False
    e o engine permanece desativado — o resto do app funciona normalmente.
    """

    name = "vosk"
    SAMPLE_RATE = 16000

    def __init__(self, model_path: str = "", record_command: Optional[list] = None):
        self._model_path = model_path
        self._record_command = record_command  # injetável p/ testes
        self._model = None
        self._load_error: Optional[str] = None
        if model_path:
            self._try_load_model(model_path)

    # -- disponibilidade ----------------------------------------------------
    def _try_load_model(self, model_path: str) -> None:
        try:
            import vosk  # type: ignore

            self._model = vosk.Model(model_path)
            logger.info("Vosk: modelo carregado de %s", model_path)
        except ImportError:
            self._load_error = "pacote 'vosk' não instalado"
            logger.info("Vosk indisponível: pacote não instalado")
        except Exception as exc:  # modelo inválido/ausente
            self._load_error = str(exc)
            logger.warning("Vosk: falha ao carregar modelo: %s", exc)

    def is_available(self) -> bool:
        return self._model is not None and self._resolve_record_command() is not None

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    # -- captura de áudio ---------------------------------------------------
    def _resolve_record_command(self) -> Optional[list]:
        if self._record_command is not None:
            return self._record_command
        import shutil

        if shutil.which("pw-record"):
            # 16kHz mono s16 — formato esperado pelo KaldiRecognizer
            return ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "-"]
        return None

    def stream_transcripts(self):
        """Gerador de transcripts parciais/finais a partir do microfone.

        Lê PCM do ``pw-record`` e alimenta o KaldiRecognizer. Emite o texto
        parcial e o final (o matching por substring funciona em ambos).
        """
        import json
        import subprocess

        if not self.is_available():
            return
        import vosk  # type: ignore

        rec = vosk.KaldiRecognizer(self._model, self.SAMPLE_RATE)
        cmd = self._resolve_record_command()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            assert proc.stdout is not None
            while True:
                data = proc.stdout.read(4000)
                if not data:
                    break
                if rec.AcceptWaveform(data):
                    text = json.loads(rec.Result()).get("text", "")
                else:
                    text = json.loads(rec.PartialResult()).get("partial", "")
                if text:
                    yield text
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()


def default_backend(model_path: str = ""):
    """Seleciona o backend: Vosk se modelo configurado, senão Null."""
    if model_path:
        backend = VoskWakeWordBackend(model_path)
        if backend.is_available():
            return backend
        logger.info("Vosk indisponível (%s); wake word desativado", backend.load_error)
    return NullWakeWordBackend()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class WakeWordEngine:
    """Motor de wake word: consome transcripts e dispara on_wake.

    Uso típico (UI)::

        engine = WakeWordEngine(phrases=cfg.wake_phrases, backend=..., on_wake=abrir_live)
        engine.start()
        ...
        engine.pause()   # durante a sessão de voz
        engine.resume()  # após a sessão
        engine.stop()    # ao sair
    """

    def __init__(
        self,
        phrases: Iterable[str],
        backend=None,
        on_wake: Optional[Callable[..., None]] = None,
    ):
        # normaliza e remove vazios
        self._phrases = [p for p in (phrases or []) if normalize_text(p)]
        self.backend = backend if backend is not None else NullWakeWordBackend()
        # on_wake(phrase, command): command é o texto dito após a frase (Fase 4A),
        # "" quando o usuário só disse a palavra de ativação.
        self.on_wake = on_wake
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None

    # -- propriedades -------------------------------------------------------
    @property
    def phrases(self) -> list:
        return list(self._phrases)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def paused(self) -> bool:
        return self._paused

    def is_available(self) -> bool:
        """True se há frases configuradas E o backend está funcional."""
        return bool(self._phrases) and self.backend.is_available()

    # -- ciclo de vida ------------------------------------------------------
    def start(self) -> bool:
        """Inicia a detecção. Retorna True se iniciou."""
        if self._running or not self.is_available():
            return False
        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._loop, daemon=True, name="wake-word")
        self._thread.start()
        logger.info("Wake word ativo (backend=%s, frases=%s)", self.backend.name, self._phrases)
        return True

    def stop(self) -> None:
        self._running = False
        self._thread = None

    def pause(self) -> None:
        """Pausa a detecção (ex.: durante sessão Live, que usa o microfone)."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def set_phrases(self, phrases: Iterable[str]) -> None:
        """Atualiza as frases em tempo de execução (após editar preferências)."""
        self._phrases = [p for p in (phrases or []) if normalize_text(p)]

    # -- loop ----------------------------------------------------------------
    def _loop(self) -> None:
        try:
            for transcript in self.backend.stream_transcripts():
                if not self._running:
                    break
                self._handle_transcript(transcript)
        except Exception as exc:
            logger.warning("Wake word: loop encerrado com erro: %s", exc)
        finally:
            self._running = False

    def _handle_transcript(self, transcript: str) -> bool:
        """Processa um transcript; dispara on_wake se casar e não pausado.

        Retorna True se uma frase foi detectada (e o callback disparado).
        """
        if self._paused or not self._running:
            return False
        hit = contains_wake_phrase(transcript, self._phrases)
        if hit and self.on_wake:
            command = extract_command(transcript, hit)
            logger.info("Wake word detectada: %r (comando: %r)", hit, command)
            try:
                self.on_wake(hit, command)
            except Exception as exc:  # callback não deve derrubar o loop
                logger.warning("Wake word: erro no callback on_wake: %s", exc)
        return hit is not None

    # -- teste/diagnóstico --------------------------------------------------
    def simulate_phrase(self, transcript: str) -> bool:
        """Simula um transcript (para testes headless e diagnóstico na UI)."""
        return self._handle_transcript(transcript)
