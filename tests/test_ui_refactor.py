"""Testes de regressão da refatoração da UI em componentes (Sprint 1).

Cobrem os bugs descobertos durante a extração de `ui/app.py` para `ui/widgets/`:
  1. `TopicSession.record_turn` não devolvia o turno, impedindo a renderização da resposta.
  2. `Esc` escondia a janela mesmo com um popover aberto.
  3. `zorin_copilot.ui` não era descoberto pelo empacotamento (falta de `__init__.py`).
"""

import os
import sys
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from unittest.mock import patch  # noqa: E402

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction  # noqa: E402
from zorin_copilot.core.session import ChatTurn, TopicSession  # noqa: E402
from zorin_copilot.core.shortcuts import APP_SHORTCUTS  # noqa: E402
from zorin_copilot.ui.app import CopilotWindow  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_loop_until(predicate, timeout_ms=2000):
    """Executa o main loop até que `predicate` seja verdadeiro ou o timeout estoure."""
    loop = GLib.MainLoop()
    result = {"ok": False}

    def check():
        if predicate():
            result["ok"] = True
            loop.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(50, check)
    GLib.timeout_add(timeout_ms, loop.quit)
    loop.run()
    return result["ok"]


class SessionTurnRegressionTest(unittest.TestCase):
    """`record_turn` deve devolver o turno criado para permitir a renderização."""

    def test_record_turn_returns_turn(self):
        session = TopicSession(auto_persist=True)
        turn = session.record_turn("Qual é o meu IP?", "Use `ip a`.")
        self.assertIsInstance(turn, ChatTurn)
        self.assertEqual(turn.prompt, "Qual é o meu IP?")
        self.assertEqual(turn.answer, "Use `ip a`.")

    def test_record_turn_returns_turn_when_not_persisted(self):
        """Mesmo sem auto-persistência o turno precisa ser devolvido."""
        session = TopicSession(auto_persist=False)
        turn = session.record_turn("Pergunta", "Resposta")
        self.assertIsInstance(turn, ChatTurn)
        self.assertEqual(turn.prompt, "Pergunta")


class WindowCompositionTest(unittest.TestCase):
    """A janela deve compor os componentes extraídos mantendo a API pública."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.refactor")

    def setUp(self):
        self.win = CopilotWindow(self.app)

    def test_components_are_mounted(self):
        for attr in ("header", "sidebar", "chat_stream", "prompt_bar", "vision"):
            self.assertTrue(hasattr(self.win, attr), f"componente ausente: {attr}")

    def test_legacy_api_preserved(self):
        """Atributos usados por código legado e testes antigos continuam acessíveis."""
        for attr in (
            "sidebar_revealer", "sidebar_search", "history_listbox", "sidebar_toggle_btn",
            "chat_stream_box", "welcome_box", "prompt_bar_box", "entry",
            "vision_btn", "clipboard_btn", "bottom_voice_btn", "submit_btn",
            "app_preview_revealer", "vision_preview_box",
            "fence_menu_btn", "fence_lbl", "window_title",
        ):
            self.assertIsNotNone(getattr(self.win, attr, None), f"atributo ausente: {attr}")

    def test_submit_renders_assistant_response(self):
        """Regressão: a resposta da IA precisa aparecer no fluxo (turno não pode ser None)."""
        plan = ActionPlan(
            thought="Abrindo a calculadora.",
            actions=[DesktopAction(ActionType.LAUNCH_APP, "calc")],
        )
        with patch.object(self.win.engine, "parse", return_value=plan):
            self.win.entry.set_text("abrir calculadora")
            self.win._on_submit(self.win.entry)

        self.assertTrue(run_loop_until(lambda: not self.win._is_busy))
        self.assertEqual(self.win.session.turn_count, 1)

        children = []
        child = self.win.chat_stream_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        # Balão do usuário + cartão de resposta do assistente
        self.assertGreaterEqual(len(children), 2)

    def test_escape_closes_popover_before_hiding_window(self):
        """Regressão: Esc deve fechar popovers antes de esconder a janela."""
        self.win.set_visible(True)
        popover = self.win.vision_btn.get_popover()
        popover.popup()
        self.assertTrue(popover.get_visible())

        self.win._handle_escape()

        self.assertFalse(popover.get_visible())
        self.assertTrue(self.win.get_visible())

    def test_escape_hides_window_when_nothing_open(self):
        self.win.set_visible(True)
        self.win.entry.set_text("")
        self.win.sidebar_search.set_text("")
        self.win.live_client = None

        self.win._handle_escape()
        self.assertFalse(self.win.get_visible())

    def test_sidebar_search_uses_debounce(self):
        """A busca da sidebar não deve reconstruir a lista a cada tecla."""
        self.win.engine.memory.save_chat_topic("dbg1", "Receita de bolo", [], is_pinned=True)
        self.win.engine.memory.save_chat_topic("dbg2", "Configurar VPN", [], is_pinned=True)

        populated = []
        original = self.win.sidebar.populate

        def spy(*args, **kwargs):
            populated.append(1)
            return original(*args, **kwargs)

        self.win.sidebar.populate = spy
        entry = self.win.sidebar_search
        entry.set_text("VPN")
        # Imediatamente após digitar nenhuma reconstrução deve ter ocorrido
        self.assertEqual(len(populated), 0)

        self.assertTrue(run_loop_until(lambda: len(populated) > 0))
        self.assertGreaterEqual(len(populated), 1)

        self.win.sidebar.populate = original
        self.win.engine.memory.delete_chat_topic("dbg1")
        self.win.engine.memory.delete_chat_topic("dbg2")


class AppShortcutsTest(unittest.TestCase):
    """Os atalhos internos devem vir do registro declarativo, não de keyvals soltos."""

    def test_registry_is_declared(self):
        names = {s.name for s in APP_SHORTCUTS}
        self.assertEqual(
            names,
            {"app.quit", "app.toggle-live-voice", "app.toggle-sidebar", "app.new-topic", "app.toggle-pin"},
        )
        for shortcut in APP_SHORTCUTS:
            self.assertTrue(shortcut.accelerator.startswith("<Control>"))
            self.assertTrue(shortcut.description)

    def test_window_installs_shortcut_controller(self):
        app = Adw.Application(application_id="org.zorin.copilot.test.shortcuts")
        win = CopilotWindow(app)
        controllers = [
            c for c in win.observe_controllers()
            if isinstance(c, Gtk.ShortcutController)
        ]
        self.assertTrue(controllers, "nenhum Gtk.ShortcutController instalado")

        controller = win.app_shortcut_controller
        self.assertEqual(controller.get_n_items(), len(APP_SHORTCUTS))

        installed = set()
        for i in range(controller.get_n_items()):
            trigger = controller.get_item(i).get_trigger()
            if trigger is not None:
                installed.add(trigger.to_string())

        for shortcut in APP_SHORTCUTS:
            self.assertIn(shortcut.accelerator, installed)


class PackagingTest(unittest.TestCase):
    """O pacote `zorin_copilot.ui` precisa ser descoberto pelo setuptools."""

    def test_ui_package_is_discoverable(self):
        try:
            from setuptools import find_packages
        except ImportError:  # pragma: no cover - setuptools sempre presente
            self.skipTest("setuptools indisponível")

        packages = find_packages(where=os.path.join(ROOT, "src"))
        self.assertIn("zorin_copilot.ui", packages)
        self.assertIn("zorin_copilot.ui.widgets", packages)

    def test_init_files_exist(self):
        for rel in (
            "src/zorin_copilot/ui/__init__.py",
            "src/zorin_copilot/ui/widgets/__init__.py",
        ):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, rel)), f"ausente: {rel}")


if __name__ == "__main__":
    unittest.main()
