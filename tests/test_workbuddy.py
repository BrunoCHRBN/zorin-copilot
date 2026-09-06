"""Testes unitários para o provedor WorkBuddy AI (Tencent Hunyuan HY4)."""

import json
import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ai.actions import ActionType
from zorin_copilot.ai.providers import WorkBuddyProvider, get_llm_provider
from zorin_copilot.core.config import CopilotConfig


class WorkBuddyProviderTest(unittest.TestCase):
    def setUp(self):
        self.provider = WorkBuddyProvider(
            api_key="ck_test_key_123",
            model="hy4-preview",
            api_url="https://www.workbuddy.ai/v2",
        )

    def test_is_configured(self):
        self.assertTrue(self.provider.is_configured())

        empty_key = WorkBuddyProvider(api_key="", model="hy4-preview")
        self.assertFalse(empty_key.is_configured())

    @patch("requests.post")
    def test_test_connection_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        ok, msg = self.provider.test_connection()
        self.assertTrue(ok)
        self.assertIn("bem-sucedida", msg)

        # Garante que enviou stream=True e o primeiro item como system
        args, kwargs = mock_post.call_args
        self.assertTrue(kwargs.get("stream"))
        payload = kwargs.get("json", {})
        self.assertEqual(payload.get("messages")[0]["role"], "system")

    @patch("requests.post")
    def test_test_connection_failure(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = '{"message":"unauthorized"}'
        mock_post.return_value = mock_resp

        ok, msg = self.provider.test_connection()
        self.assertFalse(ok)
        self.assertIn("401", msg)

    @patch("requests.post")
    def test_chat_streaming_aggregation(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        chunk1 = json.dumps({"choices": [{"delta": {"content": '{"explanation": "Abrindo navegador", '}}]})
        chunk2 = json.dumps({"choices": [{"delta": {"content": '"actions": [{"type": "open_url", "target": "https://google.com", "description": "Abrir Google"}]}'}}]})
        mock_resp.iter_lines.return_value = [
            f"data: {chunk1}".encode("utf-8"),
            f"data: {chunk2}".encode("utf-8"),
            b"data: [DONE]",
        ]
        mock_post.return_value = mock_resp

        explanation, actions = self.provider.chat("abrir o google")
        self.assertEqual(explanation, "Abrindo navegador")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, ActionType.OPEN_URL)
        self.assertEqual(actions[0].target, "https://google.com")

    @patch("requests.post")
    def test_chat_multimodal_image(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        chunk = json.dumps({"choices": [{"delta": {"content": '{"explanation": "Imagem analisada", "actions": []}'}}]})
        mock_resp.iter_lines.return_value = [
            f"data: {chunk}".encode("utf-8"),
            b"data: [DONE]",
        ]
        mock_post.return_value = mock_resp

        dummy_image = b"fake_jpeg_bytes"
        self.provider.chat("o que tem na tela?", image_bytes=dummy_image, image_mime="image/jpeg")

        args, kwargs = mock_post.call_args
        payload = kwargs.get("json", {})
        messages = payload.get("messages", [])
        user_msg = messages[-1]
        self.assertIsInstance(user_msg["content"], list)
        has_img = any(p.get("type") == "image_url" for p in user_msg["content"])
        self.assertTrue(has_img)

    def test_get_llm_provider_factory(self):
        cfg = CopilotConfig(
            provider="workbuddy",
            workbuddy_api_key="ck_sample_key",
            workbuddy_model="hy4-preview",
            workbuddy_url="https://www.workbuddy.ai/v2",
        )
        self.assertTrue(cfg.is_configured())

        provider = get_llm_provider(cfg)
        self.assertIsInstance(provider, WorkBuddyProvider)
        self.assertEqual(provider.model, "hy4-preview")


if __name__ == "__main__":
    unittest.main()
