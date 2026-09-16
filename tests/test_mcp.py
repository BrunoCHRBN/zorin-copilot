"""Testes do ecossistema de extensibilidade MCP (Model Context Protocol)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.actions import ActionType, DesktopAction
from zorin_copilot.ai.agent_tools import ToolRegistry
from zorin_copilot.mcp.adapter import is_mcp_tool_mutating, register_mcp_tools_in_registry
from zorin_copilot.mcp.client import MCPClient
from zorin_copilot.mcp.manager import DEFAULT_MCP_TEMPLATE, MCPManager, MCPServerConfig
from zorin_copilot.mcp.protocol import (
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPCallResult,
    MCPResource,
    MCPServerState,
    MCPTool,
)
from zorin_copilot.shell.action_status import ActionOutcome
from zorin_copilot.shell.executor import ActionExecutor
from zorin_copilot.shell.risk import RiskLevel, RiskPolicy


class MCPProtocolTest(unittest.TestCase):
    """Testes do protocolo JSON-RPC 2.0 e modelos de dados MCP."""

    def test_jsonrpc_request_serialization(self):
        req = JSONRPCRequest(id=1, method="tools/list", params={"cursor": "abc"})
        payload = req.to_dict()
        self.assertEqual(payload["jsonrpc"], "2.0")
        self.assertEqual(payload["id"], 1)
        self.assertEqual(payload["method"], "tools/list")
        self.assertEqual(payload["params"], {"cursor": "abc"})

    def test_jsonrpc_response_success(self):
        raw = {
            "jsonrpc": "2.0",
            "id": 42,
            "result": {"tools": [{"name": "test"}]},
        }
        parsed = JSONRPCResponse.from_dict(raw)
        self.assertEqual(parsed.id, 42)
        self.assertEqual(parsed.result, {"tools": [{"name": "test"}]})
        self.assertIsNone(parsed.error)
        self.assertFalse(parsed.is_error)

    def test_jsonrpc_response_error(self):
        raw = {
            "jsonrpc": "2.0",
            "id": 99,
            "error": {"code": -32601, "message": "Method not found"},
        }
        parsed = JSONRPCResponse.from_dict(raw)
        self.assertEqual(parsed.id, 99)
        self.assertIsNotNone(parsed.error)
        self.assertEqual(parsed.error.get("code"), -32601)
        self.assertEqual(parsed.error.get("message"), "Method not found")
        self.assertTrue(parsed.is_error)

    def test_jsonrpc_notification(self):
        notif = JSONRPCNotification(method="notifications/tools/list_changed")
        payload = notif.to_dict()
        self.assertEqual(payload["jsonrpc"], "2.0")
        self.assertNotIn("id", payload)
        self.assertEqual(payload["method"], "notifications/tools/list_changed")

    def test_mcp_tool_qualified_name(self):
        tool = MCPTool(name="query", server_name="sqlite", description="Consulta sql")
        self.assertEqual(tool.qualified_name, "mcp__sqlite__query")
        d = tool.to_dict()
        self.assertEqual(d["qualified_name"], "mcp__sqlite__query")

    def test_mcp_call_result_text(self):
        res = MCPCallResult(
            content=[
                {"type": "text", "text": "Linha 1"},
                {"type": "text", "text": "Linha 2"},
            ]
        )
        self.assertEqual(res.text, "Linha 1\nLinha 2")

    def test_is_mcp_tool_mutating_classification(self):
        # Read-only
        self.assertFalse(is_mcp_tool_mutating("get_system_info", "Retorna informações"))
        self.assertFalse(is_mcp_tool_mutating("read_file", "Lê arquivo do disco"))
        self.assertFalse(is_mcp_tool_mutating("list_directory", "Lista arquivos"))
        self.assertFalse(is_mcp_tool_mutating("search_web", "Busca na internet"))
        self.assertFalse(is_mcp_tool_mutating("fetch_weather", "Consulta clima"))

        # Mutating
        self.assertTrue(is_mcp_tool_mutating("create_file", "Cria arquivo"))
        self.assertTrue(is_mcp_tool_mutating("delete_item", "Apaga item"))
        self.assertTrue(is_mcp_tool_mutating("write_data", "Escreve no disco"))
        self.assertTrue(is_mcp_tool_mutating("execute_shell", "Executa comando"))
        self.assertTrue(is_mcp_tool_mutating("kill_process", "Encerra processo"))
        self.assertTrue(is_mcp_tool_mutating("drop_database", "Remove banco"))
        self.assertTrue(is_mcp_tool_mutating("safe_tool_name", "Aviso: esta ação irá alterar e apagar dados"))


class FakeStdioProcess:
    """Emula um processo filho com stdio conectado a pipes em memória."""

    def __init__(self, responses: dict[str, Any] | None = None):
        self.responses = responses or {}
        self.stdin = MagicMock()
        self.stdout = MagicMock()
        self.stderr = MagicMock()
        self.returncode = None
        self._is_alive = True
        self._lock = threading.Lock()
        self._queue: list[str] = []

        self.stdin.write = MagicMock(side_effect=self._on_stdin_write)
        self.stdout.readline = MagicMock(side_effect=self._on_stdout_readline)

    def _on_stdin_write(self, data: str | bytes):
        line = data.decode("utf-8") if isinstance(data, bytes) else data
        try:
            req = json.loads(line.strip())
            method = req.get("method")
            req_id = req.get("id")

            if req_id is not None:
                resp_data = self.responses.get(method, {})
                resp = {"jsonrpc": "2.0", "id": req_id, "result": resp_data}
                with self._lock:
                    self._queue.append(json.dumps(resp) + "\n")
        except Exception:
            pass

    def _on_stdout_readline(self) -> str:
        timeout = 2.0
        start = time.time()
        while time.time() - start < timeout and self._is_alive:
            with self._lock:
                if self._queue:
                    return self._queue.pop(0)
            time.sleep(0.01)
        return ""

    def poll(self):
        return self.returncode

    def terminate(self):
        self._is_alive = False
        self.returncode = 0

    def kill(self):
        self._is_alive = False
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode or 0


class MCPClientTest(unittest.TestCase):
    """Testes do cliente Stdio JSON-RPC MCPClient."""

    def test_mcp_client_handshake_and_list_tools(self):
        fake_responses = {
            "initialize": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "test-server", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
            "tools/list": {
                "tools": [
                    {
                        "name": "read_note",
                        "description": "Lê uma nota",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"title": {"type": "string"}},
                            "required": ["title"],
                        },
                    }
                ]
            },
        }
        fake_proc = FakeStdioProcess(fake_responses)

        with patch("subprocess.Popen", return_value=fake_proc):
            client = MCPClient("test-server", command="echo", args=["test"])
            started = client.start()
            self.assertTrue(started)
            self.assertEqual(client.state, MCPServerState.CONNECTED)
            self.assertEqual(client.server_info.get("name"), "test-server")

            tools = client.list_tools()
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0].name, "read_note")
            self.assertEqual(tools[0].description, "Lê uma nota")

            client.stop()
            self.assertEqual(client.state, MCPServerState.DISCONNECTED)

    def test_mcp_client_call_tool_success(self):
        fake_responses = {
            "initialize": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "test-server", "version": "1.0.0"},
                "capabilities": {},
            },
            "tools/call": {
                "content": [{"type": "text", "text": "Nota encontrada com sucesso"}],
                "isError": False,
            },
        }
        fake_proc = FakeStdioProcess(fake_responses)

        with patch("subprocess.Popen", return_value=fake_proc):
            client = MCPClient("test-server", command="echo")
            client.start()

            result = client.call_tool("read_note", {"title": "Minha Nota"})
            self.assertFalse(result.is_error)
            self.assertIn("Nota encontrada", result.text)
            self.assertEqual(result.content[0]["text"], "Nota encontrada com sucesso")

            client.stop()

    def test_mcp_client_call_tool_server_not_running(self):
        client = MCPClient("offline-server", command="echo")
        # Sem start(), call_tool tentará start() que falhará sem mock ou comando real inválido
        with patch.object(client, "start", return_value=False):
            result = client.call_tool("read_note", {})
            self.assertTrue(result.is_error)
            self.assertIn("indisponível", result.text)


class MCPManagerTest(unittest.TestCase):
    """Testes de ciclo de vida e roteamento no MCPManager."""

    def test_mcp_manager_creates_template_config(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_file = os.path.join(tmp_dir, "mcp_servers.json")
            mgr = MCPManager(config_path=config_file)
            self.assertTrue(os.path.exists(config_file))

            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("mcpServers", data)
            self.assertIn("git", data["mcpServers"])
            self.assertFalse(data["mcpServers"]["git"]["enabled"])

    def test_mcp_manager_load_and_aggregate_tools(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_file = os.path.join(tmp_dir, "mcp_servers.json")
            custom_cfg = {
                "mcpServers": {
                    "fs": {
                        "command": "node",
                        "args": ["server.js"],
                        "enabled": True,
                        "description": "Filesystem server",
                    },
                    "disabled_srv": {
                        "command": "python",
                        "args": ["-m", "dummy"],
                        "enabled": False,
                    },
                }
            }
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(custom_cfg, f)

            mgr = MCPManager(config_path=config_file)
            configs = mgr.get_server_configs()
            self.assertEqual(len(configs), 2)
            self.assertTrue(configs["fs"].enabled)
            self.assertFalse(configs["disabled_srv"].enabled)

            mock_client = MagicMock()
            mock_client.is_running = True
            mock_client.state = MCPServerState.CONNECTED
            mock_client.name = "fs"
            mock_client.tools = [
                MCPTool(
                    name="read_file",
                    server_name="fs",
                    description="Lê arquivo",
                    input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                )
            ]
            mock_client.list_tools.return_value = mock_client.tools

            with patch.object(mgr, "_create_client", return_value=mock_client):
                started = mgr.start_server("fs")
                self.assertTrue(started)
                self.assertIn("fs", mgr.get_running_servers())

                all_tools = mgr.get_all_tools()
                self.assertEqual(len(all_tools), 1)
                tool = all_tools[0]
                self.assertEqual(tool.qualified_name, "mcp__fs__read_file")

                resolved = mgr.get_tool("mcp__fs__read_file")
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.qualified_name, "mcp__fs__read_file")

                mock_client.call_tool.return_value = MCPCallResult(
                    content=[{"type": "text", "text": "conteudo do arquivo"}],
                    is_error=False,
                )
                res = mgr.call_tool("mcp__fs__read_file", {"path": "/tmp/a.txt"})
                self.assertFalse(res.is_error)
                self.assertIn("conteudo do arquivo", res.text)
                mock_client.call_tool.assert_called_once_with("read_file", {"path": "/tmp/a.txt"})

                mgr.stop_server("fs")
                mock_client.stop.assert_called_once()

    def test_mcp_manager_enable_disable_server(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_file = os.path.join(tmp_dir, "mcp_servers.json")
            mgr = MCPManager(config_path=config_file)

            self.assertFalse(mgr.get_server_configs()["git"].enabled)

            mgr.enable_server("git", True)
            self.assertTrue(mgr.get_server_configs()["git"].enabled)

            # Persistência
            mgr2 = MCPManager(config_path=config_file)
            self.assertTrue(mgr2.get_server_configs()["git"].enabled)

            mgr2.enable_server("git", False)
            self.assertFalse(mgr2.get_server_configs()["git"].enabled)


class MCPAdapterAndAgentToolsTest(unittest.TestCase):
    """Testes de adaptação para ToolRegistry e RiskPolicy."""

    def test_register_mcp_tools_into_agent_registry(self):
        mgr = MagicMock()
        tool_safe = MCPTool(
            name="search",
            server_name="web",
            description="Pesquisa web segura",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        tool_mutating = MCPTool(
            name="drop_table",
            server_name="db",
            description="Remove uma tabela do banco de dados",
            input_schema={"type": "object", "properties": {"table": {"type": "string"}}},
        )
        mgr.get_all_tools.return_value = [tool_safe, tool_mutating]
        mgr.call_tool.return_value = MCPCallResult(
            content=[{"type": "text", "text": "resultado ok"}],
            is_error=False,
        )

        registry = ToolRegistry(mcp_manager=mgr)
        names = registry.names()
        self.assertIn("mcp__web__search", names)
        self.assertIn("mcp__db__drop_table", names)

        # Verificação de política de risco
        risk_policy = RiskPolicy()
        level_safe, _ = risk_policy.classify("mcp__web__search")
        level_mut, _ = risk_policy.classify("mcp__db__drop_table")
        self.assertEqual(level_safe, RiskLevel.SAFE)
        self.assertEqual(level_mut, RiskLevel.CONFIRM)

        # Execução via registry.call()
        res = registry.call("mcp__web__search", {"q": "Zorin OS"})
        self.assertTrue(res["ok"])
        self.assertIn("resultado ok", res["output"])
        mgr.call_tool.assert_called_with("mcp__web__search", {"q": "Zorin OS"})

    def test_mcp_tool_execution_failure_handling(self):
        mgr = MagicMock()
        tool = MCPTool(name="fail", server_name="srv", description="Tool de falha")
        mgr.get_all_tools.return_value = [tool]
        mgr.call_tool.return_value = MCPCallResult(
            content=[{"type": "text", "text": "Servidor offline ou sem permissão"}],
            is_error=True,
        )

        registry = ToolRegistry(mcp_manager=mgr)
        res = registry.call("mcp__srv__fail", {})
        self.assertFalse(res["ok"])
        self.assertIn("Servidor offline", res["error"])


class MCPActionExecutorTest(unittest.TestCase):
    """Testes de execução de DesktopAction do tipo ActionType.MCP_TOOL."""

    def test_action_executor_executes_mcp_tool(self):
        mgr = MagicMock()
        mgr.call_tool.return_value = MCPCallResult(
            content=[{"type": "text", "text": "Ação MCP concluída com sucesso"}],
            is_error=False,
        )

        executor = ActionExecutor(mcp_manager=mgr)
        action = DesktopAction(
            action_type=ActionType.MCP_TOOL,
            target="mcp__github__create_issue",
            description="Criar issue no GitHub via MCP",
            params={"title": "Bug fix", "body": "Detalhes"},
        )

        report = executor.execute(action)
        self.assertTrue(report.success)
        self.assertIn("executada com sucesso", report.message)
        self.assertEqual(report.output, "Ação MCP concluída com sucesso")
        mgr.call_tool.assert_called_once_with(
            "mcp__github__create_issue",
            {"title": "Bug fix", "body": "Detalhes"},
        )

    def test_action_executor_mcp_tool_when_manager_not_configured(self):
        executor = ActionExecutor(mcp_manager=None)
        action = DesktopAction(
            action_type=ActionType.MCP_TOOL,
            target="mcp__test__tool",
            description="Teste sem MCP",
        )
        report = executor.execute(action)
        self.assertFalse(report.success)
        self.assertIn("não está ativo", report.message)

    def test_action_executor_mcp_tool_dry_run(self):
        from zorin_copilot.ai.actions import ActionPlan
        mgr = MagicMock()
        executor = ActionExecutor(mcp_manager=mgr)
        action = DesktopAction(
            action_type=ActionType.MCP_TOOL,
            target="mcp__db__delete_record",
            description="Deletar registro",
            params={"id": 10},
        )
        plan = ActionPlan(actions=[action], thought="Teste")

        reports = executor.execute_plan(plan, dry_run=True)
        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0].success)
        self.assertIn("Simulação", reports[0].message)
        mgr.call_tool.assert_not_called()


class MCPPreferencesAndPaletteTest(unittest.TestCase):
    """Testes de integração com PreferencesDialog e CommandPalette."""

    def test_preferences_dialog_mcp_page(self):
        from zorin_copilot.ui.gi_versions import require_gtk4
        require_gtk4()
        from gi.repository import Adw
        Adw.init()
        from zorin_copilot.ui.preferences import PreferencesDialog

        dlg = PreferencesDialog(None)
        self.assertTrue(hasattr(dlg, "mcp_switch_row"))
        self.assertTrue(hasattr(dlg, "mcp_servers_group"))

        # Carregamento do valor inicial
        self.assertTrue(dlg.mcp_switch_row.get_active())
        cfg = dlg._collect_current_config()
        self.assertTrue(cfg.mcp_enabled)

        # Alternar desativação
        dlg.mcp_switch_row.set_active(False)
        cfg2 = dlg._collect_current_config()
        self.assertFalse(cfg2.mcp_enabled)

    def test_command_palette_has_mcp_reload(self):
        from zorin_copilot.ui.gi_versions import require_gtk4
        require_gtk4()
        from gi.repository import Adw
        Adw.init()
        from zorin_copilot.ui.app import CopilotWindow

        app_mock = Adw.Application(application_id="com.zorin.copilot.testmcp")
        win = CopilotWindow(app_mock)
        commands = {c.name: c for c in win.palette_commands()}
        self.assertIn("app.mcp-reload", commands)
        self.assertIn("mcp", commands["app.mcp-reload"].keywords)

        handlers = win._palette_handlers()
        self.assertIn("app.mcp-reload", handlers)
        self.assertEqual(handlers["app.mcp-reload"], win._reload_mcp_servers)


class MCPPhase4AdvancedTest(unittest.TestCase):
    """Testes dos aprimoramentos da Fase 4 do ecossistema MCP."""

    def test_client_handles_tools_list_changed_notification(self):
        client = MCPClient("notify-server", command="echo")
        tools_changed = []
        client.on_tools_changed = lambda name: tools_changed.append(name)

        raw = json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        client._handle_incoming_message(raw)

        self.assertIn("notify-server", tools_changed)

    def test_client_resources_list_and_read(self):
        fake_responses = {
            "initialize": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "res-server", "version": "1.0.0"},
                "capabilities": {"resources": {}},
            },
            "resources/list": {
                "resources": [
                    {
                        "uri": "memo://general/notes.txt",
                        "name": "General Notes",
                        "description": "Anotações do sistema",
                        "mimeType": "text/plain",
                    }
                ]
            },
            "resources/read": {
                "contents": [
                    {"uri": "memo://general/notes.txt", "mimeType": "text/plain", "text": "Conteudo da memoria"}
                ]
            },
        }
        fake_proc = FakeStdioProcess(fake_responses)

        with patch("subprocess.Popen", return_value=fake_proc):
            client = MCPClient("res-server", command="echo")
            client.start()

            resources = client.list_resources()
            self.assertEqual(len(resources), 1)
            self.assertEqual(resources[0].uri, "memo://general/notes.txt")
            self.assertEqual(resources[0].server_name, "res-server")

            content = client.read_resource("memo://general/notes.txt")
            self.assertEqual(content, "Conteudo da memoria")

            client.stop()

    def test_manager_get_all_resources_and_tool_adapter(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_file = os.path.join(tmp_dir, "mcp_servers.json")
            mgr = MCPManager(config_path=config_file)

            mock_client = MagicMock()
            mock_client.is_running = True
            mock_client.state = MCPServerState.CONNECTED
            mock_client.name = "docs"
            mock_res = MCPResource(
                uri="doc://manual",
                name="Manual",
                server_name="docs",
                description="Manual do usuário",
            )
            mock_client.list_resources.return_value = [mock_res]
            mock_client.read_resource.return_value = "Texto completo do manual"

            with patch.object(mgr, "get_client", return_value=mock_client), \
                 patch.object(mgr, "_clients", {"docs": mock_client}):
                resources = mgr.get_all_resources()
                self.assertEqual(len(resources), 1)
                self.assertEqual(resources[0].uri, "doc://manual")

                content = mgr.read_resource("doc://manual")
                self.assertEqual(content, "Texto completo do manual")

                # Teste do adapter de registro de ferramentas:
                registry = ToolRegistry()
                register_mcp_tools_in_registry(registry, mgr)
                self.assertIn("mcp__read_resource", registry.names())
                spec = registry.spec("mcp__read_resource")
                self.assertIsNotNone(spec)

                risk_policy = RiskPolicy()
                level, _ = risk_policy.classify("mcp__read_resource")
                self.assertEqual(level, RiskLevel.SAFE)
            mgr.stop_file_watcher()

    def test_manager_change_listeners(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_file = os.path.join(tmp_dir, "mcp_servers.json")
            mgr = MCPManager(config_path=config_file)

            notifications = []
            listener = lambda: notifications.append(True)

            mgr.add_change_listener(listener)
            mgr._notify_listeners()
            self.assertEqual(len(notifications), 1)

            mgr.remove_change_listener(listener)
            mgr._notify_listeners()
            self.assertEqual(len(notifications), 1)
            mgr.stop_file_watcher()

    def test_preferences_dialog_expander_and_lifecycle(self):
        from zorin_copilot.ui.gi_versions import require_gtk4
        require_gtk4()
        from gi.repository import Adw
        Adw.init()
        from zorin_copilot.ui.preferences import PreferencesDialog

        dlg = PreferencesDialog(None)
        self.assertIsNotNone(dlg._mcp_change_listener)
        self.assertIn(dlg._mcp_change_listener, dlg._mcp_manager._change_listeners)

        # Simula notificação para repopular UI
        dlg._on_mcp_changed()

        # Simula fechamento do diálogo
        dlg.emit("closed")
        self.assertTrue(dlg._is_closed)
        self.assertNotIn(dlg._mcp_change_listener, dlg._mcp_manager._change_listeners)


if __name__ == "__main__":
    unittest.main()
