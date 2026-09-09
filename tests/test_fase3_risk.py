# Decisão de design: valida a Fase 3 (parte A — governança de risco). O classificador é
# puro; o portão de confirmação é exercitado via _dispatch_tool com inspector/driver falsos,
# sem WebSocket nem AT-SPI real.

"""Testes da Fase 3 (parte A): classificador de risco e portão de confirmação ao vivo."""

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
from zorin_copilot.shell.risk import BLOCKED_APPS, RiskLevel, RiskPolicy  # noqa: E402


class _FakeInputDriver:
    def __init__(self):
        self.calls: list[tuple] = []

    def hotkey(self, *keys):
        self.calls.append(keys)
        return True, f"hotkey {'+'.join(keys)}"


class _FakeInspector:
    def __init__(self, root):
        self.root = root
        self.last_text = None

    def get_ui_tree(self, app_name=None):
        return self.root

    def find_element_by_uid(self, root, uid):
        return DesktopInspector.find_element_by_uid(root, uid)

    def text_insert(self, element, text, append=False):
        self.last_text = text
        return True, f"inserido em {element.name}"


def _tree_password():
    pwd = UIElement(name="Senha", role="password_text", uid="0.0")
    return UIElement(name="TestApp", role="application", uid="0", children=[pwd])


def _tree_app():
    return UIElement(name="TestApp", role="application", uid="0")


class RiskPolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = RiskPolicy()

    def test_safe_tools(self):
        for name in ("launch_app", "open_url", "capture_screen", "mouse_click",
                     "keyboard_type", "get_ui_tree", "click_element", "type_element"):
            level, desc = self.policy.classify(name, {})
            self.assertEqual(level, RiskLevel.SAFE, name)
            self.assertEqual(desc, "")

    def test_high_risk_tools(self):
        for name in ("email_compose", "write_document", "organize_directory"):
            level, desc = self.policy.classify(name, {})
            self.assertEqual(level, RiskLevel.CONFIRM, name)
            self.assertTrue(desc)

    def test_destructive_hotkey_blocked(self):
        level, desc = self.policy.classify("keyboard_hotkey", {"keys": ["alt", "f4"]})
        self.assertEqual(level, RiskLevel.CONFIRM)
        self.assertIn("destrutivo", desc)

    def test_benign_hotkey_safe(self):
        level, _ = self.policy.classify("keyboard_hotkey", {"keys": ["ctrl", "c"]})
        self.assertEqual(level, RiskLevel.SAFE)


class LiveRiskGateTest(unittest.TestCase):
    def _client(self, inspector=None, driver=None):
        c = GeminiLiveClient.__new__(GeminiLiveClient)
        c.risk_policy = RiskPolicy()
        c._pending_actions = {}
        c._bypass_risk_gate = False
        c.inspector = inspector or _FakeInspector(_tree_app())
        c.input_driver = driver or _FakeInputDriver()
        return c

    def test_confirm_action_declared(self):
        names = {
            f["name"]
            for grp in LIVE_TOOLS_DECLARATION
            for f in grp["functionDeclarations"]
        }
        self.assertIn("confirm_action", names)

    def test_destructive_hotkey_requires_confirmation(self):
        c = self._client()
        out = c._dispatch_tool("keyboard_hotkey", {"keys": ["alt", "f4"]})
        self.assertFalse(out["success"])
        self.assertTrue(out["requires_confirmation"])
        self.assertIn("confirmation_id", out)
        # nada executado ainda
        self.assertEqual(c.input_driver.calls, [])

    def test_confirm_action_approve_executes(self):
        c = self._client()
        out = c._dispatch_tool("keyboard_hotkey", {"keys": ["alt", "f4"]})
        cid = out["confirmation_id"]
        res = c._dispatch_tool("confirm_action", {"confirmation_id": cid, "approve": True})
        self.assertTrue(res["success"])
        self.assertEqual(c.input_driver.calls, [("alt", "f4")])
        # pending consumido
        self.assertNotIn(cid, c._pending_actions)

    def test_confirm_action_deny_cancels(self):
        c = self._client()
        out = c._dispatch_tool("keyboard_hotkey", {"keys": ["alt", "f4"]})
        cid = out["confirmation_id"]
        res = c._dispatch_tool("confirm_action", {"confirmation_id": cid, "approve": False})
        self.assertTrue(res["success"])
        self.assertIn("cancelada", res["message"])
        self.assertEqual(c.input_driver.calls, [])

    def test_safe_hotkey_executes_immediately(self):
        c = self._client()
        out = c._dispatch_tool("keyboard_hotkey", {"keys": ["ctrl", "c"]})
        self.assertTrue(out["success"])
        self.assertEqual(c.input_driver.calls, [("ctrl", "c")])

    def test_email_compose_requires_confirmation(self):
        c = self._client()
        out = c._dispatch_tool("email_compose", {"recipient": "a@b.com"})
        self.assertFalse(out["success"])
        self.assertTrue(out["requires_confirmation"])

    def test_password_field_requires_confirmation(self):
        c = self._client(inspector=_FakeInspector(_tree_password()))
        out = c._dispatch_tool("type_element", {"uid": "0.0", "text": "segredo"})
        self.assertFalse(out["success"])
        self.assertTrue(out["requires_confirmation"])
        cid = out["confirmation_id"]
        res = c._dispatch_tool("confirm_action", {"confirmation_id": cid, "approve": True})
        self.assertTrue(res["success"])
        self.assertEqual(c.inspector.last_text, "segredo")

    def test_confirm_action_unknown_id_fails(self):
        c = self._client()
        res = c._dispatch_tool("confirm_action", {"confirmation_id": "nope", "approve": True})
        self.assertFalse(res["success"])

    def test_pending_store_and_confirm_isolated(self):
        # Dois riscos distintos: confirmar um não executa o outro.
        c = self._client()
        a = c._dispatch_tool("email_compose", {"recipient": "a@b.com"})
        b = c._dispatch_tool("keyboard_hotkey", {"keys": ["alt", "f4"]})
        self.assertNotEqual(a["confirmation_id"], b["confirmation_id"])
        c._dispatch_tool("confirm_action", {"confirmation_id": a["confirmation_id"], "approve": True})
        self.assertIn(b["confirmation_id"], c._pending_actions)


class LiveBlocklistTest(unittest.TestCase):
    def setUp(self):
        self._saved = set(BLOCKED_APPS)
        BLOCKED_APPS.clear()

    def tearDown(self):
        BLOCKED_APPS.clear()
        BLOCKED_APPS.update(self._saved)

    def _client(self, root):
        c = GeminiLiveClient.__new__(GeminiLiveClient)
        c.risk_policy = None  # foca no blocklist; o gate de risco é ignorado
        c._pending_actions = {}
        c._bypass_risk_gate = False
        c.inspector = _FakeInspector(root)
        return c

    def test_get_ui_tree_refuses_blocked_app(self):
        BLOCKED_APPS.add("Banco Secreto")
        tree = UIElement(name="Banco Secreto", role="application", uid="0")
        c = self._client(tree)
        out = c._dispatch_tool("get_ui_tree", {})
        self.assertFalse(out["success"])
        self.assertIn("lista de bloqueio", out["message"])

    def test_get_ui_tree_allows_normal_app(self):
        tree = UIElement(name="Navegador", role="application", uid="0")
        c = self._client(tree)
        out = c._dispatch_tool("get_ui_tree", {})
        self.assertTrue(out["success"])
        self.assertEqual(out["app"], "Navegador")


class EndSessionRiskTest(unittest.TestCase):
    """`end_session` é classificado pelo ARGUMENTO, não só pelo nome.

    Entrar em HIGH_RISK_TOOLS travaria o modo standby — que é justamente o
    caminho seguro que não pode exigir confirmação a cada despedida.
    """

    def setUp(self):
        self.policy = RiskPolicy()

    def test_standby_is_safe(self):
        self.assertEqual(self.policy.classify("end_session", {"mode": "standby"})[0], RiskLevel.SAFE)

    def test_mode_omitido_eh_standby_e_portanto_seguro(self):
        self.assertEqual(self.policy.classify("end_session", {})[0], RiskLevel.SAFE)
        self.assertEqual(self.policy.classify("end_session", None)[0], RiskLevel.SAFE)

    def test_maiusculo_eh_normalizado(self):
        self.assertEqual(self.policy.classify("end_session", {"mode": "STANDBY"})[0], RiskLevel.SAFE)
        self.assertEqual(self.policy.classify("end_session", {"mode": " Quit "})[0], RiskLevel.CONFIRM)

    def test_quit_requires_confirmation(self):
        level, desc = self.policy.classify("end_session", {"mode": "quit"})
        self.assertEqual(level, RiskLevel.CONFIRM)
        self.assertIn("aplicativo", desc)

    def test_end_session_nao_esta_em_high_risk_tools(self):
        from zorin_copilot.shell.risk import HIGH_RISK_TOOLS

        self.assertNotIn("end_session", HIGH_RISK_TOOLS)

    def test_end_session_declarada(self):
        names = {
            f["name"]
            for grp in LIVE_TOOLS_DECLARATION
            for f in grp["functionDeclarations"]
        }
        self.assertIn("end_session", names)


if __name__ == "__main__":
    unittest.main()
