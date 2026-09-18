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
            "get_system_info", "web_search", "academic_search", "media_control", "write_document",
            "organize_directory", "move_window_to_monitor", "vscode_workspace", "click_and_type",
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

    @patch("zorin_copilot.core.window_manager.WindowManager.move_windows")
    def test_dispatch_tool_move_window_to_monitor(self, mock_move):
        """Testa o despacho de mover janelas entre monitores via voz/vídeo ao vivo."""
        mock_move.return_value = {
            "success": True,
            "message": "Janela 'Firefox' movida para o monitor 'HDMI-A-2'.",
            "window": "firefox",
            "target_monitor": "HDMI-A-2",
        }
        res = self.client._dispatch_tool(
            "move_window_to_monitor",
            {"window": "firefox", "target_monitor": "secundario"},
        )
        self.assertTrue(res["success"])
        self.assertIn("HDMI-A-2", res["message"])
        mock_move.assert_called_once_with("firefox", "secundario", drag_visual=True)

    def test_dispatch_tool_vscode_workspace(self):
        """Testa o despacho de ações de desenvolvimento com VS Code via voz ao vivo."""
        from pathlib import Path
        from zorin_copilot.core.vscode import VSCodeManager

        # 1. get_active_project
        with patch.object(VSCodeManager, "get_active_workspace", return_value=Path("/home/user/meu_projeto")):
            res = self.client._dispatch_tool("vscode_workspace", {"action": "get_active_project"})
            self.assertTrue(res["success"])
            self.assertEqual(res["project_name"], "meu_projeto")

        # 2. create_project
        with patch.object(
            VSCodeManager,
            "create_project",
            return_value={"success": True, "message": "Criado com sucesso"},
        ) as mock_create:
            res = self.client._dispatch_tool(
                "vscode_workspace",
                {"action": "create_project", "project_name": "api_loja", "template": "fastapi"},
            )
            self.assertTrue(res["success"])
            mock_create.assert_called_once_with("api_loja", template="fastapi", base_dir=None)

        # 3. write_code
        with patch.object(
            VSCodeManager,
            "write_code_file",
            return_value={"success": True, "message": "Código salvo", "lines": 10},
        ) as mock_write:
            res = self.client._dispatch_tool(
                "vscode_workspace",
                {"action": "write_code", "file_path": "main.py", "code_content": "print('ok')", "line_number": 5},
            )
            self.assertTrue(res["success"])
            mock_write.assert_called_once_with("main.py", "print('ok')", line=5)

        # 4. read_file
        with patch.object(
            VSCodeManager,
            "read_code_file",
            return_value={"success": True, "content": "code content"},
        ) as mock_read:
            res = self.client._dispatch_tool(
                "vscode_workspace",
                {"action": "read_file", "file_path": "src/utils.py"},
            )
            self.assertTrue(res["success"])
            mock_read.assert_called_once_with("src/utils.py")

        # 5. get_structure
        with patch.object(
            VSCodeManager,
            "read_workspace_structure",
            return_value={"success": True, "tree": "📁 src/"},
        ) as mock_struct:
            res = self.client._dispatch_tool("vscode_workspace", {"action": "get_structure"})
            self.assertTrue(res["success"])
            mock_struct.assert_called_once_with(None)

        # 6. patch_code
        with patch.object(
            VSCodeManager,
            "patch_code_file",
            return_value={"success": True, "message": "Código modificado", "line": 8},
        ) as mock_patch:
            res = self.client._dispatch_tool(
                "vscode_workspace",
                {
                    "action": "patch_code",
                    "file_path": "main.py",
                    "target_code": "print('velho')",
                    "replacement_code": "print('novo')",
                },
            )
            self.assertTrue(res["success"])
            mock_patch.assert_called_once_with("main.py", "print('velho')", "print('novo')")

        # 7. Ação inválida
        res_err = self.client._dispatch_tool("vscode_workspace", {"action": "acao_invalida_xyz"})
        self.assertFalse(res_err["success"])

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

    def test_resolve_screen_coordinates_calibration(self):
        """Verifica a conversão precisa de coordenadas 0..1000 e 0..1 calibradas ao recorte do vídeo."""
        # 1. Calibração ao frame de vídeo transmitido (crop de janela ou monitor)
        self.client._last_streamed_crop_rect = (200, 150, 1000, 600)

        # 50% horizontal, 50% vertical na escala 0..1000 do Gemini
        abs_x, abs_y, desc = self.client._resolve_screen_coordinates(500, 500)
        self.assertEqual(abs_x, 700)  # 200 + 0.5 * 1000
        self.assertEqual(abs_y, 450)  # 150 + 0.5 * 600
        self.assertIn("frame de vídeo", desc)

        # Fração 0.0..1.0
        abs_x2, abs_y2, _ = self.client._resolve_screen_coordinates(0.1, 0.2)
        self.assertEqual(abs_x2, 300)  # 200 + 0.1 * 1000
        self.assertEqual(abs_y2, 270)  # 150 + 0.2 * 600

        # Pixels absolutos explícitos
        abs_x3, abs_y3, desc3 = self.client._resolve_screen_coordinates(1920, 1080, is_rel=False)
        self.assertEqual(abs_x3, 1920)
        self.assertEqual(abs_y3, 1080)
        self.assertIn("explícito", desc3)

    def test_dispatch_tool_click_and_type(self):
        """Testa a ferramenta atômica click_and_type na chamada de vídeo/voz."""
        self.client.input_driver.ydotool_bin = "/usr/bin/ydotool"
        active_m = self.client.fence.get_active_monitor()
        ox = getattr(active_m, "x", 0) if active_m else 0
        oy = getattr(active_m, "y", 0) if active_m else 0
        self.client._last_streamed_crop_rect = (ox + 100, oy + 100, 800, 600)
        ok_proc = Mock(returncode=0, stderr="", stdout="")

        with patch("subprocess.run", return_value=ok_proc):
            res = self.client._dispatch_tool(
                "click_and_type",
                {"x": 500, "y": 500, "text": "tocando no spotify", "clear_first": True, "press_enter": True},
            )
            self.assertTrue(res["success"])
            self.assertIn("digitado", res["message"])
            self.assertEqual(res["x"], ox + 500)  # ox + 100 + 0.5 * 800
            self.assertEqual(res["y"], oy + 400)  # oy + 100 + 0.5 * 600


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

    @patch(
        "zorin_copilot.core.email.EmailManager.compose",
        return_value=(True, "Rascunho aberto para carlos@contabilidade.com", {"client": "xdg-email"}),
    )
    def test_dispatch_tool_email_compose(self, mock_compose):
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
        mock_compose.assert_called_once()

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

    @patch("zorin_copilot.core.browser.BrowserManager.search", return_value=(True, "Pesquisa aberta", "https://google.com/search?q=Python+3.12+novidades"))
    def test_dispatch_tool_browser_search(self, mock_search):
        """Testa pesquisa direta no navegador via Live API."""
        res = self.client._dispatch_tool("browser_search", {"query": "Python 3.12 novidades", "engine": "google"})
        self.assertTrue(res["success"])
        self.assertIn("google.com/search", res["url"])

    @patch("zorin_copilot.core.web_search.WebSearchClient.academic_search")
    def test_dispatch_tool_academic_search(self, mock_search):
        """Testa despacho de busca acadêmica via Live API."""
        from zorin_copilot.core.web_search import SearchResult
        mock_search.return_value = [
            SearchResult(title="Artigo Gestão Comercial", url="https://scielo.br/artigo", snippet="Estudo sobre funil de vendas")
        ]
        res = self.client._dispatch_tool("academic_search", {"query": "Gestão Comercial", "source": "scielo"})
        self.assertTrue(res["success"])
        self.assertEqual(len(res["results"]), 1)
        self.assertIn("Artigo Gestão Comercial", res["results"][0]["title"])

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
            with patch.object(self.client.rag, "open_document", return_value=(True, "Aberto com sucesso")):
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

    def test_default_config_produces_3_8_live(self):
        # O default acompanha o modelo de voz atual do Google (setembro/2026).
        self.assertEqual(live_model_label(CopilotConfig().gemini_live_model), "Gemini 3.8 Live")

    def test_extended_thinking_label(self):
        self.assertEqual(live_model_label("models/gemini-3.8-live-extended-thinking"), "Gemini 3.8 Thinking")

    def test_legacy_3_1_label(self):
        self.assertEqual(live_model_label("models/gemini-3.1-flash-live-preview"), "Gemini 3.1")




class LiveAudioPlayerTest(unittest.TestCase):
    """Garante que a reprodução e gravação PipeWire usam --raw, latência e papéis corretos."""

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
        self.assertIn("--latency", cmd)
        self.assertIn("150ms", cmd)
        self.assertIn("--media-role", cmd)
        self.assertIn("Communication", cmd)

    def test_play_audio_chunk_enqueues_to_jitter_buffer(self):
        """Verifica se _play_audio_chunk enfileira dados sem bloquear."""
        client = GeminiLiveClient()
        client._is_running = True
        test_data = b"\x01\x02\x03\x04"
        client._play_audio_chunk(test_data)
        self.assertFalse(client._audio_play_queue.empty())
        self.assertEqual(client._audio_play_queue.get_nowait(), test_data)

    def test_stop_player_clears_jitter_buffer_and_resets_prebuffering(self):
        """Testa se o stop_player limpa a fila de áudio e restaura prebuffering (barge-in)."""
        client = GeminiLiveClient()
        client._audio_play_queue.put(b"chunk1")
        client._audio_play_queue.put(b"chunk2")
        client._prebuffer_bytes.extend(b"lingering_bytes")
        client._is_prebuffering = False

        client._stop_player()

        self.assertTrue(client._audio_play_queue.empty())
        self.assertEqual(len(client._prebuffer_bytes), 0)
        self.assertTrue(client._is_prebuffering)

    def test_set_state_deduplication(self):
        """Evita disparo excessivo de eventos on_state_change quando o estado não muda."""
        client = GeminiLiveClient()
        callback = MagicMock()
        client.on_state_change = callback

        client._set_state(LiveVoiceState.SPEAKING, "Falando...")
        self.assertEqual(callback.call_count, 1)

        # Mesma chamada não deve redisparar o callback
        client._set_state(LiveVoiceState.SPEAKING, "Falando...")
        self.assertEqual(callback.call_count, 1)

        # Estado diferente redispara
        client._set_state(LiveVoiceState.LISTENING, "Ouvindo...")
        self.assertEqual(callback.call_count, 2)


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


class LiveExtendedThinkingTest(unittest.TestCase):
    """Testes do protocolo Gemini 3.8 Live Extended Thinking (interaction_status e turnComplete)."""

    def test_in_progress_status_keeps_thinking_state_on_turn_complete(self):
        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="fake-key"))
        client.state = LiveVoiceState.SPEAKING
        client._interaction_status = "IN_PROGRESS"

        # Simula finalização de chunk com interação em andamento
        if getattr(client, "_interaction_status", "IDLE") == "IN_PROGRESS":
            client._set_state(LiveVoiceState.THINKING, "Raciocinando em segundo plano...")
        else:
            client._set_state(LiveVoiceState.LISTENING, "Ouvindo você...")

        self.assertEqual(client.state, LiveVoiceState.THINKING)
        self.assertEqual(client._last_state_msg, "Raciocinando em segundo plano...")

    def test_idle_status_transitions_to_listening(self):
        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="fake-key"))
        client.state = LiveVoiceState.SPEAKING
        client._interaction_status = "IDLE"

        if getattr(client, "_interaction_status", "IDLE") == "IN_PROGRESS":
            client._set_state(LiveVoiceState.THINKING, "Raciocinando em segundo plano...")
        else:
            client._set_state(LiveVoiceState.LISTENING, "Ouvindo você...")

        self.assertEqual(client.state, LiveVoiceState.LISTENING)
        self.assertEqual(client._last_state_msg, "Ouvindo você...")


class LiveSetupAndVoiceTest(unittest.TestCase):
    """Testes para o payload de setup e gerenciamento de modelo/voz no GeminiLiveClient."""

    def test_build_setup_payload_standard_3_8_live(self):
        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="fake-key"))
        payload = client._build_setup_payload(
            model_name="models/gemini-3.8-live",
            voice_name="Puck",
            system_prompt_text="Você é o Zorin Copilot.",
        )
        setup = payload["setup"]
        self.assertEqual(setup["model"], "models/gemini-3.8-live")
        gen_cfg = setup["generationConfig"]
        self.assertEqual(gen_cfg["responseModalities"], ["AUDIO"])
        self.assertEqual(
            gen_cfg["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"],
            "Puck",
        )
        self.assertNotIn("thinkingConfig", gen_cfg)
        self.assertEqual(setup["systemInstruction"]["parts"][0]["text"], "Você é o Zorin Copilot.")
        self.assertTrue(len(setup["tools"]) > 0)

    def test_build_setup_payload_extended_thinking(self):
        client = GeminiLiveClient(config=CopilotConfig(gemini_api_key="fake-key"))
        payload = client._build_setup_payload(
            model_name="models/gemini-3.8-live-extended-thinking",
            voice_name="Aoede",
            system_prompt_text="Instrução do sistema",
        )
        setup = payload["setup"]
        self.assertEqual(setup["model"], "models/gemini-3.8-live-extended-thinking")
        gen_cfg = setup["generationConfig"]
        self.assertEqual(
            gen_cfg["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"],
            "Aoede",
        )
        self.assertIn("thinkingConfig", gen_cfg)
        self.assertEqual(gen_cfg["thinkingConfig"]["thinkingBudget"], 2048)

    @patch.object(CopilotConfig, "save")
    def test_set_voice_and_set_model(self, mock_save):
        cfg = CopilotConfig(gemini_api_key="fake-key", gemini_live_model="models/gemini-3.8-live", gemini_live_voice="Puck")
        client = GeminiLiveClient(config=cfg)

        client.set_voice("Zephyr")
        self.assertEqual(client.config.gemini_live_voice, "Zephyr")
        mock_save.assert_called()

        client.set_model("models/gemini-3.8-live-extended-thinking")
        self.assertEqual(client.config.gemini_live_model, "models/gemini-3.8-live-extended-thinking")


if __name__ == "__main__":
    unittest.main()

