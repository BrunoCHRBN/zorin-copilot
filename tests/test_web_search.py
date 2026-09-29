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

    @patch("requests.get")
    def test_web_page_reader_fetch_and_clean(self, mock_get):
        from zorin_copilot.core.browser import WebPageReader
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_resp.text = """
        <html>
            <head><title>Zorin OS 18 Lançamento</title></head>
            <body>
                <nav><a href="/">Home</a><a href="/login">Login</a></nav>
                <article>
                    <h1>Novidades do Zorin OS 18</h1>
                    <p>O Zorin OS 18 traz integração nativa com inteligência artificial e GNOME 46.</p>
                    <p>O desempenho gráfico no Wayland aumentou significativamente em 40%.</p>
                </article>
                <footer>Todos os direitos reservados.</footer>
                <script>console.log('tracker');</script>
            </body>
        </html>
        """
        mock_get.return_value = mock_resp

        data = WebPageReader.fetch_and_clean("https://zorin.com/news/zorin-18")
        self.assertTrue(data["success"])
        self.assertEqual(data["title"], "Zorin OS 18 Lançamento")
        self.assertIn("Novidades do Zorin OS 18", data["text"])
        self.assertIn("GNOME 46", data["text"])
        # Garante que nav, footer e scripts foram removidos
        self.assertNotIn("Home", data["text"])
        self.assertNotIn("tracker", data["text"])

    @patch("zorin_copilot.core.browser.WebPageReader.fetch_and_clean")
    @patch.object(WebSearchClient, "search")
    def test_deep_web_researcher(self, mock_search, mock_fetch):
        from zorin_copilot.core.web_search import DeepWebResearcher
        mock_search.return_value = [
            SearchResult(
                title="Review Zorin OS 18",
                url="https://reviewlinux.com/zorin18",
                snippet="Análise detalhada do novo sistema Zorin.",
            ),
            SearchResult(
                title="Benchmarks Linux 2026",
                url="https://phoronix.com/benchmarks-2026",
                snippet="Testes de velocidade e gráficos no Wayland.",
            ),
        ]
        mock_fetch.side_effect = [
            {
                "success": True,
                "title": "Review Zorin OS 18",
                "url": "https://reviewlinux.com/zorin18",
                "text": "O Zorin OS 18 alcançou nota máxima em usabilidade com a barra HUD do Copilot.",
                "length": 85,
            },
            {
                "success": True,
                "title": "Benchmarks Linux 2026",
                "url": "https://phoronix.com/benchmarks-2026",
                "text": "Consumo de memória RAM reduzido para 750MB em idle.",
                "length": 55,
            },
        ]

        researcher = DeepWebResearcher(search_client=self.client)
        report = researcher.deep_search("pesquisa aprofundada sobre o Zorin OS 18")
        self.assertTrue(report["success"])
        self.assertEqual(len(report["sources"]), 2)
        self.assertIn("Zorin OS 18", report["report"])
        self.assertIn("https://reviewlinux.com/zorin18", report["report"])

    @patch.object(WebSearchClient, "search")
    @patch("zorin_copilot.core.browser.WebPageReader.fetch_and_clean")
    def test_intent_engine_deep_research_intent(self, mock_fetch, mock_search):
        mock_search.return_value = [
            SearchResult(title="Tech News", url="https://tech.com/news", snippet="Tech updates")
        ]
        mock_fetch.return_value = {
            "success": True,
            "title": "Tech News",
            "url": "https://tech.com/news",
            "text": "Detalhes sobre arquitetura de sistemas operacionais modernos.",
            "length": 60,
        }

        cfg = CopilotConfig(provider="gemini", gemini_api_key="")
        engine = IntentEngine(config=cfg, search_client=self.client)

        plan = engine.parse("faça uma pesquisa aprofundada sobre computação quântica")
        self.assertTrue(any(a.action_type == ActionType.DEEP_RESEARCH for a in plan.actions))
        self.assertIn("computação quântica", plan.thought.lower())

    @patch("zorin_copilot.core.browser.BrowserManager.read_page")
    def test_intent_engine_read_open_page_intent(self, mock_read):
        mock_read.return_value = {
            "success": True,
            "title": "Artigo Sobre IA",
            "url": "https://ia.org/artigo",
            "text": "O artigo discute a evolução de assistentes locais integrados ao desktop.",
            "description": "Resumo do artigo de IA.",
            "length": 75,
        }

        cfg = CopilotConfig(provider="gemini", gemini_api_key="")
        engine = IntentEngine(config=cfg)

        plan = engine.parse("leia a página aberta")
        self.assertTrue(any(a.action_type == ActionType.READ_PAGE for a in plan.actions))
        self.assertIn("Artigo Sobre IA", plan.thought)

    @patch("zorin_copilot.core.browser.BrowserManager.read_page")
    def test_erro_do_provedor_nao_vira_leitura_da_pagina(self, mock_read):
        """A leitura da página não pode ser respondida com a mensagem de erro.

        Mesmo mecanismo do `deep_search`: o provedor devolve a frase de erro no canal
        de texto, então o `except` do ramo de leitura nunca dispara. Sem a checagem, o
        `thought` devolvido era a mensagem de cota esgotada — e o `thought`
        determinístico logo abaixo, com o título e o conteúdo, nunca rodava.
        """
        mock_read.return_value = {
            "success": True,
            "title": "Artigo Sobre IA",
            "url": "https://ia.org/artigo",
            "text": "O artigo discute a evolução de assistentes locais integrados ao desktop.",
            "description": "Resumo do artigo de IA.",
            "length": 75,
        }
        cfg = CopilotConfig(provider="gemini", gemini_api_key="")
        engine = IntentEngine(config=cfg)
        # Substitui o provedor real por um que devolve a frase de erro (sem levantar).
        engine.llm_provider = _FakeProvider(
            reply="Não foi possível obter resposta do Gemini: Erro no modelo (429): RESOURCE_EXHAUSTED"
        )

        plan = engine.parse("leia a página aberta")

        self.assertNotIn("RESOURCE_EXHAUSTED", plan.thought)
        self.assertNotIn("Não foi possível obter resposta do Gemini", plan.thought)
        # O caminho determinístico tem de ter rodado.
        self.assertIn("Artigo Sobre IA", plan.thought)

    @patch("zorin_copilot.core.browser.BrowserManager.read_page")
    def test_sintese_real_do_provedor_e_usada_na_leitura(self, mock_read):
        """Contraprova: com resposta legítima, a leitura usa a síntese do modelo."""
        mock_read.return_value = {
            "success": True,
            "title": "Artigo Sobre IA",
            "url": "https://ia.org/artigo",
            "text": "Conteúdo qualquer sobre assistentes locais.",
            "description": "Resumo.",
            "length": 40,
        }
        cfg = CopilotConfig(provider="gemini", gemini_api_key="")
        engine = IntentEngine(config=cfg)
        engine.llm_provider = _FakeProvider(reply="O artigo trata de assistentes locais.")

        plan = engine.parse("leia a página aberta")

        self.assertEqual(plan.thought, "O artigo trata de assistentes locais.")

    @patch.object(WebSearchClient, "search")
    def test_academic_search_query_building(self, mock_search):
        mock_search.return_value = [
            SearchResult(title="Estudo SciELO", url="https://scielo.br/artigo", snippet="Resumo do artigo")
        ]
        res_scielo = self.client.academic_search("funil de vendas", source="scielo")
        self.assertEqual(len(res_scielo), 1)
        # Verifica se o filtro de domínio foi aplicado na query enviada para search()
        args, _ = mock_search.call_args
        self.assertIn("site:scielo", args[0])

        res_ibge = self.client.academic_search("comércio varejista", source="ibge")
        args_ibge, _ = mock_search.call_args
        self.assertIn("site:ibge.gov.br", args_ibge[0])

    def test_academic_search_empty_query(self):
        self.assertEqual(self.client.academic_search("   "), [])


class _FakeProvider:
    """Provedor mínimo para a pesquisa profunda.

    Existe para reproduzir o comportamento REAL dos provedores deste projeto: em vez
    de levantar, eles devolvem a frase de erro no canal de texto. Um dublê que
    levanta sempre esconderia exatamente o defeito que estes testes protegem.
    """

    def __init__(self, reply: str = "", raises: Exception | None = None, configured: bool = True):
        self.reply = reply
        self.raises = raises
        self.configured = configured
        self.prompts: list[str] = []

    def is_configured(self) -> bool:
        return self.configured

    def chat(self, prompt, *args, **kwargs):
        self.prompts.append(prompt)
        if self.raises is not None:
            raise self.raises
        return self.reply, []


_FONTE_A = SearchResult(title="Zorin OS 18", url="https://reviewlinux.com/zorin18", snippet="Análise do Zorin.")
_FONTE_B = SearchResult(title="Benchmarks 2026", url="https://phoronix.com/b2026", snippet="Testes no Wayland.")

_PAGINA_OK = {
    "success": True,
    "title": "Zorin OS 18",
    "url": "https://reviewlinux.com/zorin18",
    "text": "O Zorin OS 18 alcançou nota máxima em usabilidade.",
    "length": 51,
}
_PAGINA_VAZIA = {"success": False, "title": "Benchmarks 2026", "url": "https://phoronix.com/b2026", "text": "", "length": 0}


class DeepSearchHonestyTest(unittest.TestCase):
    """A pesquisa profunda não pode apresentar erro de provedor como relatório.

    Defeito que originou estes testes: `llm_provider.chat()` devolvia a frase de erro
    (cota 429 do Gemini, Ollama inalcançável) no lugar da resposta, e `deep_search` a
    devolvia como `report` com `success: True` e um evento "sintetizada com sucesso".
    O usuário recebia a mensagem de erro rotulada de relatório.
    """

    def setUp(self):
        self.client = WebSearchClient()

    @patch("zorin_copilot.core.browser.WebPageReader.fetch_and_clean")
    @patch.object(WebSearchClient, "search")
    def test_erro_do_provedor_nao_vira_relatorio(self, mock_search, mock_fetch):
        mock_search.return_value = [_FONTE_A, _FONTE_B]
        mock_fetch.return_value = _PAGINA_OK
        erro = (
            "Não foi possível obter resposta do Gemini: Erro no modelo gemini-3.5-flash "
            "(429): RESOURCE_EXHAUSTED"
        )
        eventos: list[tuple[str, str]] = []
        from zorin_copilot.core.web_search import DeepWebResearcher

        res = DeepWebResearcher(search_client=self.client).deep_search(
            "Zorin OS 18",
            llm_provider=_FakeProvider(reply=erro),
            on_progress=lambda s, m: eventos.append((s, m)),
        )

        # O texto de erro NÃO pode aparecer no relatório.
        self.assertNotIn("RESOURCE_EXHAUSTED", res["report"])
        self.assertNotIn("Não foi possível obter resposta do Gemini", res["report"])
        self.assertNotIn(erro, res["summary"])
        # E a procedência tem de ser declarada.
        self.assertEqual(res["synthesis"], "offline")
        self.assertIn("sem síntese por IA", res["report"])
        # Nenhum evento pode afirmar sucesso da síntese.
        self.assertNotIn(("done", "Pesquisa aprofundada sintetizada com sucesso."), eventos)
        self.assertIn("synthesis_failed", [e[0] for e in eventos])

    @patch("zorin_copilot.core.browser.WebPageReader.fetch_and_clean")
    @patch.object(WebSearchClient, "search")
    def test_sintese_real_e_declarada_como_llm(self, mock_search, mock_fetch):
        mock_search.return_value = [_FONTE_A]
        mock_fetch.return_value = _PAGINA_OK
        eventos: list[tuple[str, str]] = []
        from zorin_copilot.core.web_search import DeepWebResearcher

        res = DeepWebResearcher(search_client=self.client).deep_search(
            "Zorin OS 18",
            llm_provider=_FakeProvider(reply="📌 Resumo Executivo\n\nO Zorin OS 18 é..."),
            on_progress=lambda s, m: eventos.append((s, m)),
        )

        self.assertEqual(res["synthesis"], "llm")
        self.assertEqual(res["report"], "📌 Resumo Executivo\n\nO Zorin OS 18 é...")
        self.assertIn(("done", "Pesquisa aprofundada sintetizada com sucesso."), eventos)
        self.assertEqual(res["pages_read"], 1)
        self.assertEqual(res["pages_total"], 1)

    @patch("zorin_copilot.core.browser.WebPageReader.fetch_and_clean")
    @patch.object(WebSearchClient, "search")
    def test_provedor_que_levanta_tambem_cai_para_offline(self, mock_search, mock_fetch):
        mock_search.return_value = [_FONTE_A]
        mock_fetch.return_value = _PAGINA_OK
        from zorin_copilot.core.web_search import DeepWebResearcher

        res = DeepWebResearcher(search_client=self.client).deep_search(
            "Zorin OS 18",
            llm_provider=_FakeProvider(raises=RuntimeError("rede caiu")),
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["synthesis"], "offline")

    @patch("zorin_copilot.core.browser.WebPageReader.fetch_and_clean")
    @patch.object(WebSearchClient, "search")
    def test_pagina_que_nao_abre_e_marcada_como_snippet(self, mock_search, mock_fetch):
        mock_search.return_value = [_FONTE_A, _FONTE_B]
        mock_fetch.side_effect = [_PAGINA_OK, _PAGINA_VAZIA]
        from zorin_copilot.core.web_search import DeepWebResearcher

        res = DeepWebResearcher(search_client=self.client).deep_search("Zorin OS 18")

        # A segunda fonte só teve o resumo do resultado de busca — e o relatório diz isso.
        self.assertEqual(res["pages_total"], 2)
        self.assertEqual(res["pages_read"], 1)
        self.assertIn("sem síntese por IA", res["report"])
        self.assertIn("a página não abriu", res["report"])
        self.assertIn("1 de 2", res["report"])

    @patch("zorin_copilot.core.browser.WebPageReader.fetch_and_clean")
    @patch.object(WebSearchClient, "search")
    def test_prompt_avisa_quais_fontes_sao_so_resumo(self, mock_search, mock_fetch):
        mock_search.return_value = [_FONTE_A, _FONTE_B]
        mock_fetch.side_effect = [_PAGINA_OK, _PAGINA_VAZIA]
        prov = _FakeProvider(reply="relatório sintetizado")
        from zorin_copilot.core.web_search import DeepWebResearcher

        DeepWebResearcher(search_client=self.client).deep_search("Zorin OS 18", llm_provider=prov)

        self.assertEqual(len(prov.prompts), 1)
        prompt = prov.prompts[0]
        # O modelo tem de saber a fidelidade de cada fonte e quantas foram lidas.
        self.assertIn('fidelity="snippet"', prompt)
        self.assertIn('fidelity="full"', prompt)
        self.assertIn("De 2 fontes, 1 foram lidas por inteiro", prompt)

    @patch("zorin_copilot.core.browser.WebPageReader.fetch_and_clean")
    @patch.object(WebSearchClient, "search")
    def test_nenhuma_fonte_lida_e_falha_e_nao_relatorio_vazio(self, mock_search, mock_fetch):
        mock_search.return_value = [_FONTE_A, _FONTE_B]
        mock_fetch.side_effect = RuntimeError("sem rede")
        from zorin_copilot.core.web_search import DeepWebResearcher

        res = DeepWebResearcher(search_client=self.client).deep_search("Zorin OS 18")
        self.assertFalse(res["success"])
        self.assertEqual(res["synthesis"], "none")
        self.assertEqual(res["report"], "")
        self.assertTrue(res.get("error"))


if __name__ == "__main__":
    unittest.main()
