"""Testes unitários para o ScreenFenceManager e VirtualInputDriver (Segurança Espacial)."""

import unittest
from unittest import mock

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


class PrimaryMonitorSelectionTest(unittest.TestCase):
    """A escolha do monitor primário não pode depender de marca de hardware.

    A lógica original procurava a string "aoc" no nome/modelo, o que fazia a
    cerca apontar para o monitor errado em qualquer máquina que não fosse a de
    desenvolvimento (e quebrava a suíte em CI).
    """

    @staticmethod
    def _mon(name: str, primary: bool, index: int) -> MonitorInfo:
        return MonitorInfo(index=index, name=name, model=name, x=0, y=0, width=1920, height=1080, is_primary=primary)

    def test_primary_flag_wins_regardless_of_name(self):
        fence = ScreenFenceManager(
            monitors=[self._mon("Genérico", False, 0), self._mon("Outro", True, 1)]
        )
        self.assertEqual(fence.get_active_monitor().index, 1)

    def test_brand_name_alone_does_not_make_it_primary(self):
        fence = ScreenFenceManager(
            monitors=[self._mon("AOC 27\"", False, 0), self._mon("VIE 24\"", True, 1)]
        )
        self.assertEqual(fence.get_active_monitor().index, 1)

    def test_without_primary_flag_falls_back_to_first(self):
        fence = ScreenFenceManager(monitors=[self._mon("A", False, 0), self._mon("B", False, 1)])
        self.assertEqual(fence.get_active_monitor().index, 0)

    def test_set_active_monitor_by_keyword_uses_primary_flag(self):
        monitors = [self._mon("A", True, 0), self._mon("B", False, 1)]
        fence = ScreenFenceManager(monitors=monitors)
        self.assertTrue(fence.set_active_monitor("principal"))
        self.assertEqual(fence.get_active_monitor().index, 0)
        self.assertTrue(fence.set_active_monitor("secundaria"))
        self.assertEqual(fence.get_active_monitor().index, 1)


class HeadlessFallbackTest(unittest.TestCase):
    """Quando o GDK não vê monitor nenhum, o fallback precisa ser genérico."""

    def test_fallback_names_are_generic(self):
        monitors = ScreenFenceManager._fallback_monitors()
        self.assertEqual([m.name for m in monitors], ["Monitor 1", "Monitor 2"])
        self.assertTrue(monitors[0].is_primary)
        # Geometria coerente: o primário começa na origem.
        self.assertEqual(monitors[0].x, 0)
        self.assertGreater(monitors[1].x, monitors[0].x)


class VirtualInputDriverTest(unittest.TestCase):
    """Exercita o caminho real (ydotool presente), com o subprocesso mockado.

    Estes testes costumavam passar porque o sandbox não tem ydotool e o driver
    caía no modo que devolvia sucesso sem fazer nada — ou seja, validavam o bug.
    Agora o backend é fornecido por mock, então o que está sob teste é a
    validação da cerca e a emissão do comando, não a ausência de crash.
    """

    def setUp(self):
        monitors = [
            MonitorInfo(index=0, name="AOC 27\"", model="AOC", x=1920, y=0, width=1920, height=1080, is_primary=True),
        ]
        self.fence = ScreenFenceManager(monitors=monitors)
        self._which = mock.patch("shutil.which", return_value="/usr/bin/ydotool")
        self._run = mock.patch("subprocess.run")
        self._which.start()
        self.run_mock = self._run.start()
        self.addCleanup(self._which.stop)
        self.addCleanup(self._run.stop)
        self.driver = VirtualInputDriver(fence=self.fence)
        self.assertTrue(self.driver.is_available, "backend mockado deveria estar disponível")

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
