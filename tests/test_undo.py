"""Testes do histórico de desfazer (item 10 do backlog da UI).

A pilha e as funções de reversão são puras; a costura com a janela e com as
linhas de ação fica no final.
"""

import os
import sys
import tempfile
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction  # noqa: E402
from zorin_copilot.core.files import FileManager  # noqa: E402
from zorin_copilot.shell.executor import (  # noqa: E402
    MAX_SNAPSHOT_BYTES,
    ActionExecutor,
    make_file_revert,
    snapshot_file,
)
from zorin_copilot.shell.action_status import ActionOutcome  # noqa: E402
from zorin_copilot.shell.undo import UNDO_HISTORY_LIMIT, UndoStack  # noqa: E402

try:
    Adw.init()
    HAS_DISPLAY = True
except Exception:  # pragma: no cover - só acontece sem servidor gráfico
    HAS_DISPLAY = False

if HAS_DISPLAY:
    from zorin_copilot.ui.app import CopilotWindow  # noqa: E402


def _ok(message: str = "ok"):
    return lambda: (True, message)


def _fail(message: str = "falhou"):
    return lambda: (False, message)


class UndoStackTest(unittest.TestCase):
    def test_undo_on_empty_stack(self):
        stack = UndoStack()
        ok, message, entry = stack.undo()
        self.assertFalse(ok)
        self.assertIn("Nada para desfazer", message)
        self.assertIsNone(entry)

    def test_push_and_peek(self):
        stack = UndoStack()
        entry = stack.push("A", _ok())
        self.assertEqual(len(stack), 1)
        self.assertIs(stack.peek(), entry)

    def test_undo_runs_revert_and_pops(self):
        stack = UndoStack()
        stack.push("A", _ok("desfeito A"))
        ok, message, entry = stack.undo()
        self.assertTrue(ok)
        self.assertEqual(message, "desfeito A")
        self.assertEqual(entry.label, "A")
        self.assertEqual(len(stack), 0)

    def test_most_recent_goes_first(self):
        """LIFO: desfaz a última ação feita, não a primeira."""
        stack = UndoStack()
        stack.push("primeira", _ok())
        stack.push("segunda", _ok())
        _, _, entry = stack.undo()
        self.assertEqual(entry.label, "segunda")

    def test_history_limit_keeps_only_the_last_five(self):
        stack = UndoStack(max_size=UNDO_HISTORY_LIMIT)
        for i in range(7):
            stack.push(f"a{i}", _ok())
        self.assertEqual(len(stack), UNDO_HISTORY_LIMIT)
        labels = [e.label for e in stack.entries]
        self.assertEqual(labels, ["a2", "a3", "a4", "a5", "a6"])

    def test_failed_undo_does_not_block_the_previous_ones(self):
        """Um desfazer impossível (arquivo movido) não pode trancar a pilha."""
        stack = UndoStack()
        stack.push("antiga", _ok())
        stack.push("impossivel", _fail("não existe mais"))
        ok, message, _ = stack.undo()
        self.assertFalse(ok)
        self.assertEqual(message, "não existe mais")
        self.assertEqual(len(stack), 1)
        _, _, entry = stack.undo()
        self.assertEqual(entry.label, "antiga")

    def test_revert_exception_is_contained(self):
        def boom():
            raise RuntimeError("estourou")

        stack = UndoStack()
        stack.push("A", boom)
        ok, message, _ = stack.undo()
        self.assertFalse(ok)
        self.assertIn("estourou", message)

    def test_clear(self):
        stack = UndoStack()
        stack.push("A", _ok())
        stack.clear()
        self.assertEqual(len(stack), 0)

    def test_undone_hook_receives_result(self):
        seen = []
        stack = UndoStack()
        stack.push("A", _ok("voltei"), on_undone=lambda ok, msg: seen.append((ok, msg)))
        stack.undo()
        self.assertEqual(seen, [(True, "voltei")])

    def test_broken_hook_does_not_break_undo(self):
        def broken(_ok, _msg):
            raise RuntimeError("ui quebrou")

        stack = UndoStack()
        stack.push("A", _ok("voltei"), on_undone=broken)
        ok, message, _ = stack.undo()
        self.assertTrue(ok)
        self.assertEqual(message, "voltei")


class SnapshotTest(unittest.TestCase):
    def test_missing_file(self):
        snap = snapshot_file("/nao/existe/x.txt", append=False)
        self.assertFalse(snap.existed)

    def test_existing_file_keeps_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.txt")
            with open(path, "w") as fh:
                fh.write("original")
            snap = snapshot_file(path, append=False)
            self.assertTrue(snap.existed)
            self.assertEqual(snap.content, "original")

    def test_append_only_needs_the_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.txt")
            with open(path, "w") as fh:
                fh.write("12345")
            snap = snapshot_file(path, append=True)
            self.assertTrue(snap.existed)
            self.assertEqual(snap.size, 5)
            self.assertEqual(snap.content, "")

    def test_binary_file_is_not_snapshot(self):
        """Restaurar binário por texto corromperia o arquivo — melhor não prometer."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.bin")
            with open(path, "wb") as fh:
                fh.write(b"\x00\x01\x02")
            self.assertIsNone(snapshot_file(path, append=False))

    def test_huge_file_is_not_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "grande.txt")
            with open(path, "w") as fh:
                fh.write("a" * (MAX_SNAPSHOT_BYTES + 10))
            self.assertIsNone(snapshot_file(path, append=False))


class FileRevertTest(unittest.TestCase):
    def test_undo_of_created_file_removes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "novo.txt")
            with open(path, "w") as fh:
                fh.write("escrito pela IA")
            ok, _ = make_file_revert(path, snapshot_file("/nao/existe", False))()
            self.assertTrue(ok)
            self.assertFalse(os.path.exists(path))

    def test_undo_of_overwrite_restores_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.txt")
            with open(path, "w") as fh:
                fh.write("original")
            snap = snapshot_file(path, append=False)
            with open(path, "w") as fh:
                fh.write("reescrito")
            ok, _ = make_file_revert(path, snap)()
            self.assertTrue(ok)
            with open(path) as fh:
                self.assertEqual(fh.read(), "original")

    def test_undo_of_append_truncates_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.txt")
            with open(path, "w") as fh:
                fh.write("base")
            snap = snapshot_file(path, append=True)
            with open(path, "a") as fh:
                fh.write("\n\nacrescentado")
            ok, _ = make_file_revert(path, snap)()
            self.assertTrue(ok)
            with open(path) as fh:
                self.assertEqual(fh.read(), "base")

    def test_undo_without_the_file_reports_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.txt")
            with open(path, "w") as fh:
                fh.write("x")
            snap = snapshot_file(path, append=False)
            os.remove(path)
            ok, message = make_file_revert(path, snap)()
            self.assertFalse(ok)
            self.assertIn("não existe mais", message)


class ExecutorUndoTest(unittest.TestCase):
    def setUp(self):
        self.executor = ActionExecutor()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _plan(self, action: DesktopAction) -> ActionPlan:
        return ActionPlan(thought="", actions=[action])

    def _run(self, action: DesktopAction):
        return self.executor.execute_plan(self._plan(action), dry_run=False)[0]

    def test_write_file_is_undoable(self):
        directory = self.tmp.name
        path = os.path.join(directory, "relatorio.md")
        with open(path, "w") as fh:
            fh.write("antes")

        action = DesktopAction(
            ActionType.WRITE_FILE, "relatorio.md",
            {"filename": "relatorio.md", "content": "depois", "directory": directory},
        )
        report = self._run(action)
        self.assertTrue(report.success)
        self.assertEqual(len(self.executor.undo_stack), 1)

        ok, message, _ = self.executor.undo_stack.undo()
        self.assertTrue(ok)
        with open(path) as fh:
            self.assertEqual(fh.read(), "antes")

    def test_new_file_undo_removes_it(self):
        action = DesktopAction(
            ActionType.WRITE_FILE, "novo.md",
            {"filename": "novo.md", "content": "oi", "directory": self.tmp.name},
        )
        self._run(action)
        path = os.path.join(self.tmp.name, "novo.md")
        self.assertTrue(os.path.exists(path))

        self.executor.undo_stack.undo()
        self.assertFalse(os.path.exists(path))

    def test_organize_files_is_undoable(self):
        directory = self.tmp.name
        for name in ("a.txt", "b.png", "c.pdf"):
            with open(os.path.join(directory, name), "w") as fh:
                fh.write("x")

        action = DesktopAction(
            ActionType.ORGANIZE_FILES, directory, {"directory": directory}
        )
        report = self._run(action)
        self.assertTrue(report.success)
        self.assertEqual(len(self.executor.undo_stack), 1)
        self.assertFalse(os.path.exists(os.path.join(directory, "a.txt")))

        ok, message, _ = self.executor.undo_stack.undo()
        self.assertTrue(ok)
        self.assertIn("3 arquivo(s)", message)
        for name in ("a.txt", "b.png", "c.pdf"):
            self.assertTrue(os.path.exists(os.path.join(directory, name)))
        # As pastas de categoria criadas e esvaziadas não devem sobrar.
        self.assertFalse(os.path.exists(os.path.join(directory, "Imagens")))

    def test_organize_undo_reports_missing_files(self):
        directory = self.tmp.name
        for name in ("a.txt", "b.png"):
            with open(os.path.join(directory, name), "w") as fh:
                fh.write("x")

        self._run(DesktopAction(
            ActionType.ORGANIZE_FILES, directory, {"directory": directory}
        ))
        # O usuário tirou um dos arquivos do lugar antes de desfazer.
        os.remove(os.path.join(directory, "Documentos", "a.txt"))

        ok, message, _ = self.executor.undo_stack.undo()
        self.assertFalse(ok)
        self.assertIn("não foram encontrados", message)
        self.assertIn("a.txt", message)
        self.assertTrue(os.path.exists(os.path.join(directory, "b.png")))

    def test_dry_run_does_not_record_undo(self):
        action = DesktopAction(
            ActionType.ORGANIZE_FILES, self.tmp.name,
            {"directory": self.tmp.name, "dry_run": True},
        )
        self.executor.execute_plan(self._plan(action), dry_run=True)
        self.assertEqual(len(self.executor.undo_stack), 0)

    def test_irreversible_action_is_not_recorded(self):
        """Prometer desfazer clique ou digitação seria uma promessa falsa."""
        self._run(DesktopAction(ActionType.ANSWER, "só uma resposta"))
        self.assertEqual(len(self.executor.undo_stack), 0)

    def test_failed_write_is_not_recorded(self):
        action = DesktopAction(
            ActionType.WRITE_FILE, "x.md",
            {"filename": "x.md", "content": "oi", "directory": "/proc/nao-existe/x"},
        )
        report = self._run(action)
        self.assertFalse(report.success)
        self.assertEqual(len(self.executor.undo_stack), 0)


class FileManagerContractTest(unittest.TestCase):
    def test_resolve_target_path_matches_write_document(self):
        """O snapshot só serve se o caminho previsto for o mesmo da escrita."""
        with tempfile.TemporaryDirectory() as tmp:
            expected = FileManager.resolve_target_path("relatorio", tmp)
            _, _, written = FileManager.write_document(
                "relatorio", "conteudo", directory=tmp
            )
            self.assertEqual(expected, written)

    def test_organize_records_moves_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.txt"), "w") as fh:
                fh.write("x")
            moves: list[tuple[str, str]] = []
            FileManager.organize_directory(directory=tmp, moves=moves)
            self.assertEqual(len(moves), 1)
            source, destination = moves[0]
            self.assertTrue(source.endswith("a.txt"))
            self.assertTrue(os.path.exists(destination))
            self.assertFalse(os.path.exists(source))

    def test_organize_without_moves_argument_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "a.txt"), "w") as fh:
                fh.write("x")
            ok, _msg, stats = FileManager.organize_directory(directory=tmp)
            self.assertTrue(ok)
            self.assertIn("Documentos", stats)


@unittest.skipUnless(HAS_DISPLAY, "requer servidor gráfico")
class WindowUndoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.undo")

    def setUp(self):
        self.win = CopilotWindow(self.app)
        self.win.executor.undo_stack.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_palette_hides_undo_when_stack_is_empty(self):
        names = {c.name for c in self.win.palette_commands()}
        self.assertNotIn("app.undo-action", names)

    def test_palette_offers_undo_after_reversible_action(self):
        path = os.path.join(self.tmp.name, "r.md")
        with open(path, "w") as fh:
            fh.write("antes")

        self.win.execute_plan_with_undo(ActionPlan(thought="", actions=[
            DesktopAction(ActionType.WRITE_FILE, "r.md", {
                "filename": "r.md", "content": "depois", "directory": self.tmp.name,
            }),
        ]))

        commands = {c.name: c for c in self.win.palette_commands()}
        self.assertIn("app.undo-action", commands)
        self.assertIn("r.md", commands["app.undo-action"].title)

        self.win.undo_last_action()
        with open(path) as fh:
            self.assertEqual(fh.read(), "antes")
        self.assertNotIn("app.undo-action", {c.name for c in self.win.palette_commands()})

    def test_undo_shortcut_does_not_steal_text_undo(self):
        """Com o foco no campo, Ctrl+Z continua sendo desfazer a digitação."""
        self.win.entry.grab_focus()
        self.assertFalse(self.win._on_undo_shortcut())

    def test_undo_on_empty_stack_does_not_crash(self):
        self.win.undo_last_action()  # só não pode levantar

    def test_undone_row_shows_its_own_state(self):
        """Desfeito não é sucesso nem falha — a linha precisa dizer isso."""
        row = Adw.ActionRow(title="Ação", subtitle="Salvar documento")
        btn = Gtk.Button(label="Executar")
        outcome = ActionOutcome(success=True, message="conteúdo restaurado", undone=True)

        self.win.chat_stream._apply_outcome_to_row(row, btn, outcome)

        self.assertEqual(btn.get_label(), "Desfeito ↩")
        self.assertFalse(btn.get_sensitive())
        self.assertIn("conteúdo restaurado", row.get_subtitle())


if __name__ == "__main__":
    unittest.main()
