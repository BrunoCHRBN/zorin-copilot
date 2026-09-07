# Decisão de design: testes da Fase 4 (partes A e B) no cliente Gemini Live.
# A: comando embutido na wake word é enfileirado (queue_initial_text) e injetado
# como clientContent na sessão (send_text_input). B: robustez do portão de risco —
# confirmações pendentes expiram por TTL e são descartadas no start/stop da sessão.
# Tudo headless: o cliente é construído via __new__ e o websocket é um fake.

"""Testes da Fase 4: wake word com comando + robustez do loop Live."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.live import GeminiLiveClient  # noqa: E402
from zorin_copilot.shell.risk import RiskPolicy  # noqa: E402


def _bare_client() -> GeminiLiveClient:
    """Cliente mínimo sem dependências pesadas (mesmo padrão de test_fase3_risk)."""
    c = GeminiLiveClient.__new__(GeminiLiveClient)
    c.risk_policy = RiskPolicy()
    c._pending_actions = {}
    c._bypass_risk_gate = False
    c._queued_initial_text = ""
    c._is_running = False
    c._ws = None
    c._loop = None
    return c


class _FakeWS:
    """WebSocket fake: registra as mensagens enviadas (já decodificadas)."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, msg: str):
        self.sent.append(json.loads(msg))


class QueueInitialTextTest(unittest.TestCase):
    def test_queue_stores_stripped_text(self):
        c = _bare_client()
        c.queue_initial_text("  abre o navegador  ")
        self.assertEqual(c._queued_initial_text, "abre o navegador")

    def test_queue_empty_clears(self):
        c = _bare_client()
        c.queue_initial_text("algo")
        c.queue_initial_text("   ")
        self.assertEqual(c._queued_initial_text, "")

    def test_queue_none_safe(self):
        c = _bare_client()
        c.queue_initial_text(None)
        self.assertEqual(c._queued_initial_text, "")


class SendTextInputTest(unittest.TestCase):
    def test_returns_false_when_not_running(self):
        c = _bare_client()
        self.assertFalse(c.send_text_input("abre o navegador"))

    def test_returns_false_on_empty_text(self):
        c = _bare_client()
        c._is_running = True
        c._ws = _FakeWS()
        c._loop = asyncio.new_event_loop()
        try:
            self.assertFalse(c.send_text_input("   "))
            self.assertFalse(c.send_text_input(""))
        finally:
            c._loop.close()

    def test_sends_client_content_when_running(self):
        c = _bare_client()
        ws = _FakeWS()
        c._is_running = True
        c._ws = ws
        c._loop = asyncio.new_event_loop()
        try:
            self.assertTrue(c.send_text_input("abre o navegador"))
            # Drena o loop para a coroutine agendada executar.
            c._loop.run_until_complete(asyncio.sleep(0.05))
        finally:
            c._loop.close()

        self.assertEqual(len(ws.sent), 1)
        msg = ws.sent[0]
        self.assertIn("clientContent", msg)
        self.assertTrue(msg["clientContent"]["turnComplete"])
        turn = msg["clientContent"]["turns"][0]
        self.assertEqual(turn["role"], "user")
        self.assertEqual(turn["parts"][0]["text"], "abre o navegador")


class PendingTtlTest(unittest.TestCase):
    def test_store_pending_records_created_at(self):
        c = _bare_client()
        cid = c._store_pending("email_compose", {"recipient": "a@b.com"})
        self.assertIn("created_at", c._pending_actions[cid])
        self.assertLessEqual(c._pending_actions[cid]["created_at"], time.time())

    def test_fresh_pending_does_not_expire(self):
        c = _bare_client()
        c._store_pending("email_compose", {})
        self.assertEqual(c._sweep_expired_pending(), 0)
        self.assertEqual(len(c._pending_actions), 1)

    def test_old_pending_expires(self):
        c = _bare_client()
        cid = c._store_pending("email_compose", {})
        # Envelhece a entrada artificialmente além do TTL.
        c._pending_actions[cid]["created_at"] = time.time() - (
            c.PENDING_CONFIRMATION_TTL_SEC + 10
        )
        self.assertEqual(c._sweep_expired_pending(), 1)
        self.assertNotIn(cid, c._pending_actions)

    def test_expired_confirmation_id_is_rejected(self):
        c = _bare_client()
        cid = c._store_pending("email_compose", {"recipient": "a@b.com"})
        c._pending_actions[cid]["created_at"] = time.time() - (
            c.PENDING_CONFIRMATION_TTL_SEC + 10
        )
        res = c._dispatch_tool("confirm_action", {"confirmation_id": cid, "approve": True})
        self.assertFalse(res["success"])
        self.assertIn("expirado", res["message"])
        self.assertNotIn(cid, c._pending_actions)

    def test_store_sweeps_previous_expired(self):
        # Armazenar uma nova ação limpa pendências expiradas anteriores.
        c = _bare_client()
        old = c._store_pending("email_compose", {})
        c._pending_actions[old]["created_at"] = time.time() - (
            c.PENDING_CONFIRMATION_TTL_SEC + 10
        )
        c._store_pending("write_document", {})
        self.assertNotIn(old, c._pending_actions)
        self.assertEqual(len(c._pending_actions), 1)


class PendingSessionCleanupTest(unittest.TestCase):
    def _client_with_pending(self) -> GeminiLiveClient:
        c = _bare_client()
        c._store_pending("email_compose", {"recipient": "a@b.com"})
        return c

    def test_stop_clears_pending(self):
        c = self._client_with_pending()
        c._is_video_streaming = False
        c._record_proc = None
        c._play_proc = None
        c.state = None
        c.on_state_change = None
        c.stop()
        self.assertEqual(c._pending_actions, {})

    def test_start_clears_pending_from_previous_session(self):
        c = self._client_with_pending()
        # start() valida a chave antes de limpar os pendings.
        from zorin_copilot.core.config import CopilotConfig

        c.config = CopilotConfig(gemini_api_key="fake-key")
        # Simula os atributos que start() toca antes de lançar a thread real.
        c._is_muted = False
        c._video_frames_count = 0
        c._session_start_time = 0.0
        c._executed_actions_log = []
        c._transcripts_log = []

        import threading as _th

        spawned = {}
        real_thread = _th.Thread

        class _DummyThread:
            def __init__(self, target=None, daemon=None, name=None):
                spawned["target"] = target

            def start(self):
                pass  # não executa o loop real

        try:
            _th.Thread = _DummyThread
            GeminiLiveClient.start(c)
        finally:
            _th.Thread = real_thread

        self.assertEqual(c._pending_actions, {})


if __name__ == "__main__":
    unittest.main()
