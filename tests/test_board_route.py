# Decisão de design: a abertura do Quadro Negro foi dividida em **decidir** a rota
# (`_board_route`, sem UI, segura fora da main thread) e **executá-la**
# (`_run_board_route`, main thread). A separação existe porque o gancho das
# ferramentas roda na thread do executor e precisa conhecer o desfecho ANTES de
# remeter o desenho para a main thread — sem isso só restava afirmar "aberto" sem
# ter como verificar, que era o defeito.
#
# Aqui se verifica a DECISÃO, que é o que a fala e o log consomem. O desenho em si
# tem cobertura própria em `test_blackboard_render.py` (render real).
#
# `self` é um `SimpleNamespace`, não uma `CopilotWindow`: a rota só consulta
# `self.engine`, e um `GObject` de verdade exige `__init__` completo (janela,
# config, serviços). Os métodos são chamados **não vinculados**, com o stub como
# primeiro argumento — o que mantém o teste longe de display e de GTK.

"""Testes da rota de abertura do Quadro Negro na janela."""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.core.board_outcome import (  # noqa: E402
    BOARD_OK,
    BOARD_PENDING,
    BOARD_UNAVAILABLE,
)

import gi  # noqa: E402

try:
    from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
    require_gtk4()
    from gi.repository import Adw  # noqa: E402

    Adw.init()
    from zorin_copilot.ui.app import CopilotWindow  # noqa: E402

    HAS_GTK = True
except Exception:  # pragma: no cover - só acontece sem typelibs do GTK
    HAS_GTK = False

#: Problema que a álgebra pura resolve sozinha (sem modelo, sem provedor).
_ALGEBRAIC = "x² - 5x + 6 = 0"
#: Problema que só o modelo resolve.
_NOT_ALGEBRAIC = "qual a capital da França?"


class _FakeProvider:
    def __init__(self, configured: bool) -> None:
        self._configured = configured

    def is_configured(self) -> bool:
        return self._configured


class _FakeEngine:
    def __init__(self, provider) -> None:
        self.llm_provider = provider


def _window(provider=None):
    """Stub de janela: só o que a rota consulta."""
    return types.SimpleNamespace(engine=_FakeEngine(provider) if provider else None)


def _route(problem: str, graph=None, provider=None):
    return CopilotWindow._board_route(_window(provider), problem, graph)


def _opener_payload(problem: str, provider=None, focus: str = "") -> dict:
    """Chama o gancho com `GLib.idle_add` espiado.

    Sem o espião, o callback agendado ficaria pendente e executaria mais tarde,
    dentro do laço principal de outro teste — onde o stub não tem `show_toast`, e
    a falha apareceria longe daqui.
    """
    win = _window(provider)
    win._board_route = lambda p, g: CopilotWindow._board_route(win, p, g)
    with patch("zorin_copilot.ui.app.GLib.idle_add", return_value=1):
        return CopilotWindow._open_blackboard_for_tool(win, problem, None, focus)


@unittest.skipUnless(HAS_GTK, "requer os typelibs do GTK4")
class BoardRouteTest(unittest.TestCase):
    """`_board_route`: qual caminho, decidido sem tocar na UI."""

    def test_algebraic_problem_is_ok_without_any_provider(self):
        status, graph, _ = _route(_ALGEBRAIC)
        self.assertEqual(status, BOARD_OK)
        self.assertIsNotNone(graph, "a álgebra pura deveria ter resolvido")

    def test_no_problem_is_ok_without_any_provider(self):
        """Reusar a última solução da conversa não depende de modelo."""
        status, graph, _ = _route("")
        self.assertEqual(status, BOARD_OK)
        self.assertIsNone(graph)

    def test_non_algebraic_problem_without_provider_is_unavailable(self):
        """O caso do defeito: nada abriria — e antes se dizia "aberto"."""
        status, graph, _ = _route(_NOT_ALGEBRAIC)
        self.assertEqual(status, BOARD_UNAVAILABLE)
        self.assertIsNone(graph)

    def test_provider_present_but_unconfigured_is_unavailable(self):
        """Ter o objeto do provedor não basta: sem chave, nada resolve."""
        status, _, _ = _route(_NOT_ALGEBRAIC, provider=_FakeProvider(False))
        self.assertEqual(status, BOARD_UNAVAILABLE)

    def test_non_algebraic_problem_with_a_ready_provider_is_pending(self):
        status, graph, provider = _route(_NOT_ALGEBRAIC, provider=_FakeProvider(True))
        self.assertEqual(status, BOARD_PENDING)
        self.assertIsNone(graph)
        self.assertIsNotNone(provider, "a execução precisa do provedor para escalar")

    def test_precomputed_graph_skips_the_model_even_with_a_problem(self):
        """Grafo já resolvido vence tudo — é o caminho mais barato."""
        from zorin_copilot.ai.math_solver import build_quadratic_sample

        graph = build_quadratic_sample()
        status, resolved, _ = _route(_NOT_ALGEBRAIC, graph=graph)
        self.assertEqual(status, BOARD_OK)
        self.assertIs(resolved, graph)


@unittest.skipUnless(HAS_GTK, "requer os typelibs do GTK4")
class BoardOpenerPayloadTest(unittest.TestCase):
    """`_open_blackboard_for_tool`: o que o gancho devolve a quem chamou."""

    def test_unavailable_is_reported_and_never_claims_it_opened(self):
        payload = _opener_payload(_NOT_ALGEBRAIC)

        self.assertFalse(payload["ok"])
        self.assertFalse(payload["opened"])
        self.assertEqual(payload["status"], BOARD_UNAVAILABLE)
        self.assertIn("provedor", payload["error"])

    def test_algebraic_problem_reports_ok(self):
        payload = _opener_payload(_ALGEBRAIC)

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["opened"])
        self.assertEqual(payload["status"], BOARD_OK)

    def test_pending_is_accepted_but_not_opened(self):
        payload = _opener_payload(_NOT_ALGEBRAIC, provider=_FakeProvider(True))

        self.assertTrue(payload["ok"], "o pedido foi aceito")
        self.assertFalse(payload["opened"], "o quadro ainda não existe")
        self.assertTrue(payload["pending"])
        self.assertEqual(payload["status"], BOARD_PENDING)

    def test_focus_is_echoed_in_the_spoken_message(self):
        payload = _opener_payload("", focus="fatoração")

        self.assertEqual(payload["status"], BOARD_OK)
        self.assertIn("fatoração", payload["message"])

    def test_no_focus_means_no_dangling_mention(self):
        payload = _opener_payload("")
        self.assertNotIn("destaque", payload["message"])


if __name__ == "__main__":
    unittest.main()
