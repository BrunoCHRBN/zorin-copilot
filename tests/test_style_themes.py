"""Testes da cadeia de folhas de estilo customizáveis (item 2.10 do plano de UI).

Cobre o carregamento do tema embutido fora do código-fonte e a ordem em que as
sobrescritas do usuário são aplicadas.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import zorin_copilot.ui.style as style  # noqa: E402


class BundledStylesheetTest(unittest.TestCase):
    """O CSS não vive mais num string gigante dentro de style.py."""

    def test_css_is_loaded(self):
        self.assertGreater(len(style.GLASS_CSS), 5000)

    def test_css_keeps_key_rules(self):
        self.assertIn("@define-color accent_color #15a6f0;", style.GLASS_CSS)
        self.assertIn("window.light-glass image", style.GLASS_CSS)
        self.assertIn("window.dark-glass image", style.GLASS_CSS)

    def test_source_of_truth_is_a_data_file(self):
        """O arquivo de dados existe e é o que foi carregado."""
        from importlib.resources import files

        resource = files("zorin_copilot").joinpath("data/zorin-copilot.css")
        self.assertTrue(resource.is_file())
        self.assertEqual(resource.read_text(encoding="utf-8"), style.GLASS_CSS)

    def test_no_huge_literal_left_in_module(self):
        """O módulo não deve mais declarar o CSS inline."""
        source = Path(style.__file__).read_text(encoding="utf-8")
        self.assertNotIn('GLASS_CSS = """', source)
        self.assertIn("_load_bundled_css", source)

    def test_bundled_theme_parses(self):
        """O CSS embutido precisa ser aceito pelo parser do GTK."""
        provider = Gtk.CssProvider()
        provider.load_from_string(style.GLASS_CSS)
        self.assertIsNotNone(provider)


class StylesheetChainTest(unittest.TestCase):
    """Ordem: embutido < sistema < temas (alfabética) < user.css."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        self.addCleanup(self._restore_xdg)

    def _restore_xdg(self):
        if self.old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.old_xdg

    def _write(self, rel: str, content: str) -> None:
        path = Path(self.tmp.name) / "zorin-copilot" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_config_dir_respects_xdg(self):
        self.assertEqual(style.config_dir(), Path(self.tmp.name) / "zorin-copilot")

    def test_chain_is_only_bundled_when_nothing_exists(self):
        self.assertEqual([n for n, _ in style.load_stylesheet_chain()], ["bundled"])

    def test_user_css_is_last(self):
        """O override do usuário precisa vencer todos os outros."""
        self._write("user.css", "window { color: red; }")
        names = [n for n, _ in style.load_stylesheet_chain()]
        self.assertEqual(names, ["bundled", "user"])

    def test_themes_are_sorted_and_come_before_user(self):
        self._write("themes/zzz.css", "/* z */")
        self._write("themes/aaa.css", "/* a */")
        self._write("user.css", "/* u */")
        names = [n for n, _ in style.load_stylesheet_chain()]
        self.assertEqual(names, ["bundled", "theme:aaa.css", "theme:zzz.css", "user"])

    def test_system_css_comes_before_themes(self):
        self._write("themes/aaa.css", "/* a */")
        with tempfile.NamedTemporaryFile("w", suffix=".css", delete=False) as fh:
            fh.write("/* system */")
            system_path = Path(fh.name)
        self.addCleanup(system_path.unlink)

        original = style.SYSTEM_CSS_PATH
        style.SYSTEM_CSS_PATH = system_path
        self.addCleanup(lambda: setattr(style, "SYSTEM_CSS_PATH", original))

        names = [n for n, _ in style.load_stylesheet_chain()]
        self.assertEqual(names, ["bundled", "system", "theme:aaa.css"])

    def test_empty_files_are_skipped(self):
        self._write("user.css", "   \n  ")
        self.assertEqual([n for n, _ in style.load_stylesheet_chain()], ["bundled"])

    def test_unreadable_user_css_does_not_raise(self):
        """CSS ilegível não pode derrubar a inicialização da interface."""
        self._write("user.css", "window { color: red; }")
        path = style.user_css_path()
        path.chmod(0o000)
        self.addCleanup(lambda: path.chmod(0o644))
        chain = style.load_stylesheet_chain()
        self.assertTrue(any(n == "bundled" for n, _ in chain))


class ThemeApplicationTest(unittest.TestCase):
    """A aplicação instala cada folha com prioridade crescente."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp.name
        self.addCleanup(self._restore_xdg)
        # Reseta o guard de instalação única entre os testes.
        self.old_flag = style._provider_installed
        style._provider_installed = False
        self.addCleanup(lambda: setattr(style, "_provider_installed", self.old_flag))

    def _restore_xdg(self):
        if self.old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.old_xdg

    def test_installs_one_provider_per_stylesheet(self):
        path = Path(self.tmp.name) / "zorin-copilot" / "user.css"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("window { color: red; }", encoding="utf-8")

        added = []
        original_add = Gtk.StyleContext.add_provider_for_display

        def spy(display, provider, priority):
            added.append(priority)

        Gtk.StyleContext.add_provider_for_display = spy
        self.addCleanup(
            lambda: setattr(Gtk.StyleContext, "add_provider_for_display", original_add)
        )

        style.apply_glass_theme(None)

        self.assertEqual(len(added), 2)
        # Prioridades estritamente crescentes: o override do usuário vence.
        self.assertLess(added[0], added[1])
        self.assertGreaterEqual(added[0], Gtk.STYLE_PROVIDER_PRIORITY_USER)

    def test_invalid_user_css_still_applies_theme(self):
        """CSS malformado do usuário não pode deixar o app sem tema."""
        path = Path(self.tmp.name) / "zorin-copilot" / "user.css"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("isto @@ nao %% é css <<<", encoding="utf-8")

        added = []
        original_add = Gtk.StyleContext.add_provider_for_display
        Gtk.StyleContext.add_provider_for_display = lambda d, p, pr: added.append(pr)
        self.addCleanup(
            lambda: setattr(Gtk.StyleContext, "add_provider_for_display", original_add)
        )

        style.apply_glass_theme(None)
        self.assertGreaterEqual(len(added), 1)


if __name__ == "__main__":
    unittest.main()
