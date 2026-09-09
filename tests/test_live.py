"""Testes unitários para o cliente de voz ao vivo (Gemini Live) e execução de ferramentas."""

import unittest
from unittest.mock import MagicMock, Mock, patch

from zorin_copilot.ai.live import (
    LIVE_TOOLS_DECLARATION,
    GeminiLiveClient,
    LiveVoiceState,
)
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.ui.live_view import live_model_label


class LiveVoiceClientTest(unittest.TestCase):
    def setUp(self):
        self.config = CopilotConfig(gemini_api_key="fake-key-live")
        self.mock_executor = MagicMock()
        self.client = GeminiLiveClient(config=self.config, executor=self.mock_executor)

    def test_live_tools_declaration_schema(self):
        """Verifica se a declaração de ferramentas contém os schemas exigidos pela Live API."""
        self.assertIsInstance(LIVE_TOOLS_DECLARATION, list)
        self.assertGreaterEqual(len(LIVE_TOOLS_DECLARATION), 1)
        funcs = LIVE_TOOLS_DECLARATION[0]["functionDeclarations"]
        func_names = [f["name"] for f in funcs]

        for expected in [
            "launch_app", "system_control", "capture_screen", "open_url",
            "get_system_info", "web_search", "media_control", "write_document",
            "organize_directory"
        ]:
            self.assertIn(expected, func_names)

    def test_toggle_mute(self):
        """Testa a alternância do estado de mudo do microfone."""
        self.assertFalse(self.client.is_muted())
        self.assertTrue(self.client.toggle_mute())
        self.assertTrue(self.client.is_muted())
        self.assertFalse(self.client.toggle_mute())
        self.assertFalse(self.client.is_muted())

    def test_start_without_key_triggers_error(self):
        """Verifica se iniciar sem chave de API define estado de erro."""
        client_no_key = GeminiLiveClient(config=CopilotConfig(gemini_api_key=""))
        client_no_key.start()
        self.assertEqual(client_no_key.state, LiveVoiceState.ERROR)

    def test_dispatch_tool_open_url(self):
        """Testa o despacho da ferramenta open_url durante a chamada."""
        rep = MagicMock()
        rep.success = True
        rep.message = "URL aberta com sucesso"
        self.mock_executor.execute_plan.return_value = [rep]

        res = self.client._dispatch_tool("open_url", {"url": "https://zorin.com"})
        self.assertTrue(res["success"])
        self.assertIn("URL", res["message"])
        self.mock_executor.execute_plan.assert_called_once()

    def test_dispatch_tool_system_control(self):
        """Testa o despacho da ferramenta system_control."""
        rep = MagicMock()
        rep.success = True
        rep.message = "Volume ajustado para 75%"
        self.mock_executor.execute_plan.return_value = [rep]

        res = self.client._dispatch_tool("system_control", {"action": "volume_set", "value": "75"})
        self.assertTrue(res["success"])
        self.assertIn("75%", res["message"])

    @patch("zorin_copilot.ai.live.AppManager")
    def test_dispatch_tool_launch_app_found(self, mock_apps):
        """Testa o lançamento de aplicativo encontrado."""
        mock_app = MagicMock()
        mock_apps.find_app.return_value = (mock_app, "Terminal do GNOME")
        mock_apps.launch.return_value = (True, "Terminal aberto")

        res = self.client._dispatch_tool("launch_app", {"app_name": "terminal"})
        self.assertTrue(res["success"])
        self.assertIn("Terminal do GNOME", res["message"])

    @patch("zorin_copilot.ai.live.AppManager")
    def test_dispatch_tool_launch_app_not_found(self, mock_apps):
        """Testa o lançamento de aplicativo não encontrado."""
        mock_apps.find_app.return_value = (None, "")
        # Nem no $PATH nem via terminal do ambiente: tem que falhar de verdade.
        mock_apps.find_binary.return_value = ""
        mock_apps.find_terminal_in_path.return_value = ""

        res = self.client._dispatch_tool("launch_app", {"app_name": "app_inexistente_xyz"})
        self.assertFalse(res["success"])
        self.assertIn("não encontrado", res["message"])

    def test_dispatch_unknown_tool(self):
        """Testa ferramenta inexistente retornando erro seguro."""
        res = self.client._dispatch_tool("ferramenta_magica", {})
        self.assertFalse(res["success"])
        self.assertIn("desconhecida", res["message"])

    @patch("zorin_copilot.core.media.MediaPlayerManager.control")
    def test_dispatch_tool_media_control(self, mock_control):
        """Testa despacho de comando de mídia na chamada de voz."""
        mock_control.return_value = (True, "Música pausada no Spotify.")
        res = self.client._dispatch_tool("media_control", {"action": "pause", "player": "spotify"})
        self.assertTrue(res["success"])
        self.assertIn("Spotify", res["message"])
        mock_control.assert_called_once_with("pause", player_name="spotify")

    @patch("zorin_copilot.core.files.FileManager.write_document")
    def test_dispatch_tool_write_document(self, mock_write):
        """Criação de arquivo é ação de risco: exige confirmação antes de executar."""
        mock_write.return_value = (True, "Arquivo salvo", "/home/bruno/doc.md")
        blocked = self.client._dispatch_tool("write_document", {"filename": "doc.md", "content": "olá mundo"})
        self.assertFalse(blocked["success"])
        self.assertTrue(blocked["requires_confirmation"])
        res = self.client._dispatch_tool(
            "confirm_action", {"confirmation_id": blocked["confirmation_id"], "approve": True}
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["path"], "/home/bruno/doc.md")
        mock_write.assert_called_once_with("doc.md", "olá mundo", directory=None)

    @patch("zorin_copilot.core.files.FileManager.organize_directory")
    def test_dispatch_tool_organize_directory(self, mock_org):
        """Organização de pasta é ação de risco: exige confirmação antes de executar."""
        mock_org.return_value = (True, "Organizado com sucesso", {"Imagens": 2})
        blocked = self.client._dispatch_tool("organize_directory", {"directory": "~/Downloads", "dry_run": False})
        self.assertFalse(blocked["success"])
        self.assertTrue(blocked["requires_confirmation"])
        res = self.client._dispatch_tool(
            "confirm_action", {"confirmation_id": blocked["confirmation_id"], "approve": True}
        )
        self.assertTrue(res["success"])
        self.assertIn("Imagens", res["stats"])
        mock_org.assert_called_once_with(directory="~/Downloads", dry_run=False)

    def test_video_streaming_toggle_and_state(self):
        """Testa início, interrupção e alternância de streaming de vídeo de tela."""
        # Se cliente não estiver rodando, start_video_stream retorna False
        self.assertFalse(self.client.start_video_stream())

        self.client._is_running = True
        self.assertFalse(self.client.is_video_streaming())

        # Inicia
        self.assertTrue(self.client.start_video_stream(fps=1.0))
        self.assertTrue(self.client.is_video_streaming())

        # Alterna (desliga)
        self.assertFalse(self.client.toggle_video_stream())
        self.assertFalse(self.client.is_video_streaming())

        # Alterna (liga)
        self.assertTrue(self.client.toggle_video_stream())
        self.assertTrue(self.client.is_video_streaming())

        # Interrompe
        self.client.stop_video_stream()
        self.assertFalse(self.client.is_video_streaming())

    def test_video_streaming_stops_on_client_stop(self):
        """Garante que ao encerrar a chamada, o streaming de vídeo é finalizado."""
        self.client._is_running = True
        self.client.start_video_stream(fps=1.0)
        self.assertTrue(self.client.is_video_streaming())

        self.client.stop()
        self.assertFalse(self.client.is_video_streaming())

    def test_session_summary_records_video_metrics(self):
        """Garante que o resumo da sessão inclui métricas de vídeo transmitido."""
        self.client._is_running = True
        self.client._video_frames_count = 12
        summary = self.client.get_session_summary()
        self.assertTrue(summary.get("video_streamed"))
        self.assertEqual(summary.get("video_frames"), 12)
        self.assertTrue(summary.get("has_activity"))

    def test_dispatch_tool_screen_fence_control(self):
        """Testa controle dinâmico da cerca espacial via Live API."""
        res = self.client._dispatch_tool("screen_fence_control", {"monitor": "principal"})
        self.assertTrue(res["success"])
        self.assertIn("Cerca", res["message"])

    def test_dispatch_tool_mouse_click_and_keyboard(self):
        """Testa clique e digitação via Live API.

        Precisa de backend de input: sem ydotool o driver falha de propósito
        (e não finge sucesso), então o backend é mockado aqui.
        """
        self.client.input_driver.ydotool_bin = "/usr/bin/ydotool"
        # subprocesso devolvendo sucesso: agora o driver checa o returncode
        # (antes fingia sucesso, então esses testes passavam mesmo sem mock).
        ok_proc = Mock(returncode=0, stderr="", stdout="")
        with patch("subprocess.run", return_value=ok_proc):
            res_click = self.client._dispatch_tool("mouse_click", {"x": 0.5, "y": 0.5, "is_relative": True})
            self.assertTrue(res_click["success"])

            res_type = self.client._dispatch_tool("keyboard_type", {"text": "Teste", "press_enter": False})
            self.assertTrue(res_type["success"])

            res_hotkey = self.client._dispatch_tool("keyboard_hotkey", {"keys": ["ctrl", "c"]})
            self.assertTrue(res_hotkey["success"])

    def test_dispatch_tool_contacts(self):
        """Testa salvamento e consulta de contato via Live API."""
        res_save = self.client._dispatch_tool(
            "contact_save",
            {"name": "Lucas Dev", "email": "lucas@dev.com", "aliases": ["lucas", "frontend"]},
        )
        self.assertTrue(res_save["success"])

        res_lookup = self.client._dispatch_tool("contact_lookup", {"query": "frontend"})
        self.assertTrue(res_lookup["success"])
        self.assertEqual(len(res_lookup["contacts"]), 1)
        self.assertEqual(res_lookup["contacts"][0]["email"], "lucas@dev.com")

    def test_dispatch_tool_email_compose(self):
        """Composição de e-mail é ação de risco: exige confirmação antes de executar."""
        self.client.memory.save_contact("Carlos Contador", "carlos@contabilidade.com", aliases=["contador"])
        blocked = self.client._dispatch_tool(
            "email_compose",
            {"recipient": "contador", "subject": "Balanço Mensal", "body": "Segue relatório."},
        )
        self.assertFalse(blocked["success"])
        self.assertTrue(blocked["requires_confirmation"])
        res = self.client._dispatch_tool(
            "confirm_action", {"confirmation_id": blocked["confirmation_id"], "approve": True}
        )
        self.assertTrue(res["success"])
        self.assertIn("carlos@contabilidade.com", res["message"])

    def test_dispatch_tool_calendar_event(self):
        """Testa agendamento e listagem de evento no calendário via Live API."""
        res_create = self.client._dispatch_tool(
            "calendar_event",
            {"action": "create", "title": "Reunião de Equipe", "datetime_str": "amanhã às 11:00"},
        )
        self.assertTrue(res_create["success"])
        self.assertIn("Reunião de Equipe", res_create["message"])

        res_list = self.client._dispatch_tool("calendar_event", {"action": "list", "datetime_str": "amanhã"})
        self.assertTrue(res_list["success"])
        self.assertGreaterEqual(res_list["count"], 1)

    def test_dispatch_tool_browser_search(self):
        """Testa pesquisa direta no navegador via Live API."""
        res = self.client._dispatch_tool("browser_search", {"query": "Python 3.12 novidades", "engine": "google"})
        self.assertTrue(res["success"])
        self.assertIn("google.com/search", res["url"])

    def test_dispatch_tool_rag_documents(self):
        """Testa busca, leitura de página e abertura de documento local via Live API."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            sample_file = Path(tmpdir) / "relatorio_financeiro.md"
            sample_file.write_text(
                "# Relatório Financeiro 2026\n\n"
                "O faturamento bruto consolidado do primeiro trimestre foi de R$ 450.000.",
                encoding="utf-8",
            )

            # Indexa o arquivo na instância RAG do client
            self.client.rag.index_file(sample_file)

            # 1. search_documents
            res_search = self.client._dispatch_tool(
                "search_documents",
                {"query": "faturamento bruto 2026", "limit": 3},
            )
            self.assertTrue(res_search["success"])
            self.assertGreaterEqual(res_search["count"], 1)
            self.assertIn("relatorio_financeiro.md", res_search["citations"])

            # 2. read_document_page
            res_read = self.client._dispatch_tool(
                "read_document_page",
                {"file_path": str(sample_file), "page_number": 1},
            )
            self.assertTrue(res_read["success"])
            self.assertIn("450.000", res_read["content"])

            # 3. open_document_file
            res_open = self.client._dispatch_tool(
                "open_document_file",
                {"file_path": str(sample_file), "page_number": 1},
            )
            self.assertTrue(res_open["success"])


class LiveModelLabelTest(unittest.TestCase):
    """O rótulo do modelo na UI de voz acompanha o modelo configurado."""

    def test_strips_path_and_suffixes(self):
        self.assertEqual(
            live_model_label("models/gemini-2.5-flash-native-audio-latest"), "Gemini 2.5"
        )
        self.assertEqual(live_model_label("models/gemini-3.1-flash-live-preview"), "Gemini 3.1")

    def test_without_gemini_prefix_returns_the_id(self):
        self.assertEqual(live_model_label("models/meu-modelo"), "meu-modelo")

    def test_empty_falls_back_to_gemini(self):
        self.assertEqual(live_model_label(""), "Gemini")
        self.assertEqual(live_model_label(None), "Gemini")

    def test_default_config_produces_3_1(self):
        # O default acompanha o modelo de voz atual do Google (março/2026).
        # Se o projeto voltar para o 2.5, este teste precisa ser revertido.
        self.assertEqual(live_model_label(CopilotConfig().gemini_live_model), "Gemini 3.1")




class LiveAudioPlayerTest(unittest.TestCase):
    """Garante que a reprodução e gravação PipeWire usam --raw para evitar rejeição por libsndfile."""

    @patch("shutil.which")
    @patch("subprocess.Popen")
    def test_start_player_uses_raw_pw_play(self, mock_popen, mock_which):
        mock_which.return_value = "/usr/bin/pw-play"
        mock_popen.return_value = MagicMock()
        client = GeminiLiveClient()
        proc = client._start_player()
        self.assertIsNotNone(proc)
        cmd = mock_popen.call_args[0][0]
        self.assertIn("--raw", cmd)
        self.assertIn("pw-play", cmd[0])


class LiveErrorHandlingTest(unittest.TestCase):
    """Testes para garantir que erros não sejam mascarados como 'Desconectado'."""

    def test_run_loop_preserves_error_state(self):
        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="fake-key"))
        with patch.object(client, "_live_session", side_effect=RuntimeError("Falha na rede")):
            client._run_loop()
        self.assertEqual(client.state, LiveVoiceState.ERROR)
        self.assertIn("Falha na rede", client._last_error or "")

    def test_execute_tool_call_includes_name_in_response(self):
        import asyncio
        import json
        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="fake-key"))
        client._dispatch_tool = MagicMock(return_value={"success": True, "message": "ok"})
        mock_ws = MagicMock()
        mock_ws.send = MagicMock(return_value=asyncio.sleep(0))

        tool_data = {
            "functionCalls": [
                {"id": "call-123", "name": "launch_app", "args": {"app_name": "terminal"}}
            ]
        }
        asyncio.run(client._execute_tool_call(mock_ws, tool_data))
        mock_ws.send.assert_called_once()
        sent_payload = json.loads(mock_ws.send.call_args[0][0])
        fr = sent_payload["toolResponse"]["functionResponses"][0]
        self.assertEqual(fr["id"], "call-123")
        self.assertEqual(fr["name"], "launch_app")


if __name__ == "__main__":
    unittest.main()

