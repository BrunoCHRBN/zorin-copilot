"""Atribuição de turno na transcrição (Entrega G / P1-7).

Antes desta entrega havia **três** divergências silenciosas, todas medidas no código:

1. o cliente de nuvem **nunca** gravava o turno do **usuário** (não lia
   `inputTranscription`) — o transcrito tinha metade dos donos;
2. o papel do copiloto tinha dois nomes (`"assistant"` na nuvem, `"agent"` no local);
3. a forma da entrada tinha duas versões (tupla na nuvem, dict no local), devolvidas sob a
   **mesma** chave `"transcripts"`.

A consequência funcional do item 1 está no resumo contínuo: como ele existe para sobreviver
a reconexões, e a nuvem só gravava `assistant`, **tudo o que o usuário disse se perdia**
quando a sessão caía. É isso que os testes abaixo fixam.

**Limite declarado:** isto é *atribuição de turno* (usuário vs. copiloto), **não** diarização
multi-falante — três pessoas numa reunião aparecem todas como `user`.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest

from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.transcript import (
    ROLE_ASSISTANT,
    ROLE_USER,
    entry_role,
    entry_text,
    extract_transcription_text,
    make_entry,
    normalize_role,
)


class RoleVocabularyTest(unittest.TestCase):
    """O vocabulário canônico e a normalização (inclusive de dados antigos)."""

    def test_canonical_roles_round_trip(self):
        self.assertEqual(normalize_role(ROLE_USER), ROLE_USER)
        self.assertEqual(normalize_role(ROLE_ASSISTANT), ROLE_ASSISTANT)

    def test_legacy_agent_becomes_assistant(self):
        """`"agent"` era o rótulo histórico do cliente local."""
        self.assertEqual(normalize_role("agent"), ROLE_ASSISTANT)
        self.assertEqual(normalize_role("AGENTE"), ROLE_ASSISTANT)

    def test_accented_and_unaccented_user_aliases(self):
        for alias in ("você", "voce", "usuário", "usuario", "USER"):
            with self.subTest(alias=alias):
                self.assertEqual(normalize_role(alias), ROLE_USER)

    def test_unknown_role_falls_back_to_assistant(self):
        """O erro perigoso seria o inverso: pôr palavras do copiloto na boca do usuário."""
        for valor in (None, "", "???", 123, "system"):
            with self.subTest(valor=valor):
                self.assertEqual(
                    normalize_role(valor),
                    ROLE_ASSISTANT,
                    "papel desconhecido não pode virar 'user'",
                )

    def test_make_entry_normalizes_and_coerces(self):
        self.assertEqual(make_entry("agent", "oi"), {"role": ROLE_ASSISTANT, "text": "oi"})
        self.assertEqual(make_entry("user", None), {"role": ROLE_USER, "text": ""})
        self.assertEqual(make_entry("user", 42), {"role": ROLE_USER, "text": "42"})

    def test_readers_tolerate_legacy_tuples(self):
        """Dados já persistidos podem estar em tupla; a leitura não pode explodir."""
        self.assertEqual(entry_role(("agent", "oi")), ROLE_ASSISTANT)
        self.assertEqual(entry_text(("agent", "oi")), "oi")
        self.assertEqual(entry_role(("user", "oi")), ROLE_USER)

    def test_readers_tolerate_new_dicts(self):
        self.assertEqual(entry_role({"role": "user", "text": "oi"}), ROLE_USER)
        self.assertEqual(entry_text({"role": "user", "text": "oi"}), "oi")

    def test_readers_tolerate_garbage(self):
        for valor in (None, 42, [], {"texto": "x"}):
            with self.subTest(valor=valor):
                self.assertEqual(entry_role(valor), ROLE_ASSISTANT)
                self.assertEqual(entry_text(valor), "")

    def test_readers_tolerate_truncated_entries(self):
        """Tupla de 1 elemento: o papel é legível, o texto não — e não pode explodir."""
        self.assertEqual(entry_role(("user",)), ROLE_USER)
        self.assertEqual(entry_text(("user",)), "")
        self.assertEqual(entry_role([]), ROLE_ASSISTANT)
        self.assertEqual(entry_text([]), "")

    def test_extract_transcription_text_handles_both_envelopes(self):
        self.assertEqual(extract_transcription_text({"text": "  oi  "}), "oi")
        self.assertEqual(extract_transcription_text("oi"), "oi")
        self.assertEqual(extract_transcription_text(None), "")
        self.assertEqual(extract_transcription_text({}), "")


class _FakeWS:
    """Entrega as mensagens uma a uma e encerra o laço na última."""

    def __init__(self, client, mensagens):
        self._client = client
        self._mensagens = list(mensagens)
        self._i = 0

    async def recv(self):
        if self._i >= len(self._mensagens):
            self._client._is_running = False
            return json.dumps({})
        msg = self._mensagens[self._i]
        self._i += 1
        if self._i >= len(self._mensagens):
            self._client._is_running = False
        return json.dumps(msg)


class ServerContentUserTurnTest(unittest.TestCase):
    """O handler de servidor precisa capturar a fala do **usuário**.

    Mutação que isto mata: remover a leitura de `inputTranscription` — o transcrito da nuvem
    volta a ter só o lado do copiloto, e nada mais quebra.
    """

    def _drive(self, mensagens, on_transcript=None):
        from zorin_copilot.ai.live import GeminiLiveClient

        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="x"))
        client._is_running = True
        client.on_transcript = on_transcript
        asyncio.run(client._server_receiver_loop(_FakeWS(client, mensagens)))
        return client

    def test_input_transcription_becomes_a_user_turn(self):
        client = self._drive([
            {"serverContent": {"inputTranscription": {"text": "qual é o preço do dólar"}}},
        ])
        self.assertEqual(client._transcripts_log, [
            {"role": ROLE_USER, "text": "qual é o preço do dólar"},
        ])

    def test_input_transcription_as_raw_string_also_works(self):
        """O envelope pode variar; perder a fala do usuário por isso seria frágil."""
        client = self._drive([
            {"serverContent": {"inputTranscription": "string crua"}},
        ])
        self.assertEqual(client._transcripts_log, [{"role": ROLE_USER, "text": "string crua"}])

    def test_empty_input_transcription_is_ignored(self):
        """Ruído/placeholder vazio não deve poluir o transcrito com turnos fantasmas."""
        client = self._drive([
            {"serverContent": {"inputTranscription": {"text": "   "}}},
            {"serverContent": {"inputTranscription": None}},
        ])
        self.assertEqual(client._transcripts_log, [])

    def test_model_turn_is_logged_as_assistant(self):
        client = self._drive([
            {"serverContent": {"modelTurn": {"parts": [{"text": "O dólar está em R$ 5,40."}]}}},
        ])
        self.assertEqual(client._transcripts_log, [
            {"role": ROLE_ASSISTANT, "text": "O dólar está em R$ 5,40."},
        ])

    def test_callback_receives_canonical_roles_for_both_sides(self):
        visto: list[tuple[str, str]] = []
        client = self._drive(
            [
                {"serverContent": {"inputTranscription": {"text": "abre o navegador"}}},
                {"serverContent": {"modelTurn": {"parts": [{"text": "Abrindo."}]}}},
            ],
            on_transcript=lambda role, txt: visto.append((role, txt)),
        )
        self.assertEqual(visto, [("user", "abre o navegador"), ("assistant", "Abrindo.")])
        self.assertEqual(client._transcripts_log[-1]["role"], ROLE_ASSISTANT)


class RollingSummaryTest(unittest.TestCase):
    """O resumo contínuo — onde a metade ausente realmente doía."""

    def _client(self, transcripts):
        from zorin_copilot.ai.live import GeminiLiveClient

        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="x"))
        client._transcripts_log = list(transcripts)
        client._session_start_time = time.time() - 12
        return client

    def test_summary_shows_both_sides(self):
        """Antes: como a nuvem só gravava `assistant`, toda linha saía como `copilot:`."""
        client = self._client([
            {"role": ROLE_USER, "text": "abre o navegador"},
            {"role": ROLE_ASSISTANT, "text": "Abrindo o Chrome."},
        ])
        texto = client._build_live_summary_text()
        self.assertIn("você: abre o navegador", texto)
        self.assertIn("copilot: Abrindo o Chrome.", texto)

    def test_summary_normalizes_the_legacy_agent_role(self):
        """Dados gravados por uma versão anterior (`agent`) continuam rotulando certo."""
        client = self._client([{"role": "agent", "text": "fala antiga"}])
        self.assertIn("copilot: fala antiga", client._build_live_summary_text())

    def test_summary_still_reads_legacy_tuples(self):
        """Uma sessão em andamento durante o upgrade não pode quebrar o resumo."""
        client = self._client([("user", "pergunta antiga"), ("assistant", "resposta antiga")])
        texto = client._build_live_summary_text()
        self.assertIn("você: pergunta antiga", texto)
        self.assertIn("copilot: resposta antiga", texto)

    def test_summary_truncates_and_skips_empty(self):
        client = self._client([
            {"role": ROLE_USER, "text": "x" * 400},
            {"role": ROLE_ASSISTANT, "text": ""},
        ])
        texto = client._build_live_summary_text()
        self.assertNotIn("x" * 400, texto)
        self.assertIn("...", texto)
        self.assertNotIn("copilot: \n", texto)


class ClientParityTest(unittest.TestCase):
    """Os dois clientes devolvem `"transcripts"` na **mesma** forma e vocabulário."""

    def _local(self, transcripts):
        from zorin_copilot.ai.local_voice import LocalLiveVoiceClient

        client = object.__new__(LocalLiveVoiceClient)   # sem Piper/Whisper/microfone
        client._transcripts_log = list(transcripts)
        client._executed_actions_log = []
        client._video_frames_count = 0
        client._session_start_time = time.time() - 5
        return client

    def _cloud(self, transcripts):
        from zorin_copilot.ai.live import GeminiLiveClient

        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="x"))
        client._transcripts_log = list(transcripts)
        client._executed_actions_log = []
        client._session_start_time = time.time() - 5
        return client

    def test_both_clients_return_the_same_entry_shape(self):
        """Antes: tupla na nuvem, dict no local — sob a mesma chave, sem teste nenhum."""
        entradas = [
            make_entry(ROLE_USER, "oi"),
            make_entry(ROLE_ASSISTANT, "olá"),
        ]
        do_local = self._local(entradas).get_session_summary()["transcripts"]
        da_nuvem = self._cloud(entradas).get_session_summary()["transcripts"]
        self.assertEqual(do_local, da_nuvem)
        for entrada in da_nuvem:
            self.assertIsInstance(entrada, dict)
            self.assertEqual(set(entrada), {"role", "text"})

    def test_local_client_emits_the_canonical_assistant_role(self):
        """O papel do copiloto no cliente local é `"assistant"`, não o histórico `"agent"`."""
        from zorin_copilot.ai.local_voice import LocalLiveVoiceClient

        class _FakeSynth:
            def __init__(self):
                self.spoken = []

            def speak(self, text):
                self.spoken.append(text)

        visto: list[tuple[str, str]] = []
        client = object.__new__(LocalLiveVoiceClient)
        client.state = None
        client.on_state_change = None
        client.on_transcript = lambda role, txt: visto.append((role, txt))
        client.on_agent_message = None
        client.synthesizer = _FakeSynth()
        client._transcripts_log = []

        client._finish_turn("Abrindo as configurações de som.", "Abrindo as configurações de som.")
        self.assertEqual(visto, [(ROLE_ASSISTANT, "Abrindo as configurações de som.")])
        self.assertEqual(client._transcripts_log, [
            {"role": ROLE_ASSISTANT, "text": "Abrindo as configurações de som."},
        ])


if __name__ == "__main__":
    unittest.main()
