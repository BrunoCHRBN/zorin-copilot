"""Testes unitários para o Pilar 3: Atalho Direto de Recorte e Visão Contínua."""

import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction
from zorin_copilot.ai.engine import IntentEngine
from zorin_copilot.ai.providers import GeminiProvider, OpenAICompatProvider
from zorin_copilot.core.config import CopilotConfig


class ContinuousVisionTest(unittest.TestCase):
    def setUp(self):
        self.sample_image = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 40

    @patch("zorin_copilot.ai.providers.requests.post")
    def test_gemini_multiturn_vision_payload(self, mock_post):
        """Verifica se o Gemini recebe histórico de turnos anteriores e a imagem ativa."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"explanation": "A linha 3 declara a variável total.", "actions": []}'
                            }
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        provider = GeminiProvider(api_key="fake-key-123", model="gemini-3.5-flash")
        history = [
            {"role": "user", "content": "Analise este código"},
            {"role": "assistant", "content": "Código de cálculo de soma em Python."},
        ]

        explanation, actions = provider.chat(
            prompt="O que faz a linha 3?",
            history=history,
            image_bytes=self.sample_image,
        )

        self.assertIn("A linha 3 declara", explanation)
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        contents = payload["contents"]

        # Deve conter os turnos do histórico (user e model) mais o turno atual com imagem
        self.assertEqual(len(contents), 3)
        self.assertEqual(contents[0]["role"], "user")
        self.assertEqual(contents[1]["role"], "model")
        self.assertEqual(contents[2]["role"], "user")

        # Turno atual deve ter imagem (inline_data) e texto da pergunta
        parts = contents[2]["parts"]
        self.assertTrue(any("inline_data" in p for p in parts))
        self.assertTrue(any(p.get("text") == "O que faz a linha 3?" for p in parts))

    @patch("zorin_copilot.ai.providers.requests.post")
    def test_openai_multiturn_vision_payload(self, mock_post):
        """Verifica se OpenAICompatProvider formata image_url quando recebe imagem."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"explanation": "A função main inicia o loop de eventos.", "actions": []}'
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        provider = OpenAICompatProvider(
            api_url="https://api.openai.com/v1",
            api_key="sk-fake",
            model="gpt-4o-mini",
        )
        history = [{"role": "user", "content": "Visão da tela"}]

        explanation, actions = provider.chat(
            prompt="Explique a função main",
            history=history,
            image_bytes=self.sample_image,
        )

        self.assertIn("função main", explanation)
        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        messages = payload["messages"]

        # Deve ter system, history, e o user_msg multimodal
        user_msg = messages[-1]
        self.assertEqual(user_msg["role"], "user")
        self.assertIsInstance(user_msg["content"], list)
        types = [item.get("type") for item in user_msg["content"]]
        self.assertIn("text", types)
        self.assertIn("image_url", types)

    def test_intent_engine_passes_continuous_vision(self):
        """Verifica se o IntentEngine preserva prompt específico e repassa imagem ativa."""
        config = CopilotConfig(gemini_api_key="test-key")
        engine = IntentEngine(config=config)

        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True
        mock_provider.chat.return_value = ("Explicação da linha 5", [])
        engine.llm_provider = mock_provider

        history = [
            {"role": "user", "content": "Print de erro"},
            {"role": "assistant", "content": "Erro de compilação C++"},
        ]

        plan = engine.parse(
            prompt="Por que deu erro na linha 5?",
            history=history,
            image_bytes=self.sample_image,
            is_area_capture=True,
        )

        self.assertEqual(plan.thought, "Explicação da linha 5")
        mock_provider.chat.assert_called_once()
        _, kwargs = mock_provider.chat.call_args
        self.assertEqual(kwargs["image_bytes"], self.sample_image)
        self.assertEqual(kwargs["history"], history)
        # O prompt não deve ser sobrescrito pelo default genérico porque é uma pergunta específica
        actual_prompt = kwargs.get("prompt") or mock_provider.chat.call_args[0][0]
        self.assertIn("linha 5", actual_prompt)


if __name__ == "__main__":
    unittest.main()
