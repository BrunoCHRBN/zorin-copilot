"""Testes unitários para WindowManager e WindowInfo (Foco dinâmico e rastreamento de janelas)."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from zorin_copilot.core.window_manager import WindowInfo, WindowManager


class WindowInfoTest(unittest.TestCase):
    def test_window_geometry_properties(self):
        win = WindowInfo(
            id="0xabc123",
            app="google-chrome",
            title="Página Inicial - Google Chrome",
            x=100,
            y=200,
            width=800,
            height=600,
        )
        self.assertEqual(win.bounds, (100, 200, 800, 600))
        self.assertEqual(win.max_x, 900)
        self.assertEqual(win.max_y, 800)
        self.assertTrue(win.contains(100, 200))
        self.assertTrue(win.contains(500, 500))
        self.assertTrue(win.contains(899, 799))
        self.assertFalse(win.contains(900, 800))
        self.assertFalse(win.contains(50, 50))

    def test_display_name_formatting(self):
        win = WindowInfo(
            id="0x1",
            app="kitty",
            title="bash",
            x=0,
            y=0,
            width=500,
            height=400,
        )
        self.assertEqual(win.display_name(), "Kitty — bash")

        win_long = WindowInfo(
            id="0x2",
            app="code",
            title="src/zorin_copilot/core/window_manager.py - Visual Studio Code",
            x=0,
            y=0,
            width=1000,
            height=800,
        )
        self.assertTrue(win_long.display_name(20).endswith("…"))


class WindowManagerHyprlandTest(unittest.TestCase):
    def setUp(self):
        self.sample_clients = [
            {
                "address": "0x55d2b7cf3520",
                "mapped": True,
                "hidden": False,
                "at": [1920, 0],
                "size": [1920, 1080],
                "workspace": {"id": 1, "name": "1"},
                "floating": False,
                "monitor": 0,
                "class": "kitty",
                "title": "bruno-vsantos@desktop:~",
                "initialClass": "kitty",
                "initialTitle": "kitty",
                "pid": 5432,
                "focusHistoryID": 1,
            },
            {
                "address": "0x55d2b86ab100",
                "mapped": True,
                "hidden": False,
                "at": [0, 148],
                "size": [1920, 1080],
                "workspace": {"id": 2, "name": "2"},
                "floating": False,
                "monitor": 1,
                "class": "Google-chrome",
                "title": "YouTube - Google Chrome",
                "initialClass": "google-chrome",
                "initialTitle": "Google Chrome",
                "pid": 6789,
                "focusHistoryID": 0,
            },
            {
                "address": "0x55d2b9999999",
                "mapped": True,
                "hidden": False,
                "at": [100, 100],
                "size": [500, 600],
                "workspace": {"id": 1, "name": "1"},
                "floating": True,
                "monitor": 0,
                "class": "zorin-copilot",
                "title": "Zorin Copilot",
                "pid": 1234,
                "focusHistoryID": 2,
            },
        ]

    @mock.patch("shutil.which", return_value="/usr/bin/hyprctl")
    @mock.patch("subprocess.run")
    def test_list_windows_hyprland_sorts_by_recency(self, mock_run, _mock_which):
        mock_proc = mock.Mock(returncode=0, stdout=json.dumps(self.sample_clients), stderr="")
        mock_run.return_value = mock_proc

        windows = WindowManager.list_windows(exclude_copilot=True)
        # Copilot deve ter sido excluído
        self.assertEqual(len(windows), 2)
        # Google-chrome tem focusHistoryID=0, então deve vir em primeiro
        self.assertEqual(windows[0].app, "Google-chrome")
        self.assertTrue(windows[0].is_active)
        self.assertEqual(windows[1].app, "kitty")

    @mock.patch("shutil.which", return_value="/usr/bin/hyprctl")
    @mock.patch("subprocess.run")
    def test_get_active_or_last_window(self, mock_run, _mock_which):
        def _fake_run(cmd, **kwargs):
            if "activewindow" in cmd:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"address": "0x55d2b86ab100", "class": "Google-chrome"}),
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout=json.dumps(self.sample_clients), stderr="")

        mock_run.side_effect = _fake_run

        win = WindowManager.get_active_or_last_window()
        self.assertIsNotNone(win)
        self.assertEqual(win.app, "Google-chrome")
        self.assertEqual(win.id, "0x55d2b86ab100")

    @mock.patch("shutil.which", return_value="/usr/bin/hyprctl")
    @mock.patch("subprocess.run")
    def test_focus_window_address(self, mock_run, _mock_which):
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

        ok = WindowManager.focus_window("0x55d2b86ab100")
        self.assertTrue(ok)
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd, ["hyprctl", "dispatch", "focuswindow", "address:0x55d2b86ab100"])

    @mock.patch("shutil.which", return_value="/usr/bin/hyprctl")
    @mock.patch("subprocess.run")
    def test_find_window(self, mock_run, _mock_which):
        mock_run.return_value = mock.Mock(returncode=0, stdout=json.dumps(self.sample_clients), stderr="")

        # Busca por nome do app
        win = WindowManager.find_window("chrome")
        self.assertIsNotNone(win)
        self.assertEqual(win.app, "Google-chrome")

        # Busca por trecho do título
        win2 = WindowManager.find_window("youtube")
        self.assertIsNotNone(win2)
        self.assertEqual(win2.id, "0x55d2b86ab100")

        # Busca inexistente
        win_none = WindowManager.find_window("aplicativo_inexistente_xyz")
        self.assertIsNone(win_none)


class WindowManagerSwayTest(unittest.TestCase):
    @mock.patch("shutil.which")
    @mock.patch("subprocess.run")
    def test_list_windows_sway(self, mock_run, mock_which):
        def _fake_which(bin_name):
            if bin_name == "swaymsg":
                return "/usr/bin/swaymsg"
            return None

        mock_which.side_effect = _fake_which

        tree = {
            "id": 1,
            "nodes": [
                {
                    "id": 2,
                    "app_id": "firefox",
                    "name": "Mozilla Firefox",
                    "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                    "focused": True,
                    "nodes": [],
                    "floating_nodes": [],
                },
                {
                    "id": 3,
                    "app_id": "zorin-copilot",
                    "name": "Zorin Copilot",
                    "rect": {"x": 500, "y": 0, "width": 800, "height": 600},
                    "focused": False,
                    "nodes": [],
                    "floating_nodes": [],
                },
            ],
            "floating_nodes": [],
        }
        mock_run.return_value = mock.Mock(returncode=0, stdout=json.dumps(tree), stderr="")

        windows = WindowManager.list_windows(exclude_copilot=True)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].app, "firefox")
        self.assertTrue(windows[0].is_active)


class WindowManagerMoveAndMonitorsTest(unittest.TestCase):
    def setUp(self):
        self.sample_monitors = [
            {
                "id": 0,
                "name": "DP-2",
                "description": "AOC 27G4",
                "width": 1920,
                "height": 1080,
                "x": 1920,
                "y": 0,
                "activeWorkspace": {"id": 1, "name": "1"},
                "focused": True,
                "scale": 1.0,
            },
            {
                "id": 1,
                "name": "HDMI-A-2",
                "description": "VIE ATHEN V3 24",
                "width": 1920,
                "height": 1080,
                "x": 0,
                "y": 0,
                "activeWorkspace": {"id": 2, "name": "2"},
                "focused": False,
                "scale": 1.0,
            },
        ]

    @mock.patch("shutil.which", return_value="/usr/bin/hyprctl")
    @mock.patch("subprocess.run")
    def test_list_monitors_hyprland(self, mock_run, _mock_which):
        mock_run.return_value = mock.Mock(returncode=0, stdout=json.dumps(self.sample_monitors), stderr="")
        monitors = WindowManager.list_monitors()
        self.assertEqual(len(monitors), 2)
        self.assertEqual(monitors[0]["name"], "DP-2")
        self.assertEqual(monitors[0]["x"], 1920)
        self.assertEqual(monitors[0]["active_workspace"], "1")
        self.assertEqual(monitors[1]["name"], "HDMI-A-2")
        self.assertEqual(monitors[1]["x"], 0)
        self.assertEqual(monitors[1]["active_workspace"], "2")

    @mock.patch.object(WindowManager, "list_monitors")
    def test_resolve_monitor_natural_language(self, mock_list):
        mock_list.return_value = [
            {
                "id": 0,
                "name": "DP-2",
                "description": "AOC 27G4",
                "x": 1920,
                "y": 0,
                "width": 1920,
                "height": 1080,
                "is_primary": True,
                "active_workspace": "1",
            },
            {
                "id": 1,
                "name": "HDMI-A-2",
                "description": "VIE ATHEN V3 24",
                "x": 0,
                "y": 0,
                "width": 1920,
                "height": 1080,
                "is_primary": False,
                "active_workspace": "2",
            },
        ]

        # Principal
        m_pri = WindowManager.resolve_monitor("principal")
        self.assertIsNotNone(m_pri)
        self.assertEqual(m_pri["name"], "DP-2")

        # Secundário
        m_sec = WindowManager.resolve_monitor("secundario")
        self.assertIsNotNone(m_sec)
        self.assertEqual(m_sec["name"], "HDMI-A-2")

        # Direita (x=1920)
        m_dir = WindowManager.resolve_monitor("direita")
        self.assertEqual(m_dir["name"], "DP-2")

        # Esquerda (x=0)
        m_esq = WindowManager.resolve_monitor("esquerda")
        self.assertEqual(m_esq["name"], "HDMI-A-2")

        # "outro" alternando conforme monitor atual
        m_other_from_0 = WindowManager.resolve_monitor("outro", current_monitor_id=0)
        self.assertEqual(m_other_from_0["name"], "HDMI-A-2")

        m_other_from_1 = WindowManager.resolve_monitor("outro", current_monitor_id=1)
        self.assertEqual(m_other_from_1["name"], "DP-2")

        # Por ID numérico
        m_id1 = WindowManager.resolve_monitor("1")
        self.assertEqual(m_id1["name"], "HDMI-A-2")

        # Por busca de modelo
        m_aoc = WindowManager.resolve_monitor("aoc")
        self.assertEqual(m_aoc["name"], "DP-2")

    @mock.patch("shutil.which", return_value="/usr/bin/hyprctl")
    @mock.patch("subprocess.run")
    def test_move_window_hyprland(self, mock_run, _mock_which):
        def _fake_run(cmd, **kwargs):
            if "monitors" in cmd:
                return mock.Mock(returncode=0, stdout=json.dumps(self.sample_monitors), stderr="")
            if "clients" in cmd:
                sample_clients = [
                    {
                        "address": "0x55d2b86ab100",
                        "mapped": True,
                        "hidden": False,
                        "at": [1920, 100],
                        "size": [800, 600],
                        "workspace": {"id": 1, "name": "1"},
                        "monitor": 0,
                        "class": "firefox",
                        "title": "Mozilla Firefox",
                        "focusHistoryID": 0,
                    }
                ]
                return mock.Mock(returncode=0, stdout=json.dumps(sample_clients), stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = _fake_run

        res = WindowManager.move_window("firefox", "secundario", drag_visual=True)
        self.assertTrue(res["success"])
        self.assertIn("HDMI-A-2", res["message"])

        # Verifica se chamou dispatch movetoworkspacesilent com workspace do HDMI-A-2 (workspace '2')
        dispatches = [call[0][0] for call in mock_run.call_args_list if "dispatch" in call[0][0]]
        self.assertTrue(any("movetoworkspacesilent" in d for d in dispatches))

    @mock.patch.object(WindowManager, "list_windows")
    @mock.patch.object(WindowManager, "resolve_monitor")
    @mock.patch.object(WindowManager, "move_window")
    def test_move_windows_all(self, mock_move, mock_resolve, mock_list):
        mock_list.return_value = [
            WindowInfo(id="0x1", app="firefox", title="Browser", x=1920, y=0, width=800, height=600, monitor_index=0),
            WindowInfo(id="0x2", app="kitty", title="Terminal", x=1920, y=100, width=800, height=600, monitor_index=0),
            WindowInfo(id="0x3", app="code", title="Editor", x=0, y=0, width=800, height=600, monitor_index=1),
        ]
        mock_resolve.return_value = {
            "id": 1,
            "name": "HDMI-A-2",
            "active_workspace": "2",
        }
        mock_move.return_value = {"success": True, "message": "OK"}

        res = WindowManager.move_windows("all", "secundario")
        self.assertTrue(res["success"])
        # Apenas as duas janelas do monitor 0 devem ser movidas para o monitor 1
        self.assertEqual(mock_move.call_count, 2)


if __name__ == "__main__":
    unittest.main()

