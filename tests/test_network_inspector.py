"""Testes unitários para o módulo de inspeção de infraestrutura de rede (NetworkInspector)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zorin_copilot.core.knowledge_graph import KnowledgeGraph
from zorin_copilot.core.network_inspector import (
    InterfaceType,
    NetworkInspector,
    OUIResolver,
)


class TestNetworkInspector(unittest.TestCase):
    def test_interface_classification(self):
        cases = [
            ("lo", InterfaceType.LOOPBACK),
            ("docker0", InterfaceType.VIRTUAL_CONTAINER),
            ("br-58c0f589cda6", InterfaceType.VIRTUAL_CONTAINER),
            ("veth123abc", InterfaceType.VIRTUAL_CONTAINER),
            ("wg0", InterfaceType.VPN_TUNNEL),
            ("tailscale0", InterfaceType.VPN_TUNNEL),
            ("wlan0", InterfaceType.PHYSICAL_WIFI),
            ("wlp3s0", InterfaceType.PHYSICAL_WIFI),
            ("enp4s0", InterfaceType.PHYSICAL_ETHERNET),
            ("eth0", InterfaceType.PHYSICAL_ETHERNET),
        ]
        for name, expected in cases:
            self.assertEqual(
                NetworkInspector.classify_interface(name),
                expected,
                f"Falha ao classificar '{name}'",
            )

    def test_oui_resolver(self):
        # 1. Roteador Sagemcom
        vendor, dev_type = OUIResolver.resolve("44:89:6d:79:4e:20")
        self.assertIn("Sagemcom", vendor)
        self.assertEqual(dev_type, "router")

        # 2. IoT Espressif (Lâmpada Avant Neo)
        vendor, dev_type = OUIResolver.resolve("ec:fa:bc:12:34:56")
        self.assertIn("Espressif", vendor)
        self.assertEqual(dev_type, "iot")

        # 3. Apple
        vendor, dev_type = OUIResolver.resolve("1c:5a:6b:6e:9f:8f")
        self.assertIn("Apple", vendor)

        # 4. Desconhecido
        vendor, dev_type = OUIResolver.resolve("00:00:00:11:22:33")
        self.assertEqual(dev_type, "unknown")

    def test_get_interfaces(self):
        interfaces = NetworkInspector.get_interfaces()
        self.assertIsInstance(interfaces, list)
        self.assertGreater(len(interfaces), 0)

        # Deve encontrar pelo menos o loopback
        lo = next((i for i in interfaces if i.iface_type == InterfaceType.LOOPBACK), None)
        self.assertIsNotNone(lo)
        self.assertIn("127.0.0.1", lo.ipv4)

    def test_get_default_gateway(self):
        gw_ip, gw_dev = NetworkInspector.get_default_gateway()
        # No EndeavourOS atual há um gateway ativo
        if gw_ip:
            self.assertTrue(gw_ip.startswith("192.") or gw_ip.startswith("10.") or gw_ip.startswith("172."))
            self.assertIsNotNone(gw_dev)

    def test_get_listening_ports(self):
        ports = NetworkInspector.get_listening_ports()
        self.assertIsInstance(ports, list)
        # Portas devem ser ordenadas
        port_numbers = [p.port for p in ports]
        self.assertEqual(port_numbers, sorted(port_numbers))

    def test_get_health_report(self):
        report = NetworkInspector.get_health_report()
        self.assertIsNotNone(report.primary_interface)
        self.assertIsNotNone(report.local_ip)
        self.assertIsInstance(report.dns_servers, list)

    def test_sync_to_knowledge_graph(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_graph.db"
            graph = KnowledgeGraph(db_path=db_path)

            synced = NetworkInspector.sync_to_knowledge_graph(graph=graph)
            self.assertGreaterEqual(synced, 1)

            # Verifica que o nó do host foi registrado
            hosts = graph.find_nodes("HostMachine")
            self.assertGreaterEqual(len(hosts), 1)


if __name__ == "__main__":
    unittest.main()
