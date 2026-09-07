# Decisão de design: testes da Fase 4 (parte C — contexto dinâmico ao vivo).
# O systemInstruction do Gemini Live é estático (montado uma vez no setup);
# a 4C injeta DELTAS de contexto na sessão ativa via realtimeInput.text (que
# acrescenta contexto SEM provocar resposta, ao contrário do clientContent).
# Cobertura headless: payload puro, envio thread-safe com ws/loop fakes,
# ferramenta memory_remember (salva na memória + injeta o fato na hora) e os
# hooks de mutação de estado (contact_save, screen_fence_control).

"""Testes da Fase 4 (parte C): contexto dinâmico na sessão Live."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.live import (  # noqa: E402
    LIVE_TOOLS_DECLARATION,
    GeminiLiveClient,
    build_realtime_text_msg,
)
from zorin_copilot.shell.risk import RiskLevel, RiskPolicy  # noqa: E402


def _bare_client(**overrides) -> GeminiLiveClient:
    c = GeminiLiveClient.__new__(GeminiLiveClient)
    c.risk_policy = RiskPolicy()
    c._pending_actions = {}
    c._bypass_risk_gate = False
    c._queued_initial_text = ""
    c._is_running = False
    c._ws = None
    c._loop = None
    c.memory = overrides.get("memory")
    c.fence = overrides.get("fence")
    return c


class _FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, msg: str):
        self.sent.append(json.loads(msg))


def _running_client(**overrides) -> tuple[GeminiLiveClient, _FakeWS, asyncio.AbstractEventLoop]:
    """Cliente com sessão 'ativa': ws e loop reais (sem rede)."""
    c = _bare_client(**overrides)
    ws = _FakeWS()
    loop = asyncio.new_event_loop()
    c._is_running = True
    c._ws = ws
    c._loop = loop
    return c, ws, loop


class _FakeMemory:
    def __init__(self):
        self.facts: list[dict] = []
        self.contacts: list[dict] = []

    def save_fact(self, key, content, category="preferencia", source="usuario"):
        self.facts.append(
            {"key": key, "content": content, "category": category, "source": source}
        )

    def save_contact(self, name, email, aliases=None, notes=""):
        contact = {"name": name, "email": email, "aliases": aliases or [], "notes": notes}
        self.contacts.append(contact)
        return contact


class _FakeMonitor:
    def __init__(self, name):
        self.name = name


class _FakeFence:
    def __init__(self, ok=True, monitor_name="HDMI-1"):
        self._ok = ok
        self._monitor = _FakeMonitor(monitor_name)

    def set_active_monitor(self, target):
        return self._ok

    def get_active_monitor(self):
        return self._monitor


class RealtimePayloadTest(unittest.TestCase):
    def test_build_realtime_text_msg_structure(self):
        msg = build_realtime_text_msg("contexto novo")
        self.assertEqual(msg, {"realtimeInput": {"text": "contexto novo"}})

    def test_memory_remember_declared(self):
        names = {
            f["name"]
            for grp in LIVE_TOOLS_DECLARATION
            for f in grp["functionDeclarations"]
        }
        self.assertIn("memory_remember", names)

    def test_memory_remember_is_safe_risk(self):
        level, _ = RiskPolicy().classify("memory_remember", {"fact": "x"})
        self.assertEqual(level, RiskLevel.SAFE)


class SendContextUpdateTest(unittest.TestCase):
    def test_false_when_not_running(self):
        c = _bare_client()
        self.assertFalse(c.send_context_update("delta"))

    def test_false_on_empty_text(self):
        c, _ws, loop = _running_client()
        try:
            self.assertFalse(c.send_context_update("   "))
            self.assertFalse(c.send_context_update(""))
        finally:
            loop.close()

    def test_sends_realtime_input_when_running(self):
        c, ws, loop = _running_client()
        try:
            self.assertTrue(c.send_context_update("memorizei: prefiro vim"))
            loop.run_until_complete(asyncio.sleep(0.05))
        finally:
            loop.close()
        self.assertEqual(len(ws.sent), 1)
        msg = ws.sent[0]
        # realtimeInput.text — NÃO clientContent (não provoca resposta).
        self.assertIn("realtimeInput", msg)
        self.assertNotIn("clientContent", msg)
        self.assertEqual(msg["realtimeInput"]["text"], "memorizei: prefiro vim")

    def test_push_context_delta_adds_label(self):
        c, ws, loop = _running_client()
        try:
            c._push_context_delta("monitor ativo agora é 'HDMI-1'.")
            loop.run_until_complete(asyncio.sleep(0.05))
        finally:
            loop.close()
        text = ws.sent[0]["realtimeInput"]["text"]
        self.assertTrue(text.startswith("[Atualização de contexto]"))
        self.assertIn("HDMI-1", text)

    def test_push_delta_noop_when_session_inactive(self):
        c = _bare_client()
        self.assertFalse(c._push_context_delta("qualquer coisa"))


class MemoryRememberToolTest(unittest.TestCase):
    def test_saves_fact_and_pushes_delta(self):
        memory = _FakeMemory()
        c, ws, loop = _running_client(memory=memory)
        try:
            res = c._dispatch_tool("memory_remember", {"fact": "prefiro o editor Vim"})
            loop.run_until_complete(asyncio.sleep(0.05))
        finally:
            loop.close()

        self.assertTrue(res["success"])
        # Salvo na memória permanente
        self.assertEqual(len(memory.facts), 1)
        self.assertEqual(memory.facts[0]["content"], "prefiro o editor Vim")
        self.assertEqual(memory.facts[0]["category"], "preferencia")
        self.assertEqual(memory.facts[0]["source"], "voz ao vivo")
        # E injetado no contexto da sessão imediatamente
        self.assertEqual(len(ws.sent), 1)
        self.assertIn("prefiro o editor Vim", ws.sent[0]["realtimeInput"]["text"])

    def test_custom_category(self):
        memory = _FakeMemory()
        c = _bare_client(memory=memory)
        res = c._dispatch_tool(
            "memory_remember", {"fact": "uso o tema escuro", "category": "sistema"}
        )
        self.assertTrue(res["success"])
        self.assertEqual(memory.facts[0]["category"], "sistema")

    def test_empty_fact_fails(self):
        memory = _FakeMemory()
        c = _bare_client(memory=memory)
        res = c._dispatch_tool("memory_remember", {"fact": "   "})
        self.assertFalse(res["success"])
        self.assertEqual(memory.facts, [])

    def test_key_is_deterministic(self):
        memory = _FakeMemory()
        c = _bare_client(memory=memory)
        c._dispatch_tool("memory_remember", {"fact": "Prefiro   o VIM"})
        c._dispatch_tool("memory_remember", {"fact": "prefiro o vim"})
        # Mesma chave normalizada -> atualização, não duplicata lógica.
        self.assertEqual(memory.facts[0]["key"], memory.facts[1]["key"])

    def test_works_without_active_session(self):
        # Sessão inativa: salva na memória mesmo assim; o delta é no-op.
        memory = _FakeMemory()
        c = _bare_client(memory=memory)
        res = c._dispatch_tool("memory_remember", {"fact": "gosto de café sem açúcar"})
        self.assertTrue(res["success"])
        self.assertEqual(len(memory.facts), 1)


class ContextHooksTest(unittest.TestCase):
    def test_contact_save_pushes_delta(self):
        memory = _FakeMemory()
        c, ws, loop = _running_client(memory=memory)
        try:
            res = c._dispatch_tool(
                "contact_save", {"name": "Maria", "email": "maria@exemplo.com"}
            )
            loop.run_until_complete(asyncio.sleep(0.05))
        finally:
            loop.close()
        self.assertTrue(res["success"])
        self.assertEqual(len(ws.sent), 1)
        self.assertIn("maria@exemplo.com", ws.sent[0]["realtimeInput"]["text"])

    def test_screen_fence_pushes_delta_on_success(self):
        c, ws, loop = _running_client(fence=_FakeFence(ok=True, monitor_name="DP-2"))
        try:
            res = c._dispatch_tool("screen_fence_control", {"monitor": "secondary"})
            loop.run_until_complete(asyncio.sleep(0.05))
        finally:
            loop.close()
        self.assertTrue(res["success"])
        self.assertEqual(len(ws.sent), 1)
        self.assertIn("DP-2", ws.sent[0]["realtimeInput"]["text"])

    def test_screen_fence_no_delta_on_failure(self):
        c, ws, loop = _running_client(fence=_FakeFence(ok=False))
        try:
            res = c._dispatch_tool("screen_fence_control", {"monitor": "inexistente"})
            loop.run_until_complete(asyncio.sleep(0.05))
        finally:
            loop.close()
        self.assertFalse(res["success"])
        self.assertEqual(ws.sent, [])


if __name__ == "__main__":
    unittest.main()
