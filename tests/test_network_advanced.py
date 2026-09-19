"""Testes unitários avançados para as capacidades de introspecção e diagnóstico de rede."""

from __future__ import annotations

import datetime
import os
import unittest
from unittest.mock import MagicMock, mock_open, patch

from zorin_copilot.core.network_actions import (
    audit_attack_surface,
    check_ssl_certificate,
    diagnose_connection_path,
    discover_mdns_services,
    measure_throughput,
)
from zorin_copilot.core.network_inspector import ListeningPort


class TestNetworkAdvanced(unittest.TestCase):
    def test_audit_attack_surface_safe_loopback(self):
        fake_ports = [
            ListeningPort(protocol="tcp", ip_binding="127.0.0.1", port=5432, process_name="postgres", pid=100),
            ListeningPort(protocol="tcp", ip_binding="::1", port=6379, process_name="redis-server", pid=101),
        ]
        with patch("zorin_copilot.core.network_inspector.NetworkInspector.get_listening_ports", return_value=fake_ports):
            report = audit_attack_surface()
            self.assertEqual(report["total_listening"], 2)
            self.assertEqual(report["exposed_count"], 0)
            self.assertEqual(report["high_risk_warnings"], [])
            self.assertIn("Seguro", report["verdict"])
            for p in report["ports"]:
                self.assertEqual(p["exposure"], "SAFE_LOOPBACK")

    def test_audit_attack_surface_exposed_dev_ports(self):
        fake_ports = [
            ListeningPort(protocol="tcp", ip_binding="0.0.0.0", port=5173, process_name="node", pid=200),
            ListeningPort(protocol="tcp", ip_binding="::", port=8080, process_name="caddy", pid=201),
        ]
        with patch("zorin_copilot.core.network_inspector.NetworkInspector.get_listening_ports", return_value=fake_ports):
            report = audit_attack_surface()
            self.assertEqual(report["total_listening"], 2)
            self.assertEqual(report["exposed_count"], 2)
            self.assertGreater(len(report["high_risk_warnings"]), 0)
            self.assertIn("5173", report["high_risk_warnings"][0])
            self.assertEqual(report["ports"][0]["exposure"], "EXPOSED_LAN")
            self.assertEqual(report["ports"][1]["exposure"], "DUAL_STACK_EXPOSED")

    def test_measure_throughput_missing_sysfs(self):
        with patch("os.path.exists", return_value=False):
            res = measure_throughput(interface_name="eth_fake")
            self.assertIn("error", res)
            self.assertEqual(res["rx_kb_s"], 0.0)
            self.assertEqual(res["tx_kb_s"], 0.0)

    def test_measure_throughput_success(self):
        # Simula leitura de rx/tx_bytes: primeira leitura 10000/20000, segunda leitura 110240/120480
        rx_values = ["10000", "112400"]
        tx_values = ["20000", "71200"]

        def fake_open(filepath, mode="r"):
            if "rx_bytes" in filepath:
                val = rx_values.pop(0) if rx_values else "112400"
            else:
                val = tx_values.pop(0) if tx_values else "71200"
            return mock_open(read_data=val)()

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", side_effect=fake_open), \
             patch("time.monotonic", side_effect=[0.0, 1.0]), \
             patch("time.sleep", return_value=None):
            res = measure_throughput(interface_name="enp4s0", sample_seconds=0.2)
            self.assertNotIn("error", res)
            self.assertEqual(res["interface"], "enp4s0")
            self.assertGreater(res["rx_kb_s"], 0.0)
            self.assertGreater(res["tx_kb_s"], 0.0)
            self.assertAlmostEqual(res["sample_seconds"], 1.0)

    def test_diagnose_carrier_failure(self):
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="0")), \
             patch("zorin_copilot.core.network_inspector.NetworkInspector.get_default_gateway", return_value=("192.168.1.1", "eth0")):
            res = diagnose_connection_path()
            self.assertFalse(res["ok"])
            self.assertIn("Enlace Físico", res["failing_stage"])
            self.assertFalse(res["stages"]["1_physical_carrier"])

    def test_diagnose_gateway_failure(self):
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="1")), \
             patch("zorin_copilot.core.network_inspector.NetworkInspector.get_default_gateway", return_value=("192.168.1.1", "eth0")), \
             patch("subprocess.run") as mock_proc:
            mock_proc.return_value.returncode = 1  # ping falhou
            res = diagnose_connection_path()
            self.assertFalse(res["ok"])
            self.assertIn("Gateway", res["failing_stage"])
            self.assertTrue(res["stages"]["1_physical_carrier"])
            self.assertFalse(res["stages"]["2_local_gateway"])

    def test_diagnose_dns_failure(self):
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="1")), \
             patch("zorin_copilot.core.network_inspector.NetworkInspector.get_default_gateway", return_value=("192.168.1.1", "eth0")), \
             patch("subprocess.run") as mock_proc, \
             patch("socket.socket") as mock_sock_cls, \
             patch("socket.getaddrinfo", side_effect=OSError("DNS unreachable")):
            mock_proc.return_value.returncode = 0  # ping gateway ok
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0  # conexão tcp externa ok
            mock_sock_cls.return_value.__enter__.return_value = mock_sock

            res = diagnose_connection_path()
            self.assertFalse(res["ok"])
            self.assertIn("DNS", res["failing_stage"])
            self.assertTrue(res["stages"]["3_public_route"])
            self.assertFalse(res["stages"]["4_dns_resolution"])

    def test_diagnose_all_success(self):
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="1")), \
             patch("zorin_copilot.core.network_inspector.NetworkInspector.get_default_gateway", return_value=("192.168.1.1", "eth0")), \
             patch("subprocess.run") as mock_proc, \
             patch("socket.socket") as mock_sock_cls, \
             patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("142.250.190.46", 80))]):
            mock_proc.return_value.returncode = 0
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            mock_sock_cls.return_value.__enter__.return_value = mock_sock

            res = diagnose_connection_path()
            self.assertTrue(res["ok"])
            self.assertEqual(res["failing_stage"], "Nenhum")
            self.assertTrue(all(res["stages"].values()))

    def test_check_ssl_certificate_invalid_host(self):
        res = check_ssl_certificate("")
        self.assertFalse(res["ok"])
        self.assertIn("inválido", res["error"])

    def test_check_ssl_certificate_mocked_success(self):
        fake_cert = {
            "notAfter": "May 23 12:00:00 2030 GMT",
            "issuer": ((("organizationName", "Let's Encrypt"),),),
        }
        with patch("socket.socket") as mock_sock_cls, \
             patch("ssl.create_default_context") as mock_ssl_ctx:
            mock_sock = MagicMock()
            mock_sock_cls.return_value.__enter__.return_value = mock_sock

            mock_ssock = MagicMock()
            mock_ssock.getpeercert.return_value = fake_cert
            mock_ssock.version.return_value = "TLSv1.3"
            mock_ssock.cipher.return_value = ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)
            mock_ssl_ctx.return_value.wrap_socket.return_value.__enter__.return_value = mock_ssock

            res = check_ssl_certificate("https://example.org")
            self.assertTrue(res["ok"])
            self.assertEqual(res["host"], "example.org")
            self.assertEqual(res["issuer"], "Let's Encrypt")
            self.assertEqual(res["tls_version"], "TLSv1.3")
            self.assertGreater(res["days_remaining"], 300)
            self.assertFalse(res["is_expired"])

    def test_discover_mdns_services_no_socket(self):
        with patch("os.path.exists", return_value=False):
            res = discover_mdns_services()
            self.assertEqual(res, [])

    def test_discover_mdns_services_parsed(self):
        fake_stdout = (
            "=;enp4s0;IPv4;Home\\032Assistant;_home-assistant._tcp;local;homeassistant.local;192.168.15.20;8123;txt\n"
            "=;enp4s0;IPv4;Living\\032Room\\032Speaker;_googlecast._tcp;local;chromecast.local;192.168.15.25;8009;txt\n"
        )
        with patch("os.path.exists", return_value=True), \
             patch("subprocess.run") as mock_proc:
            mock_proc.return_value.returncode = 0
            mock_proc.return_value.stdout = fake_stdout
            services = discover_mdns_services()
            self.assertEqual(len(services), 2)
            self.assertEqual(services[0]["ip"], "192.168.15.20")
            self.assertEqual(services[0]["port"], 8123)
            self.assertEqual(services[1]["ip"], "192.168.15.25")
            self.assertEqual(services[1]["port"], 8009)


if __name__ == "__main__":
    unittest.main()
