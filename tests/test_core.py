# Decisão de design: suíte de testes desacoplada de sessão gráfica real — valida contratos, parse de elementos, provedores de LLM e execução com mocks.

"""Testes unitários do núcleo do Zorin Copilot."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction
from zorin_copilot.ai.providers import BaseLLMProvider, GeminiProvider, OllamaProvider, get_llm_provider
from zorin_copilot.core.a11y import UIElement
from zorin_copilot.core.config import CopilotConfig
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

        url_act = DesktopAction(ActionType.OPEN_URL, "https://mail.google.com")
        self.assertIn("mail.google.com", url_act.describe())

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
                DesktopAction(ActionType.OPEN_URL, "https://mail.google.com"),
                DesktopAction(ActionType.NOTIFY, "Aviso"),
            ],
        )
        reports = executor.execute_plan(plan, dry_run=True)
        self.assertEqual(len(reports), 3)
        self.assertTrue(all(r.success for r in reports))
        self.assertIn("Simulação", reports[0].message)
        self.assertIn("Simulação", reports[1].message)


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = CopilotConfig()
        self.assertEqual(cfg.provider, "gemini")
        self.assertEqual(cfg.gemini_model, "gemini-3.8-flash")
        self.assertFalse(cfg.is_configured())

    def test_is_configured(self):
        cfg = CopilotConfig(provider="gemini", gemini_api_key="test_key_123")
        self.assertTrue(cfg.is_configured())

        cfg_ollama = CopilotConfig(provider="ollama", ollama_url="http://localhost:11434")
        self.assertTrue(cfg_ollama.is_configured())


class ProviderParsingTest(unittest.TestCase):
    def test_parse_json_payload(self):
        raw = json.dumps({
            "explanation": "Para acessar o Gmail abra mail.google.com",
            "actions": [
                {
                    "type": "open_url",
                    "target": "https://mail.google.com",
                    "description": "Abrir o Gmail"
                }
            ]
        })
        explanation, actions = BaseLLMProvider.parse_response_payload(raw)
        self.assertIn("Gmail", explanation)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, ActionType.OPEN_URL)
        self.assertEqual(actions[0].target, "https://mail.google.com")

    def test_parse_markdown_wrapped_json(self):
        raw = "```json\n{\"explanation\": \"Resposta\", \"actions\": []}\n```"
        explanation, actions = BaseLLMProvider.parse_response_payload(raw)
        self.assertEqual(explanation, "Resposta")
        self.assertEqual(actions, [])

    def test_parse_plain_text_fallback(self):
        raw = "Texto puro sem JSON formatado"
        explanation, actions = BaseLLMProvider.parse_response_payload(raw)
        self.assertEqual(explanation, raw)
        self.assertEqual(actions, [])


class IntentEngineTest(unittest.TestCase):
    def setUp(self):
        from zorin_copilot.ai.engine import IntentEngine
        self.engine = IntentEngine(config=CopilotConfig())

    def test_parse_dark_mode(self):
        plan = self.engine.parse("ativar modo escuro")
        self.assertFalse(plan.is_empty)
        self.assertEqual(plan.actions[0].action_type, ActionType.SYSTEM_CONTROL)
        self.assertEqual(plan.actions[0].params.get("setting"), "dark_mode")

    def test_parse_volume(self):
        plan = self.engine.parse("aumentar volume")
        self.assertFalse(plan.is_empty)
        self.assertEqual(plan.actions[0].action_type, ActionType.SYSTEM_CONTROL)
        self.assertEqual(plan.actions[0].params.get("change"), "up")

    def test_parse_click(self):
        plan = self.engine.parse("clicar em Salvar")
        self.assertFalse(plan.is_empty)
        self.assertEqual(plan.actions[0].action_type, ActionType.CLICK)
        self.assertEqual(plan.actions[0].target, "Salvar")

    def test_parse_app_steam(self):
        plan = self.engine.parse("abrir steam")
        self.assertFalse(plan.is_empty)
        self.assertEqual(plan.actions[0].action_type, ActionType.LAUNCH_APP)
        self.assertIn("steam", plan.actions[0].target.lower())

    def test_parse_gmail_query(self):
        plan = self.engine.parse("me explique como acessar o gmail")
        self.assertFalse(plan.is_empty)
        # Deve propor abrir o Gmail e trazer uma explicação detalhada
        self.assertIn("Gmail", plan.thought)
        self.assertEqual(plan.actions[0].action_type, ActionType.OPEN_URL)
        self.assertIn("mail.google.com", plan.actions[0].target)

    def test_parse_with_history(self):
        history = [
            {"role": "user", "content": "O que é Linux?"},
            {"role": "assistant", "content": "Linux é um sistema operacional..."},
        ]
        plan = self.engine.parse("e como uso o terminal?", history=history)
        self.assertFalse(plan.is_empty)


if __name__ == "__main__":
    unittest.main()
