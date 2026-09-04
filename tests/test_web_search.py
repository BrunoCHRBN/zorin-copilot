# Decisão de design: testes unitários para a pesquisa na web cobrindo heurísticas de ativação, limpeza de busca, parser de HTML e integração com o motor de intenção.

"""Testes unitários do módulo de Pesquisa na Web do Zorin Copilot."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.core.web_search import SearchResult, WebSearchClient
from zorin_copilot.ai.actions import ActionType
from zorin_copilot.ai.engine import IntentEngine
from zorin_copilot.core.config import CopilotConfig


class WebSearchTest(unittest.TestCase):
    def setUp(self):
        self.client = WebSearchClient()

    def test_is_search_needed_explicit_triggers(self):
        self.assertTrue(self.client.is_search_needed("pesquise notícias sobre o kernel linux"))
        self.assertTrue(self.client.is_search_needed("buscar na web documentação do libadwaita"))
        self.assertTrue(self.client.is_search_needed("procure os últimos pacotes flatpak"))
        self.assertTrue(self.client.is_search_needed("últimas notícias de tecnologia"))

    def test_is_search_needed_temporal_and_dynamic(self):
        self.assertTrue(self.client.is_search_needed("qual a previsão do tempo hoje em São Paulo"))
        self.assertTrue(self.client.is_search_needed("qual a cotação do dólar hoje"))
        self.assertTrue(self.client.is_search_needed("quem ganhou o jogo da copa de 2026"))
        self.assertTrue(self.client.is_search_needed("qual a versão mais recente do GNOME"))

    def test_is_search_not_needed_for_local_desktop_tasks(self):
        self.assertFalse(self.client.is_search_needed("abrir a calculadora"))
        self.assertFalse(self.client.is_search_needed("aumentar o volume"))
        self.assertFalse(self.client.is_search_needed("listar arquivos na pasta"))
        self.assertFalse(self.client.is_search_needed("fechar o navegador"))

    def test_clean_search_query(self):
        self.assertEqual(
            self.client.clean_search_query("pesquise sobre o lançamento do Zorin OS 18"),
            "o lançamento do Zorin OS 18",
        )
        self.assertEqual(
            self.client.clean_search_query("qual o resultado da seleção brasileira ?"),
            "resultado da seleção brasileira",
        )
        self.assertEqual(
            self.client.clean_search_query("pesquise na web novidades de inteligência artificial"),
            "novidades de inteligência artificial",
        )

    def test_format_results_for_prompt(self):
        results = [
            SearchResult(
                title="Zorin OS Oficial",
                url="https://zorin.com/os/",
                snippet="O sistema operacional rápido, seguro e fácil de usar.",
            ),
            SearchResult(
                title="Linux Kernel News",
                url="https://kernel.org",
                snippet="The latest stable release of the Linux kernel.",
            ),
        ]
        text = self.client.format_results_for_prompt(results)
        self.assertIn("[Resultados da Pesquisa Web em Tempo Real]:", text)
        self.assertIn("Zorin OS Oficial", text)
        self.assertIn("https://zorin.com/os/", text)
        self.assertIn("The latest stable release", text)

    @patch("requests.Session.post")
    def test_duckduckgo_html_parser(self, mock_post):
        mock_html = """
        <div class="result">
            <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fzorin.com%2Fos%2F&rut=1">Zorin OS Site Oficial</a>
            <a class="result__snippet">Baixe a versão mais recente do Zorin OS.</a>
        </div>
        <div class="result">
            <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdistrowatch.com%2Fzorin&rut=2">DistroWatch Zorin</a>
            <a class="result__snippet">Reviews e histórico do sistema Zorin.</a>
        </div>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = mock_html
        mock_post.return_value = mock_response

        results = self.client.search("zorin os", max_results=2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "Zorin OS Site Oficial")
        self.assertEqual(results[0].url, "https://zorin.com/os/")
        self.assertIn("Baixe a versão", results[0].snippet)
        self.assertEqual(results[1].url, "https://distrowatch.com/zorin")

    @patch.object(WebSearchClient, "search")
    def test_intent_engine_web_search_integration(self, mock_search):
        mock_search.return_value = [
            SearchResult(
                title="Copa do Mundo 2026 - Tabela e Jogos",
                url="https://globoesporte.globo.com/futebol/copa-2026",
                snippet="Informações ao vivo sobre os grupos e jogos da Copa 2026.",
            )
        ]

        cfg = CopilotConfig(
            gemini_api_key="fake-test-key",
            provider="gemini",
            web_search_enabled=True,
        )
        engine = IntentEngine(config=cfg)

        # Mock do provedor LLM para retornar plano sem quebrar chamada HTTP real
        mock_provider = MagicMock()
        mock_provider.chat.return_value = ("Encontrei os detalhes da Copa 2026 na web.", [])
        engine.llm_provider = mock_provider

        plan = engine.parse("qual o resultado do jogo da copa de 2026 ?")

        # Verifica se o cliente de pesquisa foi acionado
        mock_search.assert_called_once()
        # Verifica se a ação OPEN_URL foi adicionada automaticamente como recomendação
        self.assertTrue(any(a.action_type == ActionType.OPEN_URL for a in plan.actions))
        url_action = next(a for a in plan.actions if a.action_type == ActionType.OPEN_URL)
        self.assertEqual(url_action.target, "https://globoesporte.globo.com/futebol/copa-2026")


if __name__ == "__main__":
    unittest.main()
