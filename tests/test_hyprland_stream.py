"""Testes unitários para o motor de streaming do Hyprland e TemporalRingBuffer."""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from zorin_copilot.core.hyprland_stream import (
    HyprlandEvent,
    HyprlandStream,
    StreamState,
    TemporalRingBuffer,
)


class TestHyprlandStream(unittest.TestCase):
    def test_parse_activewindow_event(self):
        line = "activewindow>>code,main.py - zorin-copilot - Visual Studio Code"
        ev = HyprlandEvent.from_raw_line(line)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.name, "activewindow")
        self.assertEqual(ev.app, "code")
        self.assertEqual(ev.title, "main.py - zorin-copilot - Visual Studio Code")
        self.assertFalse(ev.is_sensitive)

    def test_parse_workspace_event(self):
        line = "workspace>>3"
        ev = HyprlandEvent.from_raw_line(line)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.name, "workspace")
        self.assertEqual(ev.workspace, "3")

    def test_privacy_gatekeeper_blocks_sensitive_window_in_stream(self):
        sensitive_line = "activewindow>>bitwarden,My Secret Passwords - Bitwarden"
        ev = HyprlandEvent.from_raw_line(sensitive_line)
        self.assertIsNotNone(ev)
        self.assertTrue(ev.is_sensitive)
        self.assertEqual(ev.app, "<REDACTED_APP>")
        self.assertEqual(ev.title, "<REDACTED_TITLE>")
        self.assertNotIn("Secret Passwords", ev.raw)

    def test_ring_buffer_capacity_and_fifo(self):
        buf = TemporalRingBuffer(capacity=3)
        ev1 = HyprlandEvent.from_raw_line("workspace>>1")
        ev2 = HyprlandEvent.from_raw_line("workspace>>2")
        ev3 = HyprlandEvent.from_raw_line("workspace>>3")
        ev4 = HyprlandEvent.from_raw_line("workspace>>4")

        buf.append(ev1)
        buf.append(ev2)
        buf.append(ev3)
        self.assertEqual(len(buf), 3)

        buf.append(ev4)
        self.assertEqual(len(buf), 3)
        recent = buf.get_all()
        self.assertEqual([e.workspace for e in recent], ["2", "3", "4"])

    def test_hyprland_stream_with_mock_socket_server(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = Path(tmpdir) / ".mock_socket2.sock"

            # 1. Cria servidor socket UNIX mock
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(sock_path))
            server.listen(1)

            server_ready = threading.Event()
            events_received = []

            def server_worker():
                server_ready.set()
                conn, _ = server.accept()
                try:
                    conn.sendall(b"workspace>>1\n")
                    time.sleep(0.05)
                    conn.sendall(b"activewindow>>kitty,terminal bash\n")
                    time.sleep(0.05)
                    conn.sendall(b"activewindow>>bitwarden,Vault\n")
                    time.sleep(0.1)
                finally:
                    conn.close()

            t = threading.Thread(target=server_worker, daemon=True)
            t.start()
            server_ready.wait(timeout=2.0)

            # 2. Inicia o cliente HyprlandStream apontando para o socket mock
            stream = HyprlandStream(buffer_capacity=10, custom_socket_path=sock_path)
            stream.add_listener(lambda ev: events_received.append(ev))
            stream.start()

            # Aguarda recepção dos eventos
            time.sleep(0.4)
            stream.stop()
            server.close()
            t.join(timeout=1.0)

            self.assertGreaterEqual(len(events_received), 3)
            self.assertEqual(events_received[0].workspace, "1")
            self.assertEqual(events_received[1].app, "kitty")
            # Terceiro evento deve ser mascarado pelo Gatekeeper
            self.assertTrue(events_received[2].is_sensitive)
            self.assertEqual(events_received[2].app, "<REDACTED_APP>")


if __name__ == "__main__":
    unittest.main()
