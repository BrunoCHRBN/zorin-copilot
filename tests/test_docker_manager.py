"""Testes unitários para o gerenciador de containers Docker (DockerManager)."""

import json
import unittest
from unittest.mock import MagicMock, patch

from src.zorin_copilot.core.docker_manager import (
    ContainerInfo,
    DockerManager,
    UnixHTTPConnection,
)


class TestDockerManager(unittest.TestCase):
    """Suíte de testes para DockerManager, destilação de logs e salvaguardas."""

    def setUp(self) -> None:
        self.dm = DockerManager(socket_path="/tmp/fake_docker.sock")

    def test_distill_logs_empty(self) -> None:
        res = DockerManager.distill_logs("")
        self.assertEqual(res["total_lines"], 0)
        self.assertEqual(res["distilled_lines"], [])
        self.assertEqual(res["error_count"], 0)

    def test_distill_logs_collapse_repetitions(self) -> None:
        raw_logs = "\n".join(["Connection refused to database"] * 45)
        res = DockerManager.distill_logs(raw_logs, max_output_lines=10)
        self.assertEqual(res["total_lines"], 45)
        self.assertEqual(len(res["distilled_lines"]), 1)
        self.assertIn("[Repetido 45x]", res["distilled_lines"][0])
        self.assertIn("Connection refused", res["distilled_lines"][0])
        self.assertGreater(res["error_count"], 0)

    def test_distill_logs_filter_errors(self) -> None:
        raw_logs = (
            "INFO: Application starting\n"
            "DEBUG: Initializing components\n"
            "ERROR: Failed to bind port 8080\n"
            "INFO: Heartbeat ok\n"
            "CRITICAL: Out of memory in worker\n"
        )
        res = DockerManager.distill_logs(raw_logs, filter_errors_only=True)
        self.assertEqual(len(res["distilled_lines"]), 2)
        self.assertIn("Failed to bind port", res["distilled_lines"][0])
        self.assertIn("Out of memory", res["distilled_lines"][1])

    def test_distill_logs_privacy_masking(self) -> None:
        raw_logs = "DEBUG: Connected with token=secret_token_123456 and password=my_super_password"
        res = DockerManager.distill_logs(raw_logs)
        first_line = res["distilled_lines"][0]
        self.assertNotIn("secret_token_123456", first_line)
        self.assertNotIn("my_super_password", first_line)

    @patch.object(DockerManager, "_socket_request")
    def test_list_containers_socket_success(self, mock_req: MagicMock) -> None:
        mock_data = [
            {
                "Id": "a1b2c3d4e5f67890",
                "Names": ["/web_app"],
                "Image": "nginx:alpine",
                "State": "running",
                "Status": "Up 4 hours (healthy)",
                "Created": 1700000000,
                "Ports": [{"PrivatePort": 80, "PublicPort": 8080, "Type": "tcp"}],
            }
        ]
        mock_req.return_value = (200, mock_data)

        containers = self.dm.list_containers()
        self.assertEqual(len(containers), 1)
        c = containers[0]
        self.assertEqual(c.id, "a1b2c3d4e5f6")
        self.assertEqual(c.name, "web_app")
        self.assertEqual(c.health, "healthy")
        self.assertEqual(c.ports, ["8080->80/tcp"])

    @patch.object(DockerManager, "_socket_request")
    @patch.object(DockerManager, "list_containers")
    def test_restart_container_success_verified(
        self, mock_list: MagicMock, mock_req: MagicMock
    ) -> None:
        mock_req.return_value = (204, None)
        mock_list.return_value = [
            ContainerInfo(
                id="c12345678901",
                name="homeassistant",
                image="homeassistant:latest",
                state="running",
                status="Up 2 seconds",
                created=0,
                health="healthy",
            )
        ]

        res = self.dm.restart_container("homeassistant", wait_timeout=1.0)
        self.assertTrue(res.get("success"))
        self.assertTrue(res.get("verified"))
        self.assertEqual(res.get("state"), "running")


if __name__ == "__main__":
    unittest.main()
