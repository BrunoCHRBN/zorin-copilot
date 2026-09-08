"""Testes do driver de entrada virtual, com foco no modo sem backend.

O contrato que estes testes protegem: **sem backend, a operação falha**.
O resultado de cada método volta direto para o modelo (``live.py`` monta
``{"success": ok, "message": msg}``), então um falso positivo faz o agente
continuar o plano acreditando que clicou ou digitou — o modo mais silencioso
e mais caro de falhar.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.fence import ScreenFenceManager  # noqa: E402
from zorin_copilot.shell.input_driver import (  # noqa: E402
    SIMULATION_ENV_VAR,
    VirtualInputDriver,
)


def _sem_backend(**kw):
    """Driver sem ydotool no PATH (o cenário real de uma instalação nova)."""
    with mock.patch("shutil.which", return_value=None):
        return VirtualInputDriver(**kw)


def _com_backend(**kw):
    """Driver com ydotool presente, mas sem executar subprocesso de verdade."""
    with mock.patch("shutil.which", return_value="/usr/bin/ydotool"):
        return VirtualInputDriver(**kw)


class SemBackendFalhaTest(unittest.TestCase):
    """Nada de sucesso falso quando não há quem emita o input."""

    def setUp(self):
        self.driver = _sem_backend()

    def _centro(self):
        return self.driver.fence.convert_relative_point(0.5, 0.5)

    def test_click_falha(self):
        x, y = self._centro()
        ok, msg = self.driver.click(x, y)
        self.assertFalse(ok, "clique sem backend não pode reportar sucesso")
        self.assertIn("ydotool", msg.lower())

    def test_type_text_falha(self):
        ok, msg = self.driver.type_text("olá mundo")
        self.assertFalse(ok, "digitação sem backend não pode reportar sucesso")
        self.assertIn("ydotool", msg.lower())

    def test_hotkey_falha(self):
        ok, msg = self.driver.hotkey("ctrl", "c")
        self.assertFalse(ok, "atalho sem backend não pode reportar sucesso")
        self.assertIn("ydotool", msg.lower())

    def test_mensagem_diz_como_resolver(self):
        """Falha sem saída só gera frustração; a mensagem precisa ser acionável."""
        _, msg = self.driver.type_text("x")
        self.assertIn("ydotool", msg)
        self.assertTrue(
            "pacman" in msg or "apt" in msg,
            f"mensagem deveria indicar instalação: {msg}",
        )

    def test_is_available_falso(self):
        self.assertFalse(self.driver.is_available)

    def test_backend_name_honesto(self):
        """Antes dizia 'modo simulação segura' mesmo sem simulação ligada."""
        self.assertIn("indisponível", self.driver.get_backend_name())


class UinputNaoContaComoBackendTest(unittest.TestCase):
    """Regressão: /dev/uinput gravável NÃO é backend.

    ``is_available`` costumava ser ``ydotool or uinput``, mas nenhum método
    emite por uinput direto — só via ydotool. Resultado: ``is_available``
    True, ``get_backend_name`` dizendo "direct /dev/uinput", e toda operação
    caindo no caminho que não faz nada.
    """

    def test_is_available_falso_mesmo_com_uinput_gravavel(self):
        driver = _sem_backend()
        driver._has_uinput_access = True
        self.assertFalse(
            driver.is_available,
            "uinput gravável sem ydotool não emite nada; não pode contar como disponível",
        )

    def test_click_ainda_falha_com_uinput_gravavel(self):
        driver = _sem_backend()
        driver._has_uinput_access = True
        x, y = driver.fence.convert_relative_point(0.5, 0.5)
        ok, _ = driver.click(x, y)
        self.assertFalse(ok)


class SimulacaoExplicitaTest(unittest.TestCase):
    """Simulação só existe se alguém pediu — e se marca como simulada."""

    def test_simulacao_por_parametro(self):
        driver = _sem_backend(simulation=True)
        x, y = driver.fence.convert_relative_point(0.5, 0.5)
        ok, msg = driver.click(x, y)
        self.assertTrue(ok, "simulação explícita pode reportar sucesso")
        self.assertIn("[SIMULAÇÃO]", msg)
        self.assertIn("NÃO foi executado", msg)

    def test_simulacao_por_variavel_de_ambiente(self):
        with mock.patch.dict(os.environ, {SIMULATION_ENV_VAR: "1"}):
            driver = _sem_backend()
            self.assertTrue(driver.simulation)
            ok, msg = self.driver_type_ok(driver)
            self.assertTrue(ok)
            self.assertIn("[SIMULAÇÃO]", msg)

    def driver_type_ok(self, driver):
        return driver.type_text("teste")

    def test_simulacao_nao_torna_backend_disponivel(self):
        """Simular não é executar; is_available continua False."""
        driver = _sem_backend(simulation=True)
        self.assertFalse(driver.is_available)

    def test_parametro_sobrepoe_ambiente(self):
        with mock.patch.dict(os.environ, {SIMULATION_ENV_VAR: "1"}):
            self.assertFalse(_sem_backend(simulation=False).simulation)


class SegurancaAntesDoBackendTest(unittest.TestCase):
    """Cerca e kill switch têm de barrar antes de qualquer emissão."""

    def test_kill_switch_bloqueia_mesmo_em_simulacao(self):
        driver = _sem_backend(simulation=True)
        driver.fence.trigger_emergency_stop()
        x, y = driver.fence.convert_relative_point(0.5, 0.5)
        ok, msg = driver.click(x, y)
        self.assertFalse(ok)
        self.assertIn("emergência", msg.lower())

    def test_kill_switch_bloqueia_digitacao(self):
        driver = _com_backend()
        driver.fence.trigger_emergency_stop()
        ok, msg = driver.type_text("x")
        self.assertFalse(ok)
        self.assertIn("emergência", msg.lower())

    def test_coordenada_fora_da_cerca_falha(self):
        driver = _com_backend()
        # Muito além de qualquer geometria plausível.
        ok, _ = driver.click(999999, 999999)
        self.assertFalse(ok)


class ComBackendTest(unittest.TestCase):
    """Com ydotool, o comando é emitido — e o sucesso é real."""

    def test_click_invoca_ydotool(self):
        driver = _com_backend()
        x, y = driver.fence.convert_relative_point(0.5, 0.5)
        with mock.patch("subprocess.run") as run:
            ok, _ = driver.click(x, y)
        self.assertTrue(ok)
        self.assertGreaterEqual(run.call_count, 2, "esperado mousemove + click")
        cmd = " ".join(str(a) for a in run.call_args_list[0][0][0])
        self.assertIn("mousemove", cmd)

    def test_hotkey_tecla_nao_mapeada_falha(self):
        """Backend existe, mas a tecla não tem keycode — falha de mapeamento."""
        driver = _com_backend()
        ok, msg = driver.hotkey("f13")
        self.assertFalse(ok)
        self.assertIn("mapeado", msg)
        self.assertNotIn("indisponível", msg, "não é falha de backend")

    def test_hotkey_mapeada_emite_comando(self):
        driver = _com_backend()
        with mock.patch("subprocess.run") as run:
            ok, _ = driver.hotkey("ctrl", "c")
        self.assertTrue(ok)
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
