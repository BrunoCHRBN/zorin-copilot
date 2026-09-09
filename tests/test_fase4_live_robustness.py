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
from unittest import mock

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai import live as live_module  # noqa: E402
from zorin_copilot.ai.live import GeminiLiveClient, _SessionDropped  # noqa: E402
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
    c._reconnecting = False
    c.state = live_module.LiveVoiceState.DISCONNECTED
    c.state_message = ""
    c.on_state_change = None
    c.on_error = None
    c.on_transcript = None
    c.on_audio_level = None
    c._last_error = ""
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

    def test_sends_realtime_input_when_running(self):
        # Gemini 3.1: texto durante a conversa vai por realtimeInput. O
        # clientContent do 2.5 passou a valer só para semear histórico inicial,
        # então o modelo ignoraria o comando se ainda mandássemos clientContent.
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
        self.assertIn("realtimeInput", msg)
        self.assertNotIn("clientContent", msg)
        self.assertEqual(msg["realtimeInput"]["text"], "abre o navegador")


class WaitForAppFocusTest(unittest.TestCase):
    """Abrir app é assíncrono: sem esperar o foco, a digitação vai pra janela errada."""

    def _client_with_inspector(self, active_app: str):
        """Cliente cujo inspetor sempre reporta `active_app` como janela ativa."""

        class _Inspector:
            def get_active_window_info(self):
                return active_app, "janela", (0, 0, 800, 600)

        c = _bare_client()
        c.inspector = _Inspector()
        return c

    def test_returns_true_when_window_matches(self):
        c = self._client_with_inspector("kitty")
        self.assertTrue(c._wait_for_app_focus("kitty", timeout=1.0))

    def test_matching_is_loose_in_both_directions(self):
        # "Terminal" (nome amigável) vs "gnome-terminal" (nome AT-SPI).
        c = self._client_with_inspector("gnome-terminal")
        self.assertTrue(c._wait_for_app_focus("Terminal", timeout=1.0))

    def test_returns_false_on_timeout_without_match(self):
        c = self._client_with_inspector("firefox")
        started = time.monotonic()
        self.assertFalse(c._wait_for_app_focus("kitty", timeout=0.4))
        # Tem que ter esperado de fato, não desistido instantaneamente.
        self.assertGreaterEqual(time.monotonic() - started, 0.4)

    def test_empty_app_name_skips_wait(self):
        c = self._client_with_inspector("kitty")
        started = time.monotonic()
        self.assertFalse(c._wait_for_app_focus("", timeout=1.0))
        self.assertLess(time.monotonic() - started, 0.4)

    def test_without_inspector_falls_back_to_sleep(self):
        c = _bare_client()
        c.inspector = None
        started = time.monotonic()
        self.assertFalse(c._wait_for_app_focus("kitty", timeout=0.1))
        # Fallback conservador: dá tempo da janela aparecer.
        self.assertGreaterEqual(time.monotonic() - started, 1.0)


class LaunchAppFallbackTest(unittest.TestCase):
    """`launch_app` não pode depender só do índice do Gio.

    Em Hyprland/Sway o kitty/foot instalado por pacote costuma não ter
    .desktop indexado (ou tem `should_show()` falso), e o modelo manda o nome
    do binário ("kitty") — não o apelido ("terminal"). Sem fallback, o plano
    morre em "não encontrado" mesmo com o app instalado.
    """

    def _client(self, focused: bool = True):
        """Cliente que não precisa de inspetor: o foco é forjado."""
        c = _bare_client()
        c.inspector = None
        c._wait_for_app_focus = lambda *_a, **_k: focused
        return c

    def _patch_gio_miss(self):
        """find_app devolve (None, "") — o app está fora do índice do Gio."""
        return mock.patch.object(live_module.AppManager, "find_app", return_value=(None, ""))

    def test_abre_pelo_path_quando_gio_nao_indexa(self):
        c = self._client(focused=True)
        with self._patch_gio_miss(), \
             mock.patch.object(live_module.AppManager, "find_binary", return_value="/usr/bin/kitty"), \
             mock.patch.object(live_module.AppManager, "is_executable", return_value=True), \
             mock.patch.object(live_module.subprocess, "Popen") as popen:
            out = c._dispatch_tool("launch_app", {"app_name": "kitty"})

        self.assertTrue(out["success"])
        self.assertIn("kitty", out["message"])
        popen.assert_called_once()
        self.assertEqual(popen.call_args[0][0], ["/usr/bin/kitty"])

    def test_path_fallback_respeita_foco_nao_confirmado(self):
        # Sem foco confirmado ainda é sucesso, mas avisa para esperar.
        c = self._client(focused=False)
        with self._patch_gio_miss(), \
             mock.patch.object(live_module.AppManager, "find_binary", return_value="/usr/bin/foot"), \
             mock.patch.object(live_module.AppManager, "is_executable", return_value=True), \
             mock.patch.object(live_module.subprocess, "Popen"):
            out = c._dispatch_tool("launch_app", {"app_name": "foot"})

        self.assertTrue(out["success"])
        self.assertIn("não confirmamos o foco", out["message"])

    def test_nao_abre_pelo_path_um_caminho_absoluto(self):
        # `find_binary` recusa caminho; se mesmo assim viesse, não executamos.
        c = self._client()
        with self._patch_gio_miss(), \
             mock.patch.object(live_module.AppManager, "find_binary", return_value="/usr/bin/kitty"), \
             mock.patch.object(live_module.AppManager, "is_executable", return_value=False), \
             mock.patch.object(live_module.AppManager, "find_terminal_in_path", return_value=""), \
             mock.patch.object(live_module.subprocess, "Popen") as popen:
            out = c._dispatch_tool("launch_app", {"app_name": "/usr/bin/kitty"})

        self.assertFalse(out["success"])
        popen.assert_not_called()

    def test_terminal_pedido_e_substituido_pelo_terminal_do_ambiente(self):
        # Pediu konsole, só tem foot: abre o foot em vez de travar o plano.
        c = self._client(focused=True)
        with self._patch_gio_miss(), \
             mock.patch.object(live_module.AppManager, "find_binary", return_value=""), \
             mock.patch.object(live_module.AppManager, "find_terminal_in_path", return_value="/usr/bin/foot"), \
             mock.patch.object(live_module.subprocess, "Popen") as popen:
            out = c._dispatch_tool("launch_app", {"app_name": "konsole"})

        self.assertTrue(out["success"])
        self.assertEqual(popen.call_args[0][0], ["/usr/bin/foot"])

    def test_gio_tem_preferencia_sobre_path(self):
        # Com .desktop válido, o caminho Gio continua sendo o escolhido.
        fake_app = mock.Mock()
        fake_app.get_name.return_value = "Kitty"
        c = self._client()
        with mock.patch.object(live_module.AppManager, "find_app", return_value=(fake_app, "Kitty")), \
             mock.patch.object(live_module.AppManager, "launch", return_value=(True, "ok")), \
             mock.patch.object(live_module.AppManager, "find_binary", return_value="/usr/bin/kitty"), \
             mock.patch.object(live_module.subprocess, "Popen") as popen:
            out = c._dispatch_tool("launch_app", {"app_name": "kitty"})

        self.assertTrue(out["success"])
        popen.assert_not_called()

    def test_falha_de_launch_do_gio_nao_cai_no_path(self):
        # Se o Gio achou e falhou ao lançar, o erro real tem que aparecer.
        fake_app = mock.Mock()
        fake_app.get_name.return_value = "Kitty"
        c = self._client()
        with mock.patch.object(live_module.AppManager, "find_app", return_value=(fake_app, "Kitty")), \
             mock.patch.object(live_module.AppManager, "launch", return_value=(False, "Falha ao iniciar 'Kitty': boom")), \
             mock.patch.object(live_module.subprocess, "Popen") as popen:
            out = c._dispatch_tool("launch_app", {"app_name": "kitty"})

        self.assertFalse(out["success"])
        self.assertIn("boom", out["message"])
        popen.assert_not_called()


class AppNotFoundMessageTest(unittest.TestCase):
    """A mensagem de "não encontrado" tem que dar saída ao modelo."""

    def _fake_app(self, name: str):
        app = mock.Mock()
        app.get_name.return_value = name
        app.get_id.return_value = f"{name.lower()}.desktop"
        return app

    def test_inclui_sugestoes_quando_existem(self):
        c = _bare_client()
        apps = [self._fake_app("Foot"), self._fake_app("Alacritty")]
        with mock.patch.object(live_module.AppManager, "suggest_apps", return_value=apps):
            out = c._app_not_found("kodfdy")

        self.assertFalse(out["success"])
        self.assertIn("não encontrado", out["message"])
        self.assertIn("Foot", out["message"])
        self.assertIn("Alacritty", out["message"])

    def test_sem_sugestoes_mensagem_fica_limpa(self):
        c = _bare_client()
        with mock.patch.object(live_module.AppManager, "suggest_apps", return_value=[]):
            out = c._app_not_found("kodfdy")

        self.assertEqual(out["message"], "Aplicativo 'kodfdy' não encontrado no sistema.")

    def test_sugestao_que_explode_nao_derruba_despacho(self):
        # Sugestão é cortesia: falhar nela não pode virar erro de ferramenta.
        c = _bare_client()
        with mock.patch.object(live_module.AppManager, "suggest_apps", side_effect=RuntimeError("gio caiu")):
            out = c._app_not_found("kodfdy")

        self.assertFalse(out["success"])
        self.assertIn("não encontrado", out["message"])


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


class SessionDroppedExceptionTest(unittest.TestCase):
    """A exceção interna _SessionDropped carrega código e motivo do servidor."""

    def test_carries_code_and_reason(self):
        exc = _SessionDropped(1011, "The service is currently unavailable.")
        self.assertEqual(exc.code, 1011)
        self.assertIn("1011", str(exc))
        self.assertIn("service", str(exc))

    def test_accepts_none_code(self):
        exc = _SessionDropped(None, "")
        self.assertIsNone(exc.code)
        self.assertEqual(str(exc), "código None")


import websockets  # noqa: E402


class _ConnClosed(websockets.ConnectionClosed):
    """Finge ser websockets.ConnectionClosed com code/reason arbitrários."""

    def __init__(self, code=1011, reason="server error"):
        # websockets.ConnectionClosed exige (rcvd, sent, rcvd_then_sent) com
        # invariantes. Passamos dummies só para a hierarquia bater e o
        # isinstance funcionar.
        rcvd = type("R", (), {"code": code, "reason": reason})()
        sent = type("S", (), {"code": None, "reason": ""})()
        super().__init__(rcvd, sent, rcvd_then_sent=True)


class ServerReceiverRaisesOnCloseTest(unittest.TestCase):
    """O loop do servidor traduz ConnectionClosed em _SessionDropped para
    que o wrapper de reconexão saiba o que fazer."""

    def _client_with_running(self, reconnecting=False):
        c = _bare_client()
        c._is_running = True
        c._reconnecting = reconnecting
        c._last_error = ""
        return c

    def _run_recv(self, c, ws):
        return asyncio.run(c._server_receiver_loop(ws))

    def test_raises_session_dropped_on_1011(self):
        c = self._client_with_running()
        ws = mock.Mock()
        ws.recv = mock.AsyncMock(side_effect=_ConnClosed(1011, "service unavailable"))
        c._reconnecting = False
        c._set_state = mock.Mock()
        c.on_error = mock.Mock()
        with self.assertRaises(_SessionDropped) as cm:
            self._run_recv(c, ws)
        self.assertEqual(cm.exception.code, 1011)

    def test_does_not_set_error_state_during_reconnect(self):
        c = self._client_with_running(reconnecting=True)
        ws = mock.Mock()
        ws.recv = mock.AsyncMock(side_effect=_ConnClosed(1011, "service unavailable"))
        c._set_state = mock.Mock()
        c.on_error = mock.Mock()
        with self.assertRaises(_SessionDropped):
            self._run_recv(c, ws)
        # Estado já é CONNECTING com "Reconectando (N/M)..."; trocar para
        # ERROR aqui provocaria flicker: ERROR → CONNECTING → LISTENING.
        c._set_state.assert_not_called()
        c.on_error.assert_not_called()

    def test_returns_silently_on_clean_close_1000(self):
        c = self._client_with_running()
        ws = mock.Mock()
        ws.recv = mock.AsyncMock(side_effect=_ConnClosed(1000, ""))
        c._set_state = mock.Mock()
        c.on_error = mock.Mock()
        # 1000 é fechamento pedido pelo cliente — não levanta, não seta erro.
        self._run_recv(c, ws)
        c._set_state.assert_not_called()
        c.on_error.assert_not_called()

    def test_returns_silently_when_user_already_stopped(self):
        c = _bare_client()  # _is_running=False
        ws = mock.Mock()
        ws.recv = mock.AsyncMock(side_effect=_ConnClosed(1011, "service unavailable"))
        c._set_state = mock.Mock()
        c.on_error = mock.Mock()
        # Usuário já desligou o Live: não há por que transformar isso em erro.
        self._run_recv(c, ws)
        c._set_state.assert_not_called()
        c.on_error.assert_not_called()

    def test_raises_on_server_error_field(self):
        c = self._client_with_running()
        ws = mock.Mock()
        ws.recv = mock.AsyncMock(return_value=json.dumps({"error": {"message": "boom"}}).encode("utf-8"))
        c._set_state = mock.Mock()
        c.on_error = mock.Mock()
        with self.assertRaises(_SessionDropped) as cm:
            self._run_recv(c, ws)
        self.assertIn("boom", str(cm.exception))


class LiveSessionReconnectTest(unittest.TestCase):
    """O wrapper _live_session reconecta com backoff após queda inesperada."""

    def _bare_running(self):
        c = _bare_client()
        c._is_running = True
        c._last_error = ""
        return c

    def test_exits_immediately_when_user_stopped(self):
        c = _bare_client()
        c._is_running = False  # usuário desligou antes mesmo de tentar
        c._run_one_session = mock.AsyncMock(return_value=None)
        asyncio.run(c._live_session())
        c._run_one_session.assert_not_called()

    def test_returns_after_run_one_session_clean(self):
        c = self._bare_running()
        c._run_one_session = mock.AsyncMock(return_value=None)
        asyncio.run(c._live_session())
        c._run_one_session.assert_called_once()

    def test_retries_on_session_dropped_until_max(self):
        c = self._bare_running()
        # 5 quedas consecutivas: o wrapper tenta 5 vezes, depois desiste
        c._run_one_session = mock.AsyncMock(
            side_effect=[_SessionDropped(1011, "x") for _ in range(5)]
        )

        async def _fast_sleep(_):
            return None

        with mock.patch.object(live_module.asyncio, "sleep", new=_fast_sleep):
            asyncio.run(c._live_session())
        self.assertEqual(c._run_one_session.call_count, 5)
        self.assertEqual(c.state, live_module.LiveVoiceState.ERROR)

    def test_succeeds_on_retry_after_transient_drop(self):
        c = self._bare_running()
        # Primeira tentativa cai, segunda conecta normalmente
        c._run_one_session = mock.AsyncMock(
            side_effect=[_SessionDropped(1011, "x"), None]
        )

        async def _fast_sleep(_):
            return None

        with mock.patch.object(live_module.asyncio, "sleep", new=_fast_sleep):
            asyncio.run(c._live_session())
        self.assertEqual(c._run_one_session.call_count, 2)
        # Sucesso → não fica em ERROR
        self.assertNotEqual(c.state, live_module.LiveVoiceState.ERROR)

    def test_connecting_state_during_backoff_not_error(self):
        c = self._bare_running()
        c._run_one_session = mock.AsyncMock(
            side_effect=[_SessionDropped(1011, "x"), None]
        )
        states_seen: list[str] = []

        def _capture(state, msg=""):
            states_seen.append(f"{state.value}:{msg[:60]}")

        async def _fast_sleep(_):
            return None

        with mock.patch.object(c, "_set_state", side_effect=_capture), \
             mock.patch.object(live_module.asyncio, "sleep", new=_fast_sleep):
            asyncio.run(c._live_session())
        connecting_msgs = [s for s in states_seen if s.startswith("connecting:")]
        self.assertTrue(
            any("Reconectando" in s for s in connecting_msgs),
            f"Esperava mensagem 'Reconectando...' em CONNECTING, vi: {states_seen}",
        )

    def test_user_stop_cancels_backoff(self):
        c = self._bare_running()
        c._run_one_session = mock.AsyncMock(
            side_effect=[_SessionDropped(1011, "x")]
        )

        async def _stop_after_first_sleep(_):
            c._is_running = False  # usuário clica em "Encerrar" durante o backoff
            return None

        with mock.patch.object(live_module.asyncio, "sleep", new=_stop_after_first_sleep):
            asyncio.run(c._live_session())
        # Uma tentativa + volta para sleep que detectou parada → não reentra no loop
        self.assertEqual(c._run_one_session.call_count, 1)
        self.assertNotEqual(c.state, live_module.LiveVoiceState.ERROR)


if __name__ == "__main__":
    unittest.main()
