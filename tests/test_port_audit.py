"""Testes para port_audit: watcher de transições + export Prometheus.

Roda via `PYTHONPATH=src python -m unittest tests.test_port_audit -v`.
Usa um checker FAKE injetado — não toca em portas reais.
"""

import time
import unittest
from types import SimpleNamespace

from zorin_copilot.core.port_audit import (
    PortAuditEventKind,
    PortAuditWatcher,
    PortSpec,
    check_once,
    to_prometheus,
)


def _report(freed: bool, port: int = 8080, protocol: str = "tcp") -> SimpleNamespace:
    return SimpleNamespace(
        freed=freed,
        summary=f"porta {port}/{protocol} {'livre' if freed else 'ocupada'}",
        port=port,
        protocol=protocol,
    )


class FakeChecker:
    """Devolve valores de uma sequência (repete o último) e registra chamadas."""

    def __init__(self, sequence):
        self._seq = list(sequence)
        self._i = 0
        self.calls: list[tuple] = []

    def __call__(self, port, protocol="tcp", expected_process=None, ip_binding=None):
        self.calls.append((port, protocol))
        value = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return _report(value, port, protocol)


class TransitionCase(unittest.TestCase):
    def test_first_poll_establishes_baseline_without_event(self):
        watcher = PortAuditWatcher([PortSpec(8080)], checker=FakeChecker([True]))
        self.assertEqual(watcher.poll(), [])

    def test_became_free_then_occupied(self):
        # baseline ocupada -> livre -> ocupada
        watcher = PortAuditWatcher([PortSpec(8080)], checker=FakeChecker([False, True, False]))
        self.assertEqual(watcher.poll(), [])  # baseline

        events_free = watcher.poll()
        self.assertEqual(len(events_free), 1)
        self.assertEqual(events_free[0].kind, PortAuditEventKind.BECAME_FREE)

        events_occ = watcher.poll()
        self.assertEqual(len(events_occ), 1)
        self.assertEqual(events_occ[0].kind, PortAuditEventKind.BECAME_OCCUPIED)

    def test_unchanged_emits_nothing(self):
        watcher = PortAuditWatcher([PortSpec(8080)], checker=FakeChecker([True, True, True]))
        watcher.poll()
        watcher.poll()
        self.assertEqual(watcher.poll(), [])

    def test_multiple_ports_independent(self):
        specs = [PortSpec(8080), PortSpec(22)]
        # 8080: livre->ocupada ; 22: livre->livre
        # sequência global por chamada: poll1: [T,T]; poll2: [F,T]
        watcher = PortAuditWatcher(specs, checker=FakeChecker([True, True, False, True]))
        watcher.poll()  # baseline
        events = watcher.poll()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].spec.port, 8080)
        self.assertEqual(events[0].kind, PortAuditEventKind.BECAME_OCCUPIED)


class RunLoopCase(unittest.TestCase):
    def test_run_emits_and_stops(self):
        watcher = PortAuditWatcher(
            [PortSpec(8080)], checker=FakeChecker([True, False]), interval=0.5,
        )
        watcher.poll()  # baseline: livre
        received: list = []
        thread = watcher.start(received.append)
        time.sleep(0.7)
        watcher.stop()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive(), "a thread de auditoria não parou")
        self.assertTrue(
            any(e.kind == PortAuditEventKind.BECAME_OCCUPIED for e in received),
            f"esperava evento BECAME_OCCUPIED; recebido: {received}",
        )


class ExportCase(unittest.TestCase):
    def test_check_once_keys(self):
        reports = check_once([PortSpec(8080), PortSpec(22)], checker=FakeChecker([True, False]))
        self.assertEqual(set(reports.keys()), {(8080, "tcp"), (22, "tcp")})

    def test_prometheus_text_format(self):
        reports = check_once([PortSpec(8080), PortSpec(22)], checker=FakeChecker([True, False]))
        text = to_prometheus(reports)
        self.assertIn("# TYPE zorin_port_free gauge", text)
        self.assertIn('zorin_port_free{port="8080",protocol="tcp"} 1', text)
        self.assertIn('zorin_port_free{port="22",protocol="tcp"} 0', text)
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()