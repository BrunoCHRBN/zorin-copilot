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


if __name__ == "__main__":
    unittest.main()
