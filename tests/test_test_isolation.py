"""Testes do isolamento de ambiente e do teardown de serviços da janela.

Cobrem a regressão que fazia a suíte derrubar a máquina:

1. O isolamento vivia só em `conftest.py` (arquivo do pytest), então o comando
   documentado no README — `python3 -m unittest discover -s tests` — rodava sem
   proteção nenhuma: contra o config e o banco reais do usuário, e subindo um
   servidor MCP por janela criada.
2. `_is_test_environment()` só reconhecia o pytest, então o gate não disparava
   sob `unittest` e cada `CopilotWindow` custava ~110 MB e ~1,8 processo.
3. Não havia teardown: `win.destroy()` não devolvia MCP, RAG nem wake word.
"""

import os
import sys
import unittest

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402

require_gtk4()
from gi.repository import Adw  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from unittest.mock import MagicMock, patch  # noqa: E402

from zorin_copilot.ui.app import CopilotWindow, _is_test_environment  # noqa: E402

try:
    from tests import _sandbox
except ImportError:  # descoberta como raiz simples
    import _sandbox  # type: ignore[no-redef]


class SandboxIsolationTest(unittest.TestCase):
    """O isolamento precisa valer sob qualquer runner, não só sob o pytest."""

    def test_activate_sets_test_mode_flag(self):
        _sandbox.activate()
        self.assertEqual(os.environ.get("ZORIN_TEST_MODE"), "1")

    def test_activate_redirects_xdg_directories(self):
        sandbox = _sandbox.activate()
        for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            value = os.environ.get(var, "")
            self.assertTrue(value, f"{var} não foi definido pelo isolamento")
            self.assertTrue(
                value.startswith(sandbox),
                f"{var}={value} deveria estar dentro do sandbox {sandbox}",
            )

    def test_activate_never_points_at_the_real_home(self):
        """O ponto central: nada pode apontar para o perfil real do usuário."""
        _sandbox.activate()
        real_config = os.path.expanduser("~/.config")
        real_data = os.path.expanduser("~/.local/share")
        for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME"):
            value = os.environ.get(var, "")
            self.assertFalse(
                value.startswith(real_config) or value.startswith(real_data),
                f"{var} aponta para o perfil real do usuário: {value}",
            )

    def test_activate_is_idempotent(self):
        first = _sandbox.activate()
        second = _sandbox.activate()
        self.assertEqual(first, second, "activate() deve devolver o mesmo sandbox")

    def test_config_sandbox_is_inert(self):
        """A config de teste não pode habilitar MCP automático nem wake word."""
        import json
        from pathlib import Path

        sandbox = _sandbox.activate()
        config_path = Path(sandbox) / "config" / "zorin-copilot" / "config.json"
        self.assertTrue(config_path.exists(), "a config de teste não foi escrita")

        data = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertFalse(data["mcp_auto_connect"])
        self.assertFalse(data["wake_word_enabled"])
        self.assertEqual(data["trusted_directories"], [])
        self.assertEqual(data["quarantine_directories"], [])


class TestEnvironmentDetectionTest(unittest.TestCase):
    """A matriz de detecção que faz o gate de serviços pesados disparar."""

    def test_detects_zorin_test_mode(self):
        with patch.dict(os.environ, {"ZORIN_TEST_MODE": "1"}, clear=True):
            self.assertTrue(_is_test_environment())

    def test_detects_pytest_current_test(self):
        with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "x"}, clear=True):
            self.assertTrue(_is_test_environment())

    def test_detects_unittest_runner(self):
        """O sinal que faltava: a suíte roda com `python -m unittest`.

        O `unittest` não expõe variável de ambiente por teste. O que o delata é
        o próprio processo: `__main__.__spec__.name == "unittest.__main__"`.
        Sem este ramo, o gate nunca disparava no comando documentado no README.
        """
        import types

        fake_main = types.ModuleType("__main__")
        fake_main.__spec__ = types.SimpleNamespace(name="unittest.__main__")
        with patch.dict(os.environ, {}, clear=True), \
             patch.dict(sys.modules, {"__main__": fake_main}):
            sys.modules.pop("pytest", None)
            self.assertTrue(_is_test_environment())

    def test_returns_false_in_plain_environment(self):
        """Fora de um runner, o app precisa subir os serviços normalmente."""
        import types

        fake_main = types.ModuleType("__main__")
        fake_main.__spec__ = types.SimpleNamespace(name="__main__")
        env = {k: v for k, v in os.environ.items() if k not in ("ZORIN_TEST_MODE", "PYTEST_CURRENT_TEST")}
        with patch.dict(os.environ, env, clear=True), \
             patch.dict(sys.modules, {"__main__": fake_main}):
            sys.modules.pop("pytest", None)
            self.assertFalse(_is_test_environment())

    def test_never_raises_without_env_vars(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(_is_test_environment(), bool)


class WindowTeardownTest(unittest.TestCase):
    """`destroy` precisa devolver MCP, RAG e wake word."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.teardown")

    def test_teardown_stops_every_background_service(self):
        win = CopilotWindow(self.app)
        try:
            win.mcp_manager.stop_all = MagicMock()
            win.rag.cancel_indexing = MagicMock()
            win.wake_word_engine = MagicMock()

            win.teardown_background_services()

            win.mcp_manager.stop_all.assert_called_once()
            win.rag.cancel_indexing.assert_called_once()
            win.wake_word_engine.stop.assert_called_once()
        finally:
            win.destroy()

    def test_teardown_is_idempotent(self):
        """`destroy` e `do_shutdown` podem chamar em qualquer ordem."""
        win = CopilotWindow(self.app)
        try:
            win.mcp_manager.stop_all = MagicMock()
            win.rag.cancel_indexing = MagicMock()

            win.teardown_background_services()
            win.teardown_background_services()
            win.teardown_background_services()

            win.mcp_manager.stop_all.assert_called_once()
            win.rag.cancel_indexing.assert_called_once()
        finally:
            win.destroy()

    def test_destroy_override_triggers_teardown(self):
        """`win.destroy()` é o gancho real usado pela suíte.

        Não é o sinal `destroy` do GTK4: medido, `Gtk.Window.destroy()` não
        emite esse sinal — nem em janela nunca apresentada, nem em janela
        apresentada (aí só sai `unrealize`). Por isso `CopilotWindow` sobrescreve
        o método `destroy()`.
        """
        win = CopilotWindow(self.app)
        win.mcp_manager.stop_all = MagicMock()
        win.rag.cancel_indexing = MagicMock()

        win.destroy()

        win.mcp_manager.stop_all.assert_called_once()
        win.rag.cancel_indexing.assert_called_once()

    def test_destroy_signal_is_vestigial_in_gtk4(self):
        """Fixa o fato que motivou a sobrescrita — se o GTK mudar, este teste avisa.

        Se um dia o sinal `destroy` passar a ser emitido, a sobrescrita do
        método continua correta (o teardown é idempotente), mas vale saber que a
        premissa mudou.
        """
        win = CopilotWindow(self.app)
        fired = []
        win.connect("destroy", lambda *_: fired.append(True))

        win.destroy()

        self.assertEqual(
            fired,
            [],
            "o sinal `destroy` passou a ser emitido no GTK4 — revise o comentário "
            "em CopilotWindow.destroy",
        )

    def test_teardown_survives_missing_attributes(self):
        """Janela meio desmontada não pode derrubar o teardown."""
        win = CopilotWindow(self.app)
        try:
            win.mcp_manager = None
            win.rag = None
            win.wake_word_engine = None
            win.teardown_background_services()  # não deve levantar
        finally:
            win.destroy()

    def test_window_does_not_spawn_mcp_under_test_runner(self):
        """Sob runner de teste, criar janela não pode subir subprocesso nenhum."""
        import subprocess

        def child_count() -> int:
            out = subprocess.run(
                ["ps", "-o", "pid=", "--ppid", str(os.getpid())],
                capture_output=True,
                text=True,
            ).stdout
            return len([p for p in out.split() if p])

        before = child_count()
        win = CopilotWindow(self.app)
        try:
            self.assertEqual(
                child_count(),
                before,
                "uma CopilotWindow subiu processo externo mesmo em modo de teste",
            )
        finally:
            win.destroy()


if __name__ == "__main__":
    unittest.main()
