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

    @patch("zorin_copilot.core.shortcuts.Gio.Settings")
    def test_error_handling_no_crash(self, mock_settings_cls):
        """Em caso de falha de schema ou permissão D-Bus, não deve lançar exceção não tratada."""
        mock_settings_cls.new.side_effect = Exception("Schema not found")

        self.assertFalse(ShortcutManager.is_registered())
        self.assertFalse(ShortcutManager.register("<Super>c"))
        self.assertFalse(ShortcutManager.unregister())


if __name__ == "__main__":
    unittest.main()
