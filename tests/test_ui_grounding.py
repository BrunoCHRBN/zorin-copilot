"""Testes unitários e de integração do UIGroundingService (OCR Espacial e Grounding Visual)."""

import unittest
from unittest import mock

from zorin_copilot.ai.actions import ActionType, DesktopAction
from zorin_copilot.ai.agent_tools import ToolRegistry
from zorin_copilot.core.ui_grounding import (
    UIGroundingService,
    VisualElement,
    get_tesseract_languages,
    parse_tesseract_tsv,
    score_text_match,
)
from zorin_copilot.shell.executor import ActionExecutor
from zorin_copilot.shell.input_driver import VirtualInputDriver


class UIGroundingMathAndParsingTest(unittest.TestCase):
    """Testa a pontuação de casamento de texto e parsing de TSV do Tesseract."""

    def test_score_text_match(self):
        # Casamento exato
        self.assertEqual(score_text_match("Salvar", "salvar"), 1.0)
        self.assertEqual(score_text_match("OK", "ok"), 1.0)

        # Substring
        self.assertGreater(score_text_match("Salvar", "Salvar Como..."), 0.85)
        self.assertGreater(score_text_match("Arquivo", "Menu Arquivo"), 0.80)

        # Tolerância a ruídos de OCR (typos)
        self.assertGreater(score_text_match("Salvar", "Sahar"), 0.68)
        self.assertGreater(score_text_match("Cancelar", "Cance1ar"), 0.80)

        # Não correspondência
        self.assertLess(score_text_match("Salvar", "Excluir"), 0.50)
        self.assertEqual(score_text_match("", "qualquer"), 0.0)

        # Prevenção contra falsos positivos críticos (ruído OCR de 1-2 letras não pode casar)
        self.assertLess(score_text_match("Máximo", "o"), 0.35)
        self.assertLess(score_text_match("Preço", "e"), 0.35)
        self.assertLess(score_text_match("Até R$ 1.500", "1."), 0.35)
        self.assertLess(score_text_match("Até R$ 1.500", "Até"), 0.50)

        # Normalização de acentuação (pt-BR)
        self.assertEqual(score_text_match("Máximo", "Maximo"), 1.0)
        self.assertEqual(score_text_match("Preço", "Preco"), 1.0)

    def test_parse_tesseract_tsv(self):
        tsv_mock = """level	page_num	block_num	par_num	line_num	word_num	left	top	width	height	conf	text
1	1	0	0	0	0	0	0	1920	1080	-1	
5	1	1	1	1	1	100	200	60	25	96.0	Salvar
5	1	1	1	1	2	165	200	55	25	94.0	Arquivo
5	1	2	1	1	1	500	800	80	30	90.0	Cancelar
"""
        elements = parse_tesseract_tsv(tsv_mock, offset_x=1920, offset_y=0)
        # Deve ter 3 palavras + 1 frase ("Salvar Arquivo") = 4 elementos
        self.assertEqual(len(elements), 4)

        # Verifica palavra 1 com offset de monitor
        w1 = next(e for e in elements if e.text == "Salvar")
        self.assertEqual(w1.bbox, (1920 + 100, 200, 60, 25))
        self.assertEqual(w1.x, 1920 + 100 + 30)  # centro X
        self.assertEqual(w1.y, 200 + 12)  # centro Y

        # Verifica frase composta
        phrase = next(e for e in elements if e.is_phrase)
        self.assertEqual(phrase.text, "Salvar Arquivo")
        self.assertEqual(phrase.bbox, (1920 + 100, 200, 120, 25))

    def test_low_confidence_words_ignored(self):
        tsv_low_conf = """level	page_num	block_num	par_num	line_num	word_num	left	top	width	height	conf	text
5	1	1	1	1	1	50	50	20	20	10.0	Lixo
5	1	1	1	1	2	80	50	20	20	85.0	Bom
"""
        elements = parse_tesseract_tsv(tsv_low_conf, min_confidence=30.0)
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0].text, "Bom")


class UIGroundingServiceSearchTest(unittest.TestCase):
    """Testa a busca de elementos visuais na tela."""

    def test_find_elements_ranking(self):
        elements = [
            VisualElement(text="Salvar Como...", x=100, y=100, bbox=(80, 90, 40, 20), confidence=90.0),
            VisualElement(text="Salvar", x=200, y=200, bbox=(180, 190, 40, 20), confidence=95.0),
            VisualElement(text="Cancelar", x=300, y=300, bbox=(280, 290, 40, 20), confidence=92.0),
        ]
        results = UIGroundingService.find_elements("Salvar", elements=elements)
        self.assertEqual(len(results), 2)
        # O casamento exato "Salvar" deve ser o primeiro
        self.assertEqual(results[0][0].text, "Salvar")
        self.assertEqual(results[0][1], 1.0)
        # O segundo deve ser "Salvar Como..."
        self.assertEqual(results[1][0].text, "Salvar Como...")

    def test_find_elements_excludes_copilot_window(self):
        fence = mock.MagicMock()
        # Janela do Copilot simulada em x=1920..2880, y=0..1080
        fence.get_all_excluded_rects.return_value = [(1920, 0, 960, 1080)]
        elements = [
            # Elemento dentro da janela do Copilot (ex: chat history dizendo "Buscar produtos")
            VisualElement(text="Buscar produtos", x=2370, y=324, bbox=(2300, 310, 140, 28), confidence=95.0),
            # Elemento fora do Copilot (ex: no Chrome em x=3100, y=150)
            VisualElement(text="Buscar produtos", x=3100, y=150, bbox=(3000, 140, 200, 30), confidence=95.0),
        ]
        results = UIGroundingService.find_elements("Buscar produtos", elements=elements, fence=fence)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0].x, 3100)


class UIGroundingClickExecutionTest(unittest.TestCase):
    """Testa a execução de cliques visuais e os fallbacks."""

    def test_click_visual_element_via_ocr_success(self):
        fake_elements = [
            VisualElement(text="Confirmar Pedido", x=500, y=400, bbox=(450, 385, 100, 30), confidence=95.0),
        ]
        fake_driver = mock.Mock()
        fake_driver.click.return_value = (True, "Clique executado")
        fake_fence = mock.Mock()
        fake_fence.is_emergency_stopped = False
        fake_fence.is_coordinate_allowed.return_value = (True, "ok")

        with mock.patch.object(UIGroundingService, "scan_screen", return_value=fake_elements):
            ok, msg, coords = UIGroundingService.click_visual_element(
                "Confirmar", driver=fake_driver, fence=fake_fence
            )
            self.assertTrue(ok)
            self.assertEqual(coords, (500, 400))
            fake_driver.click.assert_called_once_with(
                500, 400, button="left", double=False, label="Clicando em 'Confirmar Pedido'"
            )

    def test_click_blocked_by_emergency_stop(self):
        fake_fence = mock.Mock()
        fake_fence.is_emergency_stopped = True
        ok, msg, coords = UIGroundingService.click_visual_element("Salvar", fence=fake_fence)
        self.assertFalse(ok)
        self.assertIn("Parada de emergência", msg)
        self.assertIsNone(coords)

    def test_click_blocked_by_screen_fence(self):
        fake_elements = [
            VisualElement(text="Fora da tela", x=9999, y=9999, bbox=(9990, 9990, 20, 20), confidence=95.0),
        ]
        fake_fence = mock.Mock()
        fake_fence.is_emergency_stopped = False
        fake_fence.is_coordinate_allowed.return_value = (False, "Fora do monitor autorizado")

        with mock.patch.object(UIGroundingService, "scan_screen", return_value=fake_elements):
            ok, msg, coords = UIGroundingService.click_visual_element(
                "Fora", fence=fake_fence
            )
            self.assertFalse(ok)
            self.assertIn("bloqueado pela cerca", msg)


class ActionExecutorVisualFallbackTest(unittest.TestCase):
    """Garante que o ActionExecutor recorre ao UIGroundingService quando AT-SPI falha."""

    def test_click_element_falls_back_to_vision_when_atspi_misses(self):
        executor = ActionExecutor()
        fake_inspector = mock.Mock()
        fake_inspector.list_applications.return_value = []
        executor.inspector = fake_inspector

        with mock.patch(
            "zorin_copilot.core.ui_grounding.UIGroundingService.click_visual_element",
            return_value=(True, "Clicado em 'YouTube' via Visão.", (800, 300)),
        ) as mock_vis_click:
            report = executor.execute(DesktopAction(ActionType.CLICK, "YouTube"))
            self.assertTrue(report.success)
            self.assertIn("via Visão", report.message)
            mock_vis_click.assert_called_once()

    def test_type_text_falls_back_to_vision_when_atspi_misses(self):
        executor = ActionExecutor()
        fake_inspector = mock.Mock()
        fake_inspector.list_applications.return_value = []
        executor.inspector = fake_inspector

        fake_driver = mock.Mock()
        fake_driver.click.return_value = (True, "Foco ok")
        fake_driver.type_text.return_value = (True, "Digitado ok")
        executor.input_driver = fake_driver

        fake_el = VisualElement(text="Pesquisar no Google", x=600, y=250, bbox=(500, 230, 200, 40), confidence=95.0)

        with mock.patch(
            "zorin_copilot.core.ui_grounding.UIGroundingService.find_elements",
            return_value=[(fake_el, 0.95)],
        ):
            act = DesktopAction(ActionType.TYPE_TEXT, "Pesquisar", params={"text": "Zorin OS 18"})
            report = executor.execute(act)
            self.assertTrue(report.success)
            fake_driver.click.assert_called_once_with(600, 250, label="Focando 'Pesquisar no Google'")
            fake_driver.type_text.assert_called_once_with("Zorin OS 18", press_enter=False)


class AgentToolsVisualGroundingIntegrationTest(unittest.TestCase):
    """Testa as novas ferramentas find_on_screen, click_on_screen e read_screen_text."""

    def setUp(self):
        self.driver = mock.Mock()
        self.fence = mock.Mock()
        self.registry = ToolRegistry(input_driver=self.driver, fence=self.fence)

    def test_tool_find_on_screen(self):
        fake_el = VisualElement(text="Download", x=400, y=300, bbox=(350, 285, 100, 30), confidence=96.0)
        with mock.patch(
            "zorin_copilot.core.ui_grounding.UIGroundingService.find_elements",
            return_value=[(fake_el, 0.98)],
        ):
            spec = self.registry.spec("find_on_screen")
            self.assertIsNotNone(spec)
            res = spec.handler({"query": "Download"})
            self.assertTrue(res.get("ok"))
            self.assertEqual(res.get("best_match", {}).get("text"), "Download")
            self.assertEqual(res.get("best_match", {}).get("x"), 400)

    def test_tool_click_on_screen(self):
        with mock.patch(
            "zorin_copilot.core.ui_grounding.UIGroundingService.click_visual_element",
            return_value=(True, "Clique em 'Play' realizado", (700, 450)),
        ):
            spec = self.registry.spec("click_on_screen")
            self.assertIsNotNone(spec)
            res = spec.handler({"query": "Play", "button": "left"})
            self.assertTrue(res.get("ok"))
            self.assertEqual(res.get("coords"), (700, 450))

    def test_tool_read_screen_text(self):
        fake_elements = [
            VisualElement(text="Menu", x=50, y=50, bbox=(40, 40, 20, 20), confidence=95.0),
            VisualElement(text="Ajuda", x=100, y=50, bbox=(90, 40, 20, 20), confidence=94.0),
        ]
        with mock.patch(
            "zorin_copilot.core.ui_grounding.UIGroundingService.scan_screen",
            return_value=fake_elements,
        ):
            spec = self.registry.spec("read_screen_text")
            self.assertIsNotNone(spec)
            res = spec.handler({})
            self.assertTrue(res.get("ok"))
            self.assertEqual(res.get("count"), 2)


if __name__ == "__main__":
    unittest.main()
