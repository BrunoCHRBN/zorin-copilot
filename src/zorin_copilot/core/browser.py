# Decisão de design: gerenciador de pesquisas e navegação na web direta no Zorin OS.
# Suporta múltiplos motores de busca com deep links formatados (Google, YouTube, GitHub, Maps, Wikipedia, DuckDuckGo)
# e abertura no navegador padrão do usuário (Chrome, Firefox, Brave).

"""Gerenciador de pesquisas e navegação web para o Zorin Copilot."""

from __future__ import annotations

import ipaddress
import logging
import re
import shutil
import socket
import subprocess
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ENGINES = {
    "google": "https://www.google.com/search?q={query}",
    "duckduckgo": "https://duckduckgo.com/?q={query}",
    "youtube": "https://www.youtube.com/results?search_query={query}",
    "github": "https://github.com/search?q={query}",
    "maps": "https://www.google.com/maps/search/{query}",
    "wikipedia": "https://pt.wikipedia.org/wiki/Special:Search?search={query}",
}

KNOWN_BROWSER_NAMES = (
    "google-chrome",
    "google chrome",
    "chrome",
    "brave-browser",
    "brave",
    "firefox",
    "epiphany",
    "microsoft-edge",
    "edge",
    "chromium",
    "zen",
)


class WebPageReader:
    """Extrai e higieniza o conteúdo textual de páginas web e abas abertas no navegador."""

    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )

    @classmethod
    def validate_url_anti_ssrf(cls, url: str) -> tuple[bool, str]:
        """Garante que a URL é HTTP/HTTPS e não aponta para localhost, IPs privados ou redes internas."""
        try:
            parsed = urllib.parse.urlparse(url)
            scheme = (parsed.scheme or "").lower()
            if scheme not in ("http", "https"):
                return False, f"Esquema de URL não permitido: {scheme}"

            hostname = parsed.hostname
            if not hostname:
                return False, "URL inválida sem hostname"

            # Rejeita nomes óbvios de localhost
            if hostname.lower() in ("localhost", "127.0.0.1", "::1", "ip6-localhost", "ip6-loopback"):
                return False, "Acesso a localhost bloqueado por segurança anti-SSRF"

            # Resolve o hostname para verificar o IP real contra faixas privadas (RFC 1918)
            try:
                addr_info = socket.getaddrinfo(hostname, None)
                for family, _, _, _, sockaddr in addr_info:
                    ip_str = sockaddr[0]
                    ip_obj = ipaddress.ip_address(ip_str)
                    if (
                        ip_obj.is_private
                        or ip_obj.is_loopback
                        or ip_obj.is_link_local
                        or ip_obj.is_reserved
                        or ip_obj.is_multicast
                    ):
                        return False, f"Endereço IP privado/local bloqueado por segurança ({ip_str})"
            except socket.gaierror:
                return False, f"Não foi possível resolver o domínio '{hostname}'"

            return True, ""
        except Exception as exc:
            return False, f"Erro na validação de segurança da URL: {exc}"

    @classmethod
    def fetch_and_clean(cls, url: str, timeout: int = 8, max_chars: int = 12000) -> dict[str, Any]:
        """Faz requisição HTTP na URL e extrai o conteúdo do artigo limpo de scripts, banners e menus."""
        clean_url = url.strip()
        if not clean_url.startswith(("http://", "https://")):
            clean_url = f"https://{clean_url}"

        # Validação de Segurança Anti-SSRF
        is_safe, reason = cls.validate_url_anti_ssrf(clean_url)
        if not is_safe:
            logger.warning(f"Tentativa de acesso web bloqueada por Anti-SSRF: {clean_url} ({reason})")
            return {
                "success": False,
                "url": clean_url,
                "title": "",
                "description": "",
                "text": f"Requisição bloqueada por política anti-SSRF: {reason}",
                "length": 0,
                "error": reason,
            }

        try:
            headers = {
                "User-Agent": cls.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            resp = requests.get(clean_url, headers=headers, timeout=timeout)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return {
                    "success": False,
                    "url": clean_url,
                    "title": "",
                    "description": "",
                    "text": f"Tipo de mídia não suportado para leitura textual: {content_type}",
                    "length": 0,
                }

            soup = BeautifulSoup(resp.text, "html.parser")

            # 1. Extração de Título e Meta Descrição
            title = ""
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
            elif soup.find("h1"):
                title = soup.find("h1").get_text().strip()

            meta_desc = ""
            desc_el = soup.find("meta", attrs={"name": re.compile(r"description", re.I)}) or \
                      soup.find("meta", attrs={"property": re.compile(r"og:description", re.I)})
            if desc_el and desc_el.get("content"):
                meta_desc = desc_el["content"].strip()

            # 2. Remoção de ruídos (scripts, tags de navegação, rodapé, anúncios)
            for noise in soup.find_all([
                "script", "style", "nav", "footer", "header", "aside",
                "noscript", "svg", "iframe", "form", "button", "dialog"
            ]):
                noise.decompose()

            # 3. Localização do conteúdo principal (artigo, main ou body)
            root_content = (
                soup.find("article")
                or soup.find("main")
                or soup.find("div", class_=re.compile(r"content|post|article|entry|body", re.I))
                or soup.body
                or soup
            )

            blocks: list[str] = []
            for el in root_content.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre"]):
                t = el.get_text(strip=True)
                if not t or len(t) < 6:
                    continue
                if el.name in ("h1", "h2", "h3", "h4"):
                    blocks.append(f"\n### {t}\n")
                elif el.name == "li":
                    blocks.append(f"• {t}")
                elif el.name == "blockquote":
                    blocks.append(f"> {t}")
                else:
                    blocks.append(t)

            clean_text = "\n\n".join(blocks).strip()
            if len(clean_text) > max_chars:
                clean_text = clean_text[:max_chars] + "\n\n... [Conteúdo restante truncado para contexto]"

            return {
                "success": True,
                "url": clean_url,
                "title": title or clean_url,
                "description": meta_desc,
                "text": clean_text,
                "length": len(clean_text),
            }

        except Exception as exc:
            logger.warning(f"Erro ao extrair conteúdo de '{clean_url}': {exc}")
            return {
                "success": False,
                "url": clean_url,
                "title": "",
                "description": "",
                "text": f"Não foi possível ler o conteúdo do site: {exc}",
                "length": 0,
            }

    @classmethod
    def get_active_browser_tabs(cls) -> list[dict[str, Any]]:
        """Inspecciona janelas e abas abertas de navegadores via árvore AT-SPI2."""
        from .a11y import DesktopInspector
        inspector = DesktopInspector()
        apps = inspector.list_applications()

        found_browsers: list[dict[str, Any]] = []
        for app_name in apps:
            app_low = app_name.lower()
            if any(b in app_low for b in KNOWN_BROWSER_NAMES):
                root = inspector.inspect_application(app_name, max_depth=3)
                if not root:
                    continue

                tabs = []
                for child in root.children:
                    if child.role == "frame" and child.name:
                        t_name = child.name.strip()
                        if t_name and t_name not in tabs:
                            tabs.append(t_name)

                if tabs:
                    found_browsers.append({
                        "browser": app_name,
                        "tabs": tabs,
                        "active_tab": tabs[0] if tabs else "",
                    })

        return found_browsers

    @classmethod
    def read_active_tab(cls, fallback_url: str | None = None) -> dict[str, Any]:
        """Tenta ler a aba atualmente aberta no navegador ativo ou o link informado."""
        if fallback_url:
            return cls.fetch_and_clean(fallback_url)

        browsers = cls.get_active_browser_tabs()
        if not browsers:
            # Fallback para área de transferência se contiver uma URL
            from .clipboard import ClipboardService
            clip = ClipboardService.get_text().strip()
            if clip.startswith(("http://", "https://")):
                res = cls.fetch_and_clean(clip)
                res["source"] = "clipboard"
                return res

            return {
                "success": False,
                "url": "",
                "title": "",
                "description": "",
                "text": "Nenhum navegador com abas detectado via AT-SPI2 e nenhuma URL encontrada na área de transferência.",
                "length": 0,
            }

        top_browser = browsers[0]
        tab_title = top_browser.get("active_tab", "")

        # Se o título da aba for um link ou domínio direto (ex: web.whatsapp.com ou github.com/...)
        domain_match = re.search(r"([a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?)", tab_title)
        if domain_match:
            candidate_url = f"https://{domain_match.group(1)}"
            res = cls.fetch_and_clean(candidate_url)
            if res["success"]:
                res["browser"] = top_browser["browser"]
                return res

        return {
            "success": True,
            "browser": top_browser["browser"],
            "title": tab_title,
            "tabs": top_browser["tabs"],
            "url": "",
            "text": f"Navegador aberto: {top_browser['browser']}\nAba em foco: {tab_title}\nOutras abas abertas: {', '.join(top_browser['tabs'][:4])}",
            "length": 0,
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

    @classmethod
    def read_page(cls, url: str | None = None) -> dict[str, Any]:
        """Lê e higieniza a página da web informada ou da aba ativa aberta no navegador."""
        return WebPageReader.read_active_tab(fallback_url=url)

    @classmethod
    def get_open_tabs(cls) -> list[dict[str, Any]]:
        """Retorna lista de janelas e abas ativas detectadas nos navegadores da área de trabalho."""
        return WebPageReader.get_active_browser_tabs()
