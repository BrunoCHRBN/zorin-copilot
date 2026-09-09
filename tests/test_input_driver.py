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
from zorin_copilot.shell import input_driver as driver_module  # noqa: E402
from zorin_copilot.shell.input_driver import (  # noqa: E402
    SIMULATION_ENV_VAR,
    VirtualInputDriver,
)


def _sem_backend(**kw):
    """Driver sem backend nenhum (o cenário real de uma instalação nova).

    O patch é em `_find_binary`, não em `shutil.which`: o driver também
    procura nos diretórios padrão, então na máquina que *tem* o wtype
    instalado patchar só o `which` não produziria um driver sem backend.
    """
    with mock.patch.object(driver_module, "_find_binary", return_value=None):
        driver = VirtualInputDriver(**kw)
    # A redescoberta tardia existe para o caso "instalei o wtype depois" —
    # aqui ela atrapalharia: estes testes querem "sem backend", e nesta
    # máquina o wtype existe de verdade. Congelamos o estado pós-construção.
    driver.refresh_backends = lambda: False
    return driver


def _com_backend(**kw):
    """Driver com ydotool presente, mas sem executar subprocesso de verdade."""
    with mock.patch.object(driver_module, "_find_binary", return_value="/usr/bin/ydotool"):
        driver = VirtualInputDriver(**kw)
    driver.refresh_backends = lambda: True
    return driver


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
        # subprocesso devolvendo sucesso: o driver agora checa o returncode.
        ok_proc = mock.Mock(returncode=0, stderr="", stdout="")
        with mock.patch("subprocess.run", return_value=ok_proc) as run:
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
        ok_proc = mock.Mock(returncode=0, stderr="", stdout="")
        with mock.patch("subprocess.run", return_value=ok_proc) as run:
            ok, _ = driver.hotkey("ctrl", "c")
        self.assertTrue(ok)
        run.assert_called_once()


def _com_wtype(**kw):
    def fake_which(bin_name):
        return "/usr/bin/wtype" if bin_name == "wtype" else None
    with mock.patch("shutil.which", side_effect=fake_which):
        return VirtualInputDriver(**kw)


class WtypeBackendTest(unittest.TestCase):
    """Com wtype, digitação e atalhos usam o protocolo de teclado virtual do Wayland."""

    def test_is_available_com_wtype(self):
        driver = _com_wtype()
        self.assertTrue(driver.is_available)
        self.assertIn("wtype", driver.get_backend_name())

    def test_type_text_invoca_wtype(self):
        driver = _com_wtype()
        ok_proc = mock.Mock(returncode=0, stderr="", stdout="")
        with mock.patch("subprocess.run", return_value=ok_proc) as run:
            ok, msg = driver.type_text("ls -la", press_enter=True)
        self.assertTrue(ok)
        self.assertIn("wtype", msg)
        self.assertEqual(run.call_count, 2)
        cmd_type = run.call_args_list[0][0][0]
        cmd_enter = run.call_args_list[1][0][0]
        self.assertEqual(cmd_type, ["/usr/bin/wtype", "--", "ls -la"])
        self.assertEqual(cmd_enter, ["/usr/bin/wtype", "-k", "Return"])

    def test_hotkey_invoca_wtype(self):
        driver = _com_wtype()
        ok_proc = mock.Mock(returncode=0, stderr="", stdout="")
        with mock.patch("subprocess.run", return_value=ok_proc) as run:
            ok, msg = driver.hotkey("ctrl", "shift", "v")
        self.assertTrue(ok)
        self.assertIn("wtype", msg)
        cmd = run.call_args[0][0]
        self.assertIn("-M", cmd)
        self.assertIn("ctrl", cmd)
        self.assertIn("shift", cmd)
        self.assertIn("v", cmd)


class ErrosDoBackendPropagamTest(unittest.TestCase):
    """Quando o backend EXISTE mas FALHA (sai com erro), o driver tem de contar
    a verdade. A falha precisa chegar ao modelo, senão ele age sobre uma
    premissa falsa — exatamente o cenário que o usuário viveu com wtype.

    Até o commit anterior esses métodos ignoravam o returncode: chamavam o
    subprocesso com check=False e devolviam sucesso mesmo se wtype/ydotool
    saíssem com erro. Os testes abaixo travam o contrato novo.
    """

    def _proc(self, *, returncode=0, stderr="", stdout=""):
        m = mock.Mock()
        m.returncode = returncode
        m.stderr = stderr
        m.stdout = stdout
        return m

    def test_type_text_wtype_falha_propaga_erro(self):
        driver = _com_wtype()
        with mock.patch("subprocess.run", return_value=self._proc(returncode=1, stderr="Wayland connection failed")):
            ok, msg = driver.type_text("sudo pacman -S spotify")
        self.assertFalse(ok, "wtype saiu com erro e o app fingiu sucesso — regressão grave")
        self.assertIn("wtype", msg.lower())
        self.assertIn("Wayland connection failed", msg)

    def test_type_text_wtype_sucesso_simplifica(self):
        driver = _com_wtype()
        with mock.patch("subprocess.run", return_value=self._proc(returncode=0)):
            ok, msg = driver.type_text("ls")
        self.assertTrue(ok)
        # "ls" tem 2 caracteres.
        self.assertIn("2 caracteres", msg)

    def test_type_text_ydotool_falha_propaga_erro(self):
        driver = _com_backend()
        with mock.patch("subprocess.run", return_value=self._proc(returncode=2, stderr="ydotoold not running")):
            ok, msg = driver.type_text("ls")
        self.assertFalse(ok)
        self.assertIn("ydotool", msg.lower())
        self.assertIn("ydotoold not running", msg)

    def test_hotkey_wtype_falha_propaga_erro(self):
        driver = _com_wtype()
        with mock.patch("subprocess.run", return_value=self._proc(returncode=1, stderr="permission denied")):
            ok, msg = driver.hotkey("ctrl", "c")
        self.assertFalse(ok)
        self.assertIn("permission denied", msg)

    def test_click_ydotool_mousemove_falha_propaga(self):
        driver = _com_backend()
        with mock.patch("subprocess.run", return_value=self._proc(returncode=1, stderr="no seat")):
            x, y = driver.fence.convert_relative_point(0.5, 0.5)
            ok, msg = driver.click(x, y)
        self.assertFalse(ok)
        self.assertIn("no seat", msg)

    def test_type_text_com_enter_quando_enter_falha(self):
        """A primeira chamada pode dar certo (digitar o texto), mas a segunda
        (Enter) falhar. Tem que reportar a falha, não o sucesso parcial."""
        driver = _com_wtype()
        # Primeira chamada OK, segunda (Enter) falha.
        side_effects = [self._proc(returncode=0), self._proc(returncode=1, stderr="kicked")]
        with mock.patch("subprocess.run", side_effect=side_effects):
            ok, msg = driver.type_text("ls", press_enter=True)
        self.assertFalse(ok, "Enter falhou: a operação como um todo tem que falhar")
        self.assertIn("Enter", msg)
        self.assertIn("kicked", msg)


class RedescobertaDeBackendTest(unittest.TestCase):
    """O backend pode aparecer *depois* de o driver nascer.

    Caso real: o usuário só descobre que precisa do wtype quando vê o erro de
    digitação, instala na hora e não reinicia o copilot. Preso ao probe do
    `__init__`, o driver continuaria jurando que "nenhum backend existe" para
    sempre — e o usuário Não consegue distinguir isso de "instalei errado".
    """

    def _driver_sem_nada(self):
        d = VirtualInputDriver.__new__(VirtualInputDriver)
        d.fence = ScreenFenceManager()
        d.wtype_bin = None
        d.ydotool_bin = None
        d.simulation = False
        return d

    def test_refresh_encontra_binario_novo(self):
        d = self._driver_sem_nada()
        self.assertFalse(d.is_available)

        with mock.patch.object(driver_module, "_find_binary", return_value="/usr/bin/wtype"):
            self.assertTrue(d.refresh_backends())

        self.assertEqual(d.wtype_bin, "/usr/bin/wtype")
        self.assertEqual(d.get_backend_name(), "wtype (Wayland virtual keyboard)")

    def test_type_text_reprocura_antes_de_desistir(self):
        d = self._driver_sem_nada()
        ok_proc = mock.Mock(returncode=0, stderr="", stdout="")

        with mock.patch.object(driver_module, "_find_binary", return_value="/usr/bin/wtype"), \
             mock.patch("subprocess.run", return_value=ok_proc):
            ok, msg = d.type_text("ls -la")

        self.assertTrue(ok, "backend instalado depois tem que ser aproveitado")
        self.assertIn("6 caracteres", msg)  # "ls -la"

    def test_nao_reprocura_quando_backend_ja_existe(self):
        # Já tem backend: não custa um `which` a cada tecla digitada.
        d = self._driver_sem_nada()
        d.wtype_bin = "/usr/bin/wtype"
        with mock.patch.object(driver_module, "_find_binary", side_effect=AssertionError("não deveria reprocurar")):
            d._ensure_backend()

    def test_override_por_env_var_ganha_do_path(self):
        with mock.patch.dict(os.environ, {"ZORIN_COPILOT_WTYPE_BIN": "/bin/sh"}):
            self.assertEqual(driver_module._find_binary("wtype"), "/bin/sh")

    def test_procura_diretorios_padrao_quando_path_restrito(self):
        # Serviço systemd com PATH=/usr/bin:/bin, binário em /usr/local/bin.
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}, clear=False), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch.object(driver_module, "_EXTRA_BIN_DIRS", ("/bin",)), \
             mock.patch("os.path.isfile", return_value=True), \
             mock.patch("os.access", return_value=True):
            self.assertEqual(driver_module._find_binary("wtype"), "/bin/wtype")


if __name__ == "__main__":
    unittest.main()

