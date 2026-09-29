"""Classificação de servidores MCP por categoria.

Por que isto existe: a lista de servidores era plana — seis linhas com a mesma
silhueta e nenhuma noção de *o que aquele servidor alcança*. Quem vai ligar um
servidor quer saber, antes de tudo, se ele mexe nos arquivos desta máquina, se
sai para a internet ou se fala com a conta de um serviço externo.

A categoria é **derivada**, não configurada. Exigir um campo ``category`` no
``mcp.json`` obrigaria o usuário a etiquetar cada servidor à mão e deixaria todo
arquivo de configuração já existente sem etiqueta — o mesmo tipo de dívida que
o ``description`` opcional já criou.

Casamento por **token inteiro**, nunca por substring. O caso que obriga a isso:
``github`` contém ``git``, e casar por substring jogaria o servidor do GitHub no
grupo "Nesta máquina" — exatamente o erro que este módulo existe para evitar.
"""

from __future__ import annotations

import re
from typing import Any, Final

CATEGORY_LOCAL: Final[str] = "local"
CATEGORY_WEB: Final[str] = "web"
CATEGORY_SERVICE: Final[str] = "service"
CATEGORY_OTHER: Final[str] = "other"

#: Ordem de exibição: do que toca a máquina para o que sai dela, e "Outros" por
#: último — é o resto, não uma categoria de verdade.
CATEGORY_ORDER: Final[tuple[str, ...]] = (
    CATEGORY_LOCAL,
    CATEGORY_WEB,
    CATEGORY_SERVICE,
    CATEGORY_OTHER,
)

CATEGORY_LABELS: Final[dict[str, str]] = {
    CATEGORY_LOCAL: "Nesta máquina",
    CATEGORY_WEB: "Web",
    CATEGORY_SERVICE: "Serviços externos",
    CATEGORY_OTHER: "Outros",
}

CATEGORY_DESCRIPTIONS: Final[dict[str, str]] = {
    CATEGORY_LOCAL: "Leem e escrevem arquivos, repositórios e bancos desta máquina.",
    CATEGORY_WEB: "Buscam e baixam conteúdo da internet.",
    CATEGORY_SERVICE: "Falam com a API de um serviço externo, em geral com token.",
    CATEGORY_OTHER: "O nome e o comando não dizem o que este servidor alcança.",
}

# Tokens são comparados inteiros. Nomes compostos por hífen ou barra são
# partidos antes da comparação (`mcp-server-git` → `mcp`, `server`, `git`).
_TOKENS_SERVICE: Final[frozenset[str]] = frozenset({
    "github", "gitlab", "gitea", "bitbucket", "sentry", "slack", "discord",
    "telegram", "notion", "linear", "jira", "confluence", "trello", "asana",
    "stripe", "aws", "azure", "gcp", "cloud", "cloudflare", "vercel", "netlify",
    "drive", "gdrive", "gmail", "calendar", "sheets", "dropbox", "figma",
    "spotify", "openai", "anthropic", "homeassistant", "hass",
})

_TOKENS_WEB: Final[frozenset[str]] = frozenset({
    # `http`, `https` e `url` ficam de fora de propósito: são transporte, não
    # capacidade. Com eles, qualquer servidor remoto casaria por estar atrás de
    # uma URL e iria parar em "Web" mesmo sem buscar nada na internet.
    "fetch", "web", "webpage", "browser", "browse",
    "crawl", "crawler", "scrape", "scraper", "spider", "puppeteer", "playwright",
    "selenium", "duckduckgo", "brave", "bing", "yahoo", "wikipedia", "rss",
    "news", "arxiv", "pubmed", "download",
})

_TOKENS_LOCAL: Final[frozenset[str]] = frozenset({
    "git", "sqlite", "filesystem", "file", "files", "fs", "memory", "knowledge",
    "postgres", "postgresql", "mysql", "mariadb", "duckdb", "mongodb", "redis",
    "elasticsearch", "time", "datetime", "sequential", "thinking", "pandas",
    "csv", "excel", "xlsx", "docker", "podman", "shell", "bash", "terminal",
    "process", "system", "hardware", "screenshot", "clipboard",
})

#: Ordem de desempate quando o mesmo servidor casa em mais de um conjunto: vence
#: o alcance **mais amplo**, porque é o que importa para decidir se confia. Um
#: servidor que pode tocar tanto a internet quanto os arquivos locais é
#: anunciado pelo que alcança mais longe.
_PRECEDENCE: Final[tuple[tuple[frozenset[str], str], ...]] = (
    (_TOKENS_SERVICE, CATEGORY_SERVICE),
    (_TOKENS_WEB, CATEGORY_WEB),
    (_TOKENS_LOCAL, CATEGORY_LOCAL),
)

_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")

_SCHEME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


def strip_scheme(url: Any) -> str:
    """Remove ``https://`` antes de tokenizar a URL.

    O host ainda vale como pista (``api.github.com`` traz ``github``), mas o
    esquema não diz nada sobre o que o servidor faz.
    """
    if not url:
        return ""
    return _SCHEME_RE.sub("", str(url))


def normalize_tokens(*parts: Any) -> set[str]:
    """Parte os textos em tokens minúsculos, ignorando pontuação e separadores.

    Recebe qualquer coisa porque a origem é configuração escrita à mão: `None`,
    listas de argumentos e strings com caminhos aparecem todos no mesmo lugar.
    """
    joined: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple, set)):
            joined.extend(str(p) for p in part)
        else:
            joined.append(str(part))
    return {t for t in _SPLIT_RE.split(" ".join(joined).lower()) if t}


def _haystack_tokens(item: dict[str, Any]) -> set[str]:
    """Só identificadores — nome, comando e URL.

    A ``description`` fica de fora de propósito: é prosa escrita à mão ("operações
    em repositórios Git") e usá-la classificaria o servidor pelo texto que o
    usuário escreveu sobre ele, não pelo que ele executa. Classificar por prosa
    é adivinhação com aparência de dado.
    """
    return normalize_tokens(
        item.get("name"),
        item.get("command"),
        strip_scheme(item.get("url")),
    )


def is_remote(item: dict[str, Any]) -> bool:
    """Servidor hospedado fora desta máquina (transporte SSE, ou só URL)."""
    if str(item.get("transport") or "").lower() == "sse":
        return True
    return bool(item.get("url")) and not str(item.get("command") or "").strip()


def classify_mcp_server(item: dict[str, Any]) -> str:
    """Categoria de um servidor MCP a partir do que a config diz dele.

    Espera um item de ``MCPManager.get_status_summary()``. Nunca levanta: um item
    malformado cai em ``CATEGORY_OTHER``, que é uma resposta honesta ("não sei o
    que isto alcança") e não um erro que impede a tela de preferências de abrir.
    """
    if not isinstance(item, dict):
        return CATEGORY_OTHER

    tokens = _haystack_tokens(item)
    if tokens:
        for token_set, category in _PRECEDENCE:
            if tokens & token_set:
                return category

    # Nenhum token reconhecido: o transporte ainda diz alguma coisa. Um servidor
    # remoto é um serviço externo mesmo com nome que não conhecemos.
    if is_remote(item):
        return CATEGORY_SERVICE

    return CATEGORY_OTHER


def group_by_category(
    items: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Agrupa itens por categoria, na ordem de exibição.

    Devolve apenas as categorias **com conteúdo** — um grupo vazio não deve
    virar um cabeçalho sem linhas. A ordem das linhas dentro de cada grupo é a
    de entrada, que já vem ordenada por nome da configuração.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        buckets.setdefault(classify_mcp_server(item), []).append(item)

    return [
        (category, buckets[category])
        for category in CATEGORY_ORDER
        if buckets.get(category)
    ]
