# Decisão de design: testes da Fase 3 (parte B). O "spike" de backend de hotkey global
# revelou que o repositório já usa o caminho oficial Wayland no GNOME/Zorin
# (org.gnome.settings-daemon.plugins.media-keys.custom-keybinding, via ShortcutManager).
# Estes testes validam o registro de voz e o blocklist de apps (sem importar GTK pesado).

"""Testes da Fase 3 (parte B): atalho global de voz ao vivo e blocklist de apps."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.core.shortcuts import ShortcutManager  # noqa: E402
from zorin_copilot.shell.risk import BLOCKED_APPS, is_blocked_app  # noqa: E402


class ShortcutVoiceTest(unittest.TestCase):
    def test_binary_command_includes_voice_flag(self):
        cmd = ShortcutManager.get_binary_command("--voice")
        self.assertIn("--voice", cmd)

    def test_register_voice_is_graceful(self):
        # Sem GSettings real no sandbox, deve retornar bool sem levantar exceção.
        result = ShortcutManager.register_voice("<Super>v")
        self.assertIsInstance(result, bool)

    def test_get_voice_binding_returns_str(self):
        self.assertIsInstance(ShortcutManager.get_voice_binding(), str)


class BlocklistTest(unittest.TestCase):
    def setUp(self):
        self._saved = set(BLOCKED_APPS)
        BLOCKED_APPS.clear()

    def tearDown(self):
        BLOCKED_APPS.clear()
        BLOCKED_APPS.update(self._saved)

    def test_exact_match_blocks(self):
        BLOCKED_APPS.add("Banco XYZ")
        self.assertTrue(is_blocked_app("Banco XYZ"))
        self.assertTrue(is_blocked_app("banco xyz"))  # case-insensitive

    def test_unrelated_app_not_blocked(self):
        BLOCKED_APPS.add("Banco XYZ")
        self.assertFalse(is_blocked_app("Navegador"))
        self.assertFalse(is_blocked_app(None))
        self.assertFalse(is_blocked_app(""))


if __name__ == "__main__":
    unittest.main()
