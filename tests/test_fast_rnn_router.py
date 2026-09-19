"""Testes unitários para o roteador recorrente de intenções rápidas (FastRNNRouter)."""

from __future__ import annotations

import unittest
from zorin_copilot.ai.fast_rnn_router import FastRNNRouter, PrimaryIntent


class TestFastRNNRouter(unittest.TestCase):
    def test_media_control_intent(self):
        prompts = [
            ("tocar música Bohemian Rhapsody", "play", "Bohemian Rhapsody"),
            ("pausar a música", "pause", ""),
            ("próxima faixa", "next", ""),
            ("voltar música", "previous", ""),
        ]
        for prompt, expected_action, query_sub in prompts:
            pred = FastRNNRouter.classify(prompt)
            self.assertEqual(pred.intent, PrimaryIntent.MEDIA_CONTROL)
            self.assertTrue(pred.is_fast_path)
            self.assertEqual(pred.action_type, expected_action)
            self.assertGreaterEqual(pred.confidence, 0.90)
            if query_sub:
                self.assertIn(query_sub.lower(), pred.params.get("query", "").lower())

    def test_system_control_intent(self):
        prompts = [
            ("aumentar o volume", "volume_up"),
            ("abaixa o volume", "volume_down"),
            ("mutar o áudio", "mute"),
            ("ativar modo escuro", "dark_mode"),
            ("bloquear a tela", "lock"),
        ]
        for prompt, expected_action in prompts:
            pred = FastRNNRouter.classify(prompt)
            self.assertEqual(pred.intent, PrimaryIntent.SYSTEM_CONTROL)
            self.assertTrue(pred.is_fast_path)
            self.assertEqual(pred.action_type, expected_action)
            self.assertGreaterEqual(pred.confidence, 0.90)

    def test_window_management_intent(self):
        prompts = [
            ("minimizar a janela", "minimize"),
            ("maximizar", "maximize"),
            ("fechar janela", "close"),
            ("focar no firefox", "focus"),
        ]
        for prompt, expected_action in prompts:
            pred = FastRNNRouter.classify(prompt)
            self.assertEqual(pred.intent, PrimaryIntent.WINDOW_MANAGEMENT)
            self.assertTrue(pred.is_fast_path)
            self.assertEqual(pred.action_type, expected_action)

    def test_network_inspect_intent(self):
        prompts = [
            ("como tá a internet?", "health"),
            ("qual o meu ip", "ip"),
            ("ver dispositivos na rede", "devices"),
            ("quais portas abertas", "ports"),
            ("liberar a porta 8080", "release_port"),
            ("acordar pc via wake on lan", "wol"),
            ("testar velocidade do dns", "dns_bench"),
            ("reconectar a internet", "reconnect"),
        ]
        for prompt, expected_sub in prompts:
            pred = FastRNNRouter.classify(prompt)
            self.assertEqual(pred.intent, PrimaryIntent.NETWORK_INSPECT)
            self.assertTrue(pred.is_fast_path)
            self.assertEqual(pred.action_type, expected_sub)
            self.assertGreaterEqual(pred.confidence, 0.90)
            if expected_sub == "release_port":
                self.assertEqual(pred.params.get("port"), 8080)

    def test_docker_intent(self):
        prompts = [
            ("quais containers estão rodando", "list"),
            ("docker ps", "list"),
            ("reiniciar container homeassistant", "restart"),
            ("parar o container web", "stop"),
            ("logs do container redis", "logs"),
        ]
        for prompt, expected_action in prompts:
            pred = FastRNNRouter.classify(prompt)
            self.assertEqual(pred.intent, PrimaryIntent.DOCKER_CONTROL)
            self.assertTrue(pred.is_fast_path)
            self.assertEqual(pred.action_type, expected_action)
            self.assertGreaterEqual(pred.confidence, 0.90)

    def test_hardware_health_intent(self):
        prompts = [
            ("qual a temperatura da cpu", "thermals"),
            ("sensores de temperatura", "thermals"),
            ("saúde do ssd", "storage"),
            ("uso de memória ram", "memory"),
        ]
        for prompt, expected_sub in prompts:
            pred = FastRNNRouter.classify(prompt)
            self.assertEqual(pred.intent, PrimaryIntent.HARDWARE_HEALTH)
            self.assertTrue(pred.is_fast_path)
            self.assertEqual(pred.action_type, expected_sub)
            self.assertGreaterEqual(pred.confidence, 0.90)

    def test_journal_inspect_intent(self):
        prompts = [
            ("quais erros no sistema", "journal_errors"),
            ("falhas no journal", "journal_errors"),
            ("logs de erro", "journal_errors"),
        ]
        for prompt, expected_sub in prompts:
            pred = FastRNNRouter.classify(prompt)
            self.assertEqual(pred.intent, PrimaryIntent.JOURNAL_INSPECT)
            self.assertTrue(pred.is_fast_path)
            self.assertEqual(pred.action_type, expected_sub)
            self.assertGreaterEqual(pred.confidence, 0.90)


    def test_fallback_to_complex_llm(self):
        complex_prompts = [
            "elabore um resumo acadêmico do meu TCC em normas ABNT",
            "crie um endpoint FastAPI com validação Pydantic",
            "por que o céu é azul?",
            "pesquise os dados de vendas no IBGE",
        ]
        for prompt in complex_prompts:
            pred = FastRNNRouter.classify(prompt)
            self.assertEqual(pred.intent, PrimaryIntent.COMPLEX_LLM)
            self.assertFalse(pred.is_fast_path)


if __name__ == "__main__":
    unittest.main()
