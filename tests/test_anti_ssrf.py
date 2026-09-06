"""Testes para proteção anti-SSRF e relatórios de progresso no DeepWebResearcher."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.core.browser import WebPageReader
from zorin_copilot.core.web_search import DeepWebResearcher, SearchResult


class AntiSSRFTest(unittest.TestCase):
    def test_blocks_localhost(self):
        is_safe, reason = WebPageReader.validate_url_anti_ssrf("http://localhost:8080/admin")
        self.assertFalse(is_safe)
        self.assertIn("localhost", reason.lower())

    def test_blocks_loopback_ip(self):
        is_safe, reason = WebPageReader.validate_url_anti_ssrf("http://127.0.0.1:5000/secret")
        self.assertFalse(is_safe)
        self.assertIn("bloqueado", reason.lower())

    def test_blocks_private_rfc1918_ips(self):
        private_urls = [
            "http://192.168.1.1/router",
            "http://10.0.0.1/intranet",
            "http://172.16.0.5/internal",
            "http://169.254.169.254/latest/meta-data",  # Link-local AWS metadata
        ]
        for url in private_urls:
            is_safe, reason = WebPageReader.validate_url_anti_ssrf(url)
            self.assertFalse(is_safe, f"Deveria bloquear {url}")
            self.assertIn("bloqueado", reason.lower())

    def test_blocks_non_http_schemes(self):
        for bad in ["file:///etc/passwd", "gopher://evil.com", "ftp://server/file"]:
            is_safe, reason = WebPageReader.validate_url_anti_ssrf(bad)
            self.assertFalse(is_safe)
            self.assertIn("não permitido", reason.lower())

    def test_allows_public_https(self):
        # Domínio público legítimo
        is_safe, reason = WebPageReader.validate_url_anti_ssrf("https://zorin.com/os")
        self.assertTrue(is_safe)
        self.assertEqual(reason, "")

    def test_fetch_and_clean_returns_error_for_ssrf(self):
        res = WebPageReader.fetch_and_clean("http://127.0.0.1:8080/debug")
        self.assertFalse(res["success"])
        self.assertIn("anti-SSRF", res["text"])


class DeepSearchProgressTest(unittest.TestCase):
    @patch("zorin_copilot.core.web_search.WebSearchClient.search")
    @patch("zorin_copilot.core.browser.WebPageReader.fetch_and_clean")
    def test_deep_search_progress_callbacks(self, mock_fetch, mock_search):
        mock_search.return_value = [
            SearchResult(title="Notícia 1", url="https://zorin.com/news1", snippet="Resumo 1"),
            SearchResult(title="Notícia 2", url="https://zorin.com/news2", snippet="Resumo 2"),
        ]
        mock_fetch.return_value = {
            "success": True,
            "title": "Notícia 1",
            "url": "https://zorin.com/news1",
            "text": "Texto completo da notícia 1 com dados técnicos.",
            "length": 100,
        }

        stages = []

        def track_progress(stage: str, msg: str):
            stages.append(stage)

        researcher = DeepWebResearcher()
        res = researcher.deep_search("zorin os", max_sources=2, on_progress=track_progress)
        self.assertTrue(res["success"])
        self.assertIn("search", stages)
        self.assertIn("download", stages)
        self.assertIn("synthesis", stages)


if __name__ == "__main__":
    unittest.main()
