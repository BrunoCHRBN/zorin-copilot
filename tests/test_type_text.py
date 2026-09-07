# Decisão de design: suíte determinística com um AT-SPI falso (sem display, sem gi.real)
# para validar a Fase 0 — UIDs estáveis, text_insert semântico (AT-SPI) e o branch
# TYPE_TEXT do executor (caminho semântico + fallback via input virtual).

"""Testes da Fase 0: inserção de texto semântica e digitação via comando do agente."""

from __future__ import annotations

import os
import sys
import unittest
from typing import Any

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.ai.actions import ActionType, DesktopAction  # noqa: E402
from zorin_copilot.core.a11y import DesktopInspector, UIElement  # noqa: E402
from zorin_copilot.shell.executor import ActionExecutor  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes de AT-SPI2 (espelham a superfície gi.repository.Atspi que usamos)      #
# --------------------------------------------------------------------------- #
class _FakeActionIface:
    def __init__(self, names=("click",)):
        self._names = list(names)

    def get_n_actions(self):
        return len(self._names)

    def get_action_name(self, k):
        return self._names[k] if 0 <= k < len(self._names) else None

    def do_action(self, k):
        return True


class _FakeEditableText:
    def __init__(self, record):
        self._record = record

    def set_text_contents(self, text):
        self._record["set"] = text

    def insert_text(self, text, position):
        self._record["insert"] = (text, position)


class _FakeText:
    def __init__(self, length=0, record=None):
        self._length = length
        self._record = record or {}

    def get_character_count(self):
        return self._length

    def insert_text(self, position, text):
        self._record["text_insert"] = (position, text)


class FakeNode:
    """Nó de acessibilidade falso com a mesma API do gi Atspi que consumimos."""

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
    ):
        self._name = name
        self._role = role
        self._children = list(children or [])
        self._actions = actions
        self._editable = editable
        self._text = text
        self._parent = parent
        self._desc = description

    def get_name(self):
        return self._name

    def get_role_name(self):
        return self._role

    def get_description(self):
        return self._desc

    def get_action_iface(self):
        return _FakeActionIface(self._actions) if self._actions else None

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


class FakeAtspi:
    def __init__(self, app_nodes):
        self._apps = list(app_nodes)
        self._desktop = FakeNode("desktop", "desktop", children=self._apps)
        for app in self._apps:
            app._parent = self._desktop
        self._focused: FakeNode | None = None

    def init(self):
        return True

    def get_desktop(self, n):
        return self._desktop

    def get_focused_element(self):
        return self._focused


def _make_inspector(editable=True):
    """Monta uma árvore fake: TestApp contém botão 'OK' e campo 'Search' (entry)."""
    record: dict[str, Any] = {}
    editable_iface = _FakeEditableText(record) if editable else None
    # Sem EditableText, modela campo sem NENHUMA interface de texto (cai no fallback).
    text_iface = _FakeText(length=0, record=record) if editable else None
    search = FakeNode(
        "Search", "entry", editable=editable_iface, text=text_iface
    )
    button = FakeNode("OK", "push_button")
    app = FakeNode("TestApp", "application", children=[search, button])
    search._parent = app
    button._parent = app
    atspi = FakeAtspi([app])
    insp = DesktopInspector(atspi_module=atspi)
    # expõe o record para inspeção nos testes
    insp._record = record  # type: ignore[attr-defined]
    return insp


class A11yPhase0Test(unittest.TestCase):
    def test_uid_assignment_is_path_based(self):
        insp = _make_inspector()
        root = insp.inspect_application("TestApp")
        self.assertIsNotNone(root)
        assert root is not None
        self.assertEqual(root.uid, "0")  # app é o índice 0 do desktop
        entries = root.find(lambda el: el.role == "entry")
        self.assertEqual(entries[0].uid, "0.0")
        buttons = root.find(lambda el: el.role == "push_button")
        self.assertEqual(buttons[0].uid, "0.1")

    def test_find_element_by_uid(self):
        insp = _make_inspector()
        root = insp.inspect_application("TestApp")
        assert root is not None
        el = DesktopInspector.find_element_by_uid(root, "0.1")
        self.assertIsNotNone(el)
        self.assertEqual(el.name, "OK")
        missing = DesktopInspector.find_element_by_uid(root, "9.9")
        self.assertIsNone(missing)

    def test_to_summary_includes_uid(self):
        insp = _make_inspector()
        root = insp.inspect_application("TestApp")
        assert root is not None
        summary = root.to_summary()
        self.assertIn("[0]", summary)
        self.assertIn("[0.0]", summary)
        self.assertIn("entry: 'Search'", summary)

    def test_text_insert_semantic_via_editable(self):
        insp = _make_inspector(editable=True)
        root = insp.inspect_application("TestApp")
        assert root is not None
        search = root.find(lambda el: el.role == "entry")[0]
        ok, msg = insp.text_insert(search, "olá mundo")
        self.assertTrue(ok)
        self.assertIn("EditableText", msg)
        self.assertEqual(insp._record.get("set"), "olá mundo")  # type: ignore[attr-defined]

    def test_text_insert_append_uses_character_count(self):
        insp = _make_inspector(editable=True)
        # Força um texto pré-existente de 5 caracteres
        root = insp.inspect_application("TestApp")
        assert root is not None
        search = root.find(lambda el: el.role == "entry")[0]
        # reconfigura o Text para simular conteúdo existente
        search.raw_ref._text = _FakeText(length=5, record=insp._record)  # type: ignore[attr-defined]
        ok, _ = insp.text_insert(search, "abc", append=True)
        self.assertTrue(ok)
        self.assertEqual(insp._record.get("insert"), ("abc", 5))  # type: ignore[attr-defined]

    def test_text_insert_without_editable_interface_fails(self):
        insp = _make_inspector(editable=False)
        root = insp.inspect_application("TestApp")
        assert root is not None
        search = root.find(lambda el: el.role == "entry")[0]
        ok, msg = insp.text_insert(search, "x")
        self.assertFalse(ok)
        self.assertIn("não expõe interface", msg)

    def test_get_focused_app(self):
        insp = _make_inspector()
        search = insp.inspect_application("TestApp").find(lambda el: el.role == "entry")[0]
        insp._atspi._focused = search.raw_ref  # type: ignore[attr-defined]
        self.assertEqual(insp.get_focused_app(), "TestApp")

    def test_get_ui_tree_uses_focused_app(self):
        insp = _make_inspector()
        search = insp.inspect_application("TestApp").find(lambda el: el.role == "entry")[0]
        insp._atspi._focused = search.raw_ref  # type: ignore[attr-defined]
        tree = insp.get_ui_tree()
        self.assertIsNotNone(tree)
        self.assertEqual(tree.name, "TestApp")


class _FakeInputDriver:
    def __init__(self):
        self.calls: list[tuple[str, bool]] = []

    def type_text(self, text, press_enter=False):
        self.calls.append((text, press_enter))
        return True, f"typed '{text}'"


class ExecutorTypeTextTest(unittest.TestCase):
    def _executor(self, editable=True, driver=None):
        insp = _make_inspector(editable=editable)
        return ActionExecutor(
            inspector=insp, input_driver=driver or _FakeInputDriver()
        )

    def test_type_text_semantic_path(self):
        ex = self._executor(editable=True)
        action = DesktopAction(
            ActionType.TYPE_TEXT, "Search", {"text": "relatório 2024"}
        )
        reports = ex.execute_plan(__import__("zorin_copilot.ai.actions", fromlist=["ActionPlan"]).ActionPlan(thought="", actions=[action]))
        rep = reports[0]
        self.assertTrue(rep.success)
        self.assertIn("EditableText", rep.message)
        # não deve ter caído no input virtual
        self.assertEqual(ex.input_driver.calls, [])

    def test_type_text_fallback_to_virtual_input(self):
        ex = self._executor(editable=False)
        action = DesktopAction(
            ActionType.TYPE_TEXT, "Search", {"text": "fallback", "press_enter": True}
        )
        from zorin_copilot.ai.actions import ActionPlan

        reports = ex.execute_plan(ActionPlan(thought="", actions=[action]))
        rep = reports[0]
        self.assertTrue(rep.success)
        self.assertIn("input virtual", rep.message)
        self.assertEqual(ex.input_driver.calls, [("fallback", True)])

    def test_type_text_field_not_found(self):
        ex = self._executor()
        action = DesktopAction(
            ActionType.TYPE_TEXT, "Campo inexistente", {"text": "x"}
        )
        from zorin_copilot.ai.actions import ActionPlan

        reports = ex.execute_plan(ActionPlan(thought="", actions=[action]))
        rep = reports[0]
        self.assertFalse(rep.success)
        self.assertIn("não localizado", rep.message)

    def test_type_text_empty_text_fails(self):
        ex = self._executor()
        action = DesktopAction(ActionType.TYPE_TEXT, "Search", {"text": ""})
        from zorin_copilot.ai.actions import ActionPlan

        reports = ex.execute_plan(ActionPlan(thought="", actions=[action]))
        rep = reports[0]
        self.assertFalse(rep.success)
        self.assertIn("Nenhum texto", rep.message)

    def test_type_text_dry_run(self):
        ex = self._executor()
        action = DesktopAction(
            ActionType.TYPE_TEXT, "Search", {"text": "simulado"}
        )
        from zorin_copilot.ai.actions import ActionPlan

        reports = ex.execute_plan(
            ActionPlan(thought="", actions=[action]), dry_run=True
        )
        rep = reports[0]
        self.assertTrue(rep.success)
        self.assertIn("Simulação", rep.message)


if __name__ == "__main__":
    unittest.main()
