"""Testes unitários para o componente visual GTK4 NetworkInspectorView e conformidade Anti-AI-Slop."""

from __future__ import annotations

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.network_inspector import (
    InterfaceType,
    ListeningPort,
    NeighborDevice,
    NetworkHealthReport,
    WiFiMetrics,
)
from zorin_copilot.ui.widgets.network_view import (
    NetworkInspectorDialog,
    NetworkInspectorView,
    _symbolic_for_device_type,
    _symbolic_for_iface_type,
)


class TestNetworkView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.zorin.copilot.test.network_view")

    def test_anti_slop_symbolic_icons_no_emojis(self):
        """Garante que nenhum emoji é usado como ícone (regra estrita Anti-AI-Slop)."""
        device_types = ["router", "computer", "phone", "tv", "printer", "iot", "unknown"]
        for dev in device_types:
            icon = _symbolic_for_device_type(dev)
            self.assertTrue(icon.endswith("-symbolic"))
            # Garante que é uma string ascii pura (sem emojis)
            self.assertTrue(icon.isascii())

        iface_types = ["ethernet", "wifi", "vpn", "container", "loopback"]
        for iface in iface_types:
            icon = _symbolic_for_iface_type(iface)
            self.assertTrue(icon.endswith("-symbolic"))
            self.assertTrue(icon.isascii())

    def test_view_creation(self):
        """Criação básica da view e toolbar."""
        view = NetworkInspectorView(ctx=None)
        self.assertIsNotNone(view.toolbar)
        self.assertIsNotNone(view.refresh_btn)
        self.assertIsNotNone(view.copy_ip_btn)
        self.assertIsNotNone(view.sync_kg_btn)
        self.assertIsNotNone(view.content_area)

    def test_loading_state(self):
        """Verifica se o estado de carregamento constrói o spinner e label descritivo."""
        view = NetworkInspectorView(ctx=None)
        view._show_loading_state()
        first_child = view.content_area.get_first_child()
        self.assertIsNotNone(first_child)

    def test_error_state(self):
        """Verifica se o estado de erro renderiza mensagem e botão de nova tentativa."""
        view = NetworkInspectorView(ctx=None)
        view._show_error_state("Falha simulada de Netlink")
        first_child = view.content_area.get_first_child()
        self.assertIsNotNone(first_child)

    def test_loaded_state_rendering(self):
        """Verifica a renderização completa dos cards com numerais tabulares."""
        view = NetworkInspectorView(ctx=None)
        view._report = NetworkHealthReport(
            primary_interface="enp4s0",
            primary_type="ethernet",
            local_ip="192.168.15.12",
            gateway_ip="192.168.15.1",
            gateway_ping_ms=0.45,
            internet_ping_ms=11.2,
            dns_servers=["192.168.15.1", "1.1.1.1"],
            wifi=None,
            packet_loss_percent=0.0,
        )
        view._neighbors = [
            NeighborDevice(
                ip="192.168.15.1",
                mac="44:89:6d:79:4e:20",
                hostname="router.lan",
                vendor="Sagemcom",
                device_type="router",
                interface="enp4s0",
                is_online=True,
            ),
            NeighborDevice(
                ip="192.168.15.20",
                mac="ec:fa:bc:11:22:33",
                hostname="lampada.lan",
                vendor="Espressif (Avant Neo / IoT)",
                device_type="iot",
                interface="enp4s0",
                is_online=True,
            ),
        ]
        view._ports = [
            ListeningPort(
                protocol="tcp",
                port=8080,
                ip_binding="0.0.0.0",
                process_name="caddy",
                pid=1234,
            ),
            ListeningPort(
                protocol="tcp",
                port=22,
                ip_binding="127.0.0.1",
                process_name="sshd",
                pid=567,
            ),
        ]

        view._render_loaded_state()

        # O container deve conter o card de saúde e as seções de tabela
        first = view.content_area.get_first_child()
        self.assertIsNotNone(first)
        self.assertTrue(first.has_css_class("network-health-card"))

    def test_dialog_instantiation(self):
        """Verifica que o Adw.Dialog do inspetor pode ser instanciado sem erros."""
        from zorin_copilot.ui.app import CopilotWindow
        win = CopilotWindow(self.app)
        dialog = NetworkInspectorDialog(ctx=win)
        self.assertEqual(dialog.get_title(), "Infraestrutura de Rede")
        self.assertIsNotNone(dialog.get_child())

    def test_dns_benchmark_card_anti_slop(self):
        """Verifica a renderização da tabela de benchmark DNS conforme Anti-AI-Slop."""
        view = NetworkInspectorView(ctx=None)
        sample_results = [
            {
                "provider": "Cloudflare (1.1.1.1)",
                "server_ip": "1.1.1.1",
                "avg_latency_ms": 11.4,
                "success_rate": 1.0,
                "is_fastest": True,
            },
            {
                "provider": "Google (8.8.8.8)",
                "server_ip": "8.8.8.8",
                "avg_latency_ms": 18.2,
                "success_rate": 1.0,
                "is_fastest": False,
            },
            {
                "provider": "Quad9 (9.9.9.9)",
                "server_ip": "9.9.9.9",
                "avg_latency_ms": None,
                "success_rate": 0.0,
                "is_fastest": False,
            },
        ]
        card = view._make_dns_benchmark_card(sample_results)
        self.assertIsNotNone(card)
        self.assertTrue(card.has_css_class("network-table-card"))

        # Checa cabeçalho e linhas
        first_row = card.get_first_child()
        self.assertIsNotNone(first_row)
        self.assertTrue(first_row.has_css_class("network-table-header"))

    def test_wake_on_lan_action_invoked(self):
        """Verifica se o callback de Wake-on-LAN executa sem erros em segundo plano."""
        view = NetworkInspectorView(ctx=None)
        with patch("zorin_copilot.ui.widgets.network_view.wake_on_lan", return_value=(True, "Magic packet enviado")) as mock_wol:
            view._on_wake_device_clicked("44:89:6d:79:4e:20")
            time.sleep(0.05)
            mock_wol.assert_called_once_with("44:89:6d:79:4e:20")

    def test_release_port_action_invoked(self):
        """Verifica se o worker de liberação de porta chama o motor do core."""
        view = NetworkInspectorView(ctx=None)
        with patch("zorin_copilot.ui.widgets.network_view.release_local_port", return_value=(True, "Porta liberada", {})) as mock_rel:
            view._do_release_port(8080, 1234)
            time.sleep(0.05)
            mock_rel.assert_called_once_with(8080, target_pid=1234)

    def test_dns_bench_clicked_triggers_worker(self):
        """Verifica se o clique no botão de benchmark DNS dispara a medição."""
        view = NetworkInspectorView(ctx=None)
        with patch("zorin_copilot.ui.widgets.network_view.benchmark_dns", return_value=[{"provider": "Local", "avg_latency_ms": 1.2, "is_fastest": True}]) as mock_bench:
            view._on_dns_bench_clicked(view.dns_bench_btn)
            time.sleep(0.05)
            mock_bench.assert_called_once()


if __name__ == "__main__":
    unittest.main()
