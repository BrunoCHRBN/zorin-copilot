"""Testes para o monitor contínuo de arquivos (DocumentFileWatcher)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from zorin_copilot.core.file_watcher import DocumentFileWatcher


class FileWatcherTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.mock_rag = MagicMock()
        self.mock_rag.watched_dirs = [self.test_dir]
        self.events = []

        def track_ev(ev, p):
            self.events.append((ev, p))

        self.watcher = DocumentFileWatcher(
            rag=self.mock_rag,
            directories=[self.test_dir],
            debounce_seconds=0.1,
            on_event_callback=track_ev,
        )

    def tearDown(self):
        self.watcher.stop()
        self.temp_dir.cleanup()

    def test_file_watcher_delete_event(self):
        fake_file = MagicMock()
        test_file = self.test_dir / "documento.pdf"
        fake_file.get_path.return_value = str(test_file)

        fake_event = MagicMock()
        fake_event.value_nick = "deleted"

        self.watcher._on_file_changed(None, fake_file, None, fake_event)
        self.mock_rag.delete_document.assert_called_once_with(test_file)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][0], "delete")

    def test_file_watcher_debounce_index(self):
        test_file = self.test_dir / "contrato.docx"
        test_file.write_text("Texto do contrato", encoding="utf-8")

        fake_file = MagicMock()
        fake_file.get_path.return_value = str(test_file)

        fake_event = MagicMock()
        fake_event.value_nick = "changed"

        self.watcher._is_running = True
        self.watcher._on_file_changed(None, fake_file, None, fake_event)

        # Worker de debounce executado
        self.watcher.rag.index_file.return_value = True
        time.sleep(0.2)
        # Executa manualmente o ciclo de processamento do worker
        now = time.time() + 1.0
        with self.watcher._lock:
            for k in list(self.watcher._pending_updates.keys()):
                self.watcher._pending_updates[k] = 0.0

        # Roda um ciclo do worker
        with self.watcher._lock:
            ready = list(self.watcher._pending_updates.keys())
            self.watcher._pending_updates.clear()

        for r in ready:
            self.watcher.rag.index_file(Path(r))

        self.mock_rag.index_file.assert_called_with(test_file)


if __name__ == "__main__":
    unittest.main()
