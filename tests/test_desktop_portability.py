# Testes da camada de portabilidade. Nenhum deles depende de servidor gráfico:
# a detecção de ambiente recebe `environ` por injeção e os backends de arquivo
# trabalham em tmp_path. É justamente o que permite rodar a suíte em CI Arch.

"""Testes de detecção de ambiente e roteamento de backends."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zorin_copilot.core import apps  # noqa: E402
from zorin_copilot.core import fence  # noqa: E402
from zorin_copilot.core.desktop import env as env_mod  # noqa: E402
from zorin_copilot.core.desktop import menu as menu_mod  # noqa: E402
from zorin_copilot.core.desktop import shortcuts as sc  # noqa: E402
from zorin_copilot.core.desktop import screenshot as shot  # noqa: E402
from zorin_copilot.core.desktop import autostart as auto  # noqa: E402
from zorin_copilot.core.desktop import tray as tray_mod  # noqa: E402
from zorin_copilot.ui import gi_versions  # noqa: E402
from zorin_copilot.ui import layer_shell  # noqa: E402


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
        self.assertIn(backend.name, ("gnome-media-keys", "global-shortcuts-portal", "none"))

    def test_ambiente_desconhecido_usa_backend_nulo(self):
        env = env_mod.detect_environment({"XDG_CURRENT_DESKTOP": "QuitrioWM"}, probe=False)
        backend = sc.select_backend(env)
        self.assertIn(backend.name, ("global-shortcuts-portal", "none"))


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


class TestDecorRules(unittest.TestCase):
    """Regras de decoração (blur/rounding) no snippet do Hyprland: idempotentes e seguras."""

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

    @staticmethod
    def _hypr_env(with_hyprctl=False):
        bins = frozenset({"hyprctl"}) if with_hyprctl else frozenset()
        return env_mod.Environment(desktop="hyprland", session_type="wayland", binaries=bins)

    def test_escreve_bloco_e_inclui_no_config(self):
        ok, _ = sc.ensure_decor_rules(self._hypr_env())
        self.assertTrue(ok)
        snippet = (self.config_home / "hypr" / "zorin-copilot.conf").read_text()
        self.assertIn("zorin-copilot:decor", snippet)  # marcadores
        self.assertIn("windowrule = blur,class:io.github.bruno.ZorinCopilot", snippet)
        self.assertIn("layerrule = blur,zorin-copilot-pill", snippet)
        main = (self.config_home / "hypr" / "hyprland.conf").read_text()
        self.assertIn("zorin-copilot.conf", main)  # garantido o source

    def test_idempotente_nao_duplica_bloco(self):
        sc.ensure_decor_rules(self._hypr_env())
        sc.ensure_decor_rules(self._hypr_env())
        snippet = (self.config_home / "hypr" / "zorin-copilot.conf").read_text()
        # 2 = abre e fecha do único bloco decor
        self.assertEqual(snippet.count("zorin-copilot:decor"), 2)

    def test_noop_fora_do_hyprland(self):
        env = env_mod.Environment(desktop="gnome", session_type="wayland", binaries=frozenset())
        ok, msg = sc.ensure_decor_rules(env)
        self.assertFalse(ok)
        self.assertFalse((self.config_home / "hypr" / "zorin-copilot.conf").exists())

    def test_aplica_ao_vivo_via_hyprctl(self):
        env = self._hypr_env(with_hyprctl=True)
        with patch.object(sc.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            ok, _ = sc.ensure_decor_rules(env)
        self.assertTrue(ok)
        joined = " ".join(" ".join(c.args[0]) for c in mock_run.call_args_list)
        self.assertIn("hyprctl keyword windowrule blur,class:io.github.bruno.ZorinCopilot", joined)
        self.assertIn("hyprctl keyword layerrule blur,zorin-copilot-pill", joined)


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

    def test_autostart_garante_source_no_hyprland_conf(self):
        """Regressão: o snippet sozinho é ignorado pelo Hyprland.

        `setup --autostart` gravava o arquivo e respondia "ativo", mas o
        `hyprland.conf` nunca ganhava o `source =` — o app não subia no login
        e nada na saída indicava o problema.
        """
        hypr = self.config_home / "hypr"
        hypr.mkdir(parents=True, exist_ok=True)
        (hypr / "hyprland.conf").write_text("monitor=,preferred\n", encoding="utf-8")

        env = env_mod.Environment(desktop="hyprland", session_type="wayland")
        ok, msg = auto.enable("zorin-copilot", env)

        self.assertTrue(ok)
        main = (hypr / "hyprland.conf").read_text()
        self.assertIn("source =", main)
        self.assertIn("zorin-copilot.conf", main)
        # Aviso de "adicione manualmente" não deve aparecer quando deu certo.
        self.assertNotIn("ATENÇÃO", msg)

    def test_autostart_e_atalho_conviven_no_mesmo_snippet(self):
        """Os dois mecanismos gravam no mesmo arquivo e exigem um só `source`."""
        hypr = self.config_home / "hypr"
        hypr.mkdir(parents=True, exist_ok=True)
        (hypr / "hyprland.conf").write_text("monitor=,preferred\n", encoding="utf-8")

        env = env_mod.Environment(desktop="hyprland", session_type="wayland")
        sc.HyprlandShortcutBackend(env).register("hud", "<Super>c", "zorin-copilot --toggle")
        auto.enable("zorin-copilot", env)

        snippet = (hypr / "zorin-copilot.conf").read_text()
        self.assertIn("bind = SUPER, c, exec, zorin-copilot --toggle", snippet)
        self.assertIn("exec-once = zorin-copilot --background", snippet)

        main = (hypr / "hyprland.conf").read_text()
        self.assertEqual(main.count("source ="), 1)

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


class TestRedZones(unittest.TestCase):
    """Red zones precisam seguir o ambiente: 80px fixos sem barra é prejuízo."""

    def _env(self, desktop: str, session: str = "wayland") -> env_mod.Environment:
        return env_mod.Environment(desktop=desktop, session_type=session)

    def test_gnome_usa_medidas_do_painel(self):
        self.assertEqual(fence.default_insets(self._env("gnome")), (48, 32))

    def test_kde_so_tem_painel_embaixo(self):
        self.assertEqual(fence.default_insets(self._env("kde")), (44, 0))

    def test_wlroots_sem_barra_nao_bloqueia_nada(self):
        with patch.object(env_mod, "running_processes", return_value=frozenset({"sway", "foot"})):
            self.assertEqual(fence.default_insets(self._env("hyprland")), (0, 0))

    def test_wlroots_com_waybar_reserva_o_topo(self):
        with patch.object(env_mod, "running_processes", return_value=frozenset({"waybar"})):
            self.assertEqual(fence.default_insets(self._env("hyprland")), (0, 32))

    def test_ambiente_desconhecido_mantem_o_padrao_historico(self):
        self.assertEqual(fence.default_insets(self._env("")), (48, 32))

    def test_insets_zero_nao_geram_red_zone(self):
        monitors = [fence.MonitorInfo(index=0, name="eDP-1", model="", x=0, y=0, width=1920, height=1080, is_primary=True)]
        with patch.object(env_mod, "running_processes", return_value=frozenset()), \
             patch.object(fence.ScreenFenceManager, "_select_default_primary", lambda self: None):
            manager = fence.ScreenFenceManager(monitors=monitors)
            manager.set_insets(bottom=0, top=0)
            self.assertEqual(manager.red_zones, [])

    def test_set_insets_reconstroi_as_zonas(self):
        monitors = [fence.MonitorInfo(index=0, name="eDP-1", model="", x=0, y=0, width=1920, height=1080, is_primary=True)]
        with patch.object(fence.ScreenFenceManager, "_select_default_primary", lambda self: None):
            manager = fence.ScreenFenceManager(monitors=monitors)
            manager.set_insets(bottom=40, top=0)
            self.assertEqual(len(manager.red_zones), 1)
            self.assertEqual(manager.red_zones[0].height, 40)


class TestLayerShell(unittest.TestCase):
    """Onde não há gtk4-layer-shell, nada pode quebrar."""

    def test_modulo_degrada_sem_a_biblioteca(self):
        with patch.object(layer_shell, "_load", return_value=None):
            self.assertFalse(layer_shell.is_supported())
            self.assertFalse(layer_shell.anchor_corner(object(), "top-center"))
            self.assertFalse(layer_shell.reanchor(object(), "top-center"))

    def test_cantos_mapeiam_para_bordas(self):
        self.assertEqual(layer_shell.anchors_for_corner("top-center"), ("top",))
        self.assertEqual(layer_shell.anchors_for_corner("top-right"), ("top", "right"))
        self.assertEqual(layer_shell.anchors_for_corner("bottom-left"), ("bottom", "left"))
        # Canto desconhecido não derruba: cai no padrão.
        self.assertEqual(layer_shell.anchors_for_corner("meio-da-tela"), ("top",))

    def test_anchor_corner_devolve_falso_quando_init_falha(self):
        fake = MagicMock()
        fake.is_supported.return_value = True
        fake.init_for_window.side_effect = RuntimeError("compositor recusou")
        with patch.object(layer_shell, "_load", return_value=fake):
            self.assertFalse(layer_shell.anchor_corner(object(), "top-center"))


class TestDbusMenu(unittest.TestCase):
    """O menu da bandeja é D-Bus puro: dá para testar sem servidor gráfico."""

    def test_build_menu_ordena_e_numera(self):
        items = menu_mod.build_menu([
            {"label": "Abrir", "on_clicked": lambda: None},
            {"separator": True},
            {"label": "Sair", "on_clicked": lambda: None},
        ])
        self.assertEqual(len(items), 3)
        self.assertEqual(items[1].type, "separator")
        # Separadores também têm id único: 0 é reservado para a raiz.
        self.assertEqual(len({i.id for i in items}), 3)
        self.assertTrue(all(i.id != 0 for i in items))

    def test_separador_nao_recebe_rotulo(self):
        items = menu_mod.build_menu([{"separator": True}])
        self.assertEqual(items[0].label, "")
        self.assertIsNone(items[0].on_clicked)

    def test_toggle_vira_checkmark(self):
        items = menu_mod.build_menu([{"label": "Kill", "toggle": True, "on_clicked": lambda: None}])
        self.assertEqual(items[0].toggle_type, "checkmark")
        self.assertEqual(items[0].toggle_state, 1)

    def test_submenu_marca_children_display(self):
        items = menu_mod.build_menu([
            {"label": "Pai", "children": [{"label": "Filho"}]},
        ])
        self.assertEqual(items[0].children_display, "submenu")
        self.assertEqual(items[0].children[0].label, "Filho")

    def test_layout_tem_a_forma_exigida_pelo_protocolo(self):
        items = menu_mod.build_menu([{"label": "Abrir", "on_clicked": lambda: None}])
        dmenu = menu_mod.DbusMenu(items)
        root = dmenu._root_for(0)
        node = menu_mod._pack_node(root, [], -1)
        # (ia{sv}av)
        self.assertEqual(node.get_type_string(), "(ia{sv}av)")
        self.assertEqual(node.n_children(), 3)
        self.assertEqual(node.get_child_value(0).get_int32(), 0)  # raiz é 0

    def test_propriedades_incluem_tipo_e_rotulo(self):
        items = menu_mod.build_menu([{"label": "Abrir", "on_clicked": lambda: None}])
        props = menu_mod._pack_properties(items[0], []).unpack()
        self.assertEqual(props["type"], "standard")
        self.assertEqual(props["label"], "Abrir")
        self.assertTrue(props["enabled"])
        self.assertTrue(props["visible"])

    def test_find_localiza_item_por_id(self):
        clicked = []
        items = menu_mod.build_menu([{"label": "Sair", "on_clicked": lambda: clicked.append(1)}])
        dmenu = menu_mod.DbusMenu(items)
        self.assertIsNotNone(dmenu.find(1))
        dmenu._activate(1)
        self.assertEqual(clicked, [1])

    def test_activate_em_item_inexistente_e_noop(self):
        dmenu = menu_mod.DbusMenu([])
        dmenu._activate(99)  # não deve levantar

    def test_set_items_incrementa_revisao(self):
        dmenu = menu_mod.DbusMenu([])
        before = dmenu._revision
        dmenu.set_items(menu_mod.build_menu([{"label": "Novo"}]))
        self.assertEqual(dmenu._revision, before + 1)
        self.assertEqual(len(dmenu.items), 1)

    def test_get_property_devolve_variante_embrulhada(self):
        # Regressão: `a{sv}.unpack()` desembrulha o `v`, então(devolver direto
        # faria o D-Bus receber `str` onde espera `Variant`.
        from gi.repository import GLib

        dmenu = menu_mod.DbusMenu(menu_mod.build_menu([{"label": "Abrir"}]))
        captured: dict[str, object] = {}

        class Inv:
            def return_value(self, value):
                captured["value"] = value

            def return_error_literal(self, _domain, message):
                captured["error"] = message

        dmenu._handle_method(
            None, "s", "/", "i", "GetProperty",
            GLib.Variant("(is)", (1, "label")), Inv(),
        )
        self.assertNotIn("error", captured)
        self.assertEqual(captured["value"].get_type_string(), "(s)")
        self.assertEqual(captured["value"].unpack(), ("Abrir",))

    def test_get_property_desconhecida_devolve_erro(self):
        from gi.repository import GLib

        dmenu = menu_mod.DbusMenu(menu_mod.build_menu([{"label": "Abrir"}]))
        captured: dict[str, object] = {}

        class Inv:
            def return_value(self, value):
                captured["value"] = value

            def return_error_literal(self, _domain, message):
                captured["error"] = message

        dmenu._handle_method(
            None, "s", "/", "i", "GetProperty",
            GLib.Variant("(is)", (1, "nao-existe")), Inv(),
        )
        self.assertIn("error", captured)

    def test_activate_isola_erro_do_callback(self):
        def boom():
            raise RuntimeError("estourou")

        dmenu = menu_mod.DbusMenu(menu_mod.build_menu([{"label": "X", "on_clicked": boom}]))
        with self.assertLogs(menu_mod.__name__, level="ERROR"):
            dmenu._activate(1)


class TestTrayMenu(unittest.TestCase):
    def test_menu_path_e_barra_quando_nao_ha_menu(self):
        tray = tray_mod.StatusNotifierTray()
        self.assertEqual(tray.menu_path, "/")

    def test_menu_path_aponta_para_o_menu_registrado(self):
        dmenu = menu_mod.DbusMenu([])
        dmenu._registration_id = 1  # simula registro sem D-Bus
        tray = tray_mod.StatusNotifierTray(menu=dmenu)
        self.assertEqual(tray.menu_path, menu_mod.DBUSMENU_PATH)

    def test_teardown_desregistra_o_menu(self):
        dmenu = menu_mod.DbusMenu([])
        dmenu._registration_id = 1
        dmenu.unregister = MagicMock()
        tray = tray_mod.StatusNotifierTray(menu=dmenu)
        tray.teardown()
        dmenu.unregister.assert_called_once()


class TestGlobalShortcutsPortal(unittest.TestCase):
    def test_portal_entra_depois_dos_nativos_e_antes_do_nulo(self):
        env = env_mod.Environment(desktop="niri", session_type="wayland")
        names = [b.name for b in sc.iter_backends(env)]
        self.assertEqual(names[-1], "global-shortcuts-portal")

    def test_portal_sem_sessao_diz_que_nao_esta_registrado(self):
        backend = sc.GlobalShortcutsPortalBackend()
        self.assertFalse(backend.is_registered("hud"))
        self.assertFalse(backend.persists)

    def test_portal_explica_que_a_sessao_morre_com_o_processo(self):
        self.assertIn("processo", sc.GlobalShortcutsPortalBackend().hint())


class TestAppAliases(unittest.TestCase):
    def test_ambiente_reordena_sem_perder_nada(self):
        generic = apps.ordered_aliases("terminal")
        kde = apps.ordered_aliases("terminal", "kde")
        self.assertEqual(sorted(generic), sorted(kde))
        self.assertEqual(kde[0], "konsole")

    def test_hyprland_prefere_terminais_de_wlroots(self):
        order = apps.ordered_aliases("terminal", "hyprland")
        self.assertEqual(order[0], "kitty")
        self.assertIn("foot", order[:4])

    def test_gnome_prefere_nativo(self):
        self.assertEqual(apps.ordered_aliases("arquivos", "gnome")[0], "nautilus")

    def test_ambiente_desconhecido_mantem_ordem_original(self):
        self.assertEqual(apps.ordered_aliases("terminal", ""), apps.COMMON_ALIASES["terminal"])

    def test_aliases_cobrem_apps_de_arch_wlroots(self):
        for query, expected in (
            ("terminal", "kitty"),
            ("arquivos", "thunar"),
            ("calculadora", "kcalc"),
            ("video", "vlc"),
        ):
            self.assertIn(expected, apps.COMMON_ALIASES[query])


class TestToolkitVersions(unittest.TestCase):
    """A checagem de versão é em runtime — o namespace do GI é sempre 4.0."""

    def test_namespace_do_gtk_e_congelado(self):
        self.assertEqual(gi_versions._GTK_NAMESPACE, "4.0")
        self.assertEqual(gi_versions._ADW_NAMESPACE, "1")

    def test_versao_minima_e_string_comparavel_por_tupla(self):
        self.assertEqual(gi_versions.MIN_GTK, (4, 10))
        self.assertEqual(gi_versions.MIN_ADW, (1, 5))
        self.assertGreater(gi_versions.MIN_GTK, (4, 6))

    def test_toolkit_report_nao_levanta(self):
        report = gi_versions.toolkit_report()
        self.assertIn("ok", report)
        self.assertIn("Gtk", report["details"])

    def test_require_gtk4_levanta_com_mensagem_acionavel_quando_antigo(self):
        with patch.object(gi_versions, "_resolved", {"Gtk": (4, 6, 0), "Adw": (1, 1, 0)}), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop(gi_versions._FALLBACK_ENV, None)
            with self.assertRaises(gi_versions.ToolkitTooOld) as ctx:
                gi_versions.require_gtk4()
        message = str(ctx.exception)
        self.assertIn("4.10", message)
        self.assertIn("Como resolver", message)

    def test_fallback_env_var_troca_erro_por_aviso(self):
        with patch.object(gi_versions, "_resolved", {"Gtk": (4, 6, 0), "Adw": (1, 1, 0)}), \
             patch.dict(os.environ, {gi_versions._FALLBACK_ENV: "1"}):
            with self.assertLogs(gi_versions.__name__, level="WARNING"):
                gi_versions.require_gtk4()  # não levanta

    def test_versoes_suficientes_passam(self):
        with patch.object(gi_versions, "_resolved", {"Gtk": (4, 14, 5), "Adw": (1, 5, 0)}):
            gi_versions.require_gtk4()  # não levanta


if __name__ == "__main__":
    unittest.main()
