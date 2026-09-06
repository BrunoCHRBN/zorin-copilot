# Decisão de design: Estilização Glassmorphism em GTK4 / Libadwaita com transparência acrílica,
# opacidade equilibrada em 96% para impedir vazamento de textos de janelas em segundo plano,
# specular highlights (bordas translúcidas e reflexos internos), alto contraste tipográfico e
# sincronização dinâmica em tempo real entre modo claro (frosted light) e modo escuro (smoked glass).

"""Estilos visuais e temas Glassmorphism para a interface do Zorin Copilot.

O CSS base vive em ``data/zorin-copilot.css`` (arquivo de dados do pacote), não mais
num string gigante dentro deste módulo. Isso permite que o usuário sobrescreva a
aparência sem tocar no código, pela seguinte cadeia — cada item vence o anterior:

    1. tema embutido no pacote
    2. /usr/share/zorin-copilot/zorin-copilot.css   (ajuste da distro)
    3. ~/.config/zorin-copilot/themes/*.css         (temas instalados, ordem alfabética)
    4. ~/.config/zorin-copilot/user.css             (override final do usuário)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

logger = logging.getLogger(__name__)

#: CSS instalado pela distribuição; vence o tema embutido.
SYSTEM_CSS_PATH = Path("/usr/share/zorin-copilot/zorin-copilot.css")


def config_dir() -> Path:
    """Diretório de configuração do usuário (respeita ``XDG_CONFIG_HOME``)."""
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "zorin-copilot"


def user_css_path() -> Path:
    return config_dir() / "user.css"


def themes_dir() -> Path:
    return config_dir() / "themes"


def _load_bundled_css() -> str:
    """Lê o CSS embutido no pacote.

    Se o arquivo de dados não estiver instalado, devolve string vazia e registra
    erro — os testes de estilo falham alto nesse caso, o que é o comportamento
    desejado para um erro de empacotamento.
    """
    try:
        from importlib.resources import files

        resource = files("zorin_copilot").joinpath("data/zorin-copilot.css")
        return resource.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - erro de empacotamento
        logger.error("Não foi possível carregar o CSS embutido: %s", exc)
        return ""


GLASS_CSS = _load_bundled_css()


def load_stylesheet_chain() -> list[tuple[str, str]]:
    """Devolve ``[(nome, css)]`` em ordem crescente de prioridade."""
    chain: list[tuple[str, str]] = []

    if GLASS_CSS.strip():
        chain.append(("bundled", GLASS_CSS))

    for path, label in _external_sources():
        if not path.is_file():
            # Ausência é o caso normal: só o tema embutido é garantido.
            continue
        try:
            css = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Ignorando folha de estilo %s: %s", path, exc)
            continue
        if css.strip():
            chain.append((label, css))

    return chain


def _external_sources() -> Iterable[tuple[Path, str]]:
    """Caminhos externos de CSS, da menor para a maior prioridade."""
    yield SYSTEM_CSS_PATH, "system"

    themes = themes_dir()
    if themes.is_dir():
        for entry in sorted(themes.glob("*.css"), key=lambda p: p.name):
            yield entry, f"theme:{entry.name}"

    yield user_css_path(), "user"


_provider_installed = False


def apply_glass_theme(display: Gdk.Display | None = None) -> None:
    """Aplica a cadeia de CSS à aplicação, com o override do usuário por último."""
    global _provider_installed
    if _provider_installed:
        return

    if display is None:
        display = Gdk.Display.get_default()

    if display is None:
        return

    base_priority = Gtk.STYLE_PROVIDER_PRIORITY_USER
    for offset, (name, css) in enumerate(load_stylesheet_chain()):
        provider = Gtk.CssProvider()
        try:
            provider.load_from_string(css)
        except Exception as exc:
            # CSS inválido do usuário não pode derrubar a interface.
            logger.error("Folha de estilo inválida (%s): %s", name, exc)
            continue
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            base_priority + offset,
        )

    _provider_installed = True


def setup_glass_window(window: Adw.ApplicationWindow) -> None:
    """Configura o efeito glassmorphism na janela e sincroniza tema claro/escuro."""
    apply_glass_theme(window.get_display())
    window.add_css_class("glass-window")

    style_manager = Adw.StyleManager.get_default()

    def sync_color_scheme(*_):
        if style_manager.get_dark():
            window.add_css_class("dark-glass")
            window.remove_css_class("light-glass")
        else:
            window.add_css_class("light-glass")
            window.remove_css_class("dark-glass")

    style_manager.connect("notify::dark", sync_color_scheme)
    sync_color_scheme()
