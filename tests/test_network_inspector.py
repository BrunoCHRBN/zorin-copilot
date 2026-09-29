"""Testes unitários para o módulo de inspeção de infraestrutura de rede (NetworkInspector)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from zorin_copilot.core.knowledge_graph import KnowledgeGraph
from zorin_copilot.core.network_inspector import (
    DeviceInventory,
    InterfaceType,
    NeighborDevice,
    NetworkInspector,
    OUIResolver,
)

# Amostras de saída nmap (-oX) para testar os parsers sem depender do nmap/rede.
NMAP_SN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<nmaprun>
<host>
<status state="up"/>
<address addr="192.168.5.12" addrtype="ipv4"/>
<address addr="1C:36:BB:11:22:33" addrtype="mac" vendor="Apple, Inc."/>
<hostnames></hostnames>
</host>
<host>
<status state="down"/>
<address addr="192.168.5.50" addrtype="ipv4"/>
<address addr="AA:BB:CC:DD:EE:FF" addrtype="mac"/>
<hostnames></hostnames>
</host>
</nmaprun>
"""

NMAP_OS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<nmaprun>
<host>
<status state="up"/>
<address addr="192.168.5.12" addrtype="ipv4"/>
<address addr="1C:36:BB:11:22:33" addrtype="mac" vendor="Apple, Inc."/>
<hostnames><hostname name="iphone-de-bruno.lan" type="PTR"/></hostnames>
<ports>
<port protocol="tcp" portid="62078"><state state="open"/><service name="iphone-sync" product="Apple iPhone sync" version=""/></port>
<port protocol="tcp" portid="22"><state state="closed"/><service name="ssh"/></port>
<port protocol="udp" portid="5353"><state state="open"/><service name="mdns"/></port>
</ports>
<os>
<osmatch name="Apple iPhone" accuracy="95"><osclass type="phone" vendor="Apple" osfamily="iOS" accuracy="95"/></osmatch>
<osmatch name="Apple iPod touch" accuracy="90"><osclass type="phone" vendor="Apple" osfamily="iOS" accuracy="90"/></osmatch>
</os>
</host>
</nmaprun>
"""


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


class TestNetworkDefense(unittest.TestCase):
    """Testes das capacidades defensivas novas: OUI Apple, scan ativo e fingerprint."""

    def test_oui_resolver_apple_1c36(self):
        # O device da captura do usuário (1C:36:BB) deve resolver como Apple.
        vendor, dev_type = OUIResolver.resolve("1c:36:bb:11:22:33")
        self.assertIn("Apple", vendor)

    def test_parse_nmap_hosts_xml_excludes_down(self):
        devices = NetworkInspector._parse_nmap_hosts_xml(NMAP_SN_XML)
        # Apenas o host 'up' deve ser incluído; o 'down' é descartado.
        self.assertEqual(len(devices), 1)
        dev = devices[0]
        self.assertEqual(dev.ip, "192.168.5.12")
        self.assertEqual(dev.mac, "1C:36:BB:11:22:33")
        self.assertTrue(dev.is_online)
        self.assertIn("Apple", dev.vendor)

    def test_parse_nmap_device_xml_fingerprint(self):
        result = NetworkInspector._parse_nmap_device_xml(NMAP_OS_XML, "192.168.5.12")
        self.assertTrue(result["ok"])
        self.assertTrue(result["is_up"])
        self.assertEqual(result["os_name"], "Apple iPhone")
        self.assertEqual(result["os_family"], "iOS")
        self.assertEqual(result["os_vendor"], "Apple")
        self.assertEqual(result["device_type"], "phone")
        self.assertEqual(result["hostname"], "iphone-de-bruno.lan")
        self.assertEqual(result["mac"], "1C:36:BB:11:22:33")
        # Apenas portas TCP 'open' entram; a 22 (closed) e a UDP 5353 são ignoradas.
        self.assertEqual(len(result["open_ports"]), 1)
        self.assertEqual(result["open_ports"][0]["port"], 62078)
        self.assertEqual(result["open_ports"][0]["service"], "iphone-sync")

    def test_scan_subnet_nmap_missing_returns_empty(self):
        fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout=NMAP_SN_XML))
        with patch("zorin_copilot.core.network_inspector.shutil.which", return_value=None), patch(
            "zorin_copilot.core.network_inspector.subprocess.run", fake_run
        ):
            devices = NetworkInspector.scan_subnet("192.168.5.0/24")
        # Sem nmap, retorna lista vazia (e NÃO executa nada).
        self.assertEqual(devices, [])

    def test_scan_subnet_parses_xml(self):
        fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout=NMAP_SN_XML))
        with patch("zorin_copilot.core.network_inspector.shutil.which", return_value="/usr/bin/nmap"), patch(
            "zorin_copilot.core.network_inspector.subprocess.run", fake_run
        ):
            devices = NetworkInspector.scan_subnet("192.168.5.0/24")
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].ip, "192.168.5.12")

    def test_identify_device_nmap_missing(self):
        with patch("zorin_copilot.core.network_inspector.shutil.which", return_value=None):
            result = NetworkInspector.identify_device("192.168.5.12")
        self.assertFalse(result["ok"])
        self.assertIn("nmap", result["error"])

    def test_local_subnet_cidr_format(self):
        cidr = NetworkInspector.local_subnet_cidr()
        if cidr is not None:
            self.assertRegex(cidr, r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.0/24$")


class TestNetworkInventory(unittest.TestCase):
    """Baseline persistente (DeviceInventory) + monitor_lan / establish_baseline.

    Tudo testado contra um DB SQLite temporário e com ``scan_subnet`` mockado —
    sem depender de nmap ou da rede real.
    """

    def _dev(self, ip, mac, vendor="Apple Inc.", device_type="phone", hostname=""):
        return NeighborDevice(
            ip=ip, mac=mac, hostname=hostname, vendor=vendor,
            device_type=device_type, interface="", is_online=True,
        )

    def _tmp_inventory(self, tmp_path):
        return DeviceInventory(db_path=Path(tmp_path) / "inv.db")

    def test_inventory_remember_and_get(self):
        with tempfile.TemporaryDirectory() as d:
            inv = self._tmp_inventory(d)
            self.assertEqual(inv.count(), 0)
            self.assertTrue(inv.remember(self._dev("192.168.5.12", "1C:36:BB:11:22:33")))
            self.assertEqual(inv.count(), 1)
            self.assertIn("1c:36:bb:11:22:33", inv.get_known_macs())
            all_devs = inv.get_all()
            self.assertEqual(len(all_devs), 1)
            self.assertEqual(all_devs[0]["ip"], "192.168.5.12")
            self.assertFalse(all_devs[0]["trusted"])

    def test_inventory_remember_skips_empty_mac(self):
        with tempfile.TemporaryDirectory() as d:
            inv = self._tmp_inventory(d)
            self.assertFalse(inv.remember(self._dev("10.0.0.1", "")))
            self.assertEqual(inv.count(), 0)

    def test_inventory_trusted_persists_on_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            inv = self._tmp_inventory(d)
            inv.remember(self._dev("192.168.5.12", "AA:BB:CC:DD:EE:FF"), trusted=True)
            # Re-ver o device (sem trusted) NÃO deve rebaixá-lo a não-confiável.
            inv.remember(self._dev("192.168.5.12", "AA:BB:CC:DD:EE:FF"), trusted=False)
            self.assertTrue(inv.get_all()[0]["trusted"])
            # Mas mark_trusted(False) explicitamente rebaixa.
            self.assertTrue(inv.mark_trusted("AA:BB:CC:DD:EE:FF", False))
            self.assertFalse(inv.get_all()[0]["trusted"])

    def test_inventory_forget_and_clear(self):
        with tempfile.TemporaryDirectory() as d:
            inv = self._tmp_inventory(d)
            inv.remember(self._dev("192.168.5.12", "AA:BB:CC:DD:EE:FF"))
            self.assertTrue(inv.forget("aa:bb:cc:dd:ee:ff"))
            self.assertEqual(inv.count(), 0)
            inv.remember(self._dev("192.168.5.13", "11:22:33:44:55:66"))
            self.assertGreater(inv.clear(), 0)
            self.assertEqual(inv.count(), 0)

    def test_monitor_seeds_on_empty(self):
        with tempfile.TemporaryDirectory() as d:
            inv = self._tmp_inventory(d)
            devices = [
                self._dev("192.168.5.12", "1C:36:BB:11:22:33"),
                self._dev("192.168.5.13", "AA:BB:CC:DD:EE:FF"),
            ]
            with patch.object(NetworkInspector, "scan_subnet", return_value=devices):
                result = NetworkInspector.monitor_lan(cidr="192.168.5.0/24", inventory=inv)
            # Primeira execução (baseline vazio) semeia silenciosamente: sem alerta.
            self.assertTrue(result["seeded"])
            self.assertEqual(result["new_devices"], [])
            self.assertEqual(result["baseline_size"], 2)
            self.assertIn("1c:36:bb:11:22:33", inv.get_known_macs())

    def test_monitor_detects_new_device(self):
        with tempfile.TemporaryDirectory() as d:
            inv = self._tmp_inventory(d)
            # Baseline com 1 device conhecido (confiável).
            inv.remember(self._dev("192.168.5.12", "1C:36:BB:11:22:33"), trusted=True)
            devices = [
                self._dev("192.168.5.12", "1C:36:BB:11:22:33"),
                self._dev("192.168.5.99", "DE:AD:BE:EF:00:11"),
            ]
            with patch.object(NetworkInspector, "scan_subnet", return_value=devices):
                result = NetworkInspector.monitor_lan(cidr="192.168.5.0/24", inventory=inv)
            self.assertFalse(result["seeded"])
            self.assertEqual(len(result["new_devices"]), 1)
            self.assertEqual(result["new_devices"][0]["ip"], "192.168.5.99")
            self.assertEqual(result["new_devices"][0]["mac_masked"], "de:ad:be:xx:xx:xx")
            # O novo foi lembrado para não re-alertar depois.
            self.assertIn("de:ad:be:ef:00:11", inv.get_known_macs())

    def test_monitor_flags_untrusted_present(self):
        with tempfile.TemporaryDirectory() as d:
            inv = self._tmp_inventory(d)
            devices = [self._dev("192.168.5.12", "1C:36:BB:11:22:33")]
            # Semeia (trusted=False por padrão).
            with patch.object(NetworkInspector, "scan_subnet", return_value=devices):
                NetworkInspector.monitor_lan(cidr="192.168.5.0/24", inventory=inv, seed_if_empty=True)
            # Promove a confiável e re-varre: não deve aparecer em untrusted_present.
            inv.mark_trusted("1C:36:BB:11:22:33", True)
            with patch.object(NetworkInspector, "scan_subnet", return_value=devices):
                r1 = NetworkInspector.monitor_lan(cidr="192.168.5.0/24", inventory=inv)
            self.assertEqual(r1["untrusted_present"], [])
            # Rebaixa a não-confiável e re-varre: deve aparecer em untrusted_present.
            inv.mark_trusted("1C:36:BB:11:22:33", False)
            with patch.object(NetworkInspector, "scan_subnet", return_value=devices):
                r2 = NetworkInspector.monitor_lan(cidr="192.168.5.0/24", inventory=inv)
            self.assertEqual(len(r2["untrusted_present"]), 1)

    def test_establish_baseline_trusted(self):
        with tempfile.TemporaryDirectory() as d:
            inv = self._tmp_inventory(d)
            devices = [
                self._dev("192.168.5.12", "1C:36:BB:11:22:33"),
                self._dev("192.168.5.13", "AA:BB:CC:DD:EE:FF"),
            ]
            with patch.object(NetworkInspector, "scan_subnet", return_value=devices):
                result = NetworkInspector.establish_baseline(cidr="192.168.5.0/24", inventory=inv)
            self.assertEqual(result["seen"], 2)
            self.assertEqual(result["baseline_size"], 2)
            self.assertTrue(all(x["trusted"] for x in inv.get_all()))


if __name__ == "__main__":
    unittest.main()
