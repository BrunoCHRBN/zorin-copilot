# Decisão de design: testes da Fase 3 (parte C — wake word). O motor é puro e
# headless: o backend de STT é injetável, então os testes usam um backend fake
# (sem microfone, sem Vosk, sem GTK). Validam normalização de texto, matching
# por substring, ciclo de vida do engine (start/stop/pause/resume), troca de
# frases em tempo de execução (o usuário pode editar as palavras de ativação
# após a implementação) e degradação graciosa sem backend/modelo.

"""Testes da Fase 3 (parte C): wake word configurável (palavra de ativação)."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.core.config import CopilotConfig  # noqa: E402
from zorin_copilot.shell.wake_word import (  # noqa: E402
    NullWakeWordBackend,
    VoskWakeWordBackend,
    WakeWordEngine,
    contains_wake_phrase,
    default_backend,
    extract_command,
    normalize_text,
    pcm_rms,
    sensitivity_to_threshold,
    unpack_transcript,
)


class _FakeBackend:
    """Backend fake: disponível e alimentado por uma fila de transcripts."""

    name = "fake"

    def __init__(self, transcripts=None, available=True):
        self._transcripts = list(transcripts or [])
        self._available = available
        self.streamed = False

    def is_available(self):
        return self._available

    def stream_transcripts(self):
        self.streamed = True
        yield from self._transcripts


class NormalizeTest(unittest.TestCase):
    def test_lowercase_and_strip(self):
        self.assertEqual(normalize_text("  OK Copilot  "), "ok copilot")

    def test_punctuation_removed(self):
        self.assertEqual(normalize_text("ok, copilot!"), "ok copilot")
        self.assertEqual(normalize_text("Olá, Copilot."), "olá copilot")

    def test_collapses_whitespace(self):
        self.assertEqual(normalize_text("ok    copilot"), "ok copilot")

    def test_empty(self):
        self.assertEqual(normalize_text(""), "")
        self.assertEqual(normalize_text(None), "")

    def test_accents_preserved(self):
        # Português: acentos devem sobreviver à normalização.
        self.assertIn("olá", normalize_text("Olá"))


class ContainsWakePhraseTest(unittest.TestCase):
    PHRASES = ["ok copilot", "olá copilot"]

    def test_exact_match(self):
        self.assertEqual(contains_wake_phrase("ok copilot", self.PHRASES), "ok copilot")

    def test_substring_match_in_sentence(self):
        # A frase pode vir no meio de uma fala maior.
        self.assertEqual(
            contains_wake_phrase("por favor ok copilot abra o navegador", self.PHRASES),
            "ok copilot",
        )

    def test_case_and_punctuation_insensitive(self):
        self.assertEqual(contains_wake_phrase("OK, Copilot!", self.PHRASES), "ok copilot")

    def test_accented_phrase(self):
        self.assertEqual(contains_wake_phrase("Olá Copilot, tudo bem?", self.PHRASES), "olá copilot")

    def test_no_match(self):
        self.assertIsNone(contains_wake_phrase("abrir o navegador", self.PHRASES))
        self.assertIsNone(contains_wake_phrase("", self.PHRASES))
        self.assertIsNone(contains_wake_phrase("ok cop", self.PHRASES))  # parcial não casa

    def test_empty_phrases_ignored(self):
        self.assertIsNone(contains_wake_phrase("ok copilot", ["", "  "]))


class ExtractCommandTest(unittest.TestCase):
    """Fase 4A: o comando dito após a wake phrase é extraído do transcript."""

    def test_command_after_phrase(self):
        self.assertEqual(
            extract_command("ok copilot abre o navegador", "ok copilot"),
            "abre o navegador",
        )

    def test_phrase_alone_has_no_command(self):
        self.assertEqual(extract_command("ok copilot", "ok copilot"), "")

    def test_phrase_in_the_middle(self):
        # Prefixo antes da frase não faz parte do comando.
        self.assertEqual(
            extract_command("por favor ok copilot abre o navegador", "ok copilot"),
            "abre o navegador",
        )

    def test_case_and_punctuation_insensitive(self):
        self.assertEqual(
            extract_command("OK, Copilot! Abre o navegador.", "ok copilot"),
            "abre o navegador",
        )

    def test_accented_phrase(self):
        self.assertEqual(
            extract_command("Olá Copilot, que horas são?", "olá copilot"),
            "que horas são",
        )

    def test_phrase_not_found(self):
        self.assertEqual(extract_command("abre o navegador", "ok copilot"), "")

    def test_empty_inputs(self):
        self.assertEqual(extract_command("", "ok copilot"), "")
        self.assertEqual(extract_command("ok copilot abre", ""), "")
        self.assertEqual(extract_command(None, "ok copilot"), "")

    def test_uses_first_occurrence(self):
        self.assertEqual(
            extract_command("ok copilot repete ok copilot de novo", "ok copilot"),
            "repete ok copilot de novo",
        )


class EngineCommandExtractionTest(unittest.TestCase):
    """O engine entrega (frase, comando) ao callback — Fase 4A."""

    def test_on_wake_receives_phrase_and_command(self):
        events = []
        engine = WakeWordEngine(
            phrases=["ok copilot"],
            backend=_FakeBackend(),
            on_wake=lambda p, c: events.append((p, c)),
        )
        engine._running = True
        self.assertTrue(engine.simulate_phrase("ok copilot abre o navegador"))
        self.assertEqual(events, [("ok copilot", "abre o navegador")])

    def test_on_wake_command_empty_when_phrase_alone(self):
        events = []
        engine = WakeWordEngine(
            phrases=["ok copilot"],
            backend=_FakeBackend(),
            on_wake=lambda p, c: events.append((p, c)),
        )
        engine._running = True
        engine.simulate_phrase("ok copilot")
        self.assertEqual(events, [("ok copilot", "")])


class EngineLifecycleTest(unittest.TestCase):
    def _make_engine(self, phrases=("ok copilot",), backend=None, hits=None):
        on_wake = (lambda p, c="": hits.append(p)) if hits is not None else None
        return WakeWordEngine(
            phrases=phrases,
            backend=backend if backend is not None else _FakeBackend(),
            on_wake=on_wake,
        )

    def test_not_available_without_phrases(self):
        engine = self._make_engine(phrases=[])
        self.assertFalse(engine.is_available())
        self.assertFalse(engine.start())

    def test_not_available_without_backend(self):
        engine = self._make_engine(backend=_FakeBackend(available=False))
        self.assertFalse(engine.is_available())
        self.assertFalse(engine.start())

    def test_null_backend_never_available(self):
        engine = self._make_engine(backend=NullWakeWordBackend())
        self.assertFalse(engine.is_available())
        self.assertFalse(engine.start())

    def test_start_runs_loop_and_detects(self):
        hits = []
        backend = _FakeBackend(transcripts=["nada aqui", "ok copilot abra o navegador"])
        engine = self._make_engine(backend=backend, hits=hits)
        self.assertTrue(engine.start())
        # O backend fake esgota os transcripts rápido; espera a thread terminar.
        engine._thread.join(timeout=5)
        self.assertEqual(hits, ["ok copilot"])
        # O loop encerra quando o stream acaba.
        self.assertFalse(engine.running)

    def test_pause_suppresses_detection(self):
        hits = []
        engine = self._make_engine(hits=hits)
        engine._running = True  # simula engine ativo
        engine.pause()
        self.assertFalse(engine.simulate_phrase("ok copilot"))
        self.assertEqual(hits, [])
        engine.resume()
        self.assertTrue(engine.simulate_phrase("ok copilot"))
        self.assertEqual(hits, ["ok copilot"])

    def test_stopped_engine_does_not_detect(self):
        hits = []
        engine = self._make_engine(hits=hits)
        engine.pause()  # pausado e não rodando
        self.assertFalse(engine.simulate_phrase("ok copilot"))
        self.assertEqual(hits, [])

    def test_callback_exception_does_not_crash(self):
        def bad_callback(_phrase, _command=""):
            raise RuntimeError("boom")

        engine = WakeWordEngine(
            phrases=["ok copilot"], backend=_FakeBackend(), on_wake=bad_callback
        )
        engine._running = True
        # Não deve propagar a exceção do callback.
        self.assertTrue(engine.simulate_phrase("ok copilot"))

    def test_no_callback_still_reports_hit(self):
        engine = self._make_engine()  # on_wake=None
        engine._running = True
        self.assertTrue(engine.simulate_phrase("ok copilot"))
        self.assertFalse(engine.simulate_phrase("qualquer outra coisa"))


class PhraseEditingTest(unittest.TestCase):
    """O usuário pode definir e MODIFICAR as frases após a implementação."""

    def test_set_phrases_at_runtime(self):
        hits = []
        engine = WakeWordEngine(
            phrases=["ok copilot"],
            backend=_FakeBackend(),
            on_wake=lambda p, c="": hits.append(p),
        )
        engine._running = True

        # Frase antiga funciona, nova ainda não.
        self.assertTrue(engine.simulate_phrase("ok copilot"))
        self.assertFalse(engine.simulate_phrase("ei assistente"))

        # Troca as frases (como ao salvar as preferências).
        engine.set_phrases(["ei assistente", "computador"])
        self.assertEqual(engine.phrases, ["ei assistente", "computador"])
        self.assertFalse(engine.simulate_phrase("ok copilot"))  # antiga não vale mais
        self.assertTrue(engine.simulate_phrase("ei assistente, me ajude"))
        self.assertEqual(hits, ["ok copilot", "ei assistente"])

    def test_set_phrases_filters_empty(self):
        engine = WakeWordEngine(phrases=["ok copilot"], backend=_FakeBackend())
        engine.set_phrases(["", "   ", "nova frase"])
        self.assertEqual(engine.phrases, ["nova frase"])

    def test_empty_phrase_list_makes_unavailable(self):
        engine = WakeWordEngine(phrases=["ok copilot"], backend=_FakeBackend())
        self.assertTrue(engine.is_available())
        engine.set_phrases([])
        self.assertFalse(engine.is_available())


class BackendSelectionTest(unittest.TestCase):
    def test_default_backend_null_without_model(self):
        backend = default_backend("")
        self.assertIsInstance(backend, NullWakeWordBackend)

    def test_default_backend_falls_back_when_model_unusable(self):
        # Caminho inexistente: Vosk não carrega -> cai para Null (graceful).
        backend = default_backend("/caminho/inexistente/modelo-vosk")
        self.assertIsInstance(backend, NullWakeWordBackend)

    def test_vosk_backend_unavailable_without_model(self):
        backend = VoskWakeWordBackend("")
        self.assertFalse(backend.is_available())

    def test_vosk_backend_records_load_error(self):
        backend = VoskWakeWordBackend("/caminho/inexistente/modelo-vosk")
        # Sem o pacote vosk ou sem modelo válido: indisponível, mas sem exceção.
        self.assertFalse(backend.is_available())
        self.assertIsNotNone(backend.load_error)


class EngineThreadSafetyTest(unittest.TestCase):
    def test_stop_ends_loop(self):
        # Backend com stream "infinito" de transcripts irrelevantes.
        def infinite():
            while True:
                yield "silêncio"

        class _InfiniteBackend(_FakeBackend):
            def stream_transcripts(self):
                return infinite()

        engine = WakeWordEngine(phrases=["ok copilot"], backend=_InfiniteBackend())
        self.assertTrue(engine.start())
        thread = engine._thread
        engine.stop()
        thread.join(timeout=5)
        self.assertFalse(engine.running)

    def test_simulate_phrase_threadsafe_call(self):
        # simulate_phrase pode ser chamado de qualquer thread (diagnóstico na UI).
        hits = []
        engine = WakeWordEngine(
            phrases=["ok copilot"], backend=_FakeBackend(), on_wake=lambda p, c="": hits.append(p)
        )
        engine._running = True
        t = threading.Thread(target=lambda: engine.simulate_phrase("ok copilot"))
        t.start()
        t.join(timeout=5)
        self.assertEqual(hits, ["ok copilot"])


class ConfigPersistenceTest(unittest.TestCase):
    """Os campos de wake word são definidos e modificáveis via config."""

    def test_defaults(self):
        cfg = CopilotConfig()
        self.assertFalse(cfg.wake_word_enabled)  # opt-in
        self.assertEqual(cfg.wake_phrases, ["ok copilot", "olá copilot"])
        self.assertEqual(cfg.wake_word_model_path, "")

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
                cfg = CopilotConfig()
                cfg.wake_word_enabled = True
                cfg.wake_phrases = ["ei assistente", "computador"]
                cfg.wake_word_model_path = "/home/user/modelos/vosk-pt"
                cfg.save()

                loaded = CopilotConfig.load()
                self.assertTrue(loaded.wake_word_enabled)
                self.assertEqual(loaded.wake_phrases, ["ei assistente", "computador"])
                self.assertEqual(loaded.wake_word_model_path, "/home/user/modelos/vosk-pt")

    def test_load_legacy_config_without_wake_fields(self):
        # Configs antigas (sem os campos novos) carregam com os defaults.
        import json

        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = os.path.join(tmp, "zorin-copilot")
            os.makedirs(cfg_dir)
            with open(os.path.join(cfg_dir, "config.json"), "w") as f:
                json.dump({"provider": "gemini"}, f)
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
                loaded = CopilotConfig.load()
                self.assertFalse(loaded.wake_word_enabled)
                self.assertEqual(loaded.wake_phrases, ["ok copilot", "olá copilot"])


class PcmRmsTest(unittest.TestCase):
    """O gate de VAD depende deste cálculo; errado, o wake word surta ou surda."""

    def test_buffer_vazio(self):
        self.assertEqual(pcm_rms(b""), 0.0)

    def test_silencio(self):
        self.assertEqual(pcm_rms(b"\x00" * 64), 0.0)

    def test_volume_maximo(self):
        import array

        self.assertAlmostEqual(pcm_rms(array.array("h", [32767] * 100).tobytes()), 1.0, places=2)

    def test_buffer_impar_nao_estoura(self):
        self.assertGreaterEqual(pcm_rms(b"\x01\x02\x03"), 0.0)

    def test_meio_volume_fica_entre(self):
        import array

        rms = pcm_rms(array.array("h", [16000] * 100).tobytes())
        self.assertGreater(rms, 0.4)
        self.assertLess(rms, 0.6)


class SensitivityTest(unittest.TestCase):
    def test_mais_sensivel_tem_limiar_menor(self):
        self.assertLess(sensitivity_to_threshold(1.0), sensitivity_to_threshold(0.0))

    def test_clampa_fora_da_faixa(self):
        self.assertEqual(sensitivity_to_threshold(5.0), sensitivity_to_threshold(1.0))
        self.assertEqual(sensitivity_to_threshold(-3.0), sensitivity_to_threshold(0.0))

    def test_valor_invalido_usa_meio(self):
        self.assertEqual(sensitivity_to_threshold("ruim"), sensitivity_to_threshold(0.5))


class UnpackTranscriptTest(unittest.TestCase):
    def test_str_eh_tratada_como_final(self):
        # É o que mantém os testes legados (e backends fake) funcionando.
        self.assertEqual(unpack_transcript("ok copilot"), ("ok copilot", True))

    def test_tupla_preserva_flag(self):
        self.assertEqual(unpack_transcript(("ok copilot", False)), ("ok copilot", False))
        self.assertEqual(unpack_transcript(("ok copilot", True)), ("ok copilot", True))

    def test_tupla_sem_flag_assume_final(self):
        self.assertEqual(unpack_transcript(("ok copilot",)), ("ok copilot", True))

    def test_vazio(self):
        self.assertEqual(unpack_transcript(""), ("", True))
        self.assertEqual(unpack_transcript(None), ("", True))


class MultiFireRegressionTest(unittest.TestCase):
    """O bug original: o Vosk reemitia o texto a cada parcial e a wake word
    disparava várias vezes para uma única fala."""

    def _drain(self, engine, items):
        for item in items:
            text, is_final = unpack_transcript(item)
            engine._handle_transcript(text, is_final)

    def test_rajada_de_parciais_dispara_uma_vez(self):
        hits = []
        engine = WakeWordEngine(
            phrases=["ok copilot"],
            backend=NullWakeWordBackend(),
            on_wake=lambda p, c="": hits.append(p),
        )
        engine._running = True
        self._drain(
            engine,
            [
                ("ok cop", False),
                ("ok copilot", False),
                ("ok copilot", False),
                ("ok copilot abre o navegador", True),
            ],
        )
        self.assertEqual(hits, ["ok copilot"])

    def test_mesmo_final_repetido_nao_re_dispara(self):
        hits = []
        engine = WakeWordEngine(
            phrases=["ok copilot"],
            backend=NullWakeWordBackend(),
            on_wake=lambda p, c="": hits.append(p),
            dedupe_window_sec=3.0,
        )
        engine._running = True
        self._drain(engine, [("ok copilot", True), ("ok copilot", True), ("ok copilot", True)])
        self.assertEqual(hits, ["ok copilot"])

    def test_cooldown_global_bloqueia_frases_diferentes(self):
        hits = []
        engine = WakeWordEngine(
            phrases=["ok copilot"],
            backend=NullWakeWordBackend(),
            on_wake=lambda p, c="": hits.append(p),
            cooldown_sec=4.0,
        )
        engine._running = True
        self._drain(engine, [("ok copilot", True), ("ok copilot abre o navegador", True)])
        # Intencional: dentro do cooldown nada mais passa, mesmo sendo outra fala.
        self.assertEqual(hits, ["ok copilot"])

    def test_apos_o_cooldown_dispara_de_novo(self):
        hits = []
        engine = WakeWordEngine(
            phrases=["ok copilot"],
            backend=NullWakeWordBackend(),
            on_wake=lambda p, c="": hits.append(p),
            cooldown_sec=4.0,
        )
        engine._running = True
        self._drain(engine, [("ok copilot", True)])
        engine._last_fire_at -= 5.0  # volta o relógio
        self._drain(engine, [("ok copilot abre o navegador", True)])
        self.assertEqual(len(hits), 2)


class FinalOnlyTest(unittest.TestCase):
    def test_parcial_ignorado_por_padrao(self):
        hits = []
        engine = WakeWordEngine(
            phrases=["ok copilot"], backend=NullWakeWordBackend(), on_wake=lambda p, c="": hits.append(p)
        )
        engine._running = True
        self.assertFalse(engine._handle_transcript("ok copilot", is_final=False))
        self.assertEqual(hits, [])
        self.assertTrue(engine._handle_transcript("ok copilot", is_final=True))
        self.assertEqual(hits, ["ok copilot"])

    def test_match_partials_liga_parciais(self):
        hits = []
        engine = WakeWordEngine(
            phrases=["ok copilot"], backend=NullWakeWordBackend(), on_wake=lambda p, c="": hits.append(p)
        )
        engine._running = True
        engine.match_partials = True
        self.assertTrue(engine._handle_transcript("ok copilot", is_final=False))
        # O dedup ainda segura a repetição idêntica.
        self.assertFalse(engine._handle_transcript("ok copilot", is_final=False))
        self.assertEqual(hits, ["ok copilot"])


class SuspendTest(unittest.TestCase):
    """Anti-eco: sem isso a própria resposta da IA reativava a wake word."""

    def test_suspende_e_rearma_sozinho(self):
        engine = WakeWordEngine(["ok copilot"], backend=NullWakeWordBackend())
        engine.suspend(0.1)
        self.assertTrue(engine.paused)
        deadline = threading.Event()

        import time

        for _ in range(60):
            if not engine.paused:
                deadline.set()
                break
            time.sleep(0.01)
        self.assertTrue(deadline.is_set(), "suspend() não rearmou sozinho")
        self.assertFalse(engine.paused)

    def test_suspende_zero_reativa_na_hora(self):
        engine = WakeWordEngine(["ok copilot"], backend=NullWakeWordBackend())
        engine.suspend(0)
        self.assertFalse(engine.paused)

    def test_resume_cancela_rearme_pendente(self):
        engine = WakeWordEngine(["ok copilot"], backend=NullWakeWordBackend())
        engine.suspend(5.0)
        self.assertTrue(engine.paused)
        engine.resume()
        self.assertFalse(engine.paused)
        self.assertIsNone(engine._resume_timer)


class _ExplodingBackend:
    """Backend que estoura N vezes e depois entrega uma frase."""

    name = "fake"

    def __init__(self, failures=1, then=()):
        self._failures = failures
        self._then = list(then)
        self.last_stream_error = "mic morreu"
        self.is_available = lambda: True

    def stream_transcripts(self):
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("falha de captura")
        yield from self._then


class RetryAndErrorTest(unittest.TestCase):
    def test_retenta_apos_falha_e_ainda_detecta(self):
        errors = []
        hits = []
        backend = _ExplodingBackend(failures=1, then=[("ok copilot", True)])
        engine = WakeWordEngine(
            phrases=["ok copilot"],
            backend=backend,
            on_wake=lambda p, c="": hits.append(p),
            on_error=errors.append,
        )
        engine.MAX_STREAM_FAILURES = 3
        self.assertTrue(engine.start())
        engine._thread.join(timeout=5)
        self.assertEqual(hits, ["ok copilot"])
        self.assertTrue(errors)

    def test_desiste_apos_limite_de_falhas(self):
        errors = []

        class _AlwaysBroken:
            name = "fake"
            last_stream_error = "mic morreu"

            def is_available(self):
                return True

            def stream_transcripts(self):
                raise RuntimeError("sempre quebra")

        engine = WakeWordEngine(
            phrases=["ok copilot"], backend=_AlwaysBroken(), on_error=errors.append
        )
        engine.MAX_STREAM_FAILURES = 2
        with mock.patch("time.sleep"):  # não esperar o backoff de verdade
            self.assertTrue(engine.start())
            engine._thread.join(timeout=5)
        self.assertFalse(engine.running)
        self.assertTrue(any("desativada" in e for e in errors))

    def test_gerador_esgotado_nao_retenta(self):
        """Um backend que só acaba (fim normal) deve encerrar o loop.

        Retentar aqui travaria o join() dos testes em loop infinito.
        """

        class _QuietBackend:
            name = "fake"

            def is_available(self):
                return True

            def stream_transcripts(self):
                return iter(())

        engine = WakeWordEngine(phrases=["ok copilot"], backend=_QuietBackend())
        self.assertTrue(engine.start())
        engine._thread.join(timeout=5)
        self.assertFalse(engine._thread.is_alive())
        self.assertFalse(engine.running)


class StopStreamTest(unittest.TestCase):
    def test_stop_mata_o_processo_de_captura(self):
        backend = VoskWakeWordBackend(model_path="")
        proc = mock.MagicMock()
        backend._proc = proc
        backend.stop_stream()
        proc.terminate.assert_called_once()
        self.assertIsNone(backend._proc)

    def test_stop_mata_com_kill_se_wait_falha(self):
        backend = VoskWakeWordBackend(model_path="")
        proc = mock.MagicMock()
        proc.wait.side_effect = Exception("timeout")
        backend._proc = proc
        backend.stop_stream()
        proc.kill.assert_called_once()

    def test_engine_stop_chama_stop_stream(self):
        backend = VoskWakeWordBackend(model_path="")
        backend._proc = mock.MagicMock()
        engine = WakeWordEngine(["ok copilot"], backend=backend)
        engine._running = True
        engine.stop()
        self.assertIsNone(backend._proc)
        self.assertFalse(engine._running)


class ArecordFallbackTest(unittest.TestCase):
    def test_cai_para_arecord_sem_pipewire(self):
        backend = VoskWakeWordBackend(model_path="")
        with mock.patch("shutil.which", side_effect=lambda b: b == "arecord"):
            self.assertEqual(
                backend._resolve_record_command(),
                ["arecord", "-r", "16000", "-f", "S16_LE", "-c", "1", "-"],
            )

    def test_sem_nenhum_comando_devolve_none(self):
        backend = VoskWakeWordBackend(model_path="")
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(backend._resolve_record_command())

    def test_dispositivo_vai_para_o_comando(self):
        backend = VoskWakeWordBackend(model_path="", device="bluez_input.1")
        with mock.patch("shutil.which", side_effect=lambda b: b == "arecord"):
            self.assertIn("-D", backend._resolve_record_command())

    def test_stream_indisponivel_devolve_iterador_vazio(self):
        # `return` puro aqui fazia o loop estourar TypeError.
        backend = VoskWakeWordBackend(model_path="")
        self.assertEqual(list(backend.stream_transcripts()), [])


class _FakeProc:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.stdout = self
        self.terminated = False

    def read(self, _n):
        return self._chunks.pop(0) if self._chunks else b""

    def terminate(self):
        self.terminated = True

    def wait(self, _t=None):
        return 0

    def kill(self):
        pass


class _FakeVosk:
    """Vosk mínimo: conta quantas vezes o áudio chegou ao reconhecedor."""

    def __init__(self):
        self.accepted = 0

    def Model(self, _path):  # noqa: N802
        return object()

    def KaldiRecognizer(self, _model, _rate):  # noqa: N802
        outer = self

        class _Rec:
            def AcceptWaveform(self, _data):  # noqa: N802
                outer.accepted += 1
                return False

            def Result(self):  # noqa: N802
                return '{"text": "ok copilot"}'

            def PartialResult(self):  # noqa: N802
                return '{"partial": "ok copilot"}'

            def FinalResult(self):  # noqa: N802
                return '{"text": "ok copilot"}'

        return _Rec()


class VadGatingTest(unittest.TestCase):
    """Silêncio não pode ser decodificado: é CPU gasta para nada o dia todo."""

    def _run(self, chunks, vad_enabled=True):
        fake = _FakeVosk()
        with mock.patch.dict(sys.modules, {"vosk": fake}):
            backend = VoskWakeWordBackend(
                model_path="/fake-model",
                record_command=["fake-record"],
                vad_enabled=vad_enabled,
            )
            with mock.patch("subprocess.Popen", return_value=_FakeProc(chunks)):
                return list(backend.stream_transcripts()), fake.accepted

    def test_silencio_nao_eh_decodificado(self):
        silence = [b"\x00" * 4000 for _ in range(20)]
        out, accepted = self._run(silence)
        self.assertEqual(out, [])
        self.assertEqual(accepted, 0, "silêncio chegou ao reconhecedor")

    def test_fala_eh_decodificada(self):
        import array

        loud = array.array("h", [20000] * 2000).tobytes()
        out, accepted = self._run([loud])
        self.assertGreater(accepted, 0)
        self.assertTrue(out)

    def test_sem_vad_decodifica_tudo(self):
        silence = [b"\x00" * 4000 for _ in range(5)]
        _out, accepted = self._run(silence, vad_enabled=False)
        self.assertGreater(accepted, 0)


class SampleLevelTest(unittest.TestCase):
    def test_devolve_pico_do_pcm(self):
        import array

        backend = VoskWakeWordBackend(model_path="", record_command=["fake-record"])
        loud = array.array("h", [20000] * 2000).tobytes()
        with mock.patch("subprocess.Popen", return_value=_FakeProc([loud])):
            peak = backend.sample_level(0.2)
        self.assertGreater(peak, 0.4)

    def test_sem_comando_de_captura_lanca_erro_claro(self):
        backend = VoskWakeWordBackend(model_path="")
        with mock.patch.object(backend, "_resolve_record_command", return_value=None):
            with self.assertRaises(RuntimeError):
                backend.sample_level(0.1)


if __name__ == "__main__":
    unittest.main()
