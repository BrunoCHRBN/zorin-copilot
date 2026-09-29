"""Comando de voz do Quadro Negro no caminho de voz LOCAL.

O `LocalLiveVoiceClient` não tem `ToolRegistry` (o `IntentEngine` só produz
`ActionPlan`/`ActionType`), então o comando "abre o quadro e mostra X" entra por
um gancho injetado — o **mesmo contrato** do caminho Gemini Live:
`(problema, grafo, foco) -> dict`. Estes testes provam que o atalho intercepta a
fala antes do IntentEngine e que, quando não é o caso, nada muda no fluxo antigo.
"""

from __future__ import annotations

import collections
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.ai.actions import ActionPlan  # noqa: E402
from zorin_copilot.ai.live import LiveVoiceState  # noqa: E402
from zorin_copilot.ai.local_voice import LocalLiveVoiceClient  # noqa: E402
from zorin_copilot.core.board_outcome import (  # noqa: E402
    BOARD_FAILED,
    BOARD_OK,
    BOARD_PENDING,
    BOARD_UNKNOWN,
    BOARD_UNAVAILABLE,
    BoardOutcome,
    message_for,
)

import gi  # noqa: E402

try:
    from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
    require_gtk4()
    from gi.repository import Adw  # noqa: E402

    Adw.init()
    HAS_DISPLAY = True
except Exception:  # pragma: no cover - só acontece sem servidor gráfico
    HAS_DISPLAY = False


class _FakeSynth:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class _FakeTranscriber:
    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe_pcm(self, pcm: bytes) -> str:
        return self.text


class _FakeEngine:
    """Registra se o IntentEngine chegou a ser consultado."""

    def __init__(self) -> None:
        self.parsed: list[str] = []

    def parse(self, prompt: str) -> ActionPlan:
        self.parsed.append(prompt)
        return ActionPlan(thought="resposta do engine", actions=[])


def _make_client(opener=None, transcript: str = "") -> LocalLiveVoiceClient:
    """Cliente montado à mão: sem Piper, sem Whisper, sem microfone."""
    client = object.__new__(LocalLiveVoiceClient)
    client.state = LiveVoiceState.DISCONNECTED
    client.on_state_change = None
    client.on_transcript = None
    client.on_user_message = None
    client.on_agent_message = None
    client.on_tool_executed = None
    client.board_opener = opener
    client._transcripts_log = []
    client._executed_actions_log = []
    client.synthesizer = _FakeSynth()
    client.transcriber = _FakeTranscriber(transcript)
    client.engine = _FakeEngine()
    client.executor = None
    client._speech_buffer = []
    client._preroll_buffer = collections.deque(maxlen=4)
    client._is_user_speaking = False
    client._silence_chunks = 0
    client._is_running = True
    return client


class BoardCommandDispatchTest(unittest.TestCase):
    """O gancho `_handle_board_command`."""

    def test_no_opener_means_no_interception(self):
        """Sem UI injetada (CLI/headless), a fala segue o fluxo normal."""
        client = _make_client(opener=None)
        self.assertFalse(client._handle_board_command("abre o quadro"))
        self.assertEqual(client._executed_actions_log, [])

    def test_non_board_speech_is_not_intercepted(self):
        client = _make_client(opener=lambda *a: {"message": "não deveria rodar"})
        self.assertFalse(client._handle_board_command("toca uma música"))
        self.assertEqual(client._executed_actions_log, [])

    def test_dispatches_with_the_three_arg_contract(self):
        """Mesmo contrato do ToolRegistry: (problema, grafo, foco)."""
        seen: list[tuple] = []

        def opener(problem, graph, focus):
            seen.append((problem, graph, focus))
            return {"message": "Quadro Negro aberto com destaque em “fatoração”."}

        client = _make_client(opener=opener)
        self.assertTrue(client._handle_board_command("abre o quadro e mostra a fatoração"))

        self.assertEqual(len(seen), 1)
        problem, graph, focus = seen[0]
        self.assertEqual(problem, "")
        self.assertIsNone(graph, "a resolução do problema é do gancho da janela")
        self.assertEqual(focus, "fatoracao")

    def test_forwards_the_raw_phrase_when_it_carries_an_equation(self):
        seen: list[tuple] = []
        client = _make_client(
            opener=lambda p, g, f: (seen.append((p, g, f)), {"message": "ok"})[1]
        )
        frase = "abre o quadro e resolve x² - 5x + 6 = 0"
        self.assertTrue(client._handle_board_command(frase))
        self.assertEqual(seen[0][0], frase)

    def test_reports_the_action_and_logs_it(self):
        reported: list[tuple] = []
        client = _make_client(
            opener=lambda *a: BoardOutcome(BOARD_OK, "Quadro Negro aberto.").to_dict()
        )
        client.on_tool_executed = lambda name, desc, ok: reported.append((name, desc, ok))

        client._handle_board_command("abre o quadro negro")

        self.assertEqual(reported, [("study_show_board", "Quadro Negro aberto.", True)])
        self.assertEqual(len(client._executed_actions_log), 1)
        entrada = client._executed_actions_log[0]
        self.assertEqual(entrada["type"], "study_show_board")
        self.assertTrue(entrada["success"])
        self.assertEqual(entrada["status"], BOARD_OK)

    def test_closes_the_turn_with_the_opener_message(self):
        client = _make_client(
            opener=lambda *a: BoardOutcome(BOARD_OK, "Quadro Negro aberto.").to_dict()
        )
        client._handle_board_command("abre o quadro negro")

        self.assertEqual(client.synthesizer.spoken, ["Quadro Negro aberto."])
        self.assertEqual(client._transcripts_log, [
            {"role": "assistant", "text": "Quadro Negro aberto."},
        ])

    def test_unavailable_opener_is_spoken_and_logged_as_failure(self):
        """O caso real do defeito: sem provedor de IA, o quadro NÃO abre.

        Antes, a voz dizia "Quadro Negro aberto." e o log gravava
        `success: True` — uma ação que não aconteceu era afirmada e auditada
        como bem-sucedida.
        """
        client = _make_client(
            opener=lambda *a: BoardOutcome(
                BOARD_UNAVAILABLE, message_for(BOARD_UNAVAILABLE)
            ).to_dict()
        )
        self.assertTrue(client._handle_board_command("abre o quadro e resolve x² - 5x + 6 = 0"))

        self.assertEqual(client.synthesizer.spoken, [message_for(BOARD_UNAVAILABLE)])
        self.assertNotIn("aberto", client.synthesizer.spoken[0])
        entrada = client._executed_actions_log[0]
        self.assertFalse(entrada["success"])
        self.assertEqual(entrada["status"], BOARD_UNAVAILABLE)

    def test_pending_opener_is_accepted_but_not_reported_as_opened(self):
        """`pending` conta como pedido aceito, e a fala não afirma que abriu."""
        client = _make_client(
            opener=lambda *a: BoardOutcome(
                BOARD_PENDING, message_for(BOARD_PENDING)
            ).to_dict()
        )
        client._handle_board_command("abre o quadro e resolve x² - 5x + 6 = 0")

        self.assertEqual(client.synthesizer.spoken, [message_for(BOARD_PENDING)])
        entrada = client._executed_actions_log[0]
        self.assertTrue(entrada["success"], "o pedido foi aceito e a resolução começou")
        self.assertEqual(entrada["status"], BOARD_PENDING)

    def test_opener_failure_does_not_break_the_turn(self):
        """O turno fecha — mas sem afirmar que o quadro abriu.

        A versão anterior deste teste exigia a fala "Quadro Negro aberto." para um
        gancho que levantou exceção: **codificava o defeito**. O turno precisa
        fechar (senão a fala cairia no IntentEngine e viraria ação de desktop),
        mas a fala e o log têm de dizer a verdade.
        """
        def opener(*a):
            raise RuntimeError("boom")

        client = _make_client(opener=opener)
        self.assertTrue(client._handle_board_command("abre o quadro negro"))

        self.assertEqual(
            client.synthesizer.spoken, [message_for(BOARD_FAILED)]
        )
        entrada = client._executed_actions_log[0]
        self.assertFalse(entrada["success"])
        self.assertEqual(entrada["status"], BOARD_FAILED)

    def test_opener_returning_nothing_does_not_claim_success(self):
        """Desfecho desconhecido não vira sucesso.

        O gancho devolveu `None`. Não saber o que aconteceu é motivo para **não**
        afirmar; antes, essa mesma situação produzia "Quadro Negro aberto." e
        `success: True`.
        """
        client = _make_client(opener=lambda *a: None)
        self.assertTrue(client._handle_board_command("abre o quadro negro"))

        self.assertEqual(client.synthesizer.spoken, [message_for(BOARD_UNKNOWN)])
        entrada = client._executed_actions_log[0]
        self.assertFalse(entrada["success"])
        self.assertEqual(entrada["status"], BOARD_UNKNOWN)


class UtteranceIntegrationTest(unittest.TestCase):
    """`_process_utterance` de ponta a ponta, sem áudio."""

    def test_board_utterance_shortcuts_past_the_intent_engine(self):
        seen: list[tuple] = []
        client = _make_client(
            opener=lambda p, g, f: (seen.append((p, g, f)), {"message": "abri"})[1],
            transcript="abre o quadro e mostra a fatoração",
        )

        client._process_utterance(b"\x00" * 1024)

        self.assertEqual(len(seen), 1, "o gancho do quadro não foi chamado")
        self.assertEqual(seen[0][2], "fatoracao")
        self.assertEqual(
            client.engine.parsed, [],
            "o IntentEngine não deveria ser consultado para um comando de UI",
        )

    def test_non_board_utterance_still_uses_the_intent_engine(self):
        client = _make_client(
            opener=lambda *a: {"message": "não deveria rodar"},
            transcript="abre o firefox",
        )

        client._process_utterance(b"\x00" * 1024)

        self.assertEqual(client.engine.parsed, ["abre o firefox"])

    def test_board_utterance_without_opener_falls_back_to_the_engine(self):
        """Sem gancho, o comportamento antigo permanece intacto."""
        client = _make_client(opener=None, transcript="abre o quadro")

        client._process_utterance(b"\x00" * 1024)

        self.assertEqual(client.engine.parsed, ["abre o quadro"])


@unittest.skipUnless(HAS_DISPLAY, "requer servidor gráfico")
class AppWiringTest(unittest.TestCase):
    """A janela injeta o gancho no cliente de voz local ao iniciar a voz.

    Sem isto, o detector e o cliente ficam corretos mas desconectados — o
    comando de voz nunca chega ao Quadro Negro. É o elo que só o `app.py` fecha.
    """

    @classmethod
    def setUpClass(cls):
        from zorin_copilot.ui.app import CopilotWindow

        cls.app = Adw.Application(application_id="org.zorin.copilot.test.voice_board_wiring")
        cls.CopilotWindow = CopilotWindow

    def test_local_voice_client_gets_the_board_opener(self):
        from unittest.mock import patch

        win = self.CopilotWindow(self.app)
        try:
            win.config.provider = "local"  # força o caminho de voz local
            # `start()` abriria captura de microfone e pré-carga de modelos.
            with patch.object(LocalLiveVoiceClient, "start", return_value=None):
                win.start_live_voice()

            self.assertIsInstance(win.live_client, LocalLiveVoiceClient)
            # Métodos vinculados são recriados a cada acesso, então `is` nunca
            # vale: compare função + instância.
            self.assertIsNotNone(
                win.live_client.board_opener,
                "a voz local ficou sem o abridor do Quadro Negro",
            )
            self.assertIs(
                win.live_client.board_opener.__func__,
                type(win)._open_blackboard_for_tool,
            )
            self.assertIs(win.live_client.board_opener.__self__, win)
        finally:
            win.destroy()


if __name__ == "__main__":
    unittest.main()
