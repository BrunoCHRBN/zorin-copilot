# Decisão de design: cliente de pesquisa na web sem necessidade de chaves de API pagas — utiliza DuckDuckGo HTML e Lite com extração resiliente de snippets e URLs reais.

"""Módulo de pesquisa na web em tempo real para o Zorin Copilot."""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Sequence

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

    def to_text(self) -> str:
        return f"• [{self.title}]({self.url})\n  {self.snippet}"


class WebSearchClient:
    """Realiza buscas na web em tempo real de forma privada e sem custos."""

    def __init__(self, timeout: int = 6):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def search(self, query: str, max_results: int = 4) -> list[SearchResult]:
        query_clean = query.strip()
        if not query_clean:
            return []

        # 1. Tenta DuckDuckGo HTML
        results = self._search_duckduckgo_html(query_clean, max_results)
        if results:
            return results

        # 2. Fallback: DuckDuckGo Lite
        results = self._search_duckduckgo_lite(query_clean, max_results)
        if results:
            return results

        # 3. Fallback para notícias recentes via Google News RSS
        return self._search_google_news_rss(query_clean, max_results)

    def _search_duckduckgo_html(self, query: str, max_results: int) -> list[SearchResult]:
        url = "https://html.duckduckgo.com/html/"
        try:
            resp = self.session.post(url, data={"q": query}, timeout=self.timeout)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results: list[SearchResult] = []

            for res in soup.find_all("div", class_="result"):
                if len(results) >= max_results:
                    break

                link_el = res.find("a", class_="result__a")
                snippet_el = res.find("a", class_="result__snippet")
                if not link_el:
                    continue

                raw_href = link_el.get("href", "")
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                actual_url = parsed.get("uddg", [raw_href])[0]

                title = link_el.get_text(strip=True)
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                if title and actual_url.startswith("http"):
                    results.append(SearchResult(title=title, url=actual_url, snippet=snippet))

            return results
        except Exception as exc:
            logger.warning(f"Falha na busca DuckDuckGo HTML: {exc}")
            return []

    def _search_duckduckgo_lite(self, query: str, max_results: int) -> list[SearchResult]:
        url = f"https://lite.duckduckgo.com/lite/"
        try:
            resp = self.session.post(url, data={"q": query}, timeout=self.timeout)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results: list[SearchResult] = []

            links = soup.find_all("a", class_="result-link")
            snippets = soup.find_all("td", class_="result-snippet")

            for idx, link_el in enumerate(links[:max_results]):
                raw_href = link_el.get("href", "")
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                actual_url = parsed.get("uddg", [raw_href])[0]
                title = link_el.get_text(strip=True)
                snippet = snippets[idx].get_text(strip=True) if idx < len(snippets) else ""

                if title and actual_url.startswith("http"):
                    results.append(SearchResult(title=title, url=actual_url, snippet=snippet))

            return results
        except Exception as exc:
            logger.warning(f"Falha na busca DuckDuckGo Lite: {exc}")
            return []

    def _search_google_news_rss(self, query: str, max_results: int) -> list[SearchResult]:
        q = urllib.parse.quote_plus(query)
        url = f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "xml")
            items = soup.find_all("item")
            results: list[SearchResult] = []

            for item in items[:max_results]:
                title = item.find("title").get_text(strip=True) if item.find("title") else ""
                link = item.find("link").get_text(strip=True) if item.find("link") else ""
                desc = item.find("description").get_text(strip=True) if item.find("description") else ""

                if title and link:
                    clean_desc = BeautifulSoup(desc, "html.parser").get_text(strip=True)
                    results.append(SearchResult(title=title, url=link, snippet=clean_desc))

            return results
        except Exception as exc:
            logger.warning(f"Falha na busca Google News RSS: {exc}")
            return []

    @staticmethod
    def is_search_needed(prompt: str) -> bool:
        """Identifica se uma consulta requer pesquisa ao vivo na web."""
        low = prompt.lower()

        # Gatilhos explícitos de busca
        explicit_triggers = [
            "pesquise",
            "pesquisar",
            "busque",
            "buscar",
            "procure",
            "procurar",
            "pesquisa na web",
            "buscar na web",
            "notícias de",
            "noticias de",
            "últimas notícias",
            "ultimas noticias",
        ]
        if any(t in low for t in explicit_triggers):
            return True

        # Consultas temporais e dinâmicas de tempo real
        temporal_triggers = [
            "hoje",
            "ontem",
            "agora",
            "nesta semana",
            "último",
            "última",
            "recente",
            "atual",
            "previsão do tempo",
            "previsao do tempo",
            "temperatura em",
            "cotação",
            "cotacao",
            "dólar hoje",
            "placar",
            "resultado do jogo",
            "quem ganhou",
            "copa de 2026",
            "copa 2026",
            "campeonato",
            "lançamento",
            "versão mais recente",
        ]
        return any(t in low for t in temporal_triggers)

    @staticmethod
    def clean_search_query(prompt: str) -> str:
        """Limpa o prompt removendo expressões de comando para gerar uma busca objetiva."""
        cleaned = re.sub(
            r"^(?:pesquise na web|busque na web|pesquisar na web|pesquise sobre|busque sobre|procure sobre|pesquise|busque|procure|me diga|qual o|qual a|como foi o|como está o|onde fica|quem é)\s+",
            "",
            prompt,
            flags=re.I,
        ).strip(" ?.!\"'")
        return cleaned or prompt

    @staticmethod
    def format_results_for_prompt(results: Sequence[SearchResult]) -> str:
        if not results:
            return ""

        lines = ["[Resultados da Pesquisa Web em Tempo Real]:"]
        for idx, r in enumerate(results, 1):
            lines.append(f"{idx}. Título: {r.title}")
            lines.append(f"   Fonte/URL: {r.url}")
            lines.append(f"   Trecho: {r.snippet}")
        return "\n".join(lines)
