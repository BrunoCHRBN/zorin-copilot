"""Testes unitários para a interface gráfica InfraInspectorView e InfraInspectorDialog (Anti-AI-Slop)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.docker_manager import ContainerInfo
from zorin_copilot.core.system_health import JournalIncident
from zorin_copilot.ui.widgets.infra_view import (
    InfraInspectorDialog,
    InfraInspectorView,
    _make_tnum_label,
)


class TestInfraView(unittest.TestCase):
    """Testes de conformidade e integridade da interface gráfica de infraestrutura."""

    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.infra_view")

    def test_view_creation(self) -> None:
        view = InfraInspectorView(ctx=None)
        self.assertIsNotNone(view.view_stack)
        self.assertIsNotNone(view.refresh_btn)
        self.assertIsNotNone(view.hw_page)
        self.assertIsNotNone(view.docker_page)
        self.assertIsNotNone(view.journal_page)

    def test_tnum_labels_numeric_stat(self) -> None:
        lbl = _make_tnum_label("42.5 °C")
        self.assertTrue(lbl.has_css_class("numeric-stat"))

    def test_update_hardware_ui(self) -> None:
        view = InfraInspectorView(ctx=None)
        report = {
            "thermals": {
                "cpu_temp_c": 52.3,
                "gpu_temp_c": 58.0,
                "nvme_temp_c": 35.0,
                "throttle_warning": False,
                "status_summary": "Normal",
            },
            "memory": {
                "used_percent": 33.5,
                "total_mb": 16000.0,
                "available_mb": 10640.0,
                "psi_some_avg10": 0.05,
                "pressure_level": "Baixa",
            },
            "storage": {
                "notes": "Relatório S.M.A.R.T. íntegro",
            },
        }

        view._update_hardware_ui(report)
        self.assertIn("52.3", view.cpu_val_lbl.get_text())
        self.assertIn("58.0", view.gpu_val_lbl.get_text())
        self.assertIn("33.5", view.ram_val_lbl.get_text())

    def test_update_docker_ui(self) -> None:
        view = InfraInspectorView(ctx=None)
        containers = [
            ContainerInfo(
                id="c12345678901",
                name="homeassistant",
                image="homeassistant:latest",
                state="running",
                status="Up 1 hour",
                created=0,
                ports=["8123->8123/tcp"],
                health="healthy",
            )
        ]

        view._update_docker_ui(containers)
        self.assertFalse(view.docker_empty_lbl.get_visible())

    def test_update_journal_ui(self) -> None:
        view = InfraInspectorView(ctx=None)
        incidents = [
            JournalIncident(
                timestamp="1700000000",
                unit="academix.service",
                message="Timeout connecting to server",
                priority=3,
                repeat_count=4,
            )
        ]

        view._update_journal_ui(incidents)
        self.assertFalse(view.journal_empty_lbl.get_visible())

    def test_dialog_creation(self) -> None:
        mock_ctx = MagicMock()
        dialog = InfraInspectorDialog(ctx=mock_ctx)
        self.assertEqual(dialog.get_title(), "Infraestrutura & Diagnóstico do Host")
        self.assertEqual(dialog.get_content_width(), 740)
        self.assertEqual(dialog.get_content_height(), 560)


if __name__ == "__main__":
    unittest.main()
