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
        self.assertIn("@zc_gray_700", GLASS_CSS)
        self.assertIn("@zc_gray_100", GLASS_CSS)
        self.assertIn("window.light-glass image", GLASS_CSS)
        self.assertIn("window.dark-glass image", GLASS_CSS)

    def test_accent_shielding_defined(self):
        """Garante que as cores de acento são declaradas para blindar contra temas externos."""
        self.assertIn("@define-color accent_color #6b7280;", GLASS_CSS)
        self.assertIn("@define-color accent_bg_color #6b7280;", GLASS_CSS)


class AccentVariableTest(unittest.TestCase):
    """A cor de acento tem de vir da variável, não de literal espalhado.

    Com o azul repetido em ~20 regras, quem quisesse reter a interface precisava
    reescrevê-las uma a uma — e o recurso de temas do usuário não servia para
    nada na prática.
    """

    ACCENT = "#6b7280"

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


class NoLedEffectsTest(unittest.TestCase):
    """Garantias anti-LED após o refactor de UI sóbria.

    Regra: cor pode ser conteúdo (texto, ícone, fundo, visualizer), nunca chrome
    (border ou glow de halo em volta de algo).
    """

    def test_no_focus_halo(self):
        """Focus rings não podem ter box-shadow 0 0 0 Npx (halo de blur)."""
        for lineno, line in enumerate(GLASS_CSS.splitlines(), start=1):
            stripped = line.strip()
            # aceita "0 0 0 0px" (sem halo), recusa qualquer blur > 0
            self.assertNotRegex(
                stripped,
                r"box-shadow:\s*[^;]*\b0\s+0\s+0\s+[1-9]",
                f"linha {lineno}: focus halo proibido (0 0 0 Npx com N>0): {stripped}",
            )

    def test_pill_has_no_glow(self):
        """Pílula não pode ter box-shadow com 0 0 Npx (glow), nem border-color em estados."""
        import re
        # extrai cada bloco de regra de .voice-pill-container* e checa box-shadow
        block_re = re.compile(
            r"(\.voice-pill-container[^{]*\{[^}]*\}|\.pill-(?:muted|privacy|interrupting)[^{]*\{[^}]*\}"
            r"|\.voice-pill-avatar\.pill-privacy-on[^{]*\{[^}]*\})",
            re.DOTALL,
        )
        for m in block_re.finditer(GLASS_CSS):
            block = m.group(0)
            for lineno, line in enumerate(block.splitlines(), start=1):
                stripped = line.strip()
                if "box-shadow" in stripped and "none" not in stripped:
                    self.assertNotRegex(
                        stripped,
                        r"box-shadow:\s*[^;]*\b0\s+0\s+\d+px",
                        f"glow proibido em {m.group(0).split('{')[0].strip()}: {stripped}",
                    )

    def test_pill_state_uses_tint_not_border(self):
        """Estados pill-muted/privacy/interrupting usam background-image, não border-color."""
        import re
        for cls in ("pill-muted", "pill-privacy", "pill-interrupting"):
            block_re = re.compile(
                rf"(\.voice-pill-container\.{cls}[^{{]*\{{[^}}]*\}})",
                re.DOTALL,
            )
            matches = block_re.findall(GLASS_CSS)
            self.assertGreater(
                len(matches), 0, f"sem regra encontrada para .{cls}"
            )
            for block in matches:
                self.assertIn(
                    "background-image", block,
                    f".{cls} deve usar background-image (tint), não border-color: {block[:200]}",
                )
                self.assertNotIn(
                    "border-color", block,
                    f".{cls} não pode usar border-color (era LED): {block[:200]}",
                )

    def test_status_dot_rule_exists(self):
        """A classe .status-dot (órfã antes) agora tem regra real."""
        self.assertRegex(GLASS_CSS, r"\.status-dot\s*\{")
        # monocromática: variantes light/dark presentes
        self.assertIn("window.light-glass .status-dot", GLASS_CSS)
        self.assertIn("window.dark-glass .status-dot", GLASS_CSS)


class NeutralPaletteTest(unittest.TestCase):
    """A paleta é cinza neutro: sem viés azul, sem cor saturada como chrome.

    Antes do refactor o stylesheet espalhava ~25 hexes com viés azul e o
    accent era um ciano vivo (#15a6f0). Re-tematizar exigia caçar literal
    por literal — e nenhuma delas referenciava uma variável.
    """

    DEAD_BLUES = (
        "#123354", "#3a4759", "#4a607a", "#9aa7b5", "#e4ecf5",
        "#62a0ea", "#0d8fd1", "#3584e4", "#78aeed", "#15a6f0",
        "#1c2029", "#f2f5f9", "#0c7eb9",
    )

    def test_no_blue_tinted_literals(self):
        """Nenhum dos antigos literais com viés azul pode ter sobrevivido."""
        for dead in self.DEAD_BLUES:
            self.assertNotIn(
                dead, GLASS_CSS,
                f"literal com viés azul ainda presente: {dead}",
            )

    def test_gray_ramp_defined(self):
        """A rampa de cinza neutra está declarada de 50 a 900."""
        for step in (50, 100, 200, 300, 400, 500, 600, 700, 800, 900):
            self.assertRegex(
                GLASS_CSS,
                rf"@define-color zc_gray_{step}\s+#[0-9a-fA-F]{{6}};",
                f"falta @define-color zc_gray_{step}",
            )

    def test_semantic_colors_defined(self):
        """Cores semânticas existem e são dessaturadas (não #e01b24 puro)."""
        for name in ("zc_error", "zc_success", "zc_warn", "zc_speak"):
            self.assertRegex(
                GLASS_CSS,
                rf"@define-color {name}\s+#[0-9a-fA-F]{{6}};",
                f"falta @define-color {name}",
            )

    def test_window_is_actually_translucent(self):
        """0.96 era opaco na prática; o vidro só aparece quando o fundo vaza."""
        self.assertGreaterEqual(
            GLASS_CSS.count("0.85"), 2,
            "janela principal deveria estar em 0.85, não opaca",
        )

    def test_no_saturated_color_as_chrome(self):
        """Cor é conteúdo, nunca chrome: bordas usam cinza, não cor semântica."""
        for name in ("@zc_error", "@zc_success", "@zc_warn", "@zc_speak"):
            for lineno, line in enumerate(GLASS_CSS.splitlines(), start=1):
                stripped = line.strip()
                if "border" in stripped and name in stripped:
                    self.fail(
                        f"linha {lineno}: cor semântica usada como borda "
                        f"(chrome): {stripped}"
                    )

    def test_glass_has_specular_edge(self):
        """A borda especular (1px de luz no topo) é o que vende o vidro.

        Sem backdrop-filter no GTK4, é o recurso que mais compensa a falta
        do desfoque real.
        """
        self.assertGreaterEqual(
            GLASS_CSS.count("inset 0 1px 0"), 5,
            "poucas superfícies com borda especular",
        )


if __name__ == "__main__":
    unittest.main()
