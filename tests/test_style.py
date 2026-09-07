"""Testes para o módulo de estilos Glassmorphism e tema da UI."""

import unittest
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

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


class AccentVariableTest(unittest.TestCase):
    """A cor de acento tem de vir da variável, não de literal espalhado.

    Com o azul repetido em ~20 regras, quem quisesse reter a interface precisava
    reescrevê-las uma a uma — e o recurso de temas do usuário não servia para
    nada na prática.
    """

    ACCENT = "#15a6f0"

    def test_accent_literal_only_in_definition(self):
        """Fora das duas declarações, o hex não pode aparecer em nenhuma regra."""
        for lineno, line in enumerate(GLASS_CSS.splitlines(), start=1):
            if self.ACCENT in line:
                self.assertTrue(
                    line.strip().startswith("@define-color"),
                    f"linha {lineno} fixa o acento em vez de usar @accent_color: {line.strip()}",
                )

    def test_accent_is_referenced_by_variable(self):
        self.assertGreater(GLASS_CSS.count("@accent_color"), 5)
        # O mesmo azul com transparência também precisa seguir a variável.
        self.assertIn("alpha(@accent_color,", GLASS_CSS)

    def test_user_css_can_retint_the_accent(self):
        """Redefinir `@accent_color` numa folha posterior repinta quem usa a variável."""
        display = Gdk.Display.get_default()
        if display is None:  # pragma: no cover - sem servidor gráfico
            self.skipTest("sem display")

        base = Gtk.CssProvider()
        base.load_from_string("@define-color accent_color #15a6f0;\n.probe-accent { color: @accent_color; }")
        override = Gtk.CssProvider()
        override.load_from_string("@define-color accent_color #ff0000;")
        priority = Gtk.STYLE_PROVIDER_PRIORITY_USER
        Gtk.StyleContext.add_provider_for_display(display, base, priority)
        Gtk.StyleContext.add_provider_for_display(display, override, priority + 1)

        try:
            win = Gtk.Window()
            lbl = Gtk.Label(label="x")
            lbl.add_css_class("probe-accent")
            win.set_child(lbl)
            win.realize()
            color = lbl.get_color()
        finally:
            Gtk.StyleContext.remove_provider_for_display(display, override)
            Gtk.StyleContext.remove_provider_for_display(display, base)

        self.assertGreater(color.red, 0.9)
        self.assertLess(color.green, 0.1)
        self.assertLess(color.blue, 0.1)


if __name__ == "__main__":
    unittest.main()
