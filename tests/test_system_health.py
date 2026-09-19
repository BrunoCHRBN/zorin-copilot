"""Testes unitários para o inspetor de saúde física e diagnóstico de sistema (SystemHealthInspector)."""

import json
import unittest
from unittest.mock import MagicMock, patch

from src.zorin_copilot.core.system_health import (
    MemoryPressureMetrics,
    SystemHealthInspector,
    ThermalMetrics,
)


class TestSystemHealthInspector(unittest.TestCase):
    """Testes para telemetria térmica, memória PSI, S.M.A.R.T. e journalctl."""

    def test_parse_sensors_json(self) -> None:
        mock_data = {
            "k10temp-pci-00c3": {
                "Adapter": "PCI adapter",
                "Tctl": {"temp1_input": 55.4},
            },
            "amdgpu-pci-0700": {
                "Adapter": "PCI adapter",
                "edge": {"temp1_input": 62.0},
                "fan1": {"fan1_input": 1200},
            },
            "nvme-pci-0100": {
                "Adapter": "PCI adapter",
                "Composite": {"temp1_input": 38.5},
            },
        }

        metrics = ThermalMetrics()
        SystemHealthInspector._parse_sensors_json(mock_data, metrics)

        self.assertEqual(metrics.cpu_temp_c, 55.4)
        self.assertEqual(metrics.gpu_temp_c, 62.0)
        self.assertEqual(metrics.nvme_temp_c, 38.5)
        self.assertEqual(metrics.fans_rpm.get("fan1"), 1200)

    @patch("subprocess.run")
    def test_get_storage_health_standby_protected(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "smartctl": {
                    "messages": [{"string": "Device is in STANDBY mode, exiting"}]
                }
            }),
        )

        res = SystemHealthInspector.get_storage_health("/dev/sdb")
        self.assertTrue(res.standby_protected)
        self.assertIn("STANDBY", res.notes)

    @patch("subprocess.run")
    def test_get_storage_health_permission_denied(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=2,
            stdout=json.dumps({
                "smartctl": {
                    "messages": [{"string": "Smartctl open device: /dev/nvme0n1 failed: Permission denied"}]
                }
            }),
        )

        res = SystemHealthInspector.get_storage_health("/dev/nvme0n1")
        self.assertFalse(res.permission_granted)
        self.assertIn("Permissão negada", res.notes)

    @patch("subprocess.run")
    def test_get_journal_incidents_deduplication(self, mock_run: MagicMock) -> None:
        raw_output = "\n".join([
            json.dumps({
                "USER_UNIT": "academix.service",
                "MESSAGE": "Database connection timeout",
                "PRIORITY": "3",
                "__REALTIME_TIMESTAMP": "1700000000",
            })
            for _ in range(12)
        ])
        mock_run.return_value = MagicMock(returncode=0, stdout=raw_output)

        incidents = SystemHealthInspector.get_journal_incidents(lines=20)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].repeat_count, 12)
        self.assertEqual(incidents[0].unit, "academix.service")

    def test_complete_report_structure(self) -> None:
        report = SystemHealthInspector.get_complete_report()
        self.assertIn("thermals", report)
        self.assertIn("memory", report)
        self.assertIn("storage", report)
        self.assertIn("incidents_summary", report)
        self.assertIsInstance(report["thermals"]["cpu_temp_c"], (int, float))
        self.assertIsInstance(report["memory"]["used_percent"], (int, float))


if __name__ == "__main__":
    unittest.main()
