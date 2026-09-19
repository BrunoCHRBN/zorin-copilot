"""Testes para o registro e execução das ferramentas de infraestrutura no ToolRegistry."""

import unittest
from unittest.mock import MagicMock, patch

from src.zorin_copilot.ai.agent_tools import ToolRegistry
from src.zorin_copilot.shell.risk import RiskLevel, RiskPolicy


class TestInfraTools(unittest.TestCase):
    """Valida o registro e execução de ferramentas de infraestrutura."""

    def setUp(self) -> None:
        self.reg = ToolRegistry()

    def test_tools_registered(self) -> None:
        expected = [
            "docker_list_containers",
            "docker_container_logs",
            "docker_container_action",
            "system_hardware_health",
            "system_journal_errors",
        ]
        registered = self.reg.names()
        for tool in expected:
            self.assertIn(tool, registered, f"Ferramenta {tool} não encontrada no ToolRegistry")

    def test_risk_classification(self) -> None:
        policy = RiskPolicy()
        level, desc = policy.classify("docker_container_action", {"action": "restart"})
        self.assertEqual(level, RiskLevel.CONFIRM)
        self.assertIn("Docker", desc)

        level, _ = policy.classify("docker_list_containers", {})
        self.assertEqual(level, RiskLevel.SAFE)

        level, _ = policy.classify("system_hardware_health", {})
        self.assertEqual(level, RiskLevel.SAFE)

    @patch("src.zorin_copilot.core.docker_manager.DockerManager.list_containers")
    @patch("src.zorin_copilot.core.docker_manager.DockerManager.is_available")
    def test_docker_list_containers_call(
        self, mock_avail: MagicMock, mock_list: MagicMock
    ) -> None:
        mock_avail.return_value = True
        mock_list.return_value = []

        res = self.reg.call("docker_list_containers", {"all": True})
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("count"), 0)

    @patch("src.zorin_copilot.core.system_health.SystemHealthInspector.get_complete_report")
    def test_system_hardware_health_call(self, mock_report: MagicMock) -> None:
        mock_report.return_value = {
            "thermals": {"cpu_temp_c": 45.0},
            "memory": {"used_percent": 30.0},
        }

        res = self.reg.call("system_hardware_health", {})
        self.assertTrue(res.get("ok"))
        self.assertIn("report", res)
        self.assertEqual(res["report"]["thermals"]["cpu_temp_c"], 45.0)


if __name__ == "__main__":
    unittest.main()
