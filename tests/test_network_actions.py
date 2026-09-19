"""Testes unitários para o motor operacional de ações de rede (network_actions.py)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.core.network_actions import (
    PROTECTED_PROCESS_NAMES,
    _build_dns_a_query,
    _normalize_mac,
    benchmark_dns,
    probe_service,
    release_local_port,
    wake_on_lan,
)


class TestNetworkActions(unittest.TestCase):
    def test_normalize_mac_valid(self):
        cases = [
            ("44:89:6D:79:4E:20", b"\x44\x89\x6d\x79\x4e\x20"),
            ("ec-fa-bc-11-22-33", b"\xec\xfa\xbc\x11\x22\x33"),
            ("1c5a6b6e9f8f", b"\x1c\x5a\x6b\x6e\x9f\x8f"),
        ]
        for mac_str, expected in cases:
            self.assertEqual(_normalize_mac(mac_str), expected)

    def test_normalize_mac_invalid(self):
        with self.assertRaises(ValueError):
            _normalize_mac("44:89:6D:79")
        with self.assertRaises(ValueError):
            _normalize_mac("invalid_mac_address")

    def test_wake_on_lan_packet_dispatch(self):
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock_cls.return_value.__enter__.return_value = mock_sock

            ok, msg = wake_on_lan("44:89:6d:79:4e:20", burst_count=2)
            self.assertTrue(ok)
            self.assertIn("enviado", msg)
            self.assertEqual(mock_sock.sendto.call_count, 2)

            # Verifica integridade do payload do Magic Packet (102 bytes)
            sent_packet, target = mock_sock.sendto.call_args[0]
            self.assertEqual(len(sent_packet), 102)
            self.assertEqual(sent_packet[:6], b"\xff" * 6)
            self.assertEqual(target, ("255.255.255.255", 9))

    def test_wake_on_lan_invalid_mac(self):
        ok, msg = wake_on_lan("invalid")
        self.assertFalse(ok)
        self.assertIn("inválido", msg)

    def test_release_port_invalid_port(self):
        ok, msg, _ = release_local_port(0)
        self.assertFalse(ok)
        ok2, msg2, _ = release_local_port(70000)
        self.assertFalse(ok2)

    def test_release_port_protected_process(self):
        # Simula tentativa de matar um processo do compositor Hyprland ou systemd
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", unittest.mock.mock_open(read_data=b"/usr/bin/hyprland\x00")):
                with patch("os.stat") as mock_stat:
                    mock_stat.return_value.st_uid = os.getuid()
                    ok, msg, meta = release_local_port(8080, target_pid=9999)
                    self.assertFalse(ok)
                    self.assertIn("bloqueada por segurança", msg)
                    self.assertIn("vital ao sistema", msg)

    def test_release_port_wrong_uid(self):
        # Simula processo que pertence ao root (UID 0) quando usuário é comum
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", unittest.mock.mock_open(read_data=b"/usr/bin/caddy\x00")):
                with patch("os.stat") as mock_stat:
                    mock_stat.return_value.st_uid = 0  # root
                    if os.getuid() != 0:
                        ok, msg, meta = release_local_port(8080, target_pid=9998)
                        self.assertFalse(ok)
                        self.assertIn("Permissão insuficiente", msg)
                        self.assertIn("sudo kill", msg)

    def test_probe_service_closed_port(self):
        # Porta fechada em localhost deve retornar refused ou erro sem lançar exceção
        res = probe_service("127.0.0.1", 58912, timeout_sec=0.2)
        self.assertFalse(res["ok"])
        self.assertIn(res["status"], ("refused", "timeout", "error"))

    def test_probe_service_invalid_port(self):
        res = probe_service("127.0.0.1", -1)
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "invalid_port")

    def test_build_dns_a_query(self):
        packet = _build_dns_a_query("archlinux.org", tx_id=0x1234)
        self.assertIsInstance(packet, bytes)
        self.assertGreater(len(packet), 20)
        # ID 0x1234
        self.assertEqual(packet[:2], b"\x12\x34")
        # Contém rótulo 'archlinux' (9 bytes) e 'org' (3 bytes)
        self.assertIn(b"\x09archlinux\x03org\x00", packet)

    def test_benchmark_dns_structure(self):
        # Testa benchmark com resolvedor simulado
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock_cls.return_value.__enter__.return_value = mock_sock
            # Retorna um pacote DNS válido fictício (12 bytes de header)
            mock_sock.recvfrom.return_value = (b"\x12\x34\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00", ("1.1.1.1", 53))

            results = benchmark_dns(
                domains=["archlinux.org"],
                servers={"Cloudflare": "1.1.1.1", "Google": "8.8.8.8"},
                timeout_sec=0.2,
            )
            self.assertEqual(len(results), 2)
            self.assertIn("avg_latency_ms", results[0])
            self.assertIn("provider", results[0])
            self.assertTrue(any(r["is_fastest"] for r in results))


if __name__ == "__main__":
    unittest.main()
