"""Testes para o gerenciador de atalhos globais do sistema (ShortcutManager)."""

import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.shortcuts import (
    COPILOT_BINDING_PATH,
    ShortcutManager,
)


class ShortcutManagerTest(unittest.TestCase):
    def test_config_defaults(self):
        """Verifica se os valores padrão de atalho global estão presentes na configuração."""
        cfg = CopilotConfig()
        self.assertTrue(cfg.global_shortcut_enabled)
        self.assertEqual(cfg.global_shortcut_key, "<Super>c")

    def test_get_binary_command(self):
        """Comando retornado deve conter o parâmetro --toggle."""
        cmd = ShortcutManager.get_binary_command()
        self.assertIn("--toggle", cmd)
        self.assertTrue(cmd.endswith("--toggle"))

    @patch("zorin_copilot.core.shortcuts.Gio.Settings")
    def test_is_registered_true(self, mock_settings_cls):
        mock_settings = MagicMock()
        mock_settings.get_strv.return_value = [COPILOT_BINDING_PATH, "/other/path/"]
        mock_settings_cls.new.return_value = mock_settings

        self.assertTrue(ShortcutManager.is_registered())
        mock_settings.get_strv.assert_called_with("custom-keybindings")

    @patch("zorin_copilot.core.shortcuts.Gio.Settings")
    def test_is_registered_false(self, mock_settings_cls):
        mock_settings = MagicMock()
        mock_settings.get_strv.return_value = ["/other/path/"]
        mock_settings_cls.new.return_value = mock_settings

        self.assertFalse(ShortcutManager.is_registered())

    @patch("zorin_copilot.core.shortcuts.Gio.Settings")
    def test_register_success(self, mock_settings_cls):
        mock_main_settings = MagicMock()
        mock_main_settings.get_strv.return_value = []
        mock_custom_settings = MagicMock()

        mock_settings_cls.new.return_value = mock_main_settings
        mock_settings_cls.new_with_path.return_value = mock_custom_settings

        result = ShortcutManager.register("<Super>c")
        self.assertTrue(result)

        mock_main_settings.set_strv.assert_called_once_with("custom-keybindings", [COPILOT_BINDING_PATH])
        mock_custom_settings.set_string.assert_any_call("name", "Zorin Copilot")
        mock_custom_settings.set_string.assert_any_call("binding", "<Super>c")

    @patch("zorin_copilot.core.shortcuts.Gio.Settings")
    def test_unregister_success(self, mock_settings_cls):
        mock_main_settings = MagicMock()
        mock_main_settings.get_strv.return_value = [COPILOT_BINDING_PATH]
        mock_custom_settings = MagicMock()

        mock_settings_cls.new.return_value = mock_main_settings
        mock_settings_cls.new_with_path.return_value = mock_custom_settings

        result = ShortcutManager.unregister()
        self.assertTrue(result)
        mock_main_settings.set_strv.assert_called_once_with("custom-keybindings", [])
        mock_custom_settings.set_string.assert_any_call("name", "")

    def test_config_crop_defaults(self):
        """Verifica se os valores padrão do atalho de recorte estão presentes na configuração."""
        cfg = CopilotConfig()
        self.assertTrue(cfg.crop_shortcut_enabled)
        self.assertEqual(cfg.crop_shortcut_key, "<Super><Shift>s")

    def test_get_binary_command_crop(self):
        """Comando retornado com flag --crop deve conter --crop."""
        cmd = ShortcutManager.get_binary_command("--crop")
        self.assertIn("--crop", cmd)
        self.assertTrue(cmd.endswith("--crop"))

    @patch("zorin_copilot.core.shortcuts.Gio.Settings")
    def test_crop_is_registered_true(self, mock_settings_cls):
        from zorin_copilot.core.shortcuts import CROP_BINDING_PATH
        mock_settings = MagicMock()
        mock_settings.get_strv.return_value = [CROP_BINDING_PATH]
        mock_settings_cls.new.return_value = mock_settings

        self.assertTrue(ShortcutManager.is_crop_registered())

    @patch("zorin_copilot.core.shortcuts.Gio.Settings")
    def test_crop_register_and_unregister(self, mock_settings_cls):
        from zorin_copilot.core.shortcuts import CROP_BINDING_PATH
        mock_main_settings = MagicMock()
        mock_main_settings.get_strv.return_value = []
        mock_custom_settings = MagicMock()

        mock_settings_cls.new.return_value = mock_main_settings
        mock_settings_cls.new_with_path.return_value = mock_custom_settings

        ok_reg = ShortcutManager.register_crop("<Super><Shift>s")
        self.assertTrue(ok_reg)
        mock_main_settings.set_strv.assert_called_with("custom-keybindings", [CROP_BINDING_PATH])
        mock_custom_settings.set_string.assert_any_call("name", "Zorin Copilot - Recorte Inteligente")
        mock_custom_settings.set_string.assert_any_call("binding", "<Super><Shift>s")

        mock_main_settings.get_strv.return_value = [CROP_BINDING_PATH]
        ok_unreg = ShortcutManager.unregister_crop()
        self.assertTrue(ok_unreg)
        mock_main_settings.set_strv.assert_called_with("custom-keybindings", [])


if __name__ == "__main__":
    unittest.main()
