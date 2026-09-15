"""Testes unitários e de integração do Ghost Cursor Overlay (Computer-Use / Operador)."""

import time
import unittest
from unittest import mock

from zorin_copilot.ai.actions import ActionType, DesktopAction
from zorin_copilot.ai.agent_tools import ToolRegistry
from zorin_copilot.core.a11y import UIElement
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.shell.executor import ActionExecutor
from zorin_copilot.shell.input_driver import VirtualInputDriver
from zorin_copilot.ui.ghost_cursor import (
    ClickRipple,
    GhostCursorOverlay,
    GhostCursorWindow,
    _ease_out_cubic,
    _parse_hex_color,
)
from zorin_copilot.ui import layer_shell


class GhostCursorMathAndConfigTest(unittest.TestCase):
    """Testa a matemática de animação, conversão de cores e configs."""

    def test_ease_out_cubic_limits(self):
        self.assertAlmostEqual(_ease_out_cubic(0.0), 0.0)
        self.assertAlmostEqual(_ease_out_cubic(1.0), 1.0)
        # Amortecimento desacelera no final (ex: em 50% do tempo já andou ~87.5%)
        self.assertAlmostEqual(_ease_out_cubic(0.5), 0.875)
        # Clamping de segurança
        self.assertAlmostEqual(_ease_out_cubic(-0.5), 0.0)
        self.assertAlmostEqual(_ease_out_cubic(1.5), 1.0)

    def test_parse_hex_color(self):
        r, g, b = _parse_hex_color("#00d2ff")
        self.assertAlmostEqual(r, 0.0, places=2)
        self.assertAlmostEqual(g, 210.0 / 255.0, places=2)
        self.assertAlmostEqual(b, 1.0, places=2)

        # Fallback para string inválida
        fb = _parse_hex_color("invalido", fallback=(1.0, 0.0, 0.0))
        self.assertEqual(fb, (1.0, 0.0, 0.0))

    def test_config_defaults(self):
        cfg = CopilotConfig()
        self.assertTrue(getattr(cfg, "ghost_cursor_enabled", False))
        self.assertEqual(getattr(cfg, "ghost_cursor_color", ""), "#00d2ff")
        self.assertEqual(getattr(cfg, "ghost_cursor_anim_speed_ms", 0), 250)


class GhostCursorOverlayAPITest(unittest.TestCase):
    """Testa a API pública do gerenciador do Ghost Cursor."""

    def setUp(self):
        self.overlay = GhostCursorOverlay()
        self.overlay.enabled = True
        self.overlay.ripple_count = 0

    def test_singleton_get_default(self):
        inst1 = GhostCursorOverlay.get_default()
        inst2 = GhostCursorOverlay.get_default()
        self.assertIs(inst1, inst2)

    def test_point_to_records_state(self):
        self.overlay.point_to(800, 450, duration_ms=200, label="Movendo até botão")
        state = self.overlay.get_state()
        self.assertEqual(state["target"], (800.0, 450.0))
        self.assertEqual(state["label"], "Movendo até botão")
        self.assertEqual(state["action_kind"], "move")

    def test_click_at_records_ripple(self):
        initial_ripples = self.overlay.ripple_count
        self.overlay.click_at(400, 300, button="left", label="Salvar")
        state = self.overlay.get_state()
        self.assertEqual(state["target"], (400.0, 300.0))
        self.assertEqual(state["label"], "Salvar")
        self.assertEqual(state["action_kind"], "click")
        self.assertEqual(self.overlay.ripple_count, initial_ripples + 1)

    def test_double_click_records_double_ripple(self):
        initial_ripples = self.overlay.ripple_count
        self.overlay.click_at(250, 150, button="left", double=True)
        self.assertEqual(self.overlay.ripple_count, initial_ripples + 2)
        state = self.overlay.get_state()
        self.assertEqual(state["action_kind"], "double_click")
        self.assertIn("Duplo clique", state["label"])

    def test_type_at_records_state(self):
        self.overlay.type_at(300, 200, text="Zorin OS", label="Digitar pesquisa")
        state = self.overlay.get_state()
        self.assertEqual(state["target"], (300.0, 200.0))
        self.assertEqual(state["label"], "Digitar pesquisa")
        self.assertEqual(state["action_kind"], "type")

    def test_show_action_and_hide(self):
        self.overlay.show_action("Pesquisando na web...")
        state = self.overlay.get_state()
        self.assertEqual(state["label"], "Pesquisando na web...")

        self.overlay.hide()
        state_hidden = self.overlay.get_state()
        self.assertEqual(state_hidden["label"], "")

    def test_disabled_overlay_no_ops(self):
        self.overlay.enabled = False
        self.overlay.point_to(100, 100, label="Nada")
        # target e label continuam registrados para diagnóstico, mas o overlay permanece inativo
        self.assertFalse(self.overlay.is_active)


class GhostCursorWindowMockTest(unittest.TestCase):
    """Testa a lógica interna de animação do GhostCursorWindow com mocks."""

    def test_tick_animation_advances_coordinates(self):
        # Mock de janela e tick
        win = mock.MagicMock(spec=GhostCursorWindow)
        win.is_moving = True
        win.start_x = 0.0
        win.start_y = 0.0
        win.target_x = 100.0
        win.target_y = 100.0
        win.move_start_time = time.monotonic() - 0.10
        win.move_duration = 0.20
        win.cursor_opacity = 0.5
        win.target_cursor_opacity = 1.0
        win.label_opacity = 0.0
        win.target_label_opacity = 1.0
        win.ripples = []
        win.last_action_time = time.monotonic()
        win.idle_timeout_sec = 2.0

        # Executa a função do tick
        res = GhostCursorWindow._on_tick(win, mock.MagicMock(), mock.MagicMock())
        self.assertTrue(res)
        # Cursor deve ter percorrido mais da metade do caminho devido ao ease_out
        self.assertGreater(win.current_x, 50.0)
        self.assertLess(win.current_x, 100.0)

    def test_layer_shell_anchor_fullscreen_logic(self):
        fake_win = mock.MagicMock()
        with mock.patch.object(layer_shell, "is_supported", return_value=True), \
             mock.patch.object(layer_shell, "init", return_value=True), \
             mock.patch.object(layer_shell, "set_layer") as mock_layer, \
             mock.patch.object(layer_shell, "set_keyboard_mode") as mock_kb, \
             mock.patch.object(layer_shell, "set_exclusive_zone") as mock_zone, \
             mock.patch.object(layer_shell, "set_anchor") as mock_anchor, \
             mock.patch.object(layer_shell, "set_margin") as mock_margin:
            ok = layer_shell.anchor_fullscreen(fake_win, layer="overlay")
            self.assertTrue(ok)
            mock_layer.assert_called_with(fake_win, "overlay")
            mock_kb.assert_called_with(fake_win, "none")
            mock_zone.assert_called_with(fake_win, 0)
            self.assertEqual(mock_anchor.call_count, 4)
            self.assertEqual(mock_margin.call_count, 4)


class VirtualInputDriverGhostCursorIntegrationTest(unittest.TestCase):
    """Garante que chamadas ao VirtualInputDriver acionam o Ghost Cursor."""

    def test_click_triggers_ghost_cursor(self):
        driver = VirtualInputDriver(simulation=True)
        # Mock de cerca permissiva
        driver.fence = mock.Mock()
        driver.fence.is_coordinate_allowed.return_value = (True, "ok")
        driver.fence.is_emergency_stopped = False

        overlay = GhostCursorOverlay.get_default()
        with mock.patch.object(overlay, "click_at") as mock_click:
            driver.click(500, 350, button="left", label="Confirmar")
            mock_click.assert_called_once()
            args, kwargs = mock_click.call_args
            self.assertEqual(args[0], 500)
            self.assertEqual(args[1], 350)
            self.assertEqual(kwargs.get("button"), "left")
            self.assertEqual(kwargs.get("label"), "Confirmar")

    def test_click_relative_triggers_ghost_cursor_with_absolute_coords(self):
        driver = VirtualInputDriver(simulation=True)
        driver.fence = mock.Mock()
        driver.fence.convert_relative_point.return_value = (960, 540)
        driver.fence.is_coordinate_allowed.return_value = (True, "ok")
        driver.fence.is_emergency_stopped = False

        overlay = GhostCursorOverlay.get_default()
        with mock.patch.object(overlay, "click_at") as mock_click:
            driver.click_relative(0.5, 0.5, button="left", label="Clique Central")
            mock_click.assert_called_once()
            args, kwargs = mock_click.call_args
            self.assertEqual(args[0], 960)
            self.assertEqual(args[1], 540)
            self.assertEqual(kwargs.get("label"), "Clique Central")

    def test_type_text_triggers_ghost_cursor(self):
        driver = VirtualInputDriver(simulation=True)
        driver.fence = mock.Mock()
        driver.fence.is_emergency_stopped = False

        overlay = GhostCursorOverlay.get_default()
        with mock.patch.object(overlay, "show_action") as mock_show:
            driver.type_text("Zorin Linux")
            mock_show.assert_called_once()
            self.assertIn("Zorin Linux", mock_show.call_args[0][0])


class ActionExecutorGhostCursorIntegrationTest(unittest.TestCase):
    """Garante que o ActionExecutor move o Ghost Cursor até o elemento alvo."""

    def test_click_element_triggers_ghost_cursor_on_element_center(self):
        executor = ActionExecutor()
        fake_inspector = mock.Mock()
        fake_element = UIElement(
            name="Salvar Arquivo",
            role="push_button",
            bbox=(100, 200, 80, 40),
            actions=("click",),
        )
        fake_root = mock.Mock()
        fake_root.find.return_value = [fake_element]
        fake_inspector.list_applications.return_value = ["gedit"]
        fake_inspector.inspect_application.return_value = fake_root
        fake_inspector.do_action.return_value = True
        executor.inspector = fake_inspector

        overlay = GhostCursorOverlay.get_default()
        with mock.patch.object(overlay, "click_at") as mock_click:
            report = executor.execute(DesktopAction(ActionType.CLICK, "Salvar"))
            self.assertTrue(report.success)
            mock_click.assert_called_once()
            # Centro: x = 100 + 40 = 140, y = 200 + 20 = 220
            args, kwargs = mock_click.call_args
            self.assertEqual(args[0], 140)
            self.assertEqual(args[1], 220)
            self.assertIn("Salvar Arquivo", kwargs.get("label", ""))


class AgentToolsGhostCursorIntegrationTest(unittest.TestCase):
    """Garante que as ferramentas do agente acionam o Ghost Cursor."""

    def test_tool_click_element_notifies_ghost_cursor(self):
        fake_inspector = mock.Mock()
        fake_driver = mock.Mock()
        fake_fence = mock.Mock()
        fake_fence.is_coordinate_allowed.return_value = (True, "ok")

        registry = ToolRegistry(
            inspector=fake_inspector,
            input_driver=fake_driver,
            fence=fake_fence,
        )

        fake_element = UIElement(
            name="Enviar",
            role="push_button",
            uid="0.1",
            bbox=(300, 400, 100, 50),
            actions=("click",),
        )
        fake_root = mock.Mock()
        fake_root.find.return_value = [fake_element]
        fake_inspector.get_ui_tree.return_value = fake_root
        fake_inspector.do_action.return_value = True

        overlay = GhostCursorOverlay.get_default()
        with mock.patch.object(overlay, "click_at") as mock_click, \
             mock.patch("zorin_copilot.core.a11y.DesktopInspector.find_element_by_uid", return_value=fake_element):
            res = registry.spec("click_element").handler({"uid": "0.1"})
            self.assertTrue(res.get("ok"))
            mock_click.assert_called_once()
            # Centro: x = 300 + 50 = 350, y = 400 + 25 = 425
            args, kwargs = mock_click.call_args
            self.assertEqual(args[0], 350)
            self.assertEqual(args[1], 425)
            self.assertIn("Enviar", kwargs.get("label", ""))


if __name__ == "__main__":
    unittest.main()
