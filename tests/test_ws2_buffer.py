"""Testes WS2/Entrega B (P0-4): ring buffer de PCM e flush na reconexão do Gemini Live.

Cobrem N1/N3/N4/N5 do plano `docs/ENTREGA_B_RECONEXAO.md`. Não usam áudio real nem
rede: o `Popen` e o WebSocket são mockados.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from zorin_copilot.ai.live import GeminiLiveClient, LiveVoiceState, _SessionDropped
from zorin_copilot.core.audio.buffer import PcmRingBuffer
from zorin_copilot.core.config import CopilotConfig


class PcmRingBufferTest(unittest.TestCase):
    def test_fifo_push_pop(self):
        b = PcmRingBuffer(1.0)
        b.push(b"aaa")
        b.push(b"bbb")
        self.assertEqual(b.pop(), b"aaa")
        self.assertEqual(b.pop(), b"bbb")
        self.assertIsNone(b.pop())

    def test_cap_drops_oldest(self):
        b = PcmRingBuffer(0.1)  # cap = 0.1s * 16000 * 2 = 3200 bytes
        self.assertEqual(b.cap_bytes, 3200)
        b.push(b"A" * 3200)
        b.push(b"B" * 3200)  # estoura -> descarta o mais antigo (A)
        self.assertEqual(b.pop(), b"B" * 3200)
        self.assertIsNone(b.pop())

    def test_drain_returns_all_and_clears(self):
        b = PcmRingBuffer(1.0)
        b.push(b"x" * 100)
        b.push(b"y" * 50)
        self.assertEqual(len(b.drain()), 150)
        self.assertEqual(b.total_bytes, 0)
        self.assertIsNone(b.pop())

    def test_empty_push_ignored(self):
        b = PcmRingBuffer(1.0)
        b.push(b"")
        self.assertEqual(b.total_bytes, 0)

    def test_zero_seconds_disables_buffering(self):
        b = PcmRingBuffer(0.0)
        b.push(b"x" * 100)
        self.assertEqual(b.total_bytes, 0)

    def test_len_seconds(self):
        b = PcmRingBuffer(1.0)
        b.push(b"x" * 3200)  # 0.1s @ 16kHz s16
        self.assertAlmostEqual(b.len_seconds(), 0.1, places=5)


class _FakeProc:
    """Popen falso: morto por padrão (poll() != None) e sem leitura bloqueante."""

    def __init__(self, exit_code: int = 1):
        self._exit_code = exit_code
        self.terminated = False
        self.killed = False
        self.stdout = MagicMock()
        self.stdout.read.return_value = b""

    def poll(self):
        return self._exit_code

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self._exit_code

    def kill(self):
        self.killed = True


def _client() -> GeminiLiveClient:
    return GeminiLiveClient(config=CopilotConfig(gemini_api_key="k"), executor=MagicMock())


class CloudRecorderCmdTest(unittest.TestCase):
    def setUp(self):
        self.client = _client()

    def test_cmd_none_without_recorder(self):
        with patch("zorin_copilot.ai.live.shutil.which", return_value=None):
            self.assertIsNone(self.client._build_recorder_cmd())

    def test_cmd_enriches_with_filtered_source(self):
        with patch("zorin_copilot.ai.live.shutil.which", return_value="/usr/bin/pw-record"), \
             patch("zorin_copilot.ai.live.get_processed_capture_source", return_value="mysrc"):
            cmd = self.client._build_recorder_cmd()
        self.assertIn("--target", cmd)
        self.assertEqual(cmd[cmd.index("--target") + 1], "mysrc")
        self.assertEqual(cmd[-1], "-")

    def test_close_record_proc_terminates_and_clears(self):
        fake = _FakeProc()
        self.client._record_proc = fake
        self.client._close_record_proc()
        self.assertTrue(fake.terminated)
        self.assertIsNone(self.client._record_proc)

    def test_close_record_proc_idempotent(self):
        self.client._record_proc = None
        self.client._close_record_proc()  # não deve levantar


class CloudFlushTest(unittest.TestCase):
    """O flush: o áudio acumulado durante a queda é reenviado ao reconectar."""

    def setUp(self):
        self.client = _client()

    def _drain_once(self, ws):
        self.client._is_running = True

        async def _run():
            task = asyncio.ensure_future(self.client._mic_recorder_loop(ws))
            for _ in range(60):
                await asyncio.sleep(0.01)
                if ws.send.await_count >= 1:
                    break
            self.client._is_running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

    def test_buffered_audio_is_flushed_on_reconnect(self):
        for i in range(3):
            self.client._pcm_buffer.push(bytes([i]) * 3200)
        self.assertEqual(self.client._pcm_buffer.total_bytes, 9600)

        ws = MagicMock()
        ws.send = AsyncMock()
        self._drain_once(ws)

        self.assertEqual(ws.send.await_count, 3)
        self.assertEqual(self.client._pcm_buffer.total_bytes, 0)

    def test_empty_buffer_does_not_send(self):
        ws = MagicMock()
        ws.send = AsyncMock()
        self._drain_once(ws)
        self.assertEqual(ws.send.await_count, 0)

    def test_flush_respects_echo_isolation(self):
        # B3: o flush não pode furar o isolamento de eco — com o assistente falando
        # e RMS baixo, o backlog NÃO é reenviado.
        self.client._pcm_buffer.push(b"\x00\x00" * 1600)
        ws = MagicMock()
        ws.send = AsyncMock()
        self.client.state = LiveVoiceState.SPEAKING
        self._drain_once(ws)
        self.assertEqual(ws.send.await_count, 0)


class CloudPumpNoLeakTest(unittest.TestCase):
    def setUp(self):
        self.client = _client()

    def test_pump_closes_previous_recorder_before_reopening(self):
        fakes = [_FakeProc(exit_code=1), _FakeProc(exit_code=1)]
        opened = []
        it = iter(fakes)

        def fake_popen(_cmd, **_kw):
            proc = next(it)
            opened.append(proc)
            return proc

        sleeps = {"n": 0}

        async def fake_sleep(_delay):
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                self.client._is_running = False
                return False
            return True

        self.client._is_running = True
        with patch.object(self.client, "_build_recorder_cmd", return_value=["pw-record", "-"]), \
             patch("zorin_copilot.ai.live.subprocess.Popen", side_effect=fake_popen), \
             patch.object(self.client, "_pump_sleep", new=AsyncMock(side_effect=fake_sleep)):
            asyncio.run(self.client._capture_pump())

        # Dois gravadores abertos e AMBOS encerrados: sem órfão `pw-record`.
        self.assertEqual(len(opened), 2)
        self.assertTrue(all(f.terminated for f in opened))
        self.assertIsNone(self.client._record_proc)

    def test_pump_stops_without_recorder(self):
        self.client._is_running = True
        with patch.object(self.client, "_build_recorder_cmd", return_value=None):
            asyncio.run(self.client._capture_pump())  # retorna sem travar
        self.assertIsNone(self.client._record_proc)


class CloudSessionLifecycleTest(unittest.TestCase):
    """`_live_session` governa a bomba: nasce/morre com a sessão **lógica**.

    O contrato WS2 é que a bomba atravessa as reconexões — se ela subisse a cada
    tentativa, voltaríamos ao acoplamento antigo (e a um `pw-record` por tentativa).
    """

    def setUp(self):
        self.client = _client()

    def _run_session(self, drops: int) -> dict:
        """Roda `_live_session` com `drops` quedas seguidas de sucesso.

        A sessão falsa cede o controle uma vez por tentativa (`real_sleep(0)`), como
        faria o I/O real — sem isso a bomba nem chegaria a ser escalonada.
        """
        real_sleep = asyncio.sleep  # capturado antes do patch abaixo
        started = {"n": 0}
        cancelled = {"n": 0}
        attempts = {"n": 0}

        async def fake_pump():
            started["n"] += 1
            try:
                # Bloqueia até ser cancelada. Não usa asyncio.sleep: o teste o
                # substitui por um no-op para acelerar o backoff.
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled["n"] += 1
                raise

        async def fake_session():
            attempts["n"] += 1
            await real_sleep(0)
            if attempts["n"] <= drops:
                raise _SessionDropped(1011, "x")

        async def _fast_sleep(_delay):
            await real_sleep(0)

        self.client._is_running = True
        self.client._run_one_session = fake_session

        with patch.object(self.client, "_capture_pump", new=fake_pump), \
             patch("zorin_copilot.ai.live.asyncio.sleep", new=_fast_sleep):
            asyncio.run(self.client._live_session())

        return {
            "started": started["n"],
            "cancelled": cancelled["n"],
            "attempts": attempts["n"],
        }

    def test_pump_started_once_across_reconnects(self):
        # Duas quedas: 3 tentativas de sessão, mas UMA só bomba.
        r = self._run_session(drops=2)
        self.assertEqual(r["attempts"], 3)
        self.assertEqual(r["started"], 1)
        self.assertEqual(r["cancelled"], 1)

    def test_pump_cancelled_when_session_ends_cleanly(self):
        r = self._run_session(drops=0)
        self.assertEqual(r["started"], 1)
        self.assertEqual(r["cancelled"], 1)
        self.assertIsNone(self.client._capture_task)

    def test_session_end_closes_recorder(self):
        """Sem isto, sobrava um `pw-record` órfão por reconexão."""
        fake = _FakeProc()
        self.client._record_proc = fake
        self._run_session(drops=1)
        self.assertTrue(fake.terminated)
        self.assertIsNone(self.client._record_proc)


if __name__ == "__main__":
    unittest.main()
