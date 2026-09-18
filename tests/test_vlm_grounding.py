"""Testes unitários e de integração do Módulo de Grounding Visual por VLM."""

import json
import unittest
from unittest import mock

from zorin_copilot.ai.agent_tools import ToolRegistry
from zorin_copilot.core.fence import ScreenFenceManager
from zorin_copilot.core.ui_grounding import UIGroundingService, VisualElement
from zorin_copilot.core.vlm_grounding import (
    LocalVLMGroundingClient,
    VLMBoundingBox,
    VLMGroundingResult,
)


class VLMBoundingBoxTest(unittest.TestCase):
    """Testa a normalização geométrica de caixas delimitadoras e desambiguação de eixos."""

    def test_from_raw_scale_1000(self):
        # Escala 0 a 1000 (comum em Qwen2.5-VL e Gemini)
        box = VLMBoundingBox.from_raw([250, 150, 500, 350])
        self.assertAlmostEqual(box.xmin, 0.250)
        self.assertAlmostEqual(box.ymin, 0.150)
        self.assertAlmostEqual(box.xmax, 0.500)
        self.assertAlmostEqual(box.ymax, 0.350)
        self.assertAlmostEqual(box.center_rel[0], 0.375)
        self.assertAlmostEqual(box.center_rel[1], 0.250)

    def test_to_screen_rect(self):
        box = VLMBoundingBox.from_raw([200, 100, 400, 300])  # 0.2, 0.1, 0.4, 0.3
        # Monitor 1920x1080 com offset (1920, 0)
        rect = box.to_screen_rect(base_x=1920, base_y=0, width=1920, height=1080)
        # x = 1920 + 0.2 * 1920 = 1920 + 384 = 2304
        # y = 0 + 0.1 * 1080 = 108
        # w = 0.2 * 1920 = 384
        # h = 0.2 * 1080 = 216
        self.assertEqual(rect, (2304, 108, 384, 216))

    def test_disambiguation_with_point_qwen_format(self):
        # Qwen-VL: [xmin, ymin, xmax, ymax]
        # Ponto em (400, 250) -> x=400 está entre 250..550, y=250 está entre 100..400
        box = VLMBoundingBox.from_raw([250, 100, 550, 400], point=[400, 250])
        self.assertAlmostEqual(box.xmin, 0.250)
        self.assertAlmostEqual(box.ymin, 0.100)
        self.assertAlmostEqual(box.xmax, 0.550)
        self.assertAlmostEqual(box.ymax, 0.400)

    def test_disambiguation_with_point_gemini_format(self):
        # Gemini: [ymin, xmin, ymax, xmax] -> coords: [100, 250, 400, 550]
        # Point: [y, x] = [250, 400]
        box = VLMBoundingBox.from_raw([100, 250, 400, 550], point=[250, 400], is_gemini=True)
        self.assertAlmostEqual(box.xmin, 0.250)
        self.assertAlmostEqual(box.ymin, 0.100)
        self.assertAlmostEqual(box.xmax, 0.550)
        self.assertAlmostEqual(box.ymax, 0.400)


class VLMGroundingResultTest(unittest.TestCase):
    """Testa a conversão de resultados para dicionários e VisualElement."""

    def test_to_visual_element(self):
        res = VLMGroundingResult(
            found=True,
            x=640,
            y=480,
            bbox=(600, 450, 80, 60),
            confidence=0.92,
            query="botão de play",
            label="Reproduzir vídeo",
            description="Botão circular de play central",
            source="vlm_local",
        )
        el = res.to_visual_element()
        self.assertIsInstance(el, VisualElement)
        self.assertEqual(el.text, "Reproduzir vídeo")
        self.assertEqual(el.x, 640)
        self.assertEqual(el.y, 480)
        self.assertEqual(el.bbox, (600, 450, 80, 60))
        self.assertAlmostEqual(el.confidence, 92.0)
        self.assertEqual(el.source, "vision_model")


class LocalVLMGroundingClientTest(unittest.TestCase):
    """Testa o cliente Ollama VLM, auto-discovery de modelos e parsing de saídas."""

    def test_list_available_vision_models(self):
        client = LocalVLMGroundingClient(ollama_url="http://mock-ollama:11434")
        mock_tags = {
            "models": [
                {"name": "llama3:latest", "capabilities": ["completion"]},
                {"name": "qwen2.5vl:7b", "capabilities": ["vision", "completion"]},
                {"name": "nomic-embed:latest", "details": {"families": ["nomic-bert"]}},
                {"name": "minicpm-v:latest", "details": {"families": ["minicpm-v"]}},
            ]
        }
        with mock.patch("urllib.request.urlopen") as mock_url:
            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = json.dumps(mock_tags).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_url.return_value = mock_resp

            vision_models = client.list_available_vision_models()
            self.assertIn("qwen2.5vl:7b", vision_models)
            self.assertIn("minicpm-v:latest", vision_models)
            self.assertNotIn("llama3:latest", vision_models)

    def test_select_vision_model_priorities(self):
        client = LocalVLMGroundingClient()
        with mock.patch.object(
            client,
            "list_available_vision_models",
            return_value=["moondream:latest", "qwen2.5vl:7b", "qwen2.5vl:3b"],
        ):
            selected = client.select_vision_model()
            # qwen2.5vl:7b deve ter prioridade sobre moondream
            self.assertEqual(selected, "qwen2.5vl:7b")

    def test_parse_vlm_output_json_in_markdown(self):
        client = LocalVLMGroundingClient()
        raw_markdown = """Aqui está o elemento:
```json
{
  "found": true,
  "bbox": [200, 300, 400, 500],
  "point": [300, 400],
  "confidence": 0.95,
  "label": "Play",
  "description": "Botão de play"
}
```
"""
        found, bbox_obj, point_rel, conf, label, desc, _ = client._parse_vlm_output(raw_markdown)
        self.assertTrue(found)
        self.assertIsNotNone(bbox_obj)
        self.assertEqual(label, "Play")
        self.assertAlmostEqual(conf, 0.95)
        self.assertAlmostEqual(bbox_obj.xmin, 0.200)
        self.assertAlmostEqual(bbox_obj.ymin, 0.300)

    def test_parse_vlm_output_negative(self):
        client = LocalVLMGroundingClient()
        raw_neg = '{"found": false, "confidence": 0.0, "description": "Elemento ausente"}'
        found, bbox_obj, point_rel, conf, label, desc, _ = client._parse_vlm_output(raw_neg)
        self.assertFalse(found)
        self.assertIsNone(bbox_obj)
        self.assertEqual(desc, "Elemento ausente")

    def test_ground_with_ollama_mock_success(self):
        client = LocalVLMGroundingClient(ollama_url="http://127.0.0.1:11434")
        mock_response = {
            "response": json.dumps({
                "found": True,
                "bbox": [250, 200, 500, 400],
                "point": [375, 300],
                "confidence": 0.96,
                "label": "Engrenagem",
                "description": "Ícone de configurações",
            })
        }
        with mock.patch("urllib.request.urlopen") as mock_url:
            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = json.dumps(mock_response).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_url.return_value = mock_resp

            fence = mock.MagicMock()
            fence.mode.value = "primary_only"
            fence.get_target_window.return_value = None
            active_m = mock.MagicMock()
            active_m.x = 0
            active_m.y = 0
            active_m.width = 1000
            active_m.height = 1000
            fence.get_active_monitor.return_value = active_m

            result = client._ground_with_ollama(
                query="engrenagem",
                image_bytes=b"fake_image",
                model="qwen2.5vl:7b",
                fence=fence,
            )
            self.assertTrue(result.found)
            self.assertEqual(result.label, "Engrenagem")
            self.assertEqual(result.x, 375)
            self.assertEqual(result.y, 300)
            self.assertEqual(result.bbox, (250, 200, 250, 200))


class UIGroundingVLMIntegrationTest(unittest.TestCase):
    """Testa a integração da cascata OCR -> VLM em UIGroundingService."""

    def test_locate_element_ocr_cascade_hit(self):
        fake_el = VisualElement(text="Salvar Arquivo", x=100, y=100, bbox=(80, 90, 40, 20), confidence=95.0)
        with mock.patch.object(UIGroundingService, "find_elements", return_value=[(fake_el, 0.95)]):
            el, score, source = UIGroundingService.locate_element("Salvar")
            self.assertIsNotNone(el)
            self.assertEqual(source, "ocr")
            self.assertEqual(el.text, "Salvar Arquivo")

    def test_locate_element_falls_back_to_vlm_when_ocr_misses(self):
        fake_vlm_el = VisualElement(
            text="Play",
            x=800,
            y=600,
            bbox=(750, 570, 100, 60),
            confidence=90.0,
            source="vision_model",
        )
        with mock.patch.object(UIGroundingService, "find_elements", return_value=[]):
            with mock.patch.object(UIGroundingService, "ground_visual_element", return_value=fake_vlm_el):
                el, score, source = UIGroundingService.locate_element("botão de play")
                self.assertIsNotNone(el)
                self.assertEqual(source, "vision_model")
                self.assertEqual(el.x, 800)
                self.assertEqual(el.y, 600)

    def test_click_visual_element_prefer_vlm(self):
        fake_driver = mock.Mock()
        fake_driver.click.return_value = (True, "Clicado com Ghost Cursor")
        fake_fence = mock.Mock()
        fake_fence.is_emergency_stopped = False
        fake_fence.is_coordinate_allowed.return_value = (True, "Permitido")

        fake_vlm_el = VisualElement(
            text="Engrenagem",
            x=950,
            y=50,
            bbox=(930, 30, 40, 40),
            confidence=94.0,
            source="vision_model",
        )

        with mock.patch.object(UIGroundingService, "ground_visual_element", return_value=fake_vlm_el):
            ok, msg, coords = UIGroundingService.click_visual_element(
                "engrenagem de configurações",
                driver=fake_driver,
                fence=fake_fence,
                prefer_vlm=True,
            )
            self.assertTrue(ok)
            self.assertEqual(coords, (950, 50))
            fake_driver.click.assert_called_once_with(
                950, 50, button="left", double=False, label="Clicando em 'engrenagem de configurações' (VLM)"
            )


class ToolRegistryVLMGroundingTest(unittest.TestCase):
    """Testa o acionamento das ferramentas locate_element_visual e click_visual_element."""

    def setUp(self):
        self.driver = mock.Mock()
        self.fence = mock.Mock()
        self.registry = ToolRegistry(input_driver=self.driver, fence=self.fence)

    def test_locate_element_visual_tool(self):
        fake_el = VisualElement(
            text="Menu Hamburguer",
            x=30,
            y=30,
            bbox=(10, 10, 40, 40),
            confidence=95.0,
            source="vision_model",
        )
        with mock.patch.object(UIGroundingService, "find_elements", return_value=[]):
            with mock.patch.object(UIGroundingService, "ground_visual_element", return_value=fake_el):
                spec = self.registry.spec("locate_element_visual")
                self.assertIsNotNone(spec)
                res = spec.handler({"query": "menu lateral"})
                self.assertTrue(res.get("ok"))
                self.assertEqual(res.get("source"), "vision_model")
                self.assertEqual(res.get("best_match", {}).get("x"), 30)

    def test_click_visual_element_tool(self):
        with mock.patch.object(
            UIGroundingService,
            "click_visual_element",
            return_value=(True, "Clique executado", (500, 300)),
        ) as mock_click:
            spec = self.registry.spec("click_visual_element")
            self.assertIsNotNone(spec)
            res = spec.handler({"query": "fechar modal", "button": "left"})
            self.assertTrue(res.get("ok"))
            self.assertEqual(res.get("coords"), (500, 300))
            mock_click.assert_called_once_with(
                "fechar modal",
                button="left",
                double=False,
                driver=self.driver,
                fence=self.fence,
                prefer_vlm=True,
            )


if __name__ == "__main__":
    unittest.main()
