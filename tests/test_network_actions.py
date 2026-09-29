"""Testes unitários para o motor operacional de ações de rede (network_actions.py)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from zorin_copilot.core.network_actions import (
    PROTECTED_PROCESS_NAMES,
    NetworkBlocker,
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


class TestSystemdAwareRelease(unittest.TestCase):
    """Encerramento ciente de systemd (bug real: matar por PID é ineficaz)."""

    def test_parse_unit_from_cgroup(self):
        from zorin_copilot.core.network_actions import _parse_systemd_unit_from_cgroup
        content = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/aigis0-ui.service\n"
        self.assertEqual(_parse_systemd_unit_from_cgroup(content), "aigis0-ui.service")

    def test_parse_unit_none_for_scope(self):
        from zorin_copilot.core.network_actions import _parse_systemd_unit_from_cgroup
        self.assertIsNone(
            _parse_systemd_unit_from_cgroup("0::/user.slice/user-1000.slice/session-3.scope\n")
        )

    def test_is_user_unit(self):
        from zorin_copilot.core.network_actions import _is_user_unit_from_cgroup
        content = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/aigis0-ui.service"
        self.assertTrue(_is_user_unit_from_cgroup(content, 1000))
        self.assertFalse(_is_user_unit_from_cgroup(content, 0))

    def test_system_unit_refused_with_correct_guidance(self):
        # Unit de sistema (root): deve orientar 'sudo systemctl stop', não 'kill'.
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", unittest.mock.mock_open(read_data=b"/usr/bin/foo\x00")):
                with patch(
                    "zorin_copilot.core.network_actions.systemd_unit_for_pid",
                    return_value=("foo.service", False),
                ):
                    ok, msg, meta = release_local_port(8080, target_pid=9997)
                    self.assertFalse(ok)
                    self.assertIn("sudo systemctl stop foo.service", msg)
                    self.assertEqual(meta.get("systemd_scope"), "system")

    def test_user_unit_stopped_via_systemctl(self):
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", unittest.mock.mock_open(read_data=b"/home/u/aigis0\x00")):
                with patch(
                    "zorin_copilot.core.network_actions.systemd_unit_for_pid",
                    return_value=("aigis0-ui.service", True),
                ):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                        ok, msg, meta = release_local_port(8080, target_pid=9996)
                        self.assertTrue(ok)
                        self.assertIn("aigis0-ui.service", msg)
                        self.assertEqual(meta.get("systemd_unit"), "aigis0-ui.service")
                        argv = mock_run.call_args[0][0]
                        self.assertEqual(argv[:3], ["systemctl", "--user", "stop"])

    def test_user_unit_stop_failure_reported(self):
        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", unittest.mock.mock_open(read_data=b"/home/u/aigis0\x00")):
                with patch(
                    "zorin_copilot.core.network_actions.systemd_unit_for_pid",
                    return_value=("aigis0-ui.service", True),
                ):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(
                            returncode=1, stdout="", stderr="Failed to stop",
                        )
                        ok, msg, _ = release_local_port(8080, target_pid=9995)
                        self.assertFalse(ok)
                        self.assertIn("Failed to stop", msg)


class TestNetworkBlocker(unittest.TestCase):
    """NetworkBlocker: delegação ao roteador/HA, fail-closed, nunca drop local.

    Testado com um script backend temporário e inventário temporário — sem depender
    de roteador/HA reais nem de nmap.
    """

    def _make_backend_script(self, tmp_path, record_path):
        """Cria um script executável que grava seus argumentos em record_path."""
        script = tmp_path / "block_device.sh"
        script.write_text(
            "#!/bin/bash\n" f"printf '%s|%s|%s\\n' \"$1\" \"$2\" \"$3\" > '{record_path}'\n"
        )
        script.chmod(0o755)
        return str(script)

    def _cfg(self, script):
        return {"backend": "script", "script": script}

    def test_not_configured_returns_guidance(self):
        blocker = NetworkBlocker(config_path="/nonexistent/path/network_block.json")
        # Sem backend configurado: ok=False, executado nada, guidance presente.
        res = blocker.request_block(ip="192.168.5.12", action="block")
        self.assertFalse(res["ok"])
        self.assertFalse(res["executed"])
        self.assertTrue(len(res["guidance"]) > 0)
        self.assertIn("não configurado", res["error"])
        # O IP aparece não mascarado; nenhum MAC cru vaza.
        self.assertEqual(res["target"], "192.168.5.12")

    def test_invalid_action_rejected(self):
        blocker = NetworkBlocker(config_path="/nonexistent/path/network_block.json")
        res = blocker.request_block(ip="192.168.5.12", action="explode")
        self.assertFalse(res["ok"])
        self.assertIn("inválida", res["error"])

    def test_invalid_mac_rejected(self):
        blocker = NetworkBlocker(config_path="/nonexistent/path/network_block.json")
        res = blocker.request_block(mac="zz:zz", action="block")
        self.assertFalse(res["ok"])
        self.assertIn("inválido", res["error"])

    def test_missing_target_rejected(self):
        blocker = NetworkBlocker(config_path="/nonexistent/path/network_block.json")
        res = blocker.request_block(action="block")
        self.assertFalse(res["ok"])
        self.assertIn("Informe", res["error"])

    def test_script_backend_invoked(self):
        with tempfile.TemporaryDirectory() as d:
            record = Path(d) / "args.txt"
            script = self._make_backend_script(Path(d), str(record))
            blocker = NetworkBlocker(config_path=Path(d) / "cfg.json")
            res = blocker.request_block(
                mac="DE:AD:BE:EF:00:11", action="block", config=self._cfg(script)
            )
            self.assertTrue(res["ok"])
            self.assertTrue(res["executed"])
            # Script recebeu MAC cru (identificador) + ação.
            self.assertTrue(record.exists())
            args_line = record.read_text().strip()
            parts = args_line.split("|")
            self.assertEqual(parts[0], "block")
            self.assertEqual(parts[1], "de:ad:be:ef:00:11")
            # A resposta ao modelo mascara o MAC.
            self.assertEqual(res["target"], "de:ad:be:xx:xx:xx")
            self.assertNotIn("de:ad:be:ef:00:11", res["target"])

    def test_script_backend_dry_run_does_not_execute(self):
        with tempfile.TemporaryDirectory() as d:
            record = Path(d) / "args.txt"
            script = self._make_backend_script(Path(d), str(record))
            blocker = NetworkBlocker(config_path=Path(d) / "cfg.json")
            res = blocker.request_block(
                mac="DE:AD:BE:EF:00:11", action="block",
                dry_run=True, config=self._cfg(script),
            )
            self.assertTrue(res["ok"])
            self.assertFalse(res["executed"])
            self.assertTrue(res["dry_run"])
            self.assertFalse(record.exists())  # script NÃO foi chamado

    def test_block_by_ip_resolves_mac_from_inventory(self):
        with tempfile.TemporaryDirectory() as d:
            record = Path(d) / "args.txt"
            script = self._make_backend_script(Path(d), str(record))
            # Inventário temporário com o device no IP informado.
            from zorin_copilot.core.network_inspector import DeviceInventory, NeighborDevice

            inv = DeviceInventory(db_path=Path(d) / "inv.db")
            dev = NeighborDevice(
                ip="192.168.5.12", mac="1C:36:BB:11:22:33", hostname="",
                vendor="Apple Inc.", device_type="phone", interface="", is_online=True,
            )
            inv.remember(dev, trusted=False)
            blocker = NetworkBlocker(config_path=Path(d) / "cfg.json")
            res = blocker.request_block(
                ip="192.168.5.12", action="block",
                inventory=inv, config=self._cfg(script),
            )
            self.assertTrue(res["ok"])
            # O script recebeu o MAC real resolvido do inventário, não o IP.
            args_line = record.read_text().strip()
            self.assertEqual(args_line.split("|")[1], "1c:36:bb:11:22:33")
            # A resposta ao modelo mostra o IP (que o modelo já tinha).
            self.assertEqual(res["target"], "192.168.5.12")

    def test_disabled_backend_in_config_not_executed(self):
        # backend != "script" => não configurado (fail-closed).
        blocker = NetworkBlocker(config_path="/nonexistent/path/network_block.json")
        res = blocker.request_block(
            ip="192.168.5.12", action="block", config={"backend": "router_api"}
        )
        self.assertFalse(res["ok"])
        self.assertFalse(res["executed"])


if __name__ == "__main__":
    unittest.main()
