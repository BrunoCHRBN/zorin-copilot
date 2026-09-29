"""Ritmo adaptativo do stream de vídeo (Entrega F / P1-6).

Dois contratos, um só motivo: o stream usava um intervalo **fixo** (`delay = max(0.4,
1.0 / fps)`, calculado uma vez), então uma tela parada pagava captura + encode sem
necessidade e uma tela em movimento ficava picotada para o modelo. Aqui se fixa (a) a função
pura que dosa o ritmo pelo `diff` e (b) a **fiação** — que o laço realmente use o valor
calculado, e não o base fixo. Sem (b), (a) seria um ajudante bonito e inerte.
"""

from __future__ import annotations

import unittest
from unittest import mock

from zorin_copilot.ai.live import (
    FULL_CAPTURE_MAX_SIZE,
    FULL_CAPTURE_QUALITY,
    LIVE_TOOLS_DECLARATION,
    MOVING_FRAME_THRESHOLD,
    adaptive_video_delay,
)
from zorin_copilot.core.config import CopilotConfig


class AdaptiveDelayTest(unittest.TestCase):
    """A função pura: o `diff` dosa o intervalo, com piso e teto."""

    def test_static_screen_slows_down_and_moving_speeds_up(self):
        """O caso central: parado alonga, em movimento encurta — em torno do base."""
        base = 1.0
        parado = adaptive_video_delay(0.0, base)
        movendo = adaptive_video_delay(0.5, base)
        self.assertGreater(parado, base, "tela parada deveria desacelerar")
        self.assertLess(movendo, base, "tela em movimento deveria acelerar")
        # Números do plano, com fps=1.0 (base 1,0 s): 0,5 fps parado / 2 fps em movimento.
        self.assertAlmostEqual(parado, 2.0, places=3)
        self.assertAlmostEqual(movendo, 0.5, places=3)

    def test_threshold_is_inclusive(self):
        """Exatamente no limiar já conta como movimento (mesmo critério do keep-alive)."""
        base = 1.0
        self.assertAlmostEqual(adaptive_video_delay(MOVING_FRAME_THRESHOLD, base), 0.5, places=3)
        self.assertAlmostEqual(
            adaptive_video_delay(MOVING_FRAME_THRESHOLD - 1e-9, base), 2.0, places=3
        )

    def test_missing_diff_keeps_the_requested_rate(self):
        """Sem captura nova (`diff is None`) não há o que medir: mantém o base, não acelera."""
        self.assertAlmostEqual(adaptive_video_delay(None, 1.0), 1.0, places=3)
        self.assertAlmostEqual(adaptive_video_delay(None, 0.4), 0.4, places=3)

    def test_clamped_to_the_floor_and_ceiling(self):
        """Nunca fora de [0,25 s, 4,0 s] — 4 fps no topo, 0,25 fps no fundo."""
        self.assertAlmostEqual(adaptive_video_delay(0.9, 0.01), 0.25, places=3)
        self.assertAlmostEqual(adaptive_video_delay(0.0, 100.0), 4.0, places=3)
        for diff in (None, 0.0, 0.5, 1.0):
            for base in (0.01, 0.4, 1.0, 3.0, 100.0):
                with self.subTest(diff=diff, base=base):
                    valor = adaptive_video_delay(diff, base)
                    self.assertGreaterEqual(valor, 0.25)
                    self.assertLessEqual(valor, 4.0)

    def test_scales_with_the_requested_fps(self):
        """O ritmo continua derivado do `fps` pedido — não é um valor mágico novo."""
        # base = max(0.4, 1/fps), igual ao laço.
        def base_de(fps: float) -> float:
            return max(0.4, 1.0 / max(0.2, fps))

        lento = adaptive_video_delay(0.0, base_de(0.5))     # base 2,0
        rapido = adaptive_video_delay(0.0, base_de(2.0))    # base 0,5
        self.assertGreater(lento, rapido, "fps menor deveria produzir intervalo maior")

    def test_garbage_input_does_not_explode(self):
        """Entradas inválidas degradam para o piso em vez de levantar."""
        self.assertAlmostEqual(adaptive_video_delay("x", "y"), 0.25, places=3)
        self.assertAlmostEqual(adaptive_video_delay(0.5, None), 0.25, places=3)


class VideoLoopPacingTest(unittest.TestCase):
    """A fiação: o laço dorme o intervalo **calculado**, não o base fixo.

    Mutação que isto mata: voltar `time.sleep(frame_delay)` para `time.sleep(base_delay)` —
    a função pura seguiria verde e o produto voltaria ao ritmo fixo.
    """

    def _dormidos(self, diff_value, fps: float = 1.0, captura_out: list | None = None) -> list[float]:
        """Roda voltas de `_video_stream_worker` até o laço encerrar; devolve os sleeps.

        `captura_out`, se dado, recebe o dublê de `ScreenCaptureService.capture` para que o
        chamador possa inspecionar com que ajustes o stream foi capturado.
        """
        from zorin_copilot.ai.live import GeminiLiveClient
        from zorin_copilot.core.vision import ScreenCaptureService

        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="x"))
        client._is_video_streaming = True
        client._is_running = True
        client._ws = mock.MagicMock()
        client._ws.send = mock.AsyncMock()          # devolve corrotina p/ run_coroutine_threadsafe
        client._loop = mock.MagicMock()
        client._loop.is_running.return_value = True
        client.inspector = mock.MagicMock()

        chamadas = {"n": 0}

        def active_window():
            chamadas["n"] += 1
            # A 1ª volta roda inteira (com `_is_running` ainda True); a 2ª encerra o laço.
            if chamadas["n"] >= 2:
                client._is_running = False
            return ("code", "main.py", (0, 0, 100, 100))

        client.inspector.get_active_window_info.side_effect = active_window

        dormidos: list[float] = []
        with mock.patch.object(
            ScreenCaptureService, "capture", return_value=(True, b"\xff\xd8jpeg", "tela_inteira")
        ) as captura, mock.patch.object(
            ScreenCaptureService, "compute_frame_diff", return_value=diff_value
        ), mock.patch(
            "zorin_copilot.ai.live.time.sleep", side_effect=lambda s: dormidos.append(s)
        ):
            if captura_out is not None:
                captura_out.append(captura)
            client._video_stream_worker(fps=fps)

        return dormidos

    def test_loop_uses_the_adaptive_delay_when_the_screen_is_static(self):
        """Tela parada: o laço dorme o intervalo alongado (2,0 s com fps=1), não 1,0 s."""
        dormidos = self._dormidos(0.0)
        self.assertTrue(dormidos, "o laço não dormiu nenhuma vez")
        self.assertAlmostEqual(
            dormidos[0], 2.0, places=3,
            msg="o laço usou o base fixo em vez do intervalo adaptativo (tela parada)",
        )

    def test_loop_uses_the_adaptive_delay_when_the_screen_moves(self):
        """Tela em movimento: o laço dorme o intervalo encurtado (0,5 s com fps=1)."""
        dormidos = self._dormidos(0.5)
        self.assertAlmostEqual(
            dormidos[0], 0.5, places=3,
            msg="o laço não acelerou com movimento — voltou ao ritmo fixo",
        )

    def test_loop_keeps_the_base_rate_without_a_diff(self):
        """Sem diff (captura falhou), o laço mantém o ritmo pedido."""
        dormidos = self._dormidos(None)
        self.assertAlmostEqual(dormidos[0], 1.0, places=3)

    def test_loop_captures_the_stream_at_the_light_settings(self):
        """O stream contínuo segue leve (800/70) — não compete com o áudio (P1-6).

        O contraste é o ponto da entrega: leve no contínuo, nativo só sob demanda.
        """
        from zorin_copilot.ai.live import STREAM_CAPTURE_MAX_SIZE, STREAM_CAPTURE_QUALITY

        capturas: list = []
        self._dormidos(0.5, captura_out=capturas)
        self.assertTrue(capturas, "o laço não capturou nenhum quadro")
        kwargs = capturas[0].call_args.kwargs
        self.assertEqual(kwargs["max_size"], STREAM_CAPTURE_MAX_SIZE)
        self.assertEqual(kwargs["quality"], STREAM_CAPTURE_QUALITY)
        self.assertLess(
            STREAM_CAPTURE_MAX_SIZE, 1280,
            "o stream não pode subir para a resolução da captura sob demanda",
        )


class CaptureScreenFullResTest(unittest.TestCase):
    """A captura sob demanda em resolução nativa, sem mexer no caminho de sempre."""

    def _client(self):
        from zorin_copilot.ai.live import GeminiLiveClient

        return GeminiLiveClient(config=CopilotConfig(gemini_api_key="x"))

    def test_default_path_is_unchanged(self):
        """Sem `full_resolution`, o handler chama `capture` como sempre chamou."""
        from zorin_copilot.core.vision import ScreenCaptureService

        client = self._client()
        with mock.patch.object(
            ScreenCaptureService, "capture", return_value=(True, b"x", "tela_inteira")
        ) as captura:
            res = client._dispatch_tool("capture_screen", {})
        self.assertTrue(res["success"])
        captura.assert_called_once_with(interactive=False)

    def test_full_resolution_asks_for_native_size(self):
        """`full_resolution=True` pede `max_size=0` (sem downscale) e qualidade maior."""
        from zorin_copilot.core.vision import ScreenCaptureService

        client = self._client()
        with mock.patch.object(
            ScreenCaptureService, "capture", return_value=(True, b"x", "tela_inteira")
        ) as captura:
            res = client._dispatch_tool("capture_screen", {"full_resolution": True})
        self.assertTrue(res["success"])
        captura.assert_called_once_with(
            interactive=False, max_size=FULL_CAPTURE_MAX_SIZE, quality=FULL_CAPTURE_QUALITY
        )
        self.assertEqual(FULL_CAPTURE_MAX_SIZE, 0, "0 é o sentinela de 'sem downscale'")
        self.assertGreater(FULL_CAPTURE_QUALITY, 85, "resolução nativa pede qualidade maior")

    def test_declaration_advertises_the_parameter(self):
        """O modelo só pode pedir o que a declaração anuncia."""
        # `LIVE_TOOLS_DECLARATION` agrupa por `functionDeclarations`, não é uma lista plana.
        tools = [
            t
            for grupo in LIVE_TOOLS_DECLARATION
            for t in grupo.get("functionDeclarations", [])
        ]
        decl = [t for t in tools if t.get("name") == "capture_screen"]
        self.assertEqual(len(decl), 1, "capture_screen deveria estar declarada uma vez")
        self.assertIn("full_resolution", decl[0]["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
