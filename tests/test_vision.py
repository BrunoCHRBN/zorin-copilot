import unittest
from unittest.mock import MagicMock, patch
import io
from PIL import Image

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction
from zorin_copilot.ai.engine import IntentEngine
from zorin_copilot.ai.providers import GeminiProvider
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.vision import ScreenCaptureService


class VisionTest(unittest.TestCase):
    def test_image_optimization(self):
        # Cria uma imagem grande em memória (2000x1500)
        img = Image.new("RGB", (2000, 1500), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tf.write(buf.getvalue())
            tmp_path = tf.name

        try:
            opt_bytes = ScreenCaptureService._optimize_image(tmp_path, max_size=1280, quality=80)
            self.assertIsInstance(opt_bytes, bytes)
            self.assertGreater(len(opt_bytes), 0)

            # Verifica se foi redimensionada respeitando o teto de 1280
            res_img = Image.open(io.BytesIO(opt_bytes))
            self.assertLessEqual(res_img.width, 1280)
            self.assertLessEqual(res_img.height, 1280)
            self.assertEqual(res_img.format, "JPEG")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @patch("requests.post")
    def test_gemini_multimodal_payload(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '{"explanation": "Erro de sintaxe detectado", "actions": []}'}
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        provider = GeminiProvider(api_key="test_key", model="gemini-3.5-flash")
        dummy_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        explanation, actions = provider.chat(
            prompt="O que tem aqui?",
            image_bytes=dummy_bytes,
        )

        self.assertIn("Erro de sintaxe", explanation)
        self.assertTrue(mock_post.called)
        sent_json = mock_post.call_args[1]["json"]
        parts = sent_json["contents"][0]["parts"]
        # Verifica se o bloco inline_data com a imagem em base64 foi inserido
        self.assertTrue(any("inline_data" in p for p in parts))

    def test_engine_parse_with_image_area(self):
        engine = IntentEngine(config=CopilotConfig(gemini_api_key=""))
        plan = engine.parse(
            prompt="",
            image_bytes=b"dummy_bytes",
            is_area_capture=True,
        )
        # Como a chave está vazia, deve orientar a configurar
        self.assertIn("captura da tela foi realizada com sucesso", plan.thought)

    def test_engine_text_triggers_capture_screen(self):
        engine = IntentEngine(config=CopilotConfig())
        plan = engine.parse("analise minha tela")
        self.assertTrue(any(a.action_type == ActionType.CAPTURE_SCREEN for a in plan.actions))
        area_act = next(a for a in plan.actions if a.action_type == ActionType.CAPTURE_SCREEN and a.target == "area")
        self.assertIn("Recortar", area_act.describe())


if __name__ == "__main__":
    unittest.main()
