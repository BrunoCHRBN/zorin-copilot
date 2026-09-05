# Decisão de design: Testes unitários para o Pilar 2 (Self-Healing Desktop & Smart OCR)
# cobrindo parsing de payload multimodal, extração de texto, geração e execução segura de comandos de correção.

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ai.actions import ActionPlan, ActionType, DesktopAction
from zorin_copilot.ai.engine import IntentEngine
from zorin_copilot.ai.providers import BaseLLMProvider
from zorin_copilot.core.clipboard import ClipboardService
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.shell.executor import ActionExecutor


class SmartOCRAndSelfHealingParsingTest(unittest.TestCase):
    """Testa o parsing de respostas JSON e Markdown com Smart OCR e Fix Commands."""

    def test_parse_smart_ocr_payload(self):
        raw = """{
            "explanation": "Identificado código Python na tela.",
            "extracted_text": "def calculate_total(items):\\n    return sum(items)",
            "extracted_kind": "code",
            "actions": []
        }"""
        expl, actions = BaseLLMProvider.parse_response_payload(raw)
        self.assertIn("Identificado código", expl)
        ocr_actions = [a for a in actions if a.action_type == ActionType.SMART_OCR]
        self.assertEqual(len(ocr_actions), 1)
        self.assertEqual(ocr_actions[0].target, "def calculate_total(items):\n    return sum(items)")
        self.assertEqual(ocr_actions[0].params.get("kind"), "código")

    def test_parse_fix_command_in_actions(self):
        raw = """{
            "explanation": "O erro de dpkg lock indica que uma instalação anterior foi interrompida.",
            "actions": [
                {
                    "type": "fix_command",
                    "target": "Corrigir pacotes quebrados no APT",
                    "description": "Executar reparo automático no terminal",
                    "params": {
                        "command": "sudo apt --fix-broken install -y",
                        "requires_sudo": true
                    }
                }
            ]
        }"""
        expl, actions = BaseLLMProvider.parse_response_payload(raw)
        self.assertIn("dpkg lock", expl)
        fix_actions = [a for a in actions if a.action_type == ActionType.FIX_COMMAND]
        self.assertEqual(len(fix_actions), 1)
        self.assertEqual(fix_actions[0].params.get("command"), "sudo apt --fix-broken install -y")
        self.assertTrue(fix_actions[0].params.get("requires_sudo"))

    def test_parse_fix_command_at_root(self):
        raw = """{
            "explanation": "O serviço Bluetooth está desativado.",
            "fix_command": "sudo systemctl restart bluetooth",
            "actions": []
        }"""
        expl, actions = BaseLLMProvider.parse_response_payload(raw)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, ActionType.FIX_COMMAND)
        self.assertEqual(actions[0].params.get("command"), "sudo systemctl restart bluetooth")

    def test_markdown_fallback_detects_fix_command(self):
        raw = """Identifiquei que a biblioteca requests não está instalada. Execute o comando abaixo:
```bash
pip install requests
```
Isso resolverá o ModuleNotFoundError."""
        expl, actions = BaseLLMProvider.parse_response_payload(raw)
        self.assertIn("ModuleNotFoundError", expl)
        fix_actions = [a for a in actions if a.action_type == ActionType.FIX_COMMAND]
        self.assertEqual(len(fix_actions), 1)
        self.assertEqual(fix_actions[0].params.get("command"), "pip install requests")


class SelfHealingExecutionTest(unittest.TestCase):
    """Testa a execução de ações de Smart OCR e comandos de correção no ActionExecutor."""

    def setUp(self):
        self.executor = ActionExecutor()

    @patch.object(ClipboardService, "set_text", return_value=True)
    def test_execute_smart_ocr_action(self, mock_clip):
        act = DesktopAction(
            ActionType.SMART_OCR,
            target="console.log('Hello World');",
            params={"kind": "código"},
        )
        report = self.executor._execute_single(act)
        self.assertTrue(report.success)
        self.assertIn("código extraído da tela copiado", report.message.lower())
        mock_clip.assert_called_once_with("console.log('Hello World');")

    @patch("subprocess.Popen")
    @patch("shutil.which", return_value="/usr/bin/gnome-terminal")
    def test_execute_fix_command_interactive(self, _mock_which, mock_popen):
        act = DesktopAction(
            ActionType.FIX_COMMAND,
            target="Reparar pacotes",
            params={"command": "sudo apt update", "requires_sudo": True, "terminal": True},
        )
        report = self.executor._execute_single(act)
        self.assertTrue(report.success)
        self.assertIn("Terminal aberto", report.message)
        mock_popen.assert_called_once()
        cmd_args = mock_popen.call_args[0][0]
        self.assertEqual(cmd_args[0], "/usr/bin/gnome-terminal")

    @patch("subprocess.run")
    def test_execute_fix_command_background(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Sucesso total", stderr="")
        act = DesktopAction(
            ActionType.FIX_COMMAND,
            target="Criar pasta",
            params={"command": "mkdir -p ~/TestFolder", "requires_sudo": False, "terminal": False},
        )
        report = self.executor._execute_single(act)
        self.assertTrue(report.success)
        self.assertEqual(report.message, "Sucesso total")


class IntentEngineSelfHealingTest(unittest.TestCase):
    """Testa a integração do IntentEngine com Smart OCR e Auto-Cura."""

    def test_engine_plan_populates_extracted_ocr(self):
        config = CopilotConfig(gemini_api_key="test-key", provider="gemini")
        engine = IntentEngine(config=config)
        mock_provider = MagicMock()
        mock_provider.is_configured.return_value = True

        mock_actions = [
            DesktopAction(
                ActionType.SMART_OCR,
                target="const x = 42;",
                params={"kind": "código"},
            ),
            DesktopAction(
                ActionType.FIX_COMMAND,
                target="Corrigir sintaxe",
                params={"command": "eslint --fix", "requires_sudo": False},
            ),
        ]
        mock_provider.chat.return_value = ("Erro de linter identificado.", mock_actions)
        engine.llm_provider = mock_provider

        plan = engine.parse("analise este erro", image_bytes=b"fake-image-bytes", is_area_capture=True)
        self.assertEqual(plan.extracted_text, "const x = 42;")
        self.assertEqual(plan.extracted_kind, "código")
        self.assertEqual(len(plan.actions), 2)
        self.assertEqual(plan.actions[1].action_type, ActionType.FIX_COMMAND)


if __name__ == "__main__":
    unittest.main()
