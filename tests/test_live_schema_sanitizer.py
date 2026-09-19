"""Testes unitários para o sanitizador de esquemas Protobuf do Gemini Live API."""

from __future__ import annotations

import unittest
from typing import Any

from zorin_copilot.ai.agent_tools import (
    GEMINI_ALLOWED_SCHEMA_KEYS,
    ToolRegistry,
    ToolSpec,
    sanitize_gemini_schema,
)
from zorin_copilot.ai.live import GeminiLiveClient
from zorin_copilot.mcp.adapter import convert_mcp_input_schema


class TestLiveSchemaSanitizer(unittest.TestCase):
    def test_strip_exclusive_maximum_and_minimum(self):
        mcp_param_schema = {
            "type": "integer",
            "title": "Max Length",
            "description": "Maximum number of characters to return.",
            "default": 5000,
            "exclusiveMaximum": 1000000,
            "exclusiveMinimum": 0,
        }
        clean = sanitize_gemini_schema(mcp_param_schema)
        self.assertEqual(clean["type"], "INTEGER")
        self.assertIn("Maximum number of characters to return.", clean["description"])
        self.assertIn("padrão: 5000", clean["description"])
        self.assertNotIn("exclusiveMaximum", clean)
        self.assertNotIn("exclusiveMinimum", clean)
        self.assertNotIn("title", clean)
        self.assertNotIn("default", clean)

    def test_strip_additional_properties_and_constraints(self):
        complex_schema = {
            "type": "object",
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#",
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "minLength": 1,
                    "maxLength": 2048,
                    "title": "Target URL",
                    "description": "The URL to fetch",
                },
                "count": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["url"],
        }
        clean = sanitize_gemini_schema(complex_schema)
        self.assertEqual(clean["type"], "OBJECT")
        self.assertNotIn("additionalProperties", clean)
        self.assertNotIn("$schema", clean)
        self.assertEqual(clean["required"], ["url"])
        self.assertNotIn("minLength", clean["properties"]["url"])
        self.assertNotIn("maxLength", clean["properties"]["url"])
        self.assertNotIn("title", clean["properties"]["url"])
        self.assertNotIn("minimum", clean["properties"]["count"])
        self.assertNotIn("maximum", clean["properties"]["count"])
        self.assertEqual(clean["properties"]["url"]["type"], "STRING")
        self.assertEqual(clean["properties"]["count"]["type"], "NUMBER")

    def test_anyof_nullable_conversion(self):
        anyof_schema = {
            "anyOf": [
                {"type": "string"},
                {"type": "null"},
            ],
            "description": "Nome da branch base (opcional)",
            "title": "Base Branch",
        }
        clean = sanitize_gemini_schema(anyof_schema)
        self.assertEqual(clean["type"], "STRING")
        self.assertTrue(clean["nullable"])
        self.assertIn("Nome da branch base", clean["description"])
        self.assertNotIn("anyOf", clean)
        self.assertNotIn("title", clean)

    def test_convert_mcp_input_schema_integration(self):
        raw_mcp = {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "title": "Code Snippet",
                    "description": "Source code to evaluate",
                },
                "timeout": {
                    "type": "integer",
                    "default": 30,
                    "minimum": 1,
                    "maximum": 300,
                },
            },
            "required": ["code"],
        }
        converted = convert_mcp_input_schema(raw_mcp)
        self.assertEqual(converted["type"], "OBJECT")
        self.assertIn("code", converted["properties"])
        self.assertIn("timeout", converted["properties"])
        self.assertNotIn("title", converted["properties"]["code"])
        self.assertNotIn("minimum", converted["properties"]["timeout"])
        self.assertNotIn("maximum", converted["properties"]["timeout"])
        self.assertNotIn("default", converted["properties"]["timeout"])

    def test_live_tools_payload_strict_compliance(self):
        # Simula cliente Live com ToolRegistry contendo ferramenta com schema MCP agressivo
        client = GeminiLiveClient.__new__(GeminiLiveClient)
        client.config = None
        registry = ToolRegistry()

        # Injeta ferramenta estilo mcp-server-fetch que gerou o erro 1007
        fetch_mcp_schema = {
            "type": "OBJECT",
            "properties": {
                "url": {"type": "string", "minLength": 1, "title": "Url"},
                "max_length": {
                    "type": "integer",
                    "default": 5000,
                    "exclusiveMaximum": 1000000,
                    "exclusiveMinimum": 0,
                    "title": "Max Length",
                },
                "start_index": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "title": "Start Index",
                },
            },
            "required": ["url"],
        }
        registry.register(
            ToolSpec(
                name="mcp__fetch__fetch",
                description="Fetch webpage content",
                parameters=fetch_mcp_schema,
                handler=lambda args: {"ok": True},
            )
        )
        client.tool_registry = registry

        payload = client._live_tools_payload()
        self.assertIsInstance(payload, list)
        self.assertIn("functionDeclarations", payload[0])
        decls = payload[0]["functionDeclarations"]

        # Verifica conformidade absoluta com Gemini Protobuf Schema
        def verify_no_forbidden_keys(obj: Any, path: str = ""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if path != "root" and k not in GEMINI_ALLOWED_SCHEMA_KEYS:
                        self.fail(f"Chave proibida no Gemini Live encontrada: '{k}' em {path}")
                    if k == "properties" and isinstance(v, dict):
                        for pk, pv in v.items():
                            verify_no_forbidden_keys(pv, f"{path}.properties.{pk}")
                    elif k == "items" and isinstance(v, dict):
                        verify_no_forbidden_keys(v, f"{path}.items")

        for d in decls:
            self.assertIn("name", d)
            self.assertIn("description", d)
            self.assertIn("parameters", d)
            params = d["parameters"]
            self.assertEqual(params["type"], "OBJECT")
            verify_no_forbidden_keys(params, f"{d['name']}.parameters")

        # Garante que a ferramenta fetch foi sanitizada
        fetch_decl = next(d for d in decls if d["name"] == "mcp__fetch__fetch")
        props = fetch_decl["parameters"]["properties"]
        self.assertNotIn("exclusiveMaximum", props["max_length"])
        self.assertNotIn("exclusiveMinimum", props["max_length"])
        self.assertNotIn("minimum", props["start_index"])
        self.assertNotIn("minLength", props["url"])
        self.assertEqual(props["max_length"]["type"], "INTEGER")
        self.assertEqual(props["url"]["type"], "STRING")


if __name__ == "__main__":
    unittest.main()
