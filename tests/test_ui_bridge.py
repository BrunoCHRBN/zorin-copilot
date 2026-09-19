"""Testes unitários para o despachante thread-safe de UI (UIThreadBridge)."""

from __future__ import annotations

import threading
import time
import unittest
from zorin_copilot.ui.bridge import UIThreadBridge, post_to_ui, call_on_ui_sync, drain_ui_events


class TestUIThreadBridge(unittest.TestCase):
    def setUp(self):
        UIThreadBridge.mark_main_thread()
        drain_ui_events()

    def tearDown(self):
        drain_ui_events()

    def test_is_ui_thread(self):
        self.assertTrue(UIThreadBridge.is_ui_thread())

        is_subthread = []

        def worker():
            is_subthread.append(UIThreadBridge.is_ui_thread())

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertEqual(is_subthread, [False])

    def test_post_to_ui_execution(self):
        executed = []

        def callback(val: str, number: int):
            executed.append((val, number))

        post_to_ui(callback, "teste", number=42)
        drain_ui_events()
        self.assertEqual(executed, [("teste", 42)])

    def test_call_on_ui_sync_from_main(self):
        def add(a: int, b: int) -> int:
            return a + b

        res = call_on_ui_sync(add, 10, 20)
        self.assertEqual(res, 30)

    def test_call_on_ui_sync_from_worker_thread(self):
        result_holder = []

        def calculate(x: int) -> int:
            return x * 2

        def worker():
            res = call_on_ui_sync(calculate, 21)
            result_holder.append(res)

        t = threading.Thread(target=worker)
        t.start()

        # O main thread (thread dona da UI) processa eventos enquanto a trabalhadora aguarda
        start = time.time()
        while t.is_alive() and (time.time() - start) < 3.0:
            drain_ui_events()
            time.sleep(0.01)

        t.join(timeout=1.0)
        self.assertEqual(result_holder, [42])


if __name__ == "__main__":
    unittest.main()
