# Testes da camada de portabilidade. Nenhum deles depende de servidor gráfico:
# a detecção de ambiente recebe `environ` por injeção e os backends de arquivo
# trabalham em tmp_path. É justamente o que permite rodar a suíte em CI Arch.

"""Testes de detecção de ambiente e roteamento de backends."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zorin_copilot.core.desktop import env as env_mod  # noqa: E402
from zorin_copilot.core.desktop import shortcuts as sc  # noqa: E402
from zorin_copilot.core.desktop import screenshot as shot  # noqa: E402
from zorin_copilot.core.desktop import autostart as auto  # noqa: E402


ARCH_OS_RELEASE = """
NAME="EndeavourOS"
PRETTY_NAME="EndeavourOS"
ID=endeavouros
ID_LIKE=arch
VERSION_ID="2025.03"
"""

ZORIN_OS_RELEASE = """
NAME="Zorin OS"
PRETTY_NAME="Zorin OS 18.1"
ID=zorin
ID_LIKE="ubuntu debian"
VERSION_ID="18.1"
"""


class TestDistroDetection(unittest.TestCase):
    def test_endeavouros_is_arch_like(self):
        info = env_mod.parse_os_release(ARCH_OS_RELEASE)
        self.assertEqual(info.id, "endeavouros")
        self.assertIn("arch", info.id_like)
        self.assertTrue(info.is_arch_like)
        self.assertFalse(info.is_debian_like)
        self.assertEqual(info.package_manager, "pacman")

    def test_zorin_is_debian_like(self):
        info = env_mod.parse_os_release(ZORIN_OS_RELEASE)
        self.assertEqual(info.id, "zorin")
        self.assertTrue(info.is_debian_like)
        self.assertEqual(info.package_manager, "apt")

    def test_unknown_distro_has_no_package_manager(self):
        info = env_mod.parse_os_release("NAME=Nope\nID=nope\n")
        self.assertEqual(info.package_manager, "")


class TestDesktopDetection(unittest.TestCase):
    """O compositor precisa ser detectado mesmo quando XDG_CURRENT_DESKTOP mente."""

    def test_hyprland_via_signature(self):
        environ = {
            "HYPRLAND_INSTANCE_SIGNATURE": "abc123",
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "",
        }
        env = env_mod.detect_environment(environ, probe=False)
        self.assertEqual(env.desktop, "hyprland")
        self.assertTrue(env.is_wlroots)
        self.assertTrue(env.is_wayland)

    def test_sway_via_socket(self):
        environ = {"SWAYSOCK": "/run/user/1000/sway-ipc.sock", "XDG_SESSION_TYPE": "wayland"}
        self.assertEqual(env_mod.detect_environment(environ, probe=False).desktop, "sway")

    def test_gnome_alias_normalizado(self):
        environ = {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME", "XDG_SESSION_TYPE": "wayland"}
        env = env_mod.detect_environment(environ, probe=False)
        self.assertEqual(env.desktop, "gnome")
        self.assertTrue(env.is_gnome)
        self.assertFalse(env.is_wlroots)

    def test_kde(self):
        environ = {"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}
        self.assertTrue(env_mod.detect_environment(environ, probe=False).is_kde)

    def test_session_inferida_sem_xdg_session_type(self):
        env = env_mod.detect_environment({"WAYLAND_DISPLAY": "wayland-1"}, probe=False)
        self.assertEqual(env.session_type, "wayland")
        env = env_mod.detect_environment({"DISPLAY": ":0"}, probe=False)
        self.assertEqual(env.session_type, "x11")

    def test_describe_inclui_distro_e_ambiente(self):
        env = env_mod.Environment(
            distro=env_mod.DistroInfo(id="endeavouros", pretty_name="EndeavourOS"),
            session_type="wayland",
            desktop="hyprland",
        )
        self.assertIn("EndeavourOS", env.describe())
        self.assertIn("hyprland", env.describe())


class TestPromptEnvironment(unittest.TestCase):
    """O modelo precisa saber em que máquina está — senão sugere comandos falsos."""

    def test_descreve_endeavouros_hyprland(self):
        env = env_mod.Environment(
            distro=env_mod.DistroInfo(id="endeavouros", pretty_name="EndeavourOS"),
            session_type="wayland",
            desktop="hyprland",
        )
        frase = env_mod.describe_for_prompt(env)
        self.assertIn("EndeavourOS", frase)
        self.assertIn("Hyprland", frase)
        self.assertIn("Wayland", frase)

    def test_prompt_do_provedor_reflete_o_ambiente(self):
        from zorin_copilot.ai.providers import DEFAULT_ENV_DESCRIPTION, build_system_prompt

        env = env_mod.Environment(
            distro=env_mod.DistroInfo(id="endeavouros", pretty_name="EndeavourOS"),
            session_type="wayland",
            desktop="hyprland",
        )
        prompt = build_system_prompt(env)
        self.assertNotIn(DEFAULT_ENV_DESCRIPTION, prompt)
        self.assertIn("EndeavourOS", prompt)

    def test_prompt_no_gnome_zorin_mantem_texto_original(self):
        from zorin_copilot.ai.providers import SYSTEM_PROMPT, build_system_prompt

        env = env_mod.Environment(
            distro=env_mod.DistroInfo(id="zorin", pretty_name="Zorin OS 18 Core"),
            session_type="wayland",
            desktop="gnome",
        )
        # Ambiente diferente do literal padrão, mas o restante do prompt (tom,
        # diretrizes de ação) precisa continuar intacto.
        prompt = build_system_prompt(env)
        self.assertIn("Zorin OS 18 Core", prompt)
        self.assertIn("PERSONALIDADE & TOM DE VOZ", prompt)
        self.assertEqual(len(prompt), len(SYSTEM_PROMPT) - len("Zorin OS 18 Core (Linux / GNOME 46 no Wayland)") + len("Zorin OS 18 Core (Linux / GNOME no Wayland)"))


class TestAcceleratorConversion(unittest.TestCase):
    """Cada compositor fala um dialeto; a conversão precisa ser exata."""

    def test_hyprland(self):
        self.assertEqual(sc.to_hyprland_binding("<Super>c"), "SUPER, c")
        self.assertEqual(sc.to_hyprland_binding("<Super><Shift>s"), "SUPER SHIFT, s")

    def test_sway(self):
        self.assertEqual(sc.to_sway_binding("<Super>c"), "Mod4+c")
        self.assertEqual(sc.to_sway_binding("<Super><Shift>s"), "Mod4+Shift+s")

    def test_kde(self):
        self.assertEqual(sc.to_kde_binding("<Super>c"), "Meta+C")
        self.assertEqual(sc.to_kde_binding("<Control><Alt>t"), "Ctrl+Alt+T")

    def test_parse_modificadores_e_tecla(self):
        mods, key = sc.parse_gtk_accelerator("<Primary><Shift>n")
        self.assertEqual(key, "n")
        self.assertIn("ctrl", mods)
        self.assertIn("shift", mods)

    def test_acelerador_vazio(self):
        self.assertEqual(sc.parse_gtk_accelerator(""), ([], ""))
        self.assertEqual(sc.to_hyprland_binding(""), "")


class TestBackendSelection(unittest.TestCase):
    def test_hyprland_escolhe_backend_hyprland(self):
        env = env_mod.detect_environment(
            {"HYPRLAND_INSTANCE_SIGNATURE": "x", "XDG_SESSION_TYPE": "wayland"}, probe=False
        )
        self.assertEqual(sc.select_backend(env).name, "hyprland")

    def test_sway_escolhe_backend_sway(self):
        env = env_mod.detect_environment({"SWAYSOCK": "/tmp/s", "XDG_SESSION_TYPE": "wayland"}, probe=False)
        self.assertEqual(sc.select_backend(env).name, "sway")

    def test_gnome_sem_schema_cai_no_nulo(self):
        # Em CI não há schema do GNOME; o importante é não explodir.
        env = env_mod.detect_environment({"XDG_CURRENT_DESKTOP": "GNOME"}, probe=False)
        backend = sc.select_backend(env)
        self.assertIn(backend.name, ("gnome-media-keys", "none"))

    def test_ambiente_desconhecido_usa_backend_nulo(self):
        env = env_mod.detect_environment({"XDG_CURRENT_DESKTOP": "QuitrioWM"}, probe=False)
        backend = sc.select_backend(env)
        self.assertEqual(backend.name, "none")
        result = backend.register("hud", "<Super>c", "zorin-copilot --toggle")
        self.assertFalse(result.ok)
        self.assertIn("Nenhum backend", result.message)


class TestScreenshotBackendSelection(unittest.TestCase):
    def _env(self, environ, binaries):
        env = env_mod.detect_environment(environ, probe=False)
        return env_mod.Environment(
            distro=env.distro,
            session_type=env.session_type,
            desktop=env.desktop,
            package_manager=env.package_manager,
            binaries=frozenset(binaries),
        )

    def test_wlroots_com_grimblast_prefere_grimblast(self):
        env = self._env({"HYPRLAND_INSTANCE_SIGNATURE": "x", "XDG_SESSION_TYPE": "wayland"}, {"grimblast", "grim", "slurp"})
        self.assertEqual(shot.select_backend(env).name, "grimblast")

    def test_wlroots_sem_grimblast_usa_grim(self):
        env = self._env({"HYPRLAND_INSTANCE_SIGNATURE": "x", "XDG_SESSION_TYPE": "wayland"}, {"grim", "slurp"})
        self.assertEqual(shot.select_backend(env).name, "grim-slurp")

    def test_wlroots_sem_ferramenta_cai_no_portal(self):
        env = self._env({"HYPRLAND_INSTANCE_SIGNATURE": "x", "XDG_SESSION_TYPE": "wayland"}, set())
        self.assertEqual(shot.select_backend(env).name, "xdg-portal")

    def test_missing_dependencies_aponta_grim_e_slurp(self):
        env = self._env({"HYPRLAND_INSTANCE_SIGNATURE": "x", "XDG_SESSION_TYPE": "wayland"}, set())
        self.assertEqual(shot.missing_dependencies(env), ("grim", "slurp"))

    def test_missing_dependencies_vazio_quando_presentes(self):
        env = self._env({"HYPRLAND_INSTANCE_SIGNATURE": "x", "XDG_SESSION_TYPE": "wayland"}, {"grim", "slurp"})
        self.assertEqual(shot.missing_dependencies(env), ())

    def test_backends_de_arquivo_devolvem_erro_em_acelerador_invalido(self):
        env = self._env({"HYPRLAND_INSTANCE_SIGNATURE": "x", "XDG_SESSION_TYPE": "wayland"}, {"grim"})
        result = sc.HyprlandShortcutBackend(env).register("hud", "", "zorin-copilot")
        self.assertFalse(result.ok)


class TestHyprlandShortcutFile(unittest.TestCase):
    """O snippet precisa ser idempotente e não destruir o que o usuário escreveu."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_home = Path(self.tmp.name)
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.config_home)

        (self.config_home / "hypr").mkdir(parents=True)
        (self.config_home / "hypr" / "hyprland.conf").write_text("monitor=,preferred\n", encoding="utf-8")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old

    def _backend(self):
        env = env_mod.Environment(desktop="hyprland", session_type="wayland")
        return sc.HyprlandShortcutBackend(env)

    def test_registra_e_marca_como_registrado(self):
        backend = self._backend()
        self.assertTrue(backend.register("hud", "<Super>c", "zorin-copilot --toggle").ok)
        self.assertTrue(backend.is_registered("hud"))

        snippet = (self.config_home / "hypr" / "zorin-copilot.conf").read_text()
        self.assertIn("bind = SUPER, c, exec, zorin-copilot --toggle", snippet)

    def test_registro_repetido_nao_duplica_bloco(self):
        backend = self._backend()
        backend.register("hud", "<Super>c", "zorin-copilot --toggle")
        backend.register("hud", "<Super><Shift>s", "zorin-copilot --toggle")
        snippet = (self.config_home / "hypr" / "zorin-copilot.conf").read_text()
        self.assertEqual(snippet.count("zorin-copilot:hud"), 2)  # abre e fecha

    def test_preserva_conteudo_do_usuario_no_config_principal(self):
        self._backend().register("hud", "<Super>c", "zorin-copilot --toggle")
        main = (self.config_home / "hypr" / "hyprland.conf").read_text()
        self.assertIn("monitor=,preferred", main)
        self.assertIn("zorin-copilot.conf", main)

    def test_cria_backup_antes_de_editar(self):
        self._backend().register("hud", "<Super>c", "zorin-copilot --toggle")
        self.assertTrue((self.config_home / "hypr" / "hyprland.conf.bak-copilot").exists())

    def test_unregister_remove_apenas_o_slot(self):
        backend = self._backend()
        backend.register("hud", "<Super>c", "zorin-copilot --toggle")
        backend.register("crop", "<Super><Shift>s", "zorin-copilot --crop")
        backend.unregister("hud")
        self.assertFalse(backend.is_registered("hud"))
        self.assertTrue(backend.is_registered("crop"))


class TestAutostart(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_home = Path(self.tmp.name)
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.config_home)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old

    def test_hyprland_escreve_exec_once_alem_do_xdg(self):
        env = env_mod.Environment(desktop="hyprland", session_type="wayland")
        ok, msg = auto.enable("zorin-copilot", env)
        self.assertTrue(ok)
        self.assertIn("Compositor", msg)

        snippet = (self.config_home / "hypr" / "zorin-copilot.conf").read_text()
        self.assertIn("exec-once = zorin-copilot --background", snippet)
        self.assertTrue(auto.is_enabled(env))

    def test_sway_escreve_exec(self):
        env = env_mod.Environment(desktop="sway", session_type="wayland")
        auto.enable("zorin-copilot", env)
        snippet = (self.config_home / "sway" / "zorin-copilot.conf").read_text()
        self.assertIn("exec zorin-copilot --background", snippet)

    def test_gnome_usa_apenas_xdg(self):
        env = env_mod.Environment(desktop="gnome", session_type="wayland")
        auto.enable("zorin-copilot", env)
        self.assertTrue((self.config_home / "autostart" / f"{auto.APP_ID}.desktop").exists())
        self.assertFalse((self.config_home / "hypr" / "zorin-copilot.conf").exists())
        self.assertTrue(auto.is_enabled(env))

    def test_disable_limpa_os_dois_mecanismos(self):
        env = env_mod.Environment(desktop="hyprland", session_type="wayland")
        auto.enable("zorin-copilot", env)
        ok, _ = auto.disable(env)
        self.assertTrue(ok)
        self.assertFalse(auto.is_enabled(env))


if __name__ == "__main__":
    unittest.main()
