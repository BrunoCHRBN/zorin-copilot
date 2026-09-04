# Decisão de design: suíte de testes desacoplada de sessão gráfica real — valida contratos, parse de elementos e execução com mocks.

"""Testes unitários do núcleo do Zorin Copilot."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction
from zorin_copilot.core.a11y import DesktopInspector, UIElement
from zorin_copilot.shell.executor import ActionExecutor


class UIElementTest(unittest.TestCase):
    def test_interactive_roles(self):
        btn = UIElement(name="Salvar", role="push_button")
        self.assertTrue(btn.is_interactive)

        label = UIElement(name="Texto", role="label")
        self.assertFalse(label.is_interactive)

        actionable = UIElement(name="Custom", role="panel", actions=("click",))
        self.assertTrue(actionable.is_interactive)

    def test_find_recursive(self):
        root = UIElement(
            name="Janela Principal",
            role="frame",
            children=[
                UIElement(name="Header", role="panel"),
                UIElement(
                    name="Form",
                    role="panel",
                    children=[
                        UIElement(name="Enviar", role="push_button"),
                        UIElement(name="Cancelar", role="push_button"),
                    ],
                ),
            ],
        )
        buttons = root.find(lambda el: el.role == "push_button")
        self.assertEqual(len(buttons), 2)
        self.assertEqual([b.name for b in buttons], ["Enviar", "Cancelar"])

    def test_summary_format(self):
        el = UIElement(name="OK", role="push_button", actions=("press",))
        summary = el.to_summary()
        self.assertIn("push_button: 'OK'", summary)
        self.assertIn("press", summary)


class ActionTest(unittest.TestCase):
    def test_action_describe(self):
        act = DesktopAction(ActionType.LAUNCH_APP, "firefox")
        self.assertIn("firefox", act.describe())

    def test_plan_risk(self):
        safe_plan = ActionPlan("Pensamento", [DesktopAction(ActionType.NOTIFY, "Alerta")])
        self.assertFalse(safe_plan.has_high_risk_actions)

        risky_plan = ActionPlan("Pensamento", [DesktopAction(ActionType.COMMAND, "rm -rf", requires_confirmation=True)])
        self.assertTrue(risky_plan.has_high_risk_actions)


class ExecutorTest(unittest.TestCase):
    def test_dry_run_plan(self):
        executor = ActionExecutor()
        plan = ActionPlan(
            thought="Teste",
            actions=[
                DesktopAction(ActionType.LAUNCH_APP, "gnome-terminal"),
                DesktopAction(ActionType.NOTIFY, "Aviso"),
            ],
        )
        reports = executor.execute_plan(plan, dry_run=True)
        self.assertEqual(len(reports), 2)
        self.assertTrue(all(r.success for r in reports))
        self.assertIn("Simulação", reports[0].message)


if __name__ == "__main__":
    unittest.main()
