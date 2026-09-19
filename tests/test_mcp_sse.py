# Decisão de design: Testes automatizados do transporte SSE (Server-Sent Events) para MCP.
# Utiliza ThreadingHTTPServer local nativo (127.0.0.1:0) para simular conexões HTTP/SSE
# concorrentes em paralelo sem bibliotecas de terceiros nem dependência de rede externa.

"""Testes do transporte SSE e conexões remotas do Model Context Protocol (MCP)."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.mcp.client import BaseMCPClient, MCPClient, MCPSSEClient, MCPStdioClient
from zorin_copilot.mcp.manager import MCPManager, MCPServerConfig
from zorin_copilot.mcp.protocol import MCPServerState


class FakeMCPSSEHandler(BaseHTTPRequestHandler):
    """Handler HTTP que simula um servidor MCP SSE oficial."""

    server_received_headers: list[dict[str, str]] = []
    server_received_posts: list[dict[str, Any]] = []

    def log_message(self, format, *args):
        # Suprime logs no stderr durante o teste
        pass

    def do_GET(self):
        # Registra cabeçalhos recebidos para verificação de auth
        headers_dict = {k: v for k, v in self.headers.items()}
        FakeMCPSSEHandler.server_received_headers.append(headers_dict)

        if self.path.startswith("/sse"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            # Envia evento de descoberta de endpoint de envio de POST
            msg = "event: endpoint\ndata: /messages?session_id=test-sess-999\n\n"
            try:
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                return

            # Mantém stream SSE aberta concorrentemente
            while getattr(self.server, "_keep_running", True):
                time.sleep(0.05)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        req_data = json.loads(body) if body else {}
        FakeMCPSSEHandler.server_received_posts.append(req_data)

        method = req_data.get("method")
        req_id = req_data.get("id")

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "fake-cloud-mcp", "version": "2.0.0"},
                    "capabilities": {"tools": {}},
                },
            }
        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "cloud_query",
                            "description": "Executa consulta em cluster cloud",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"sql": {"type": "string"}},
                            },
                        }
                    ]
                },
            }
        elif method == "tools/call":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": "Resultado cloud: 42 registros encontrados."}],
                    "isError": False,
                },
            }
        elif method == "resources/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resources": [
                        {"uri": "cloud://metrics", "name": "Métricas em Nuvem", "mimeType": "application/json"}
                    ]
                },
            }
        elif method == "resources/read":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "contents": [{"text": "cpu_load: 12%"}]
                },
            }
        else:
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {}}

        resp_bytes = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)
        self.wfile.flush()


class TestMCPSSETransport(unittest.TestCase):
    """Bateria de testes para o transporte remoto SSE."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeMCPSSEHandler)
        cls.httpd._keep_running = True
        cls.port = cls.httpd.server_port
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd._keep_running = False
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        FakeMCPSSEHandler.server_received_headers.clear()
        FakeMCPSSEHandler.server_received_posts.clear()

    def test_mcp_client_factory_transport_selection(self):
        client_stdio = MCPClient("local-git", command="uvx", args=["mcp-server-git"])
        self.assertIsInstance(client_stdio, MCPStdioClient)
        self.assertIsInstance(client_stdio, BaseMCPClient)

        client_sse = MCPClient("remote-cloud", transport="sse", url=f"http://127.0.0.1:{self.port}/sse")
        self.assertIsInstance(client_sse, MCPSSEClient)
        self.assertIsInstance(client_sse, BaseMCPClient)

        # Se fornecer URL sem comando, infere automaticamente SSE
        client_inferred = MCPClient("auto-cloud", url=f"http://127.0.0.1:{self.port}/sse")
        self.assertIsInstance(client_inferred, MCPSSEClient)

    def test_mcp_server_config_serialization(self):
        cfg = MCPServerConfig(
            name="cloud-corp",
            transport="sse",
            url="https://api.empresa.com/mcp/sse",
            headers={"Authorization": "Bearer secret123"},
            enabled=True,
            description="Cluster Corporativo",
        )
        d = cfg.to_dict()
        self.assertEqual(d["transport"], "sse")
        self.assertEqual(d["url"], "https://api.empresa.com/mcp/sse")
        self.assertEqual(d["headers"]["Authorization"], "Bearer secret123")

        rebuilt = MCPServerConfig.from_dict("cloud-corp", d)
        self.assertEqual(rebuilt.transport, "sse")
        self.assertEqual(rebuilt.url, "https://api.empresa.com/mcp/sse")
        self.assertEqual(rebuilt.headers["Authorization"], "Bearer secret123")

    def test_sse_connection_handshake_and_endpoint_discovery(self):
        url = f"http://127.0.0.1:{self.port}/sse"
        os.environ["TEST_MCP_TOKEN"] = "token-secreto-xyz"

        client = MCPSSEClient(
            "fake-cloud",
            url=url,
            headers={"Authorization": "Bearer ${TEST_MCP_TOKEN}", "X-Custom-App": "ZorinCopilot"},
        )

        ok = client.start(timeout=5.0)
        self.assertTrue(ok)
        self.assertEqual(client.state, MCPServerState.CONNECTED)
        self.assertEqual(client.server_info.get("name"), "fake-cloud-mcp")
        self.assertEqual(client.server_info.get("version"), "2.0.0")

        # Verifica resolução de endpoint a partir do evento SSE 'endpoint'
        self.assertEqual(client._post_endpoint, f"http://127.0.0.1:{self.port}/messages?session_id=test-sess-999")

        # Verifica se os headers foram expandidos com a variável de ambiente
        found_auth = any(
            h.get("Authorization") == "Bearer token-secreto-xyz"
            for h in FakeMCPSSEHandler.server_received_headers
        )
        self.assertTrue(found_auth)

        client.stop()
        self.assertEqual(client.state, MCPServerState.DISCONNECTED)

    def test_sse_tools_list_and_call(self):
        url = f"http://127.0.0.1:{self.port}/sse"
        client = MCPSSEClient("fake-cloud", url=url)

        tools = client.list_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "cloud_query")
        self.assertEqual(tools[0].qualified_name, "mcp__fake-cloud__cloud_query")

        # Invoca a ferramenta
        res = client.call_tool("cloud_query", {"sql": "SELECT 1"})
        self.assertFalse(res.is_error)
        self.assertIn("Resultado cloud: 42 registros", res.text)

        # Recursos
        resources = client.list_resources()
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0].uri, "cloud://metrics")

        content = client.read_resource("cloud://metrics")
        self.assertEqual(content, "cpu_load: 12%")

        client.stop()

    def test_sse_connection_error_on_invalid_url(self):
        client = MCPSSEClient("error-server", url="http://127.0.0.1:59999/invalid")
        ok = client.start(timeout=1.0)
        self.assertFalse(ok)
        self.assertEqual(client.state, MCPServerState.ERROR)
        self.assertTrue(len(client.last_error) > 0)
        client.stop()

    def test_mcp_manager_integration_with_sse(self, tmp_path=None):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            cfg_data = {
                "mcpServers": {
                    "remote-test": {
                        "transport": "sse",
                        "url": f"http://127.0.0.1:{self.port}/sse",
                        "headers": {"Authorization": "Bearer local-test"},
                        "enabled": True,
                        "description": "Servidor de teste SSE",
                    }
                }
            }
            json.dump(cfg_data, tf)
            cfg_file = tf.name

        try:
            manager = MCPManager(config_path=cfg_file)
            started = manager.start_server("remote-test")
            self.assertTrue(started)

            running = manager.get_running_servers()
            self.assertIn("remote-test", running)

            summary = manager.get_status_summary()
            cloud_entry = next((s for s in summary if s["name"] == "remote-test"), None)
            self.assertIsNotNone(cloud_entry)
            self.assertEqual(cloud_entry["transport"], "sse")
            self.assertEqual(cloud_entry["state"], "connected")
            self.assertEqual(cloud_entry["tool_count"], 1)

            # Executa chamada via manager
            res = manager.call_tool("mcp__remote-test__cloud_query", {"sql": "SELECT 1"})
            self.assertFalse(res.is_error)
            self.assertIn("42 registros", res.text)

            manager.stop_all()
        finally:
            if os.path.exists(cfg_file):
                os.remove(cfg_file)


if __name__ == "__main__":
    unittest.main()
