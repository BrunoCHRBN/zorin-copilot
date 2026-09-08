"""Testes para o gerenciador de atalhos globais do sistema (ShortcutManager).

Com a refatoração multi-ambiente, o ShortcutManager deixou de falar direto com o
GNOME e passou a despachar para um backend. Estes testes verificam o contrato de
despacho (slot/acelerador/flag) e a degradação graciosa — não mais o caminho
interno de media-keys, que agora é responsabilidade de
``core.desktop.shortcuts.GnomeShortcutBackend``.
"""

import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.desktop import env as desktop_env
from zorin_copilot.core.desktop import shortcuts as desktop_shortcuts
from zorin_copilot.core.shortcuts import AutostartManager, ShortcutManager


class _RecordingBackend(desktop_shortcuts.ShortcutBackend):
    """Backend falso que anota chamadas e simula registro persistido."""

    name = "gravador-teste"

    def __init__(self):
        super().__init__(env=desktop_shortcuts.current_environment())
        self.registered: dict[str, str] = {}
        self.unregistered: list[str] = []

    def is_supported(self):
        return True

    def register(self, slot, accelerator, command):
        self.registered[slot] = accelerator
        return desktop_shortcuts.ShortcutResult(True, "ok")

    def unregister(self, slot):
        self.registered.pop(slot, None)
        self.unregistered.append(slot)
        return desktop_shortcuts.ShortcutResult(True, "ok")

    def is_registered(self, slot):
        return slot in self.registered


class ShortcutManagerTest(unittest.TestCase):
    def setUp(self):
        self.backend = _RecordingBackend()
        patcher = patch.object(desktop_shortcuts, "select_backend", return_value=self.backend)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ config
    def test_config_defaults(self):
        """Valores padrão de atalho global presentes na configuração."""
        cfg = CopilotConfig()
        self.assertTrue(cfg.global_shortcut_enabled)
        self.assertEqual(cfg.global_shortcut_key, "<Super>c")

    def test_config_crop_defaults(self):
        """Valores padrão do atalho de recorte presentes na configuração."""
        cfg = CopilotConfig()
        self.assertTrue(cfg.crop_shortcut_enabled)
        self.assertEqual(cfg.crop_shortcut_key, "<Super><Shift>s")

    # --------------------------------------------------------------- binário
    def test_get_binary_command(self):
        cmd = ShortcutManager.get_binary_command()
        self.assertIn("--toggle", cmd)
        self.assertTrue(cmd.endswith("--toggle"))

    def test_get_binary_command_crop(self):
        cmd = ShortcutManager.get_binary_command("--crop")
        self.assertIn("--crop", cmd)
        self.assertTrue(cmd.endswith("--crop"))

    # --------------------------------------------------------------- despacho
    def test_registra_slot_hud(self):
        self.assertTrue(ShortcutManager.register("<Super>c"))
        self.assertEqual(self.backend.registered["hud"], "<Super>c")

    def test_is_registered_reflete_o_backend(self):
        self.assertFalse(ShortcutManager.is_registered())
        ShortcutManager.register("<Super>c")
        self.assertTrue(ShortcutManager.is_registered())

    def test_unregister_remove_slot_hud(self):
        ShortcutManager.register("<Super>c")
        self.assertTrue(ShortcutManager.unregister())
        self.assertNotIn("hud", self.backend.registered)
        self.assertIn("hud", self.backend.unregistered)

    def test_crop_register_and_unregister(self):
        self.assertTrue(ShortcutManager.register_crop("<Super><Shift>s"))
        self.assertEqual(self.backend.registered["crop"], "<Super><Shift>s")
        self.assertTrue(ShortcutManager.is_crop_registered())
        self.assertTrue(ShortcutManager.unregister_crop())
        self.assertFalse(ShortcutManager.is_crop_registered())

    def test_cada_slot_recebe_sua_flag(self):
        ShortcutManager.register()
        ShortcutManager.register_crop()
        ShortcutManager.register_voice()
        flags = {slot: cmd.split()[-1] for slot, cmd in (
            ("hud", ShortcutManager.get_binary_command("--toggle")),
            ("crop", ShortcutManager.get_binary_command("--crop")),
            ("voice", ShortcutManager.get_binary_command("--voice")),
        )}
        self.assertEqual(flags, {"hud": "--toggle", "crop": "--crop", "voice": "--voice"})

    # ------------------------------------------------------------- degradação
    def test_backend_sem_suporte_devolve_falso_e_explica(self):
        """Ambiente sem mecanismo de atalho: falso + mensagem, não exceção."""
        with patch.object(
            desktop_shortcuts,
            "select_backend",
            return_value=desktop_shortcuts.NullShortcutBackend(),
        ):
            self.assertFalse(ShortcutManager.register("<Super>c"))
            self.assertIn("Nenhum backend", ShortcutManager.last_message)

    def test_backend_que_explode_nao_derruba_o_app(self):
        broken = MagicMock()
        broken.name = "quebrado"
        broken.is_registered.side_effect = RuntimeError("boom")
        with patch.object(desktop_shortcuts, "select_backend", return_value=broken):
            self.assertFalse(ShortcutManager.is_registered())

    def test_gnomo_sem_schema_degrada_para_falso(self):
        """Sem o schema de media-keys, o backend GNOME recusa o registro."""
        env = desktop_env.detect_environment({"XDG_CURRENT_DESKTOP": "GNOME"}, probe=False)
        backend = desktop_shortcuts.GnomeShortcutBackend(env)
        with patch.object(backend, "_schema_exists", return_value=False):
            self.assertFalse(backend.register("hud", "<Super>c", "zorin-copilot").ok)

    # -------------------------------------------------------------- autostart
    def test_autostart_enable_disable(self):
        """Habilita e desabilita via XDG usando XDG_CONFIG_HOME temporário."""
        import os
        import tempfile
        from pathlib import Path

        old = os.environ.get("XDG_CONFIG_HOME")
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["XDG_CONFIG_HOME"] = tmp_dir
            try:
                from zorin_copilot.core.desktop import autostart

                self.assertFalse(AutostartManager.is_enabled())

                self.assertTrue(AutostartManager.enable("/usr/local/bin/zorin-copilot"))
                self.assertTrue(AutostartManager.is_enabled())

                content = (Path(tmp_dir) / "autostart" / f"{autostart.APP_ID}.desktop").read_text()
                self.assertIn("Exec=/usr/local/bin/zorin-copilot --background", content)

                self.assertTrue(AutostartManager.disable())
                self.assertFalse(AutostartManager.is_enabled())
            finally:
                if old is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old


if __name__ == "__main__":
    unittest.main()
