"""Testes de integração das ferramentas de rede no ToolRegistry."""

import unittest
from zorin_copilot.ai.agent_tools import ToolRegistry
from zorin_copilot.shell.risk import RiskLevel


class TestNetworkTools(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()

    def tearDown(self):
        self.registry.close()

    def test_network_tools_registered(self):
        specs = self.registry._specs
        self.assertIn("network_inspect", specs)
        self.assertIn("lan_devices_scan", specs)
        self.assertIn("local_services_list", specs)
        self.assertIn("network_wake_on_lan", specs)
        self.assertIn("network_release_port", specs)
        self.assertIn("network_confirm_port", specs)
        self.assertIn("network_probe_service", specs)
        self.assertIn("network_dns_benchmark", specs)
        self.assertIn("network_reconnect", specs)

    def test_network_tools_risk_governance(self):
        # Ações seguras de diagnóstico e WoL
        safe_tools = [
            "network_inspect",
            "lan_devices_scan",
            "local_services_list",
            "network_wake_on_lan",
            "network_confirm_port",
            "network_probe_service",
            "network_dns_benchmark",
        ]
        for tool_name in safe_tools:
            level, _ = self.registry.classify(tool_name, {})
            self.assertEqual(level, RiskLevel.SAFE, f"Tool {tool_name} deveria ser SAFE")
            self.assertFalse(self.registry.requires_approval(tool_name, {}))

        # Ações mutadoras de risco que exigem confirmação do usuário
        confirm_tools = ["network_release_port", "network_reconnect"]
        for tool_name in confirm_tools:
            level, desc = self.registry.classify(tool_name, {})
            self.assertEqual(level, RiskLevel.CONFIRM, f"Tool {tool_name} deveria ser CONFIRM")
            self.assertTrue(self.registry.requires_approval(tool_name, {}))
            self.assertGreater(len(desc), 0)

    def test_network_wake_on_lan_call(self):
        res = self.registry.call("network_wake_on_lan", {"mac": "44:89:6d:79:4e:20"})
        self.assertTrue(res.get("ok"))
        self.assertIn("Magic packet", res.get("message", ""))

    def test_network_probe_service_call(self):
        res = self.registry.call("network_probe_service", {"host": "127.0.0.1", "port": 58912})
        # Deve retornar dict estruturado sem lançar exceção
        self.assertIn("ok", res)
        self.assertIn("status", res)

    def test_network_dns_benchmark_call(self):
        res = self.registry.call("network_dns_benchmark", {"domain": "archlinux.org"})
        self.assertTrue(res.get("ok"))
        self.assertIn("results", res)
        self.assertIsInstance(res.get("results"), list)


if __name__ == "__main__":
    unittest.main()

