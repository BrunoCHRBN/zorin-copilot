# Decisão de design: o Gio não é a única fonte de verdade sobre "o que está
# instalado". Estes testes cobrem o fallback pelo $PATH e o predicado de
# terminal — os dois caminhos que fazem `launch_app` funcionar num Hyprland
# minimalista, onde kitty/foot/wezterm não têm .desktop indexado.

"""Testes do fallback de descoberta de apps pelo $PATH."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.apps import (  # noqa: E402
    NEVER_LAUNCH_BINS,
    TERMINAL_BINS,
    AppManager,
    is_terminal_request,
)


class FindBinaryTest(unittest.TestCase):
    """`find_binary` resolve no $PATH — e recusa tudo que não é nome simples."""

    def test_resolve_binario_presente(self):
        with mock.patch("shutil.which", return_value="/usr/bin/kitty"):
            self.assertEqual(AppManager.find_binary("kitty"), "/usr/bin/kitty")

    def test_devolve_vazio_quando_nao_existe(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(AppManager.find_binary("naoexiste123"), "")

    def test_recusa_caminho_absoluto(self):
        # Nunca executamos um caminho que veio do modelo.
        self.assertEqual(AppManager.find_binary("/usr/bin/kitty"), "")

    def test_recusa_nome_com_espaco(self):
        self.assertEqual(AppManager.find_binary("kitty --single-instance"), "")

    def test_recusa_flag(self):
        self.assertEqual(AppManager.find_binary("--version"), "")

    def test_recusa_vazio(self):
        self.assertEqual(AppManager.find_binary(""), "")
        self.assertEqual(AppManager.find_binary("   "), "")

    def test_excecao_do_which_nao_propaganda(self):
        with mock.patch("shutil.which", side_effect=OSError("PATH corrompido")):
            self.assertEqual(AppManager.find_binary("kitty"), "")


class NuncaLancarTest(unittest.TestCase):
    """O fallback do $PATH não vale para binário de sistema.

    O Gio só indexa .desktop, o que bloqueava "abrir o shutdown" por acidente.
    Com o $PATH aberto, esse acidente passa a ser possível — e um comando
    desses disparado por voz não tem volta.
    """

    def test_recusa_binarios_de_sistema(self):
        for name in ("shutdown", "reboot", "rm", "dd", "sudo", "killall", "pacman"):
            with self.subTest(name=name):
                # Mesmo existindo de verdade no $PATH, não resolve.
                with mock.patch("shutil.which", return_value=f"/usr/bin/{name}"):
                    self.assertEqual(AppManager.find_binary(name), "")

    def test_denylist_cobre_os_classicos(self):
        for name in ("shutdown", "reboot", "rm", "dd", "mkfs", "sudo", "kill"):
            with self.subTest(name=name):
                self.assertIn(name, NEVER_LAUNCH_BINS)

    def test_terminais_nao_estao_na_denylist(self):
        self.assertFalse(NEVER_LAUNCH_BINS & TERMINAL_BINS)


class IsExecutableTest(unittest.TestCase):
    def test_aceita_arquivo_executavel(self):
        with mock.patch("os.path.isfile", return_value=True), mock.patch("os.access", return_value=True):
            self.assertTrue(AppManager.is_executable("/usr/bin/kitty"))

    def test_recusa_inexistente(self):
        with mock.patch("os.path.isfile", return_value=False):
            self.assertFalse(AppManager.is_executable("/usr/bin/kitty"))

    def test_recusa_vazio(self):
        self.assertFalse(AppManager.is_executable(""))


class IsTerminalRequestTest(unittest.TestCase):
    def test_aceita_terminais_conhecidos(self):
        for name in ("kitty", "alacritty", "foot", "wezterm", "konsole", "xterm"):
            with self.subTest(name=name):
                self.assertTrue(is_terminal_request(name))

    def test_aceita_o_termo_generico(self):
        # "terminal" é a *chave* em COMMON_ALIASES, não um valor — precisa
        # entrar no fallback também.
        self.assertTrue(is_terminal_request("terminal"))

    def test_case_insensitive(self):
        self.assertTrue(is_terminal_request("KITTY"))
        self.assertTrue(is_terminal_request("  Konsole  "))

    def test_recusa_o_que_nao_e_terminal(self):
        for name in ("kodfdy", "spotify", "firefox", ""):
            with self.subTest(name=name):
                self.assertFalse(is_terminal_request(name))

    def test_conjunto_deriva_dos_aliases(self):
        # Se alguém adicionar um terminal em COMMON_ALIASES, ele entra aqui.
        self.assertIn("kitty", TERMINAL_BINS)


class FindTerminalInPathTest(unittest.TestCase):
    def test_devolve_primeiro_terminal_disponivel(self):
        disponiveis = {"kitty", "foot"}

        def fake_which(name, *a, **k):
            return f"/usr/bin/{name}" if name in disponiveis else None

        with mock.patch("shutil.which", side_effect=fake_which):
            self.assertEqual(AppManager.find_terminal_in_path(), "/usr/bin/kitty")

    def test_pula_terminal_ausente(self):
        def fake_which(name, *a, **k):
            return "/usr/bin/foot" if name == "foot" else None

        with mock.patch("shutil.which", side_effect=fake_which):
            self.assertEqual(AppManager.find_terminal_in_path(), "/usr/bin/foot")

    def test_devolve_vazio_sem_terminal_nenhum(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(AppManager.find_terminal_in_path(), "")


if __name__ == "__main__":
    unittest.main()
