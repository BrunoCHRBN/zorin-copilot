"""Testes do painel de comandos (Ctrl+K), primeiro item do backlog da UI."""

import os
import sys
import unittest

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.shortcuts import APP_SHORTCUTS  # noqa: E402
from zorin_copilot.ui.app import CopilotWindow  # noqa: E402
from zorin_copilot.ui.widgets.command_palette import (  # noqa: E402
    MAX_RESULTS,
    PaletteCommand,
    _accelerator_badge,
    _accelerator_parts,
    _pretty_accelerator,
    score_command,
)


class AcceleratorRenderingTest(unittest.TestCase):
    """O atalho aparece como teclas (estilo Superhuman), não como texto cinza.

    O parser anterior removia todos os ``>`` e separava por ``<``, o que
    quebrava o formato do GTK (``<Control>q`` virava ``Controlq``) e colava a
    tecla no último modificador (``<Control><Shift>m`` -> ``Ctrl+Shiftm``).
    """

    def test_single_modifier(self):
        self.assertEqual(_accelerator_parts("<Control>q"), ["Ctrl", "Q"])

    def test_multiple_modifiers(self):
        self.assertEqual(
            _accelerator_parts("<Control><Shift>m"), ["Ctrl", "Shift", "M"]
        )

    def test_super_modifier_is_preserved(self):
        self.assertEqual(
            _accelerator_parts("<Super><Shift>d"), ["Super", "Shift", "D"]
        )

    def test_named_key_is_not_uppercased(self):
        self.assertEqual(_accelerator_parts("<Control>space"), ["Ctrl", "space"])

    def test_bare_key_passes_through(self):
        self.assertEqual(_accelerator_parts("F5"), ["F5"])

    def test_empty_input(self):
        self.assertEqual(_accelerator_parts(""), [])

    def test_pretty_joins_with_plus(self):
        self.assertEqual(_pretty_accelerator("<Control><Shift>k"), "Ctrl+Shift+K")

    def test_badge_renders_one_kbd_per_key(self):
        badge = _accelerator_badge("<Control>k")
        labels = _collect_labels(badge)
        self.assertIn("Ctrl", labels)
        self.assertIn("K", labels)

    def test_badge_marks_chord_for_tabular_numbers(self):
        badge = _accelerator_badge("<Control>k")
        self.assertIn("shortcut-chord", badge.get_css_classes())


def _collect_labels(widget) -> list[str]:
    """Coleta o texto de todos os Gtk.Label dentro de um widget."""
    from gi.repository import Gtk

    found: list[str] = []
    child = widget.get_first_child() if isinstance(widget, Gtk.Widget) else None
    if isinstance(widget, Gtk.Label):
        text = widget.get_text()
        if text:
            found.append(text)
    while child is not None:
        found.extend(_collect_labels(child))
        child = child.get_next_sibling()
    return found


def _command(
    title: str,
    name: str = "x",
    subtitle: str = "",
    keywords: tuple[str, ...] = (),
) -> PaletteCommand:
    return PaletteCommand(name=name, title=title, subtitle=subtitle, keywords=keywords)


class ScoreCommandTest(unittest.TestCase):
    def test_empty_query_matches_everything(self):
        self.assertEqual(score_command("", _command("Nova conversa")), 0)

    def test_prefix_outranks_substring(self):
        prefix = score_command("nova", _command("Nova conversa"))
        inside = score_command("conversa", _command("Nova conversa"))
        self.assertGreater(prefix, inside)

    def test_title_outranks_keyword(self):
        title = score_command("voz", _command("Conversa por voz"))
        keyword = score_command("voz", _command("Gravar", keywords=("voz",)))
        self.assertGreater(title, keyword)

    def test_subsequence_match(self):
        """'nconv' precisa achar 'Nova conversa' — é o que faz a busca valer."""
        self.assertIsNotNone(score_command("nconv", _command("Nova conversa")))
        self.assertIsNotNone(score_command("cfg", _command("Preferências", keywords=("configurações",))))

    def test_no_match(self):
        self.assertIsNone(score_command("zzzzz", _command("Nova conversa")))

    def test_search_is_case_insensitive(self):
        self.assertEqual(
            score_command("NOVA", _command("Nova conversa")),
            score_command("nova", _command("Nova conversa")),
        )


class CommandPaletteWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.palette")

    def setUp(self):
        self.win = CopilotWindow(self.app)
        self.palette = self.win.command_palette

    def _titles(self) -> list[str]:
        model = self.palette.listbox.observe_children()
        return [
            model.get_item(i).get_title()
            for i in range(model.get_n_items())
            if hasattr(model.get_item(i), "get_title")
        ]

    def test_starts_closed(self):
        self.assertFalse(self.palette.is_open)
        self.assertFalse(self.palette.get_visible())

    def test_open_lists_commands_capped(self):
        self.palette.open()
        self.assertTrue(self.palette.is_open)
        self.assertTrue(self.palette.get_visible())
        self.assertLessEqual(len(self._titles()), MAX_RESULTS)
        self.assertGreater(len(self._titles()), 0)

    def test_typing_filters(self):
        self.palette.open()
        self.palette.entry.set_text("nova")
        titles = self._titles()
        self.assertIn("Nova conversa", titles)
        self.assertNotIn("Limpar todo o histórico", titles)

    def test_no_match_shows_empty_state(self):
        self.palette.open()
        self.palette.entry.set_text("zzzzzz")
        self.assertEqual(self._titles(), ["Nenhum comando encontrado"])

    def test_selection_wraps_around(self):
        self.palette.open()
        total = len(self.palette._visible_rows)
        self.palette.move_selection(1)
        second = self.palette.listbox.get_selected_row()
        # Avança uma volta inteira: precisa cair exatamente de volta.
        self.palette.move_selection(total)
        self.assertEqual(self.palette.listbox.get_selected_row(), second)

    def test_activate_runs_and_closes(self):
        executed: list[str] = []
        self.win.run_palette_command = lambda cmd: executed.append(cmd.name)

        self.palette.open()
        self.palette.listbox.select_row(self.palette._visible_rows[0])
        self.palette.activate_selected()

        self.assertEqual(len(executed), 1)
        self.assertFalse(self.palette.is_open)
        self.assertFalse(self.palette.get_visible())

    def test_empty_state_row_is_not_actionable(self):
        self.palette.open()
        self.palette.entry.set_text("zzzzzz")
        executed: list[str] = []
        self.win.run_palette_command = lambda cmd: executed.append(cmd.name)
        self.palette.activate_selected()
        self.assertEqual(executed, [])

    def test_close_clears_query(self):
        self.palette.open()
        self.palette.entry.set_text("abc")
        self.palette.close()
        self.assertEqual(self.palette.entry.get_text(), "")

    def test_title_with_ampersand_renders_instead_of_blanking(self):
        """`Adw.ActionRow` lê `title`/`subtitle` como MARKUP, não como texto.

        Com um `&` cru o parsing falha: o GTK emite um warning, o `get_title()`
        continua devolvendo o texto original e o rótulo sai **vazio** — a linha
        aparece em branco no painel. Asserção sobre `get_title()` passaria feliz;
        só olhando o `Gtk.Label` interno o defeito aparece. Importa porque os
        títulos de prompt MCP vêm de servidores externos.
        """
        row = self.palette._make_row(
            _command("Rock & Roll", subtitle="tom & voz", keywords=("a & b",))
        )

        rendered = _collect_labels(row)
        self.assertIn("Rock & Roll", rendered)
        self.assertIn("tom & voz", rendered)

    def test_titles_without_special_chars_are_untouched(self):
        """Escapar não pode alterar o caso comum (sem `&`, `<` ou `>`)."""
        row = self.palette._make_row(_command("Nova conversa", subtitle="Começar limpo"))
        self.assertEqual(row.get_title(), "Nova conversa")
        self.assertEqual(row.get_subtitle(), "Começar limpo")
        self.assertFalse(self.palette.is_open)


class CommandPaletteIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.palette.integration")

    def setUp(self):
        self.win = CopilotWindow(self.app)

    def test_shortcut_is_registered(self):
        names = [s.name for s in APP_SHORTCUTS]
        self.assertIn("app.command-palette", names)
        accel = next(s.accelerator for s in APP_SHORTCUTS if s.name == "app.command-palette")
        self.assertEqual(accel, "<Control>k")

    def test_window_installs_the_shortcut(self):
        controller = self.win.app_shortcut_controller
        triggers = [
            controller.get_item(i).get_trigger().to_string()
            for i in range(controller.get_n_items())
        ]
        self.assertIn("<Control>k", triggers)

    def test_every_command_has_a_handler(self):
        """Comando sem handler viraria uma entrada morta no painel.

        Exceção deliberada: comandos com prefixo `mcp-prompt::` são roteados por
        prefixo em `run_palette_command`, não pelo dicionário estático — os nomes
        deles vêm dos servidores MCP conectados e mudam em tempo de execução.
        Sem este filtro o teste passaria hoje (o sandbox não tem prompts) e
        quebraria assim que houvesse um, apontando defeito onde não há.
        """
        from zorin_copilot.mcp.prompts import is_prompt_command

        names = {
            c.name for c in self.win.palette_commands() if not is_prompt_command(c.name)
        }
        handlers = set(self.win._palette_handlers())
        self.assertEqual(names - handlers, set())

    def test_command_names_are_unique(self):
        names = [c.name for c in self.win.palette_commands()]
        self.assertEqual(len(names), len(set(names)))

    def test_commands_expose_known_accelerators(self):
        declared = {s.name: s.accelerator for s in APP_SHORTCUTS}
        for command in self.win.palette_commands():
            if command.name in declared:
                self.assertEqual(command.accelerator, declared[command.name])

    def test_ctrl_k_is_declined_while_typing(self):
        """O GTK usa Ctrl+K para apagar até o fim da linha; não podemos roubá-lo.

        Nota: `get_focus()` devolve o `Gtk.Text` interno do Entry, não o Entry em
        si — por isso o teste verifica o retorno, não a identidade do widget.
        """
        self.win.entry.grab_focus()
        self.assertIsNotNone(self.win.get_focus())
        self.assertFalse(self.win._open_command_palette())
        self.assertFalse(self.win.command_palette.is_open)

    def test_ctrl_k_opens_when_focus_is_not_text(self):
        self.win.command_palette.close()
        self.win.sidebar_toggle_btn.grab_focus()
        self.assertTrue(self.win._open_command_palette())
        self.assertTrue(self.win.command_palette.is_open)

    def test_ctrl_k_toggles(self):
        self.win.sidebar_toggle_btn.grab_focus()
        self.win._open_command_palette()
        self.assertTrue(self.win.command_palette.is_open)
        self.win._open_command_palette()
        self.assertFalse(self.win.command_palette.is_open)

    def test_escape_closes_palette_before_hiding_window(self):
        self.win.set_visible(True)
        self.win.sidebar_toggle_btn.grab_focus()
        self.win._open_command_palette()

        handled = self.win._handle_escape()

        self.assertTrue(handled)
        self.assertFalse(self.win.command_palette.is_open)
        self.assertTrue(self.win.get_visible())

    def test_shortcut_callback_respects_false(self):
        """Handler que devolve False deixa o evento seguir propagando."""
        callback = CopilotWindow._make_shortcut_callback(lambda: False)
        self.assertFalse(callback(None, None))

        callback = CopilotWindow._make_shortcut_callback(lambda: None)
        self.assertTrue(callback(None, None))

    def test_network_palette_commands(self):
        commands = self.win.palette_commands()
        cmd_names = [c.name for c in commands]
        self.assertIn("app.network-inspect", cmd_names)
        self.assertIn("app.network-copy-ip", cmd_names)
        self.assertIn("app.network-dns-bench", cmd_names)
        self.assertIn("app.network-audit", cmd_names)
        self.assertIn("app.network-throughput", cmd_names)
        self.assertIn("app.network-diagnose", cmd_names)

        handlers = self.win._palette_handlers()
        self.assertIn("app.network-inspect", handlers)
        self.assertIn("app.network-copy-ip", handlers)
        self.assertIn("app.network-dns-bench", handlers)
        self.assertIn("app.network-audit", handlers)
        self.assertIn("app.network-throughput", handlers)
        self.assertIn("app.network-diagnose", handlers)

    def test_shortcut_cheatsheet_is_discoverable(self):
        """A referência de atalhos tem comando no painel E handler ligado."""
        commands = self.win.palette_commands()
        names = [c.name for c in commands]
        self.assertIn("app.shortcut-cheatsheet", names)
        self.assertIn("app.shortcut-cheatsheet", self.win._palette_handlers())

    def test_shortcut_cheatsheet_lists_app_shortcuts(self):
        """O diálogo só monta se cada atalho declarado tiver uma linha."""
        # Não abre janela: valida apenas que os atalhos usados existem.
        self.assertGreater(len(APP_SHORTCUTS), 0)
        for shortcut in APP_SHORTCUTS:
            self.assertTrue(shortcut.accelerator, f"{shortcut.name} sem acelerador")
            self.assertTrue(shortcut.description, f"{shortcut.name} sem descrição")


if __name__ == "__main__":
    unittest.main()

