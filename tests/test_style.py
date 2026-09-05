"""Testes para o módulo de estilos Glassmorphism e tema da UI."""

import unittest
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from zorin_copilot.ui.style import GLASS_CSS, apply_glass_theme, setup_glass_window


class GlassmorphismStyleTest(unittest.TestCase):
    def test_css_parses_without_error(self):
        """Verifica se o CSS do Glassmorphism é sintaticamente válido no GTK4."""
        provider = Gtk.CssProvider()
        provider.load_from_string(GLASS_CSS)
        self.assertTrue(len(GLASS_CSS) > 500)

    def test_setup_glass_window(self):
        """Verifica se a janela recebe as classes glass-window e o esquema de cor correto."""
        win = Adw.Window()
        setup_glass_window(win)

        classes = win.get_css_classes()
        self.assertIn("glass-window", classes)
        has_scheme = ("light-glass" in classes) or ("dark-glass" in classes)
        self.assertTrue(has_scheme)

    def test_apply_glass_theme_idempotency(self):
        """Verifica que aplicar o tema múltiplas vezes não causa erros."""
        apply_glass_theme()
        apply_glass_theme()
        self.assertTrue(True)

    def test_unified_icon_colors_defined(self):
        """Garante que as regras de cores unificadas para ícones estão no CSS."""
        self.assertIn("#3a4759", GLASS_CSS)
        self.assertIn("#e4ecf5", GLASS_CSS)
        self.assertIn("window.light-glass image", GLASS_CSS)
        self.assertIn("window.dark-glass image", GLASS_CSS)

    def test_accent_shielding_defined(self):
        """Garante que as cores de acento do Zorin são declaradas para blindar contra temas externos."""
        self.assertIn("@define-color accent_color #15a6f0;", GLASS_CSS)
        self.assertIn("@define-color accent_bg_color #15a6f0;", GLASS_CSS)


if __name__ == "__main__":
    unittest.main()
