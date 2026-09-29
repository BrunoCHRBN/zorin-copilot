"""Entrega D / P1-8: watchdog de THINKING — timeout adaptativo + pedido de continuação.

Mora em arquivo próprio de propósito: é **determinístico** (sem WebSocket, sem GLib, sem
timers reais — o `asyncio.sleep` é substituído por um contador que cede o controle), então
serve de alvo do harness `tests/mutacao/mutate_thinking_watchdog.py` sem contaminar as
execuções. O harness roda a suíte uma vez por mutação; `tests/test_live.py` tem laço de
WebSocket e sleeps e por isso fica fora (ver a skill `zorin-copilot-mutation-harness`).
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from zorin_copilot.ai import live as live_module
from zorin_copilot.ai.live import (
    THINKING_CONTINUATION_NUDGE,
    GeminiLiveClient,
    LiveVoiceState,
    thinking_watchdog_seconds,
)
from zorin_copilot.core.config import CopilotConfig


class ThinkingWatchdogSecondsTest(unittest.TestCase):
    """Entrega D / P1-8: timeout adaptativo do watchdog de THINKING (função pura)."""

    def test_small_output_keeps_the_base(self):
        self.assertAlmostEqual(thinking_watchdog_seconds(0, 1), 12.0, places=5)
        self.assertAlmostEqual(thinking_watchdog_seconds(40, 1), 12.02, places=5)

    def test_scales_with_the_volume_the_model_must_digest(self):
        pequeno = thinking_watchdog_seconds(200, 1)
        grande = thinking_watchdog_seconds(8000, 1)
        self.assertGreater(grande, pequeno)

    def test_respects_the_cap(self):
        # Um `git log` enorme não pode virar espera ilimitada.
        self.assertAlmostEqual(thinking_watchdog_seconds(500_000, 1), 60.0, places=5)

    def test_extra_tool_calls_add_time(self):
        # Três resultados para reconciliar na mesma resposta custam mais que um.
        self.assertAlmostEqual(thinking_watchdog_seconds(0, 3), 14.0, places=5)

    def test_never_below_the_base_even_with_incoherent_cap(self):
        # Teto menor que o piso não pode virar piso silencioso.
        self.assertAlmostEqual(
            thinking_watchdog_seconds(0, 1, base_sec=12.0, max_sec=1.0), 12.0, places=5
        )

    def test_zero_tool_calls_does_not_subtract_time(self):
        self.assertAlmostEqual(thinking_watchdog_seconds(0, 0), 12.0, places=5)


class ThinkingWatchdogTest(unittest.TestCase):
    """Entrega D / P1-8: o watchdog pede continuação antes de liberar o microfone.

    O defeito antigo: `asyncio.sleep(12.0)` fixo → liberava para LISTENING e, com isso,
    **reabria o microfone** no meio da geração (o portão de eco só bloqueia em
    THINKING/EXECUTING/SPEAKING), além de contradizer o `interaction_status` do servidor.
    """

    def setUp(self):
        self.config = CopilotConfig(gemini_api_key="fake-key-live")
        self.client = GeminiLiveClient(config=self.config, executor=MagicMock())
        self.client._is_running = True
        self.client.state = LiveVoiceState.THINKING
        self.client._interaction_status = "IDLE"
        self.ws = MagicMock()
        self.ws.send = AsyncMock()
        self.sleeps = {"n": 0}

    def _run(self, timeout: float = 0.01, on_sleep=None):
        """Roda o watchdog com um sono que conta voltas e cede o controle de verdade."""
        real_sleep = asyncio.sleep  # capturado antes do patch (o patch é global no módulo)
        sleeps = self.sleeps

        async def _fast_sleep(_delay):
            sleeps["n"] += 1
            if on_sleep is not None:
                on_sleep(sleeps["n"])
            await real_sleep(0)

        with patch.object(live_module.asyncio, "sleep", new=_fast_sleep):
            asyncio.run(self.client._thinking_watchdog(self.ws, timeout))

    def _spy_set_state(self):
        calls: list[tuple] = []
        real = self.client._set_state

        def spy(state, message=""):
            calls.append((state, message))
            real(state, message)

        self.client._set_state = spy
        return calls

    def test_nudges_then_releases_with_an_honest_message(self):
        """Esgotados os pedidos, libera o microfone — mas diz a verdade, não "Ouvindo você..."."""
        self.config.live_thinking_max_nudges = 2
        estados = self._spy_set_state()
        self._run()

        self.assertEqual(self.ws.send.await_count, 2, "esperava dois pedidos de continuação")
        self.assertEqual(self.client.state, LiveVoiceState.LISTENING)
        liberacoes = [m for s, m in estados if s == LiveVoiceState.LISTENING]
        self.assertEqual(len(liberacoes), 1)
        self.assertIn("Sem resposta", liberacoes[0])
        self.assertNotIn("Ouvindo você", liberacoes[0])

    def test_nudge_is_a_realtime_text_turn(self):
        self.config.live_thinking_max_nudges = 1
        self._run()
        enviado = json.loads(self.ws.send.call_args_list[0][0][0])
        self.assertEqual(enviado["realtimeInput"]["text"], THINKING_CONTINUATION_NUDGE)

    def test_in_progress_buys_patience_before_releasing(self):
        """O servidor diz que ainda trabalha: o watchdog espera de novo em vez de desmenti-lo."""
        self.config.live_thinking_max_nudges = 0
        self.client._interaction_status = "IN_PROGRESS"
        self._run()
        com_in_progress = self.sleeps["n"]
        self.assertEqual(self.client.state, LiveVoiceState.LISTENING)

        # Mesmo cenário, mas o servidor está IDLE: libera já na primeira volta.
        self.setUp()
        self.config.live_thinking_max_nudges = 0
        self._run()
        self.assertEqual(self.sleeps["n"], 1)
        self.assertGreater(
            com_in_progress, 1,
            "IN_PROGRESS tem de comprar pelo menos uma espera extra",
        )

    def test_model_starting_to_speak_keeps_the_mic_closed(self):
        """Se o modelo fala, o turno seguiu: o watchdog sai sem liberar o microfone."""
        self.config.live_thinking_max_nudges = 2
        estados = self._spy_set_state()

        def on_sleep(n):
            if n >= 2:
                self.client.state = LiveVoiceState.SPEAKING

        self._run(on_sleep=on_sleep)
        self.assertEqual(self.client.state, LiveVoiceState.SPEAKING)
        # Um nudge antes de o modelo falar é legítimo; o que importa é nunca liberar o mic.
        self.assertEqual(self.ws.send.await_count, 1)
        self.assertFalse([s for s, _ in estados if s == LiveVoiceState.LISTENING])

    def test_audio_already_queued_keeps_the_mic_closed(self):
        self.config.live_thinking_max_nudges = 2
        self.client._audio_play_queue.put(b"\x00\x00")
        self._run()
        self.assertEqual(self.client.state, LiveVoiceState.THINKING)
        self.assertEqual(self.ws.send.await_count, 0)

    def test_stops_when_the_session_is_not_running(self):
        self.client._is_running = False
        self._run()
        self.assertEqual(self.client.state, LiveVoiceState.THINKING)
        self.assertEqual(self.ws.send.await_count, 0)

    def test_failed_nudge_still_releases_instead_of_hanging(self):
        """Sem isto, um `ws` morto deixaria o estado preso em THINKING (mic fechado)."""
        self.config.live_thinking_max_nudges = 2
        self.ws.send = AsyncMock(side_effect=RuntimeError("ws morto"))
        self._run()
        self.assertEqual(self.client.state, LiveVoiceState.LISTENING)

    def test_continuation_can_be_disabled(self):
        """`max_nudges = 0` mantém o comportamento antigo: libera no primeiro estouro."""
        self.config.live_thinking_max_nudges = 0
        self._run()
        self.assertEqual(self.ws.send.await_count, 0)
        self.assertEqual(self.client.state, LiveVoiceState.LISTENING)


class ThinkingWatchdogWiringTest(unittest.TestCase):
    """A **fiação**: `_execute_tool_call` tem de armar o watchdog com o timeout adaptativo.

    Sem isto, os testes acima passariam testando `_thinking_watchdog` direto e o watchdog
    poderia simplesmente **nunca ser armado** — a lacuna clássica ("a mutação apaga uma
    CHAMADA e a suíte fica verde"). O `de` da mutação correspondente no harness apaga o
    `create_task`.
    """

    def _drive(self, saida: str, n_chamadas: int = 1):
        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="fake-key"), executor=MagicMock())
        client._dispatch_tool = MagicMock(return_value={"success": True, "message": saida})
        client._thinking_watchdog = AsyncMock()

        ws = MagicMock()
        ws.send = AsyncMock()
        tool_data = {
            "functionCalls": [
                {"id": f"call-{i}", "name": "launch_app", "args": {"app_name": "terminal"}}
                for i in range(n_chamadas)
            ]
        }
        asyncio.run(client._execute_tool_call(ws, tool_data))
        return client

    def _timeout_for(self, saida: str, n_chamadas: int = 1) -> float:
        """Timeout com que o watchdog foi armado, lido da chamada real.

        As asserções são sobre a **relação** entre os casos (cresce com o volume, respeita o
        teto), não sobre um número exato: o que a fiação conta são os caracteres da
        observação **serializada**, que inclui o envelope do resultado e por isso é maior
        que a string crua passada aqui.
        """
        client = self._drive(saida, n_chamadas)
        client._thinking_watchdog.assert_awaited_once()
        _, timeout = client._thinking_watchdog.await_args[0]
        return timeout

    def test_watchdog_is_armed(self):
        client = self._drive("ok")
        client._thinking_watchdog.assert_awaited_once()

    def test_bigger_output_gets_a_longer_timeout(self):
        self.assertGreater(self._timeout_for("x" * 8000), self._timeout_for("ok"))

    def test_multiple_calls_extend_the_timeout(self):
        self.assertGreater(self._timeout_for("ok", 3), self._timeout_for("ok", 1))

    def test_timeout_never_exceeds_the_configured_cap(self):
        self.assertLessEqual(self._timeout_for("x" * 500_000), 60.0)

    def test_timeout_never_goes_below_the_base(self):
        self.assertGreaterEqual(self._timeout_for(""), 12.0)


if __name__ == "__main__":
    unittest.main()
