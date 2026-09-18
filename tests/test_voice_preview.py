"""Testes unitários para VoicePreviewService (Prévia auditiva e demonstração de vozes do assistente)."""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from zorin_copilot.ai.voice_preview import VoicePreviewService, VOICE_SCRIPTS, VOICE_CHORD_FREQS


class VoicePreviewServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmp_dir.name)
        self.service = VoicePreviewService(cache_dir=self.cache_dir)

    def tearDown(self):
        self.service.stop()
        self.tmp_dir.cleanup()

    def test_voice_catalog_consistency(self):
        # Assegura que todas as 8 vozes possuem scripts e acordes harmônicos definidos
        voices = ["Puck", "Aoede", "Charon", "Kore", "Fenrir", "Zephyr", "Callirrhoe", "Sulafat"]
        for v in voices:
            self.assertIn(v, VOICE_SCRIPTS)
            self.assertIn(v, VOICE_CHORD_FREQS)
            self.assertTrue(len(VOICE_SCRIPTS[v]) > 10)
            self.assertTrue(len(VOICE_CHORD_FREQS[v]) >= 2)

    def test_generate_acoustic_chime_wav(self):
        target = self.cache_dir / "puck.wav"
        out_path = self.service.generate_acoustic_chime_wav("Puck", target)
        self.assertTrue(out_path.exists())
        self.assertTrue(out_path.stat().st_size > 1000)

        # Valida que é um WAV 24kHz, 16-bit mono válido
        with wave.open(str(out_path), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 24000)
            frames = wf.readframes(wf.getnframes())
            self.assertTrue(len(frames) > 0)

    def test_get_or_create_preview_audio_caching(self):
        # Primeira chamada: gera o arquivo no cache
        path1 = self.service.get_or_create_preview_audio("Aoede")
        self.assertTrue(path1.exists())
        mtime1 = path1.stat().st_mtime

        # Segunda chamada: deve reutilizar o arquivo existente sem regenerar
        path2 = self.service.get_or_create_preview_audio("Aoede")
        self.assertEqual(path1, path2)
        mtime2 = path2.stat().st_mtime
        self.assertEqual(mtime1, mtime2)

    def test_play_voice_and_stop(self):
        finished_called = False

        def _on_done():
            nonlocal finished_called
            finished_called = True

        ok = self.service.play_voice("Charon", on_finished=_on_done)
        self.assertTrue(ok)
        # Em ambiente de teste automatizado (ZORIN_TEST_MODE/pytest), deve concluir sem travar
        self.assertTrue(finished_called)

        # Teste de parada
        self.service.stop()
        self.assertFalse(self.service.is_playing())

    def test_is_playing_query(self):
        # Quando parado
        self.assertFalse(self.service.is_playing("Puck"))
        self.assertFalse(self.service.is_playing())


if __name__ == "__main__":
    unittest.main()
