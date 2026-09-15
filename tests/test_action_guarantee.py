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

    def test_ajuda_inicio_introducao_projeto_integrador_gera_acoes_completas(self):
        """Solicitação exata do usuário 'me ajude com o inicio de uma introducao de um projeto integrador' gera Introducao_Projeto_Integrador.docx."""
        mock_reply = "Aqui está a proposta completa para o seu Projeto Integrador no Senac."
        self.engine.llm_provider.chat = MagicMock(return_value=(mock_reply, []))

        plan = self.engine.parse("me ajude com o inicio de uma introducao de um projeto integrador")

        # Verifica ação WRITE_FILE para Projeto Integrador
        write_act = next((a for a in plan.actions if a.action_type == ActionType.WRITE_FILE), None)
        self.assertIsNotNone(write_act, "Deveria ter gerado WRITE_FILE")
        self.assertEqual(write_act.target, "Introducao_Projeto_Integrador.docx")
        self.assertIn("Gestao_Comercial/TCC_Artigos", write_act.params.get("directory", ""))

        # Verifica ação OPEN_DOCUMENT
        open_act = next((a for a in plan.actions if a.action_type == ActionType.OPEN_DOCUMENT), None)
        self.assertIsNotNone(open_act, "Deveria ter gerado OPEN_DOCUMENT")
        self.assertIn("Introducao_Projeto_Integrador.docx", open_act.target)

    def test_truncated_json_repair_and_anti_leak(self):
        """Valida que JSON truncado por limite de tokens é reparado e nunca vaza sintaxe JSON crua."""
        from zorin_copilot.ai.providers import BaseLLMProvider

        truncated_sample = (
            '{\n'
            '  "explanation": "Com certeza! Para o Projeto Integrador em Gestão Comercial do Senac...\\n\\n### 1. INTRODUÇÃO",\n'
            '  "actions": [\n'
            '    {\n'
            '      "type": "write_file",\n'
            '      "target": "Introducao_Projeto_Integrador.docx",\n'
            '      "description": "Salvar proposta em Word",\n'
            '      "params": {\n'
            '        "filename": "Introducao_Projeto_Integrador.docx",\n'
            '        "directory": "~/Documentos/Gestao_Comercial/TCC_Artigos",\n'
            '        "content": "# PROJETO INTEGRADOR\\n### 1. INTRODUÇÃO... ###'
        )

        explanation, actions = BaseLLMProvider.parse_response_payload(truncated_sample)

        # Não deve vazar chave ou sintaxe JSON no texto
        self.assertFalse(explanation.strip().startswith("{"))
        self.assertNotIn('"explanation":', explanation)
        self.assertIn("Com certeza! Para o Projeto Integrador", explanation)

        # Deve extrair a ação de escrita de arquivo com o conteúdo preservado
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action_type, ActionType.WRITE_FILE)
        self.assertEqual(actions[0].target, "Introducao_Projeto_Integrador.docx")
        self.assertTrue(len(actions[0].params.get("content", "")) > 10)

    def test_auto_content_write_file_resolves_to_explanation(self):
        """Valida que quando o modelo emite content='auto', o provider injeta a explanation integral."""
        from zorin_copilot.ai.providers import BaseLLMProvider

        payload = (
            '{\n'
            '  "explanation": "Texto integral elaborado com contextualização, problema de pesquisa e objetivos.",\n'
            '  "actions": [\n'
            '    {\n'
            '      "type": "write_file",\n'
            '      "target": "Artigo.docx",\n'
            '      "params": {\n'
            '        "filename": "Artigo.docx",\n'
            '        "content": "auto"\n'
            '      }\n'
            '    }\n'
            '  ]\n'
            '}'
        )

        explanation, actions = BaseLLMProvider.parse_response_payload(payload)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].params.get("content"), explanation)


if __name__ == "__main__":
    unittest.main()
