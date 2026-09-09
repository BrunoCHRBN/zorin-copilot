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

import array
import logging
import math
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Iterable, Optional, Tuple

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
# Áudio + ajustes (PURO — testável headless)
# ---------------------------------------------------------------------------


def pcm_rms(data: bytes) -> float:
    """RMS normalizado (0..1) de um buffer PCM s16-le mono.

    Base do gate de VAD: sem ele o Vosk decodifica vocabulário inteiro mesmo
    em silêncio, queimando CPU à toa. Buffer vazio/curto/ímpar -> 0.0.
    """
    if not data or len(data) < 2:
        return 0.0
    usable = len(data) - (len(data) % 2)  # descarta eventual byte ímpar
    if usable < 2:
        return 0.0
    try:
        samples = array.array("h")
        samples.frombytes(data[:usable])
    except Exception:
        return 0.0
    if not samples:
        return 0.0
    acc = 0.0
    for s in samples:
        acc += float(s) * float(s)
    return min(1.0, math.sqrt(acc / len(samples)) / 32768.0)


def sensitivity_to_threshold(sensitivity: float) -> float:
    """Converte sensibilidade 0..1 em limiar RMS de fala.

    1.0 = muito sensível (0.005, detecta até voz baixa, mais falsos positivos);
    0.0 = exige voz alta (0.05). Fora da faixa é clampado.
    """
    try:
        s = float(sensitivity)
    except (TypeError, ValueError):
        s = 0.5
    s = max(0.0, min(1.0, s))
    return 0.005 + (1.0 - s) * 0.045


def unpack_transcript(item: Any) -> Tuple[str, bool]:
    """Normaliza um item do stream para ``(texto, is_final)``.

    Backends novos emitem tuplas ``(texto, is_final)`` para que o engine possa
    ignorar resultados parciais. Uma ``str`` simples (backends legados e os
    fakes de teste) é tratada como **final** — é isso que mantém os testes
    existentes de detecção passando sem alteração.
    """
    if isinstance(item, (tuple, list)):
        text = item[0] if len(item) > 0 else ""
        is_final = bool(item[1]) if len(item) > 1 else True
        return (text or ""), is_final
    return (item or ""), True


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

    # Janela de silêncio (em chunks de 100ms) que encerra uma fala, e quantos
    # chunks de "pré-rolagem" mantemos para não perder o começo da frase.
    # Mesmos valores já usados pelo VAD do Live local (ai/local_voice.py).
    SILENCE_CHUNKS_TIMEOUT = 8  # 800ms
    PREROLL_CHUNKS = 4  # 400ms

    def __init__(
        self,
        model_path: str = "",
        record_command: Optional[list] = None,
        vad_enabled: bool = True,
        sensitivity: float = 0.5,
        device: str = "",
    ):
        self._model_path = model_path
        self._record_command = record_command  # injetável p/ testes
        self._vad_enabled = bool(vad_enabled)
        self._sensitivity = float(sensitivity)
        self._device = (device or "").strip()
        self._model = None
        self._load_error: Optional[str] = None
        self._proc = None
        self._reset_requested = False
        self._last_stream_error: Optional[str] = None
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
    @property
    def last_stream_error(self) -> Optional[str]:
        """Último erro do stream de captura ( None = terminou de forma limpa).

        O engine usa isto para distinguir "o gerador acabou" (normal em testes
        e ao encerrar) de "o microfone morreu" (precisa retentar).
        """
        return self._last_stream_error

    def _resolve_record_command(self) -> Optional[list]:
        if self._record_command is not None:
            return self._record_command
        import shutil

        # 16kHz mono s16 — formato esperado pelo KaldiRecognizer.
        # O arecord entra como fallback: em máquinas sem PipeWire só ele existe
        # (mesmo padrão já usado em ai/local_voice.py e ai/live.py).
        if shutil.which("pw-record"):
            cmd = ["pw-record", "--raw", "--rate", "16000", "--channels", "1", "--format", "s16", "-"]
            if self._device:
                cmd = ["pw-record", "--raw", "--rate", "16000", "--channels", "1",
                       "--format", "s16", "--target", self._device, "-"]
            return cmd
        if shutil.which("arecord"):
            cmd = ["arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-"]
            if self._device:
                cmd = ["arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-D", self._device, "-"]
            return cmd
        return None

    def stream_transcripts(self):
        """Gerador de ``(texto, is_final)`` a partir do microfone.

        Com o VAD ligado (padrão), chunks abaixo do limiar de fala **nem
        chegam ao KaldiRecognizer** — é o que evita queimar CPU decodificando
        silêncio o dia inteiro. Ao fim de uma fala (800ms de silêncio) emite o
        resultado final e recria o recognizer para descartar estado velho.
        """
        import json
        import subprocess

        if not self.is_available():
            # Tem que devolver um ITERADOR: `return` puro aqui fazia o loop do
            # engine estourar TypeError ao tentar iterar None.
            return iter(())
        import vosk  # type: ignore

        self._last_stream_error = None
        rec = vosk.KaldiRecognizer(self._model, self.SAMPLE_RATE)
        cmd = self._resolve_record_command()
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception as exc:
            # Sem isto o microfone indisponível matava o loop em silêncio,
            # sem retry e sem avisar o usuário.
            self._last_stream_error = f"falha ao iniciar {cmd[0] if cmd else 'captura'}: {exc}"
            raise
        self._proc = proc

        threshold = sensitivity_to_threshold(self._sensitivity)
        preroll: deque = deque(maxlen=self.PREROLL_CHUNKS)
        speech = False
        silence_chunks = 0

        try:
            assert proc.stdout is not None
            while True:
                if self._reset_requested:
                    self._reset_requested = False
                    rec = vosk.KaldiRecognizer(self._model, self.SAMPLE_RATE)
                    speech = False
                    silence_chunks = 0
                    preroll.clear()

                data = proc.stdout.read(4000)
                if not data:
                    self._last_stream_error = (
                        f"{cmd[0] if cmd else 'captura'} encerrou (EOF) — microfone indisponível?"
                    )
                    break

                if not self._vad_enabled:
                    if rec.AcceptWaveform(data):
                        text = json.loads(rec.Result()).get("text", "")
                        if text:
                            yield (text, True)
                    else:
                        text = json.loads(rec.PartialResult()).get("partial", "")
                        if text:
                            yield (text, False)
                    continue

                rms = pcm_rms(data)

                if rms >= threshold:
                    if not speech:
                        speech = True
                        # Alimenta a pré-rolagem: sem ela o começo da frase
                        # (justamente a wake word) era engolido.
                        while preroll:
                            rec.AcceptWaveform(preroll.popleft())
                    silence_chunks = 0
                    if rec.AcceptWaveform(data):
                        text = json.loads(rec.Result()).get("text", "")
                        if text:
                            yield (text, True)
                    else:
                        text = json.loads(rec.PartialResult()).get("partial", "")
                        if text:
                            yield (text, False)
                    continue

                # Silêncio
                if speech:
                    rec.AcceptWaveform(data)
                    silence_chunks += 1
                    if silence_chunks >= self.SILENCE_CHUNKS_TIMEOUT:
                        text = json.loads(rec.FinalResult()).get("text", "")
                        if text:
                            yield (text, True)
                        rec = vosk.KaldiRecognizer(self._model, self.SAMPLE_RATE)
                        speech = False
                        silence_chunks = 0
                else:
                    preroll.append(data)
        finally:
            self._proc = None
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def stop_stream(self) -> None:
        """Mata o processo de captura imediatamente.

        Sem isto, ``stop()`` deixava o filho preso em ``read()`` e a thread do
        engine demorava a acordar — inclusive atrasando o shutdown do app.
        """
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._proc = None

    def reset_stream(self) -> None:
        """Descarta o estado do recognizer no próximo chunk (após um disparo).

        Impede que o rabo da mesma frase reemita o texto e dispare de novo.
        """
        self._reset_requested = True

    def sample_level(self, seconds: float = 2.0) -> float:
        """Abre o microfone e devolve o RMS máximo captado (0..1).

        Alimenta o botão "Testar microfone" nas preferências: o usuário confere
        o nível antes de sair falando com o Copilot.
        """
        import subprocess

        cmd = self._resolve_record_command()
        if not cmd:
            raise RuntimeError("nenhum comando de captura disponível (pw-record/arecord)")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception as exc:
            raise RuntimeError(f"falha ao abrir o microfone: {exc}") from exc
        deadline = time.monotonic() + max(0.1, float(seconds))
        peak = 0.0
        try:
            assert proc.stdout is not None
            while time.monotonic() < deadline:
                data = proc.stdout.read(4000)
                if not data:
                    break
                peak = max(peak, pcm_rms(data))
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return peak


def default_backend(
    model_path: str = "",
    vad_enabled: bool = True,
    sensitivity: float = 0.5,
    device: str = "",
):
    """Seleciona o backend: Vosk se modelo configurado, senão Null."""
    if model_path:
        backend = VoskWakeWordBackend(
            model_path,
            vad_enabled=vad_enabled,
            sensitivity=sensitivity,
            device=device,
        )
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

    # Quantas falhas seguidas de stream antes de desistir de vez.
    MAX_STREAM_FAILURES = 5

    def __init__(
        self,
        phrases: Iterable[str],
        backend=None,
        on_wake: Optional[Callable[..., None]] = None,
        cooldown_sec: float = 0.0,
        dedupe_window_sec: float = 3.0,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        # normaliza e remove vazios
        self._phrases = [p for p in (phrases or []) if normalize_text(p)]
        self.backend = backend if backend is not None else NullWakeWordBackend()
        # on_wake(phrase, command): command é o texto dito após a frase (Fase 4A),
        # "" quando o usuário só disse a palavra de ativação.
        self.on_wake = on_wake
        self.on_error = on_error
        # O default do cooldown PRECISA ser 0.0: o engine é construído sem esse
        # argumento nos testes, que disparam duas frases diferentes em sequência
        # e exigem as duas. O valor real (4s) vem do config via ui/app.py.
        self._cooldown_sec = max(0.0, float(cooldown_sec or 0.0))
        self._dedupe_window_sec = max(0.0, float(dedupe_window_sec or 0.0))
        self.match_partials = False
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._last_fire_at = 0.0
        self._last_fire_norm = ""
        self._resume_timer: Optional[threading.Timer] = None
        self._failures = 0
        self.last_error: Optional[str] = None

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
        # Sem join(): parar não pode travar o shutdown do app.
        self._running = False
        self._cancel_resume_timer()
        stop_stream = getattr(self.backend, "stop_stream", None)
        if callable(stop_stream):
            try:
                stop_stream()
            except Exception as exc:
                logger.debug("Wake word: falha ao encerrar stream: %s", exc)
        self._thread = None

    def pause(self) -> None:
        """Pausa a detecção (ex.: durante sessão Live, que usa o microfone)."""
        self._paused = True

    def resume(self) -> None:
        """Reativa a detecção, cancelando qualquer rearme pendente do suspend()."""
        self._cancel_resume_timer()
        self._paused = False

    def suspend(self, seconds: float) -> None:
        """Pausa e rearma sozinho após `seconds` (anti-eco depois da IA falar).

        Sem essa surdez temporária, a própria resposta falada pelo Copilot era
        transcrita de volta e reativava a wake word — um loop de auto-chamada.
        """
        self._paused = True
        self._cancel_resume_timer()
        seconds = max(0.0, float(seconds or 0.0))
        if seconds <= 0:
            self._paused = False
            return
        timer = threading.Timer(seconds, self._resume_from_suspend)
        timer.daemon = True
        self._resume_timer = timer
        timer.start()

    def _resume_from_suspend(self) -> None:
        self._resume_timer = None
        self._paused = False
        logger.debug("Wake word rearmada após a suspensão anti-eco.")

    def _cancel_resume_timer(self) -> None:
        timer = self._resume_timer
        self._resume_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def set_phrases(self, phrases: Iterable[str]) -> None:
        """Atualiza as frases em tempo de execução (após editar preferências)."""
        self._phrases = [p for p in (phrases or []) if normalize_text(p)]

    # -- loop ----------------------------------------------------------------
    def _loop(self) -> None:
        """Consome o stream com retry e backoff exponencial.

        Só retenta quando o stream terminou de forma **anormal** (exceção ou
        `last_stream_error` preenchido). Um backend que simplesmente esgota o
        gerador terminou limpo — retentar ali faria o loop girar para sempre e
        travaria o join() dos testes.
        """
        backoff = 1.0
        while self._running:
            ended_clean = True
            try:
                for item in self.backend.stream_transcripts():
                    if not self._running:
                        return
                    text, is_final = unpack_transcript(item)
                    self._handle_transcript(text, is_final)
            except Exception as exc:
                ended_clean = False
                self._failures += 1
                self.last_error = str(exc)
                logger.warning("Wake word: falha no stream (%d): %s", self._failures, exc)
                self._notify_error(str(exc))

            if not self._running:
                break

            unexpected = bool(getattr(self.backend, "last_stream_error", ""))
            if ended_clean and not unexpected:
                break  # stream esgotado normalmente (fim/fake de teste)

            if self._failures >= self.MAX_STREAM_FAILURES:
                msg = f"Palavra de ativação desativada após {self._failures} falhas: {self.last_error}"
                logger.error(msg)
                self._notify_error(msg)
                self._running = False
                break

            time.sleep(min(backoff, 15.0))
            backoff = min(backoff * 2, 15.0)
        self._running = False

    def _notify_error(self, message: str) -> None:
        """Avisa a UI sobre falhas — o callback nunca pode derrubar o loop."""
        handler = getattr(self, "on_error", None)
        if handler is None:
            return
        try:
            handler(message)
        except Exception as exc:
            logger.warning("Wake word: erro no callback on_error: %s", exc)

    def _handle_transcript(self, transcript: str, is_final: bool = True) -> bool:
        """Processa um transcript; dispara on_wake se casar e não pausado.

        Retorna True se uma frase foi detectada (e o callback disparado).

        Três travas contra o disparo múltiplo — o Vosk reemite o mesmo texto
        várias vezes por segundo enquanto a fala acontece:
          1. ignora parciais (a não ser que `match_partials` esteja ligado);
          2. dedup: mesmo texto normalizado dentro da janela;
          3. cooldown global entre duas ativações.
        """
        if self._paused or not self._running:
            return False
        if not is_final and not self.match_partials:
            return False

        hit = contains_wake_phrase(transcript, self._phrases)
        if not hit:
            return False

        now = time.monotonic()
        norm = normalize_text(transcript)
        if norm and norm == self._last_fire_norm and (now - self._last_fire_at) < self._dedupe_window_sec:
            logger.debug("Wake word ignorada: mesmo transcript em %.2fs.", now - self._last_fire_at)
            return False
        if (now - self._last_fire_at) < self._cooldown_sec:
            logger.debug("Wake word ignorada: dentro do intervalo de %.1fs.", self._cooldown_sec)
            return False

        self._last_fire_at = now
        self._last_fire_norm = norm

        command = extract_command(transcript, hit)
        logger.info("Wake word detectada: %r (comando: %r)", hit, command)
        if self.on_wake:
            try:
                self.on_wake(hit, command)
            except Exception as exc:  # callback não deve derrubar o loop
                logger.warning("Wake word: erro no callback on_wake: %s", exc)
        # Descarta o estado do recognizer: o rabo da frase não pode re-disparar.
        reset = getattr(self.backend, "reset_stream", None)
        if callable(reset):
            try:
                reset()
            except Exception:
                pass
        return True

    # -- teste/diagnóstico --------------------------------------------------
    def simulate_phrase(self, transcript: str) -> bool:
        """Simula um transcript (para testes headless e diagnóstico na UI)."""
        return self._handle_transcript(transcript)
