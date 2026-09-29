"""Testes para port_termination: confirmação de liberação de porta.

Roda via `PYTHONPATH=src python -m unittest tests.test_port_termination -v`
(não usa pytest aqui). O teste destrutivo usa um subprocess-filho próprio —
nunca um PID de sistema.
"""

import os
import socket
import subprocess
import sys
import unittest

from zorin_copilot.core.port_termination import (
    Messages,
    PidState,
    PortState,
    PortTerminationReport,
    _try_bind_target,
    confirm_port_freed,
    wait_until_port_freed,
)


def _make_report(**overrides) -> PortTerminationReport:
    """Constrói um PortTerminationReport para testar summary/next_steps."""
    base = dict(
        port=8080,
        protocol="tcp",
        expected_pid=1234,
        expected_process="caddy",
        ip_binding="0.0.0.0",
        pid_state=PidState.GONE,
        pid_actual=None,
        port_state=PortState.FREE,
        occupying_pid=None,
        evidence={},
        elapsed_ms=1.0,
    )
    base.update(overrides)
    return PortTerminationReport(**base)


def _free_tcp_port() -> int:
    """Pega uma porta TCP livre do SO (ephemeral) e libera imediatamente."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _local_lan_ip() -> str | None:
    """Descobre um IP de LAN não-loopback, se houver (não envia pacotes)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip if ip and ip != "127.0.0.1" else None
    except Exception:
        return None


class BindTestCase(unittest.TestCase):
    """Verifica _try_bind_target em estados livre e ocupado (IPv4 loopback)."""

    def test_free_port_returns_free(self):
        port = _free_tcp_port()
        state, msg = _try_bind_target(port, "tcp", socket.AF_INET, "127.0.0.1")
        self.assertEqual(state, PortState.FREE, msg)

    def test_occupied_port_returns_occupied(self):
        port = _free_tcp_port()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(1)
            state, msg = _try_bind_target(port, "tcp", socket.AF_INET, "127.0.0.1")
            self.assertEqual(state, PortState.OCCUPIED, msg)
        finally:
            listener.close()


class ConfirmPortFreedCase(unittest.TestCase):
    """confirm_port_freed sem pid (somente porta)."""

    def test_returns_report_with_free_state(self):
        port = _free_tcp_port()
        report = confirm_port_freed(port, "tcp")
        self.assertTrue(report.freed)
        self.assertEqual(report.port, port)
        self.assertEqual(report.protocol, "tcp")
        self.assertIn("Porta", report.summary)
        self.assertTrue(report.next_steps)

    def test_returns_report_with_occupied_state(self):
        port = _free_tcp_port()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(1)
            report = confirm_port_freed(port, "tcp")
            self.assertFalse(report.freed)
            self.assertEqual(report.port_state, PortState.OCCUPIED)
        finally:
            listener.close()


class IPv6Case(unittest.TestCase):
    """S2: portas IPv6 precisam ser detectadas quando ip_binding é informado."""

    def test_ipv6_listener_detected_with_ip_binding(self):
        if not socket.has_ipv6:
            self.skipTest("sem suporte a IPv6")
        listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("::1", 0))
            except OSError:
                self.skipTest("loopback IPv6 (::1) indisponível")
            listener.listen(1)
            port = listener.getsockname()[1]
            report = confirm_port_freed(port, "tcp", ip_binding="::1")
            self.assertEqual(report.port_state, PortState.OCCUPIED, report.summary)
        finally:
            listener.close()


class MultiHomedCase(unittest.TestCase):
    """S2: porta amarrada em IP concreto deve ser detectada; loopback a ignora."""

    def test_specific_binding_detected_but_loopback_misses(self):
        lan = _local_lan_ip()
        if not lan:
            self.skipTest("sem IP de LAN disponível")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((lan, 0))
            except OSError:
                self.skipTest(f"não foi possível bindar em {lan}")
            listener.listen(1)
            port = listener.getsockname()[1]

            # Com ip_binding correto -> detecta ocupada.
            rep_lan = confirm_port_freed(port, "tcp", ip_binding=lan)
            self.assertEqual(rep_lan.port_state, PortState.OCCUPIED, rep_lan.summary)

            # No loopback (default) -> aparece livre: exatamente o gap da S2.
            rep_lo = confirm_port_freed(port, "tcp", ip_binding="127.0.0.1")
            self.assertTrue(rep_lo.freed)
        finally:
            listener.close()


class WaitUntilPortFreedCase(unittest.TestCase):
    """Polling e saída por timeout."""

    def test_returns_quickly_when_already_free(self):
        port = _free_tcp_port()
        report = wait_until_port_freed(port, "tcp", timeout_sec=0.5)
        self.assertTrue(report.freed)
        self.assertLess(report.elapsed_ms, 500)

    def test_times_out_when_still_occupied(self):
        port = _free_tcp_port()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            listener.listen(1)
            report = wait_until_port_freed(port, "tcp", timeout_sec=0.3, poll_interval=0.05)
            self.assertFalse(report.freed)
            self.assertEqual(report.port_state, PortState.OCCUPIED)
        finally:
            listener.close()


class PidClassificationCase(unittest.TestCase):
    """classify_pid_state em casos seguros (sem encerrar nada)."""

    def test_pid_zero_is_unknown(self):
        from zorin_copilot.core.port_termination import classify_pid_state
        state, _ = classify_pid_state(0)
        self.assertEqual(state, PidState.UNKNOWN)

    def test_nonexistent_pid_is_gone(self):
        from zorin_copilot.core.port_termination import classify_pid_state
        state, _ = classify_pid_state(2**22)  # PID improvável de existir
        self.assertEqual(state, PidState.GONE)


class PrivilegedPortCase(unittest.TestCase):
    """S3: porta privilegiada não deve lançar; estado precisa ser válido."""

    def test_privileged_port_returns_valid_state(self):
        if os.geteuid() == 0:
            self.skipTest("rodando como root — bind em :80 seria permitido")
        report = confirm_port_freed(80, "tcp", ip_binding="0.0.0.0")
        self.assertIn(
            report.port_state,
            (PortState.PRIVILEGE, PortState.OCCUPIED, PortState.FREE),
        )


class DestructiveConfirmationCase(unittest.TestCase):
    """S4: encerra um subprocess-filho PRÓPRIO e confirma a liberação ponta a ponta."""

    def test_kill_child_then_port_confirmed_freed(self):
        code = (
            "import socket,time\n"
            "s=socket.socket()\n"
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
            "s.bind(('127.0.0.1',0));s.listen(1)\n"
            "print(s.getsockname()[1],flush=True)\n"
            "time.sleep(30)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            line = (proc.stdout.readline() or "").strip()
            self.assertTrue(line.isdigit(), f"filho não reportou porta: {line!r}")
            port = int(line)

            # Antes de matar: porta ocupada.
            before = confirm_port_freed(port, "tcp", expected_pid=proc.pid)
            self.assertFalse(before.freed)
            self.assertEqual(before.port_state, PortState.OCCUPIED)

            # Encerra e confirma.
            proc.terminate()
            proc.wait(timeout=5)
            after = wait_until_port_freed(port, "tcp", expected_pid=proc.pid, timeout_sec=2.0)
            self.assertTrue(after.freed, after.summary)
            self.assertEqual(after.pid_state, PidState.GONE)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()


class MessagesCase(unittest.TestCase):
    """S9: mensagens de usuário centralizadas no catálogo `Messages`."""

    def test_catalog_constants_are_non_empty_strings(self):
        found = 0
        for name in dir(Messages):
            if name.isupper():
                value = getattr(Messages, name)
                self.assertIsInstance(value, str, name)
                self.assertTrue(value.strip(), f"mensagem vazia: {name}")
                found += 1
        self.assertGreater(found, 0, "o catálogo Messages está vazio")

    def test_summary_freed_uses_catalog(self):
        report = _make_report()
        expected = Messages.PORT_FREED.format(port=8080, protocol="tcp", ms=1.0)
        self.assertEqual(report.summary, expected + Messages.PID_TERMINATED.format(pid=1234))

    def test_summary_still_alive_uses_catalog(self):
        report = _make_report(pid_state=PidState.STILL_ALIVE, port_state=PortState.OCCUPIED)
        self.assertEqual(report.summary, Messages.PID_STILL_ALIVE.format(pid=1234))

    def test_summary_occupied_with_occupant_uses_catalog(self):
        report = _make_report(port_state=PortState.OCCUPIED, occupying_pid=4321)
        expected = Messages.PORT_OCCUPIED.format(
            port=8080, protocol="tcp", occupant=Messages.OCCUPANT_SUFFIX.format(pid=4321),
        )
        self.assertEqual(report.summary, expected)

    def test_next_steps_freed_uses_catalog(self):
        self.assertEqual(_make_report().next_steps, [Messages.STEP_PORT_FREED])

    def test_next_steps_sigkill_uses_catalog(self):
        report = _make_report(pid_state=PidState.STILL_ALIVE, port_state=PortState.OCCUPIED)
        self.assertIn(Messages.STEP_SIGKILL, report.next_steps)
        self.assertIn(Messages.STEP_INVESTIGATE_SIGTERM, report.next_steps)

    def test_next_steps_reused_pid_uses_catalog(self):
        report = _make_report(
            pid_state=PidState.DIFFERENT_PROCESS, pid_actual="other", port_state=PortState.OCCUPIED,
        )
        self.assertIn(Messages.STEP_PID_REUSED.format(pid=1234, name="other"), report.next_steps)


if __name__ == "__main__":
    unittest.main()