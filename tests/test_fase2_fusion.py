# Decisão de design: Fase 2 (fusão vídeo + AT-SPI). Valida (1) extração de geometria
# (bbox) do AT-SPI no parse, (2) to_summary com bounds, (3) element_at_point (ponto ->
# UID mais específico) e (4) os tools get_ui_tree(com bounds) / locate_element no live.

"""Testes da Fase 2: fusão de vídeo ao vivo com a árvore semântica de acessibilidade."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.live import (  # noqa: E402
    GeminiLiveClient,
    LIVE_TOOLS_DECLARATION,
)
from zorin_copilot.core.a11y import DesktopInspector, UIElement  # noqa: E402


class _FakeCoordType:
    SCREEN = 0


class FakeNode:
    def __init__(
        self,
        name,
        role="unknown",
        children=None,
        actions=("click",),
        editable=None,
        text=None,
        parent=None,
        description="",
        bbox=None,
    ):
        self._name = name
        self._role = role
        self._children = list(children or [])
        self._actions = actions
        self._editable = editable
        self._text = text
        self._parent = parent
        self._desc = description
        self._bbox = bbox or (0, 0, 0, 0)

    def get_name(self):
        return self._name

    def get_role_name(self):
        return self._role

    def get_description(self):
        return self._desc

    def get_action_iface(self):
        return None

    def get_child_count(self):
        return len(self._children)

    def get_child_at_index(self, c):
        return self._children[c] if 0 <= c < len(self._children) else None

    def get_editable_text_iface(self):
        return self._editable

    def get_text_iface(self):
        return self._text

    def grab_focus(self):
        return True

    def get_parent(self):
        return self._parent

    def get_extents(self, coord):
        return tuple(self._bbox)


class FakeAtspi:
    def __init__(self, app_nodes):
        self._apps = list(app_nodes)
        self._desktop = FakeNode("desktop", "desktop", children=self._apps)
        for app in self._apps:
            app._parent = self._desktop
        self.CoordType = _FakeCoordType

    def init(self):
        return True

    def get_desktop(self, n):
        return self._desktop

    def get_focused_element(self):
        return None


class _FakeInspector:
    """Inspector falso que devolve uma árvore fixa (com UIDs e bboxes)."""

    def __init__(self, root):
        self.root = root

    def get_ui_tree(self, app_name=None):
        return self.root

    def find_element_by_uid(self, root, uid):
        return DesktopInspector.find_element_by_uid(root, uid)


def _tree_with_bounds():
    btn = UIElement(name="Enviar", role="push_button", uid="0.0.0", bbox=(120, 120, 100, 40))
    panel = UIElement(
        name="Painel", role="panel", uid="0.0", bbox=(100, 100, 500, 400), children=[btn]
    )
    panel2 = UIElement(name="Lateral", role="panel", uid="0.1", bbox=(700, 100, 500, 400))
    return UIElement(
        name="TestApp",
        role="application",
        uid="0",
        bbox=(0, 0, 1920, 1080),
        children=[panel, panel2],
    )


class A11yFusionTest(unittest.TestCase):
    def test_parse_populates_bbox_from_extents(self):
        search = FakeNode("Search", "entry", bbox=(200, 80, 300, 30))
        app = FakeNode("TestApp", "application", children=[search])
        search._parent = app
        atspi = FakeAtspi([app])
        insp = DesktopInspector(atspi_module=atspi)
        root = insp.inspect_application("TestApp")
        self.assertIsNotNone(root)
        found = root.find(lambda el: el.role == "entry")[0]
        self.assertEqual(found.bbox, (200, 80, 300, 30))

    def test_to_summary_includes_bounds(self):
        tree = _tree_with_bounds()
        with_b = tree.to_summary(include_bounds=True)
        self.assertIn("@(0,0 1920x1080)", with_b)
        self.assertIn("@(120,120 100x40)", with_b)
        without_b = tree.to_summary(include_bounds=False)
        self.assertNotIn("@(", without_b)

    def test_element_at_point_returns_most_specific(self):
        tree = _tree_with_bounds()
        # (150,140) está dentro do botão, do painel e da app; o mais específico é o botão.
        el = DesktopInspector.element_at_point(tree, 150, 140)
        self.assertIsNotNone(el)
        self.assertEqual(el.uid, "0.0.0")
        # ponto fora de tudo
        self.assertIsNone(DesktopInspector.element_at_point(tree, 5000, 5000))


class LiveFusionTest(unittest.TestCase):
    def _client(self):
        c = GeminiLiveClient.__new__(GeminiLiveClient)
        c.inspector = _FakeInspector(_tree_with_bounds())
        c.fence = _FakeFence()
        return c

    def test_locate_element_declared(self):
        names = {
            f["name"]
            for grp in LIVE_TOOLS_DECLARATION
            for f in grp["functionDeclarations"]
        }
        self.assertIn("locate_element", names)
        self.assertIn("get_ui_tree", names)

    def test_get_ui_tree_includes_bounds_by_default(self):
        c = self._client()
        out = c._dispatch_tool("get_ui_tree", {})
        self.assertTrue(out["success"])
        self.assertIn("@(0,0 1920x1080)", out["tree"])

    def test_get_ui_tree_bounds_can_be_disabled(self):
        c = self._client()
        out = c._dispatch_tool("get_ui_tree", {"include_bounds": False})
        self.assertTrue(out["success"])
        self.assertNotIn("@(", out["tree"])

    def test_locate_element_resolves_point_to_uid(self):
        c = self._client()
        # (0.16, 0.28) -> (307, 302) cai no painel 0.0 mas fora do botão 0.0.0.
        out = c._dispatch_tool("locate_element", {"x": 0.16, "y": 0.28, "is_relative": True})
        self.assertTrue(out["success"])
        self.assertEqual(out["uid"], "0.0")  # painel, mais específico que a app

    def test_locate_element_not_found(self):
        c = self._client()
        # Ponto absoluto fora de todos os contornos (app cobre 0..1920x1080).
        out = c._dispatch_tool("locate_element", {"x": 2000, "y": 2000, "is_relative": False})
        self.assertFalse(out["success"])


class _FakeFence:
    """Fence falso: mapeia relativo [0,1] -> absoluto num monitor 1920x1080."""

    def convert_relative_point(self, rel_x, rel_y):
        return int(rel_x * 1920), int(rel_y * 1080)


if __name__ == "__main__":
    unittest.main()
