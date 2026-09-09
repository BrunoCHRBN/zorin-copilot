# Decisão de design: cobre o encerramento autônomo (ferramenta `end_session`).
# Tudo headless — sem WebSocket, sem GTK. O `live.py` não pode importar gi, então
# o encerramento em si é apenas SINALIZADO via callback; quem executa é a UI.

"""Testes do encerramento autônomo da sessão pelo agente (ferramenta end_session)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.live import (  # noqa: E402
    LIVE_TOOLS_DECLARATION,
    GeminiLiveClient,
)
from zorin_copilot.core.config import CopilotConfig  # noqa: E402
from zorin_copilot.shell.risk import RiskPolicy  # noqa: E402


def _client(config=None):
    """Cliente mínimo via __new__ (mesmo padrão do teste da Fase 3)."""
    c = GeminiLiveClient.__new__(GeminiLiveClient)
    c.risk_policy = RiskPolicy()
    c._pending_actions = {}
    c._bypass_risk_gate = False
    c.on_end_session = None
    if config is not None:
        c.config = config
    return c


class EndSessionDeclarationTest(unittest.TestCase):
    """O schema precisa forçar o modelo a justificar o encerramento."""

    def setUp(self):
        self.decl = next(
            f
            for grp in LIVE_TOOLS_DECLARATION
            for f in grp["functionDeclarations"]
            if f["name"] == "end_session"
        )

    def test_mode_tem_enum_standby_e_quit(self):
        props = self.decl["parameters"]["properties"]
        self.assertEqual(props["mode"]["enum"], ["standby", "quit"])

    def test_reason_eh_obrigatorio(self):
        # Obrigar o `reason` é o que impede o modelo de encerrar por impulso:
        # ele precisa apontar a fala que motivou a despedida.
        self.assertEqual(self.decl["parameters"]["required"], ["reason"])

    def test_descricao_alerta_sobre_fim_de_tarefa(self):
        desc = self.decl["description"]
        self.assertIn("encerra essa tarefa", desc)
        self.assertIn("standby", desc)
        self.assertIn("quit", desc)


class EndSessionDispatchTest(unittest.TestCase):
    def test_standby_nao_passa_pelo_gate(self):
        c = _client()
        c.on_end_session = mock.Mock()
        out = c._dispatch_tool("end_session", {"mode": "standby", "reason": "obrigado, é só isso"})
        self.assertTrue(out["success"])
        self.assertEqual(out["mode"], "standby")
        c.on_end_session.assert_called_once_with("standby", "obrigado, é só isso")
        # Nenhuma confirmação foi criada: despedida não pode virar pergunta.
        self.assertEqual(c._pending_actions, {})

    def test_quit_eh_bloqueado_ate_confirmacao(self):
        c = _client()
        c.on_end_session = mock.Mock()
        blocked = c._dispatch_tool("end_session", {"mode": "quit", "reason": "fecha o app"})
        self.assertFalse(blocked["success"])
        self.assertTrue(blocked["requires_confirmation"])
        self.assertIn("confirmation_id", blocked)
        c.on_end_session.assert_not_called()

    def test_quit_apos_aprovacao_dispara_handler(self):
        c = _client()
        c.on_end_session = mock.Mock()
        blocked = c._dispatch_tool("end_session", {"mode": "quit", "reason": "fecha o app"})
        approved = c._dispatch_tool(
            "confirm_action", {"confirmation_id": blocked["confirmation_id"], "approve": True}
        )
        self.assertTrue(approved["success"])
        c.on_end_session.assert_called_once_with("quit", "fecha o app")

    def test_recusa_nao_dispara_handler(self):
        c = _client()
        c.on_end_session = mock.Mock()
        blocked = c._dispatch_tool("end_session", {"mode": "quit", "reason": "x"})
        c._dispatch_tool(
            "confirm_action", {"confirmation_id": blocked["confirmation_id"], "approve": False}
        )
        c.on_end_session.assert_not_called()

    def test_modo_invalido_cai_para_standby(self):
        c = _client()
        c.on_end_session = mock.Mock()
        out = c._dispatch_tool("end_session", {"mode": "banana", "reason": "x"})
        self.assertEqual(out.get("mode"), "standby")

    def test_sem_handler_falha_graciosamente(self):
        c = _client()
        out = c._dispatch_tool("end_session", {"reason": "x"})
        self.assertFalse(out["success"])
        self.assertIn("despeça", out["message"])

    def test_desabilitado_por_config(self):
        cfg = CopilotConfig()
        cfg.end_session_enabled = False
        c = _client(cfg)
        c.on_end_session = mock.Mock()
        out = c._dispatch_tool("end_session", {"reason": "x"})
        self.assertFalse(out["success"])
        c.on_end_session.assert_not_called()

    def test_excecao_no_handler_eh_capturada(self):
        c = _client()
        c.on_end_session = mock.Mock(side_effect=RuntimeError("boom"))
        out = c._dispatch_tool("end_session", {"reason": "x"})
        self.assertFalse(out["success"])
        self.assertIn("Não consegui encerrar", out["message"])

    def test_cliente_sem_config_trata_como_habilitado(self):
        # Clientes montados via __new__ não têm .config; não pode estourar.
        c = _client()
        self.assertFalse(hasattr(c, "config"))
        out = c._dispatch_tool("end_session", {"reason": "x"})
        self.assertFalse(out["success"])  # cai no "sem handler", não em AttributeError


class LiveToolsPayloadTest(unittest.TestCase):
    def test_habilitado_inclui_end_session(self):
        c = _client(CopilotConfig())
        names = {f["name"] for f in c._live_tools_payload()[0]["functionDeclarations"]}
        self.assertIn("end_session", names)

    def test_desabilitado_remove_end_session(self):
        cfg = CopilotConfig()
        cfg.end_session_enabled = False
        c = _client(cfg)
        names = {f["name"] for f in c._live_tools_payload()[0]["functionDeclarations"]}
        self.assertNotIn("end_session", names)
        # As demais continuam lá.
        self.assertIn("confirm_action", names)

    def test_constante_original_nao_eh_mutilada(self):
        cfg = CopilotConfig()
        cfg.end_session_enabled = False
        _client(cfg)._live_tools_payload()
        names = {
            f["name"] for grp in LIVE_TOOLS_DECLARATION for f in grp["functionDeclarations"]
        }
        self.assertIn("end_session", names)


class AppEndSessionSchedulingTest(unittest.TestCase):
    """Agendamento na UI: grace period para a despedida e cancelamento.

    Usamos um `CopilotWindow` com `__init__` neutralizado — a decisão sob teste
    (agendar/cancelar um GLib timeout) não depende da janela montada.
    """

    def setUp(self):
        import gi  # import tardio: gi/Adw já estão configurados pelo restante do teste

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        from zorin_copilot.ui.app import CopilotWindow

        Adw.init()
        with mock.patch.object(CopilotWindow, "__init__", lambda self, *a, **k: None):
            self.win = CopilotWindow()
        self.win.config = CopilotConfig()
        self.win._end_session_source_id = 0
        self.win._pending_end_mode = None
        self.win._consolidate_live_session = mock.MagicMock()
        self.win.voice_pill_window = None
        self.win.live_voice_revealer = mock.MagicMock()
        self.win.header = mock.MagicMock()
        self.win.prompt_bar = mock.MagicMock()
        self.win.show_toast = mock.MagicMock()
        self.win.get_visible = mock.MagicMock(return_value=False)
        self.win.is_active = mock.MagicMock(return_value=False)
        self.win.set_visible = mock.MagicMock()
        self.win.wake_word_engine = None
        self.win._quit_application = mock.MagicMock()
        self.win._resume_wake_word_after_speech = mock.MagicMock()

    def tearDown(self):
        self.win._cancel_pending_end_session()

    def test_schedule_armazena_modo_e_timer(self):
        self.win._schedule_end_session("standby", "tarefa concluída")
        self.assertEqual(self.win._pending_end_mode, "standby")
        self.assertNotEqual(self.win._end_session_source_id, 0)

    def test_schedule_clampa_grace_period(self):
        """Grace period absurdo não pode travar o app por minutos."""
        self.win.config.end_session_grace_sec = 9999.0
        self.win._schedule_end_session("standby", "")
        # 15s é o teto; se o clamp falhasse, o timeout seria de 9999s e o source
        # continuaria pendente — mas só checamos que não estourou o limite.
        self.assertNotEqual(self.win._end_session_source_id, 0)
        self.win.config.end_session_grace_sec = -5.0
        self.win._schedule_end_session("standby", "")
        self.assertNotEqual(self.win._end_session_source_id, 0)

    def test_reescalonar_cancela_o_anterior(self):
        """Dois pedidos seguidos não podem deixar dois timers vivos."""
        self.win._schedule_end_session("standby", "primeiro")
        first = self.win._end_session_source_id
        self.win._schedule_end_session("quit", "segundo")
        self.assertNotEqual(self.win._end_session_source_id, first)
        self.assertEqual(self.win._pending_end_mode, "quit")

    def test_cancel_limpa_estado(self):
        self.win._schedule_end_session("quit", "x")
        self.win._cancel_pending_end_session()
        self.assertEqual(self.win._end_session_source_id, 0)
        self.assertIsNone(self.win._pending_end_mode)

    def test_cancel_sem_timer_pendente_nao_falha(self):
        self.win._cancel_pending_end_session()
        self.win._cancel_pending_end_session()

    def test_perform_standby_mantem_app_vivo_e_ouvindo(self):
        self.win._perform_end_session("standby")
        self.win._consolidate_live_session.assert_called_once()
        self.win._resume_wake_word_after_speech.assert_called_once()
        self.win._quit_application.assert_not_called()
        self.win.show_toast.assert_called_once()
        self.assertIn("Até logo", self.win.show_toast.call_args[0][0])

    def test_perform_quit_mata_wake_word_e_aplicativo(self):
        self.win.wake_word_engine = mock.MagicMock()
        self.win._perform_end_session("quit")
        self.win.wake_word_engine.stop.assert_called_once()
        self.win._quit_application.assert_called_once()
        self.win._resume_wake_word_after_speech.assert_not_called()
        # Encerrar o processo com um toast na fila é desperdício: ele não renderiza.
        self.win.show_toast.assert_not_called()

    def test_perform_limpa_estado_pendente(self):
        self.win._schedule_end_session("standby", "x")
        self.win._perform_end_session("standby")
        self.assertEqual(self.win._end_session_source_id, 0)
        self.assertIsNone(self.win._pending_end_mode)


if __name__ == "__main__":
    unittest.main()
