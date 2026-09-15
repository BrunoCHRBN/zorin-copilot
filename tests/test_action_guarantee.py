"""Testes unitários para a Camada de Garantia de Ação e Anti-Truncamento (Action Guarantee)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from zorin_copilot.ai.actions import ActionType
from zorin_copilot.ai.engine import IntentEngine
from zorin_copilot.core.config import CopilotConfig


class ActionGuaranteeTest(unittest.TestCase):
    """Garante que solicitações de criação e desenvolvimento de documentos geram ações executáveis."""

    def setUp(self):
        self.config = CopilotConfig(gemini_api_key="test-key", auto_execute_safe_actions=True)
        self.engine = IntentEngine(config=self.config)
        self.engine.llm_provider.is_configured = MagicMock(return_value=True)

    def test_inicie_desenvolvimento_introducao_gera_arquivo_e_abertura(self):
        """Quando o usuário pede 'inicie com o desenvolvimento da introducao', a IA não deve só falar: deve gerar ações."""
        # Simula resposta conversacional curta ou truncada do LLM (como a que ocorreu no print com Ollama)
        mock_reply = (
            "Vamos começar estruturando a introdução do seu projeto de teste. "
            "Vou sugerir um esboço que você pode adaptar conforme necessário. "
            "Aqui está um exemplo básico que você pode usar como base:"
        )
        self.engine.llm_provider.chat = MagicMock(return_value=(mock_reply, []))

        plan = self.engine.parse("inicie com o desenvolvimento da introducao")

        # Verifica se o texto truncado foi enriquecido e completado
        self.assertIn("INTRODUÇÃO:", plan.thought)
        self.assertIn("Problema de Pesquisa", plan.thought)
        self.assertIn("Objetivos", plan.thought)
        self.assertIn("Metodologia", plan.thought)

        # Verifica se gerou a ação de escrever arquivo
        write_act = next((a for a in plan.actions if a.action_type == ActionType.WRITE_FILE), None)
        self.assertIsNotNone(write_act, "Deveria ter gerado a ação WRITE_FILE")
        self.assertTrue(write_act.target.endswith(".docx") or write_act.target.endswith(".md"))
        self.assertIn("Gestao_Comercial", write_act.params.get("directory", ""))

        # Verifica se gerou a ação de abrir documento no LibreOffice
        open_act = next((a for a in plan.actions if a.action_type == ActionType.OPEN_DOCUMENT), None)
        self.assertIsNotNone(open_act, "Deveria ter gerado a ação OPEN_DOCUMENT")
        self.assertTrue(open_act.target.endswith(".docx") or open_act.target.endswith(".md"))

    def test_salvar_relatorio_cria_acao_correta(self):
        """Pedido direto para salvar relatório cria ação WRITE_FILE."""
        self.engine.llm_provider.chat = MagicMock(return_value=("Relatório financeiro estruturado.", []))
        plan = self.engine.parse("gere um relatorio de vendas")
        write_act = next((a for a in plan.actions if a.action_type == ActionType.WRITE_FILE), None)
        self.assertIsNotNone(write_act)


if __name__ == "__main__":
    unittest.main()
