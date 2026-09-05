"""Testes unitários para o ScreenFenceManager e VirtualInputDriver (Segurança Espacial)."""

import unittest

from zorin_copilot.core.fence import FenceMode, MonitorInfo, RedZone, ScreenFenceManager
from zorin_copilot.shell.input_driver import VirtualInputDriver


class ScreenFenceTest(unittest.TestCase):
    def setUp(self):
        # Cria setup com os 2 monitores reais do usuário
        self.monitors = [
            MonitorInfo(
                index=0,
                name="AOC 27\"",
                model="AOC 27G2",
                x=1920,
                y=0,
                width=1920,
                height=1080,
                is_primary=True,
            ),
            MonitorInfo(
                index=1,
                name="VIE 24\"",
                model="VIE 24",
                x=0,
                y=148,
                width=1920,
                height=1080,
                is_primary=False,
            ),
        ]
        self.fence = ScreenFenceManager(monitors=self.monitors)

    def test_default_primary_monitor_selection(self):
        """Verifica se o monitor primário (AOC 27) é o padrão da cerca."""
        active = self.fence.get_active_monitor()
        self.assertIsNotNone(active)
        self.assertEqual(active.index, 0)
        self.assertIn("AOC", active.name)
        self.assertEqual(self.fence.mode, FenceMode.PRIMARY_ONLY)

    def test_coordinate_allowed_inside_active_monitor(self):
        """Ponto central do monitor AOC (x=2500, y=500) deve ser permitido."""
        allowed, reason = self.fence.is_coordinate_allowed(2500, 500)
        self.assertTrue(allowed)
        self.assertIn("Permitido", reason)

    def test_coordinate_blocked_on_other_monitor_when_primary_only(self):
        """Ponto no monitor secundário VIE (x=500, y=500) deve ser BLOQUEADO no modo primary_only."""
        allowed, reason = self.fence.is_coordinate_allowed(500, 500)
        self.assertFalse(allowed)
        self.assertIn("fora do monitor", reason)

    def test_switch_to_secondary_monitor(self):
        """Alterna a cerca para o monitor secundário VIE e valida as novas permissões."""
        ok = self.fence.set_active_monitor("VIE")
        self.assertTrue(ok)
        self.assertEqual(self.fence.get_active_monitor().index, 1)

        # Agora ponto no VIE (x=500, y=500) é permitido
        allowed_vie, _ = self.fence.is_coordinate_allowed(500, 500)
        self.assertTrue(allowed_vie)

        # E ponto no AOC (x=2500, y=500) é bloqueado
        allowed_aoc, reason = self.fence.is_coordinate_allowed(2500, 500)
        self.assertFalse(allowed_aoc)
        self.assertIn("fora do monitor", reason)

    def test_red_zone_blocks_taskbar_clicks(self):
        """Cliques na barra de tarefas inferior do Zorin OS (últimos 48px) devem ser rejeitados."""
        # No AOC: max_y é 1080, barra fica de 1032 a 1080
        allowed, reason = self.fence.is_coordinate_allowed(2500, 1050)
        self.assertFalse(allowed)
        self.assertIn("área restrita", reason)
        self.assertIn("taskbar", reason)

    def test_all_monitors_mode(self):
        """Modo ALL_MONITORS permite cliques em ambos os monitores válidos."""
        self.fence.set_all_monitors()
        # AOC
        self.assertTrue(self.fence.is_coordinate_allowed(2500, 500)[0])
        # VIE
        self.assertTrue(self.fence.is_coordinate_allowed(500, 500)[0])
        # Ponto no infinito fora de qualquer monitor
        self.assertFalse(self.fence.is_coordinate_allowed(9999, 9999)[0])

    def test_relative_coordinate_conversion(self):
        """Converte pontos [0.5, 0.5] para o centro do monitor AOC (1920 + 960 = 2880, 540)."""
        abs_x, abs_y = self.fence.convert_relative_point(0.5, 0.5)
        self.assertEqual(abs_x, 1920 + 960)
        self.assertEqual(abs_y, 540)

    def test_kill_switch_emergency_stop(self):
        """Ativar o Kill Switch deve bloquear imediatamente qualquer clique."""
        self.fence.trigger_emergency_stop()
        self.assertTrue(self.fence.is_emergency_stopped)

        allowed, reason = self.fence.is_coordinate_allowed(2500, 500)
        self.assertFalse(allowed)
        self.assertIn("Kill Switch", reason)

        # Reset
        self.fence.reset_emergency_stop()
        self.assertFalse(self.fence.is_emergency_stopped)
        self.assertTrue(self.fence.is_coordinate_allowed(2500, 500)[0])


class VirtualInputDriverTest(unittest.TestCase):
    def setUp(self):
        monitors = [
            MonitorInfo(index=0, name="AOC 27\"", model="AOC", x=1920, y=0, width=1920, height=1080, is_primary=True),
        ]
        self.fence = ScreenFenceManager(monitors=monitors)
        self.driver = VirtualInputDriver(fence=self.fence)

    def test_driver_blocks_out_of_bounds_click(self):
        """Driver deve rejeitar clique fora da cerca sem emitir subprocess."""
        # x=500 está fora do AOC (que começa em x=1920)
        ok, msg = self.driver.click(500, 500)
        self.assertFalse(ok)
        self.assertIn("fora do monitor", msg)

    def test_driver_allows_valid_click(self):
        """Driver valida coordenada correta dentro do monitor AOC."""
        ok, msg = self.driver.click(2500, 500)
        self.assertTrue(ok)
        self.assertTrue("permitido" in msg.lower() or "executado" in msg.lower())

    def test_driver_relative_click(self):
        """Driver converte ponto relativo e clica no monitor ativo."""
        ok, msg = self.driver.click_relative(0.5, 0.5)
        self.assertTrue(ok)

    def test_driver_type_text(self):
        """Driver simula digitação de texto com sucesso."""
        ok, msg = self.driver.type_text("Zorin Copilot Test")
        self.assertTrue(ok)

    def test_driver_hotkey(self):
        """Driver envia atalhos de teclado."""
        ok, msg = self.driver.hotkey("ctrl", "c")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
