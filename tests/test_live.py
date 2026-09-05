"""Testes unitários para o cliente de voz ao vivo (Gemini Live) e execução de ferramentas."""

import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ai.live import (
    LIVE_TOOLS_DECLARATION,
    GeminiLiveClient,
    LiveVoiceState,
)
from zorin_copilot.core.config import CopilotConfig


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

        for expected in ["launch_app", "system_control", "capture_screen", "open_url", "get_system_info", "web_search"]:
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

        res = self.client._dispatch_tool("launch_app", {"app_name": "app_inexistente_xyz"})
        self.assertFalse(res["success"])
        self.assertIn("não encontrado", res["message"])

    def test_dispatch_unknown_tool(self):
        """Testa ferramenta inexistente retornando erro seguro."""
        res = self.client._dispatch_tool("ferramenta_magica", {})
        self.assertFalse(res["success"])
        self.assertIn("desconhecida", res["message"])


if __name__ == "__main__":
    unittest.main()
