# Decisão de design: a partir do Hyprland 0.55 o config pode ser Lua, e os dois
# formatos são EXCLUDENTES — com `hyprland.lua` presente o `hyprland.conf` não é
# lido. O pior dessa mudança é que ela falha em silêncio: o setup grava o
# snippet, responde "ativo" e nada acontece no próximo login.
#
# Estes testes fixam o comportamento nos dois mundos sem precisar de compositor:
# o diretório de config é um tmp_path e nenhum subprocesso real é disparado.

"""Config Lua do Hyprland (0.55+): detecção de sabor, snippet, require e decor."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zorin_copilot.core.desktop import autostart as auto  # noqa: E402
from zorin_copilot.core.desktop import env as env_mod  # noqa: E402
from zorin_copilot.core.desktop import shortcuts as sc  # noqa: E402

SNIPPET = "zorin-copilot.lua"


class _ConfigHomeTestCase(unittest.TestCase):
    """Isola XDG_CONFIG_HOME num diretório temporário."""

    def setUp(self):
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

    def make_hypr_dir(self, *files: str) -> Path:
        hypr = self.config_home / "hypr"
        hypr.mkdir(parents=True, exist_ok=True)
        for name in files:
            (hypr / name).write_text('hl.monitor({ output = "" })\n', encoding="utf-8")
        return hypr

    def lua_env(self, path: Path | None = None) -> env_mod.Environment:
        return env_mod.Environment(
            desktop="hyprland",
            session_type="wayland",
            hyprland=env_mod.HyprlandConfig(
                flavor="lua", version="0.55.3", path=path
            ),
        )


class TestVersionParsing(unittest.TestCase):
    """`hyprctl version` imprime a versão em mais de um formato; aceitar todos."""

    def test_formato_hyprland(self):
        text = "Hyprland 0.55.3 built from branch main at commit abc123"
        self.assertEqual(env_mod.parse_hyprland_version(text), "0.55.3")

    def test_formato_tag(self):
        text = "Hyprland 0.55.3 built...\nTag: v0.55.3\ndirty: false\n"
        self.assertEqual(env_mod.parse_hyprland_version(text), "0.55.3")

    def test_versao_antiga(self):
        self.assertEqual(env_mod.parse_hyprland_version("Hyprland 0.48.1 built"), "0.48.1")

    def test_saida_vazia(self):
        self.assertEqual(env_mod.parse_hyprland_version(""), "")

    def test_saida_sem_versao(self):
        self.assertEqual(env_mod.parse_hyprland_version("command not found"), "")


class TestConfigFlavorDetection(_ConfigHomeTestCase):
    def test_lua_vence_quando_ambos_existem(self):
        """É a regra do compositor: com `.lua`, o `.conf` é ignorado."""
        self.make_hypr_dir("hyprland.lua", "hyprland.conf")
        cfg = env_mod.detect_hyprland_config(self.config_home / "hypr")
        self.assertEqual(cfg.flavor, "lua")
        self.assertTrue(cfg.is_lua)
        self.assertEqual(cfg.path.name, "hyprland.lua")

    def test_conf_quando_nao_ha_lua(self):
        self.make_hypr_dir("hyprland.conf")
        cfg = env_mod.detect_hyprland_config(self.config_home / "hypr")
        self.assertEqual(cfg.flavor, "hyprlang")
        self.assertFalse(cfg.is_lua)

    def test_sem_nada_em_055_vai_de_lua(self):
        """Num perfil novo o Hyprland gera `hyprland.lua` — é o alvo certo."""
        self.make_hypr_dir()
        cfg = env_mod.detect_hyprland_config(self.config_home / "hypr", version="0.55.3")
        self.assertEqual(cfg.flavor, "lua")

    def test_sem_nada_antes_do_lua_vai_de_hyprlang(self):
        self.make_hypr_dir()
        cfg = env_mod.detect_hyprland_config(self.config_home / "hypr", version="0.48.1")
        self.assertEqual(cfg.flavor, "hyprlang")

    def test_suporta_lua_pela_versao(self):
        self.assertTrue(env_mod.HyprlandConfig(version="0.55.0").supports_lua)
        self.assertFalse(env_mod.HyprlandConfig(version="0.54.2").supports_lua)
        self.assertFalse(env_mod.HyprlandConfig(version="").supports_lua)

    def test_diretorio_respeita_xdg_config_home(self):
        self.assertEqual(env_mod.hypr_config_dir(), self.config_home / "hypr")


class TestBackendSelection(_ConfigHomeTestCase):
    def test_lua_e_escolhido_quando_o_sabor_e_lua(self):
        env = self.lua_env(self.make_hypr_dir("hyprland.lua") / "hyprland.lua")
        self.assertEqual(sc.select_backend(env).name, "hyprland-lua")
        self.assertEqual(sc.select_compositor_backend(env).name, "hyprland-lua")

    def test_hyprlang_e_escolhido_quando_nao_ha_lua(self):
        env = env_mod.Environment(desktop="hyprland", session_type="wayland")
        self.assertEqual(sc.select_backend(env).name, "hyprland")

    def test_backend_hyprlang_recusa_sabor_lua(self):
        """Escrever `.conf` num mundo Lua é gravar num arquivo morto."""
        env = self.lua_env(self.make_hypr_dir("hyprland.lua") / "hyprland.lua")
        self.assertFalse(sc.HyprlandShortcutBackend(env).is_supported())
        self.assertTrue(sc.HyprlandLuaBackend(env).is_supported())


class TestAcceleratorConversion(unittest.TestCase):
    def test_bind_lua(self):
        self.assertEqual(sc.to_hyprland_lua_binding("<Super>c"), "SUPER+C")
        self.assertEqual(sc.to_hyprland_lua_binding("<Super><Shift>s"), "SUPER+SHIFT+S")

    def test_tecla_nomeada_nao_e_maiusculizada(self):
        self.assertEqual(sc.to_hyprland_lua_binding("<Super>left"), "SUPER+left")

    def test_acelerador_vazio(self):
        self.assertEqual(sc.to_hyprland_lua_binding(""), "")


class TestLuaSnippet(_ConfigHomeTestCase):
    def setUp(self):
        super().setUp()
        self.hypr = self.make_hypr_dir("hyprland.lua")
        self.env = self.lua_env(self.hypr / "hyprland.lua")

    def test_bind_vira_hl_bind(self):
        backend = sc.HyprlandLuaBackend(self.env)
        self.assertTrue(backend.register("hud", "<Super>c", "zorin-copilot --toggle").ok)
        snippet = (self.hypr / SNIPPET).read_text()
        self.assertIn('hl.bind("SUPER+C", hl.dsp.exec_cmd("zorin-copilot --toggle"))', snippet)

    def test_marcadores_usam_comentario_lua(self):
        """`#` no começo da linha é erro de sintaxe em Lua, não comentário."""
        sc.HyprlandLuaBackend(self.env).register("hud", "<Super>c", "zorin-copilot --toggle")
        snippet = (self.hypr / SNIPPET).read_text()
        self.assertIn("-- >>> zorin-copilot:hud", snippet)
        self.assertIn("-- <<< zorin-copilot:hud", snippet)
        self.assertNotIn("# >>>", snippet)

    def test_require_entra_no_hyprland_lua(self):
        sc.HyprlandLuaBackend(self.env).register("hud", "<Super>c", "zorin-copilot --toggle")
        main = (self.hypr / "hyprland.lua").read_text()
        self.assertIn('require("zorin-copilot")', main)
        # O `.conf` não deve ser criado nem tocado: ele não é lido.
        self.assertFalse((self.hypr / "hyprland.conf").exists())

    def test_require_nao_duplica(self):
        backend = sc.HyprlandLuaBackend(self.env)
        backend.register("hud", "<Super>c", "zorin-copilot --toggle")
        backend.register("crop", "<Super><Shift>s", "zorin-copilot --crop")
        main = (self.hypr / "hyprland.lua").read_text()
        self.assertEqual(main.count('require("zorin-copilot")'), 1)

    def test_registro_repetido_nao_duplica_bloco(self):
        backend = sc.HyprlandLuaBackend(self.env)
        backend.register("hud", "<Super>c", "zorin-copilot --toggle")
        backend.register("hud", "<Super><Shift>s", "zorin-copilot --toggle")
        snippet = (self.hypr / SNIPPET).read_text()
        self.assertEqual(snippet.count("zorin-copilot:hud"), 2)  # abre e fecha

    def test_preserva_conteudo_do_usuario(self):
        sc.HyprlandLuaBackend(self.env).register("hud", "<Super>c", "zorin-copilot --toggle")
        main = (self.hypr / "hyprland.lua").read_text()
        self.assertIn('hl.monitor({ output = "" })', main)

    def test_unregister_remove_apenas_o_slot(self):
        backend = sc.HyprlandLuaBackend(self.env)
        backend.register("hud", "<Super>c", "zorin-copilot --toggle")
        backend.register("crop", "<Super><Shift>s", "zorin-copilot --crop")
        backend.unregister("hud")
        self.assertFalse(backend.is_registered("hud"))
        self.assertTrue(backend.is_registered("crop"))

    def test_comando_com_aspas_e_escapado(self):
        backend = sc.HyprlandLuaBackend(self.env)
        backend.register("hud", "<Super>c", 'zorin-copilot --say "oi"')
        snippet = (self.hypr / SNIPPET).read_text()
        self.assertIn('hl.dsp.exec_cmd("zorin-copilot --say \\"oi\\"")', snippet)


class TestLuaAutostart(_ConfigHomeTestCase):
    def setUp(self):
        super().setUp()
        self.hypr = self.make_hypr_dir("hyprland.lua")
        self.env = self.lua_env(self.hypr / "hyprland.lua")

    def test_autostart_emite_hl_exec_cmd(self):
        ok, msg = auto.enable("zorin-copilot", self.env)
        self.assertTrue(ok)
        snippet = (self.hypr / SNIPPET).read_text()
        self.assertIn('hl.exec_cmd("zorin-copilot --background")', snippet)

    def test_autostart_garante_require(self):
        auto.enable("zorin-copilot", self.env)
        main = (self.hypr / "hyprland.lua").read_text()
        self.assertIn('require("zorin-copilot")', main)

    def test_atalho_e_autostart_convivem_no_mesmo_snippet(self):
        sc.HyprlandLuaBackend(self.env).register("hud", "<Super>c", "zorin-copilot --toggle")
        auto.enable("zorin-copilot", self.env)
        snippet = (self.hypr / SNIPPET).read_text()
        self.assertIn("hl.bind(", snippet)
        self.assertIn("hl.exec_cmd(", snippet)
        main = (self.hypr / "hyprland.lua").read_text()
        self.assertEqual(main.count('require("zorin-copilot")'), 1)

    def test_disable_limpa_o_bloco(self):
        auto.enable("zorin-copilot", self.env)
        ok, _ = auto.disable(self.env)
        self.assertTrue(ok)
        snippet = (self.hypr / SNIPPET).read_text()
        self.assertNotIn("zorin-copilot:autostart", snippet)


class TestLuaDecor(_ConfigHomeTestCase):
    def setUp(self):
        super().setUp()
        self.hypr = self.make_hypr_dir("hyprland.lua")
        self.env = self.lua_env(self.hypr / "hyprland.lua")

    def test_decor_usa_window_rule_e_layer_rule(self):
        ok, msg = sc.ensure_decor_rules(self.env)
        self.assertTrue(ok)
        snippet = (self.hypr / SNIPPET).read_text()
        self.assertIn("hl.window_rule(", snippet)
        self.assertIn("hl.layer_rule(", snippet)
        self.assertIn("^io.github.bruno.ZorinCopilot$", snippet)
        # Blur pertence à layer da pílula; na janela fica o arredondamento.
        self.assertIn("blur = true", snippet)
        self.assertIn("rounding = 10", snippet)

    def test_decor_idempotente(self):
        sc.ensure_decor_rules(self.env)
        sc.ensure_decor_rules(self.env)
        snippet = (self.hypr / SNIPPET).read_text()
        self.assertEqual(snippet.count("zorin-copilot:decor"), 2)

    def test_decor_nao_aplica_hyprctl_em_config_lua(self):
        """`hyprctl keyword` fala hyprlang; com Lua a regra entra no reload."""
        env = env_mod.Environment(
            desktop="hyprland",
            session_type="wayland",
            binaries=frozenset({"hyprctl"}),
            hyprland=env_mod.HyprlandConfig(flavor="lua", version="0.55.3"),
        )
        with patch.object(sc.subprocess, "run") as mock_run:
            sc.ensure_decor_rules(env)
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
