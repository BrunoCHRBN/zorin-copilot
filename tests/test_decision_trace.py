"""Rastro de decisão (`core/decision_trace.py`) — item #4 / §6.2.

Módulo puro: thread-safety, serialização round-trip, contexto ativo e no-op sem
trace. Tudo determinístico, sem rede e sem display.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, ROOT)

from zorin_copilot.core.decision_trace import (  # noqa: E402
    DecisionTrace,
    get_trace,
    record,
    reset_trace,
    set_trace,
)


class RecordTest(unittest.TestCase):
    def test_records_in_order(self):
        t = DecisionTrace(root="test")
        t.record("a", "dispatch", x=1)
        t.record("b", "match", y=2)
        self.assertEqual(t.steps(), ["a", "b"])
        self.assertEqual(len(t), 2)
        entries = t.as_dict()["entries"]
        self.assertEqual(entries[0]["data"], {"x": 1})
        self.assertEqual(entries[1]["kind"], "match")

    def test_trace_id_generated_and_stable(self):
        t = DecisionTrace()
        self.assertTrue(t.trace_id)
        self.assertEqual(t.trace_id, t.as_dict()["trace_id"])

    def test_explicit_trace_id(self):
        t = DecisionTrace(trace_id="abc123")
        self.assertEqual(t.trace_id, "abc123")


class SerializationTest(unittest.TestCase):
    def test_to_json_and_from_dict_roundtrip(self):
        t = DecisionTrace(trace_id="z", root="reasoning")
        t.record("try_solve", "dispatch", problem="x^2 - 1 = 0")
        t.record("parse_quadratic", "match", a=1.0, b=0.0, c=-1.0)
        restored = DecisionTrace.from_dict(json.loads(t.to_json()))
        self.assertEqual(restored.trace_id, "z")
        self.assertEqual(restored.steps(), ["try_solve", "parse_quadratic"])
        self.assertEqual(restored.as_dict()["entries"][1]["data"]["a"], 1.0)

    def test_from_dict_rejects_future_version(self):
        with self.assertRaises(ValueError):
            DecisionTrace.from_dict({"version": 999, "entries": []})


class ConcurrencyTest(unittest.TestCase):
    def test_concurrent_records_are_not_lost(self):
        t = DecisionTrace()
        n_threads, per_thread = 8, 50

        def worker(i):
            for j in range(per_thread):
                t.record(f"step{i}", "work", j=j)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(len(t), n_threads * per_thread)


class ActiveTraceTest(unittest.TestCase):
    def test_set_get_reset(self):
        self.assertIsNone(get_trace())
        t = DecisionTrace()
        token = set_trace(t)
        try:
            self.assertIs(get_trace(), t)
            record("x", "k", v=1)
            self.assertEqual(t.steps(), ["x"])
        finally:
            reset_trace(token)
        self.assertIsNone(get_trace())

    def test_record_without_active_trace_is_noop(self):
        self.assertIsNone(get_trace())
        record("x", "k")  # não deve levantar

    def test_record_goes_to_active_trace_only(self):
        a, b = DecisionTrace(), DecisionTrace()
        token = set_trace(a)
        try:
            record("only_a", "k")
        finally:
            reset_trace(token)
        self.assertEqual(a.steps(), ["only_a"])
        self.assertEqual(b.steps(), [])


if __name__ == "__main__":
    unittest.main()
