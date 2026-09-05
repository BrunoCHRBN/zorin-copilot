# Decisão de design: gerenciador de pesquisas e navegação na web direta no Zorin OS.
# Suporta múltiplos motores de busca com deep links formatados (Google, YouTube, GitHub, Maps, Wikipedia, DuckDuckGo)
# e abertura no navegador padrão do usuário (Chrome, Firefox, Brave).

"""Gerenciador de pesquisas e navegação web para o Zorin Copilot."""

from __future__ import annotations

import logging
import shutil
import subprocess
import urllib.parse

logger = logging.getLogger(__name__)

ENGINES = {
    "google": "https://www.google.com/search?q={query}",
    "duckduckgo": "https://duckduckgo.com/?q={query}",
    "youtube": "https://www.youtube.com/results?search_query={query}",
    "github": "https://github.com/search?q={query}",
    "maps": "https://www.google.com/maps/search/{query}",
    "wikipedia": "https://pt.wikipedia.org/wiki/Special:Search?search={query}",
}


class BrowserManager:
    """Gerencia pesquisas na internet e abertura de páginas no navegador da área de trabalho."""

    @classmethod
    def search(cls, query: str, engine: str = "google") -> tuple[bool, str, str]:
        """Abre uma pesquisa no motor indicado no navegador padrão."""
        q = query.strip()
        if not q:
            return False, "Termo de busca vazio.", ""

        eng_key = engine.strip().lower()
        template = ENGINES.get(eng_key, ENGINES["google"])
        url = template.format(query=urllib.parse.quote(q))

        ok = cls.open_url(url)
        engine_name = eng_key.capitalize() if eng_key in ENGINES else "Google"
        if ok:
            return True, f"Pesquisa sobre '{q}' aberta no {engine_name}.", url
        return False, f"Falha ao abrir pesquisa no {engine_name}.", url

    @classmethod
    def open_url(cls, url: str) -> bool:
        """Abre URL no navegador padrão usando gio open ou xdg-open."""
        clean_url = url.strip()
        if not clean_url.startswith(("http://", "https://", "mailto:", "file://")):
            clean_url = f"https://{clean_url}"

        for opener in ("gio", "xdg-open"):
            if shutil.which(opener):
                try:
                    subprocess.Popen(
                        [opener, "open" if opener == "gio" else "", clean_url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return True
                except Exception as exc:
                    logger.debug(f"{opener} falhou: {exc}")

        import webbrowser
        try:
            return webbrowser.open(clean_url)
        except Exception:
            return False
