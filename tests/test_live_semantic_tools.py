# Decisão de design: valida os tools semânticos ao vivo (get_ui_tree / click_element /
# type_element) da Fase 1 sem abrir WebSocket nem carregar AT-SPI real — injeta-se um
# inspector falso e um input driver falso diretamente no GeminiLiveClient.

"""Testes da Fase 1: ferramentas de interação semântica no Gemini Live."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.live import (  # noqa: E402
    GeminiLiveClient,
    LIVE_TOOLS_DECLARATION,
)
from zorin_copilot.core.a11y import DesktopInspector, UIElement  # noqa: E402


class _FakeInputDriver:
    def __init__(self):
        self.calls: list[tuple[str, bool]] = []

    def type_text(self, text, press_enter=False):
        self.calls.append((text, press_enter))
        return True, f"typed '{text}'"


class _FakeInspector:
    def __init__(self, root, semantic=True):
        self.root = root
        self.semantic = semantic
        self.clicked = None
        self.last_text = None

    def get_ui_tree(self, app_name=None):
        return self.root

    def find_element_by_uid(self, root, uid):
        return DesktopInspector.find_element_by_uid(root, uid)

    def do_action(self, element, idx=0):
        self.clicked = element.uid
        return True

    def text_insert(self, element, text, append=False):
        if not self.semantic:
            return False, "sem interface"
        self.last_text = text
        return True, f"inserido em {element.name}"

    def focus_element(self, element):
        return True


def _make_tree():
    return UIElement(
        name="TestApp",
        role="application",
        uid="0",
        children=[
            UIElement(name="Search", role="entry", uid="0.0"),
            UIElement(name="OK", role="push_button", uid="0.1"),
        ],
    )


class LiveSemanticToolsTest(unittest.TestCase):
    def _client(self, semantic=True):
        # Bypass do __init__ (evita carregar managers/AT-SPI reais); só o que o
        # dispatch destes três tools usa é injetado.
        c = GeminiLiveClient.__new__(GeminiLiveClient)
        c.inspector = _FakeInspector(_make_tree(), semantic=semantic)
        c.input_driver = _FakeInputDriver()
        return c

    def test_tools_declared(self):
        names = {
            f["name"]
            for grp in LIVE_TOOLS_DECLARATION
            for f in grp["functionDeclarations"]
        }
        for n in ("get_ui_tree", "click_element", "type_element"):
            self.assertIn(n, names)

    def test_get_ui_tree_returns_summary_with_uids(self):
        c = self._client()
        out = c._dispatch_tool("get_ui_tree", {})
        self.assertTrue(out["success"])
        self.assertIn("[0.0]", out["tree"])
        self.assertIn("[0.1]", out["tree"])
        self.assertEqual(out["app"], "TestApp")

    def test_click_element_by_uid(self):
        c = self._client()
        out = c._dispatch_tool("click_element", {"uid": "0.1"})
        self.assertTrue(out["success"])
        self.assertEqual(c.inspector.clicked, "0.1")

    def test_click_element_not_found(self):
        c = self._client()
        out = c._dispatch_tool("click_element", {"uid": "9.9"})
        self.assertFalse(out["success"])
        self.assertIn("não encontrado", out["message"])

    def test_click_element_missing_uid(self):
        c = self._client()
        out = c._dispatch_tool("click_element", {})
        self.assertFalse(out["success"])
        self.assertIn("obrigatório", out["message"])

    def test_type_element_semantic_path(self):
        c = self._client(semantic=True)
        out = c._dispatch_tool("type_element", {"uid": "0.0", "text": "olá"})
        self.assertTrue(out["success"])
        self.assertEqual(c.inspector.last_text, "olá")
        self.assertEqual(c.input_driver.calls, [])  # não caiu no fallback

    def test_type_element_fallback(self):
        c = self._client(semantic=False)
        out = c._dispatch_tool(
            "type_element", {"uid": "0.0", "text": "fallback", "press_enter": True}
        )
        self.assertTrue(out["success"])
        self.assertIn("input virtual", out["message"])
        self.assertEqual(c.input_driver.calls, [("fallback", True)])

    def test_type_element_not_found(self):
        c = self._client()
        out = c._dispatch_tool("type_element", {"uid": "9.9", "text": "x"})
        self.assertFalse(out["success"])
        self.assertIn("não encontrado", out["message"])

    def test_type_element_missing_text(self):
        c = self._client()
        out = c._dispatch_tool("type_element", {"uid": "0.0"})
        self.assertFalse(out["success"])
        self.assertIn("obrigatório", out["message"])


if __name__ == "__main__":
    unittest.main()
