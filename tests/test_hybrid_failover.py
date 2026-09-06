"""Testes unitários para o Provedor Híbrido e Auto-Failover Gemini -> Ollama Local."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.ai.actions import ActionType, DesktopAction
from zorin_copilot.ai.providers import (
    GeminiProvider,
    HybridProvider,
    OllamaProvider,
    OpenAICompatProvider,
    get_llm_provider,
)
from zorin_copilot.core.config import CopilotConfig


class HybridProviderTest(unittest.TestCase):
    """Testes de conformidade e failover do HybridProvider."""

    def setUp(self):
        self.mock_gemini = MagicMock(spec=GeminiProvider)
        self.mock_gemini.model = "gemini-3.5-flash"
        self.mock_ollama = MagicMock(spec=OllamaProvider)
        self.mock_ollama.model = "qwen2.5:7b"
        self.mock_ollama.host_url = "http://127.0.0.1:11434"

    def test_is_configured_modes(self):
        """Valida is_configured nos modos hybrid e gemini_with_fallback."""
        # Modo Hybrid: True se qualquer um estiver configurado
        self.mock_gemini.is_configured.return_value = False
        self.mock_ollama.is_configured.return_value = True
        prov_hybrid = HybridProvider(self.mock_gemini, self.mock_ollama, mode="hybrid")
        self.assertTrue(prov_hybrid.is_configured())

        self.mock_gemini.is_configured.return_value = True
        self.mock_ollama.is_configured.return_value = False
        self.assertTrue(prov_hybrid.is_configured())

        self.mock_gemini.is_configured.return_value = False
        self.mock_ollama.is_configured.return_value = False
        self.assertFalse(prov_hybrid.is_configured())

        # Modo gemini_with_fallback: requer que Gemini esteja configurado
        prov_gemini_fallback = HybridProvider(self.mock_gemini, self.mock_ollama, mode="gemini_with_fallback")
        self.mock_gemini.is_configured.return_value = False
        self.mock_ollama.is_configured.return_value = True
        self.assertFalse(prov_gemini_fallback.is_configured())

        self.mock_gemini.is_configured.return_value = True
        self.assertTrue(prov_gemini_fallback.is_configured())

    def test_gemini_success_no_failover(self):
        """Quando o Gemini responde com sucesso, não chama o Ollama."""
        self.mock_gemini.is_configured.return_value = True
        self.mock_ollama.is_configured.return_value = True

        expected_action = DesktopAction(ActionType.ANSWER, "Resposta OK")
        self.mock_gemini.chat.return_value = ("Tudo certo por aqui!", [expected_action])

        prov = HybridProvider(self.mock_gemini, self.mock_ollama, mode="hybrid")
        expl, actions = prov.chat("Olá")

        self.assertEqual(expl, "Tudo certo por aqui!")
        self.assertEqual(actions, [expected_action])
        self.mock_ollama.chat.assert_not_called()

    def test_gemini_429_quota_exhausted_failover(self):
        """Quando o Gemini retorna erro 429 de cota, comuta automaticamente para Ollama."""
        self.mock_gemini.is_configured.return_value = True
        self.mock_ollama.is_configured.return_value = True

        self.mock_gemini.chat.return_value = (
            "Não foi possível obter resposta do Gemini: Erro no modelo gemini-3.5-flash (429): RESOURCE_EXHAUSTED",
            [],
        )
        local_action = DesktopAction(ActionType.LAUNCH_APP, "Calculadora")
        self.mock_ollama.chat.return_value = ("Abrindo a calculadora para você.", [local_action])

        prov = HybridProvider(self.mock_gemini, self.mock_ollama, mode="hybrid")
        expl, actions = prov.chat("abrir calculadora")

        # Verifica chamada ao Ollama
        self.mock_ollama.chat.assert_called_once()
        self.assertIn("Modo Local Offline - qwen2.5:7b", expl)
        self.assertIn("Abrindo a calculadora", expl)
        self.assertEqual(actions, [local_action])

    def test_gemini_exception_failover(self):
        """Quando o Gemini lança exceção de conexão/timeout, comuta para Ollama."""
        self.mock_gemini.is_configured.return_value = True
        self.mock_ollama.is_configured.return_value = True

        self.mock_gemini.chat.side_effect = ConnectionError("Sem internet")
        self.mock_ollama.chat.return_value = ("Modo offline ativo: respondi localmente.", [])

        prov = HybridProvider(self.mock_gemini, self.mock_ollama, mode="hybrid")
        expl, actions = prov.chat("ajuda com comando linux")

        self.mock_ollama.chat.assert_called_once()
        self.assertIn("Modo Local Offline - qwen2.5:7b", expl)
        self.assertIn("Modo offline ativo", expl)

    def test_hybrid_mode_no_gemini_key_uses_ollama_directly(self):
        """No modo híbrido sem chave Gemini configurada, usa Ollama diretamente."""
        self.mock_gemini.is_configured.return_value = False
        self.mock_ollama.is_configured.return_value = True
        self.mock_ollama.chat.return_value = ("Resposta puramente local do Qwen.", [])

        prov = HybridProvider(self.mock_gemini, self.mock_ollama, mode="hybrid")
        expl, actions = prov.chat("como listar arquivos")

        self.mock_gemini.chat.assert_not_called()
        self.mock_ollama.chat.assert_called_once()
        self.assertEqual(expl, "Resposta puramente local do Qwen.")

    def test_multimodal_local_vision_failover(self):
        """Quando Gemini falha e requisição tem imagem, comuta para o modelo local de visão (MiniCPM-V)."""
        self.mock_gemini.is_configured.return_value = True
        self.mock_ollama.is_configured.return_value = True
        self.mock_ollama.model = "qwen2.5:7b"
        self.mock_ollama.vision_model = "minicpm-v"

        self.mock_gemini.chat.return_value = (
            "Não foi possível obter resposta do Gemini: Erro no modelo (429)",
            [],
        )
        vision_action = DesktopAction(ActionType.ANSWER, "Análise do recorte")
        self.mock_ollama.chat.return_value = ("Vejo uma janela com um formulário de login.", [vision_action])

        prov = HybridProvider(self.mock_gemini, self.mock_ollama, mode="hybrid")
        expl, actions = prov.chat("analise esta imagem", image_bytes=b"fake_png_data")

        # Verifica delegação para visão local
        self.mock_ollama.chat.assert_called_once()
        self.assertIn("Visão Computacional Local - minicpm-v", expl)
        self.assertIn("janela com um formulário", expl)
        self.assertEqual(actions, [vision_action])

    def test_connection_composite_status(self):
        """test_connection relata status claro de ambos provedores."""
        self.mock_gemini.is_configured.return_value = True
        self.mock_gemini.test_connection.return_value = (True, "Gemini OK")
        self.mock_ollama.test_connection.return_value = (True, "Modelo 'qwen2.5:7b' pronto na GPU.")

        prov = HybridProvider(self.mock_gemini, self.mock_ollama)
        ok, msg = prov.test_connection()

        self.assertTrue(ok)
        self.assertIn("Gemini: ✓ Conectado", msg)
        self.assertIn("Ollama: ✓ Modelo 'qwen2.5:7b' pronto na GPU.", msg)


class FactoryProviderTest(unittest.TestCase):
    """Testes para get_llm_provider com diferentes configurações."""

    def test_factory_hybrid(self):
        cfg = CopilotConfig(provider="hybrid")
        prov = get_llm_provider(cfg)
        self.assertIsInstance(prov, HybridProvider)
        self.assertEqual(prov.mode, "hybrid")

    def test_factory_gemini_fallback_enabled(self):
        cfg = CopilotConfig(provider="gemini", fallback_to_ollama=True)
        prov = get_llm_provider(cfg)
        self.assertIsInstance(prov, HybridProvider)
        self.assertEqual(prov.mode, "gemini_with_fallback")

    def test_factory_gemini_fallback_disabled(self):
        cfg = CopilotConfig(provider="gemini", fallback_to_ollama=False)
        prov = get_llm_provider(cfg)
        self.assertIsInstance(prov, GeminiProvider)

    def test_factory_ollama_direct(self):
        cfg = CopilotConfig(provider="ollama")
        prov = get_llm_provider(cfg)
        self.assertIsInstance(prov, OllamaProvider)

    def test_factory_openai_direct(self):
        cfg = CopilotConfig(provider="openai")
        prov = get_llm_provider(cfg)
        self.assertIsInstance(prov, OpenAICompatProvider)


class OllamaProviderVisionTest(unittest.TestCase):
    """Testes para o roteamento dinâmico de visão do OllamaProvider."""

    @patch("requests.post")
    def test_ollama_routes_text_to_text_model(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": '{"explanation": "OK", "actions": []}'}}
        mock_post.return_value = mock_resp

        provider = OllamaProvider(model="qwen2.5:7b", vision_model="minicpm-v")
        provider.chat("pergunta de texto")

        self.assertTrue(mock_post.called)
        sent_payload = mock_post.call_args[1]["json"]
        self.assertEqual(sent_payload["model"], "qwen2.5:7b")
        self.assertEqual(sent_payload["format"], "json")

    @patch("requests.post")
    def test_ollama_routes_image_to_vision_model(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": "Análise da janela"}}
        mock_post.return_value = mock_resp

        provider = OllamaProvider(model="qwen2.5:7b", vision_model="minicpm-v")
        provider.chat("o que tem na tela?", image_bytes=b"fake_jpeg_data")

        self.assertTrue(mock_post.called)
        sent_payload = mock_post.call_args[1]["json"]
        self.assertEqual(sent_payload["model"], "minicpm-v")
        # Imagens não forçam format json para flexibilidade
        self.assertNotIn("format", sent_payload)
        self.assertIn("images", sent_payload["messages"][-1])


if __name__ == "__main__":
    unittest.main()
