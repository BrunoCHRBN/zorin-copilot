# Decisão de design: descoberta de aplicativos via Gio.AppInfo nativo — encontra qualquer app instalado (APT, Flatpak, Snap) sem listas hardcoded.

"""Gerenciador de aplicativos instalados no sistema Zorin OS."""

from __future__ import annotations

import difflib
import re
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio


COMMON_ALIASES: dict[str, list[str]] = {
    "navegador": [
        "firefox", "brave", "google-chrome", "chromium", "vivaldi",
        "librewolf", "zen-browser", "qutebrowser", "microsoft-edge", "epiphany",
    ],
    "browser": [
        "firefox", "brave", "google-chrome", "chromium", "vivaldi",
        "librewolf", "zen-browser", "qutebrowser", "microsoft-edge", "epiphany",
    ],
    "terminal": [
        "kitty", "alacritty", "foot", "wezterm", "gnome-terminal", "kgx",
        "konsole", "xfce4-terminal", "tilix", "terminator", "xterm",
    ],
    "arquivos": [
        "dolphin", "thunar", "nautilus", "org.gnome.nautilus", "nemo",
        "caja", "pcmanfm", "krusader",
    ],
    "explorer": [
        "dolphin", "thunar", "nautilus", "org.gnome.nautilus", "nemo",
        "caja", "pcmanfm", "krusader",
    ],
    "pasta": [
        "dolphin", "thunar", "nautilus", "org.gnome.nautilus", "nemo",
        "caja", "pcmanfm", "krusader",
    ],
    "calculadora": ["kcalc", "gnome-calculator", "org.gnome.calculator", "qalculate-gtk", "speedcrunch"],
    "configuracoes": ["systemsettings", "gnome-control-center", "xfce4-settings-manager", "cinnamon-settings"],
    "configurações": ["systemsettings", "gnome-control-center", "xfce4-settings-manager", "cinnamon-settings"],
    "ajustes": ["gnome-tweaks", "zorin-appearance", "systemsettings", "xfce4-settings-manager"],
    "loja": ["discover", "gnome-software", "pamac-manager", "octopi", "bauh"],
    "software": ["discover", "gnome-software", "pamac-manager", "octopi", "bauh"],
    "editor": [
        "code", "codium", "vscodium", "zed", "gnome-text-editor", "gedit",
        "kate", "mousepad", "sublime_text", "emacs",
    ],
    "editor de texto": ["gnome-text-editor", "gedit", "kate", "mousepad", "xed", "code"],
    "codigo": ["code", "codium", "vscodium", "zed", "sublime_text"],
    "código": ["code", "codium", "vscodium", "zed", "sublime_text"],
    "musica": ["spotify", "rhythmbox", "elisa", "lollypop", "strawberry", "clementine", "audacious"],
    "música": ["spotify", "rhythmbox", "elisa", "lollypop", "strawberry", "clementine", "audacious"],
    "video": ["vlc", "mpv", "celluloid", "haruna", "smplayer", "totem", "kdenlive"],
    "vídeo": ["vlc", "mpv", "celluloid", "haruna", "smplayer", "totem", "kdenlive"],
    "email": ["thunderbird", "betterbird", "geary", "evolution", "kmail"],
    "e-mail": ["thunderbird", "betterbird", "geary", "evolution", "kmail"],
    "notas": ["obsidian", "joplin", "xournalpp", "gnote", "tumbler"],
    "chat": ["discord", "telegram-desktop", "signal-desktop", "element", "slack"],
    "captura de tela": ["flameshot", "spectacle", "gnome-screenshot", "grim"],
    "monitor do sistema": ["btop", "htop", "gnome-system-monitor", "ksysguard", "mission-center"],
}


#: Em cada ambiente alguns aliases devem ser tentados antes dos outros — a lista
#: genérica acima cobre "existe", esta cobre "é o nativo daqui". Sem isso, pedir
#: "terminal" no KDE poderia abrir o xterm só porque ele vem primeiro na lista.
ENV_PREFERRED_ALIASES: dict[str, tuple[str, ...]] = {
    "gnome": (
        "gnome-terminal", "kgx", "nautilus", "org.gnome.nautilus",
        "gnome-control-center", "gnome-text-editor", "gedit",
        "gnome-calculator", "org.gnome.calculator", "gnome-software",
        "gnome-tweaks", "totem", "eog", "gnome-system-monitor",
    ),
    "kde": (
        "konsole", "dolphin", "systemsettings", "kate", "kcalc",
        "discover", "kmail", "elisa", "haruna", "ksysguard", "spectacle",
    ),
    "hyprland": ("kitty", "foot", "alacritty", "wezterm", "thunar", "nemo"),
    "sway": ("foot", "kitty", "alacritty", "wezterm", "thunar", "nemo"),
    "niri": ("kitty", "foot", "alacritty", "wezterm", "thunar", "nemo"),
    "labwc": ("foot", "kitty", "alacritty", "wezterm", "thunar", "nemo"),
    "xfce": ("xfce4-terminal", "thunar", "xfce4-settings-manager", "mousepad"),
    "cinnamon": ("gnome-terminal", "nemo", "cinnamon-settings", "xed"),
    "mate": ("mate-terminal", "caja", "pluma"),
}


def ordered_aliases(query: str, desktop: str | None = None) -> list[str]:
    """Aliases do termo, com os naturais do ambiente na frente.

    Ordem estável: nada é removido nem duplicado, apenas reordenado.
    """
    aliases = list(COMMON_ALIASES.get(query, []))
    if not aliases or not desktop:
        return aliases

    preferred = ENV_PREFERRED_ALIASES.get(desktop, ())
    if not preferred:
        return aliases

    rank = {alias: index for index, alias in enumerate(preferred)}
    return sorted(aliases, key=lambda alias: (rank.get(alias, len(rank)),))


class AppManager:
    """Indexa e localiza aplicativos visíveis no sistema."""

    @staticmethod
    def get_all_apps() -> list[Gio.AppInfo]:
        try:
            return [app for app in Gio.AppInfo.get_all() if app.should_show()]
        except Exception:
            return []

    get_installed_apps = get_all_apps

    @classmethod
    def is_app_launch_intent(cls, text: str) -> tuple[bool, str]:
        """Identifica se a intenção do usuário é abrir/executar um programa."""
        t = text.strip()
        low = t.lower()
        if not t:
            return False, ""

        # 1. Padrões explícitos com verbos de lançamento
        launch_prefixes = [
            r"^(?:abrir|abre|abra|iniciar|inicia|execute|executa|rodar|roda|lançar|lança|open|start)\s+(?:o\s+|a\s+|o\s+app\s+|o\s+aplicativo\s+)?(.+)$",
        ]
        for pattern in launch_prefixes:
            m = re.match(pattern, low)
            if m:
                target = m.group(1).strip(" ?.!\"'")
                return True, target

        # 2. Se for um termo curto (até 3 palavras) que não seja uma pergunta conversacional
        question_starters = (
            "como", "onde", "qual", "quem", "por que", "porque",
            "o que", "quando", "quanto", "pesquise", "pesquisar",
            "busque", "buscar", "lembre", "guarde", "salve",
        )
        words = low.split()
        if not any(low.startswith(q) for q in question_starters) and len(words) <= 3:
            return True, t

        return False, ""

    @classmethod
    def suggest_apps(cls, query: str, limit: int = 3) -> list[Gio.AppInfo]:
        """Retorna uma lista ordenada com os melhores aplicativos compatíveis com a busca."""
        clean_query = cls._sanitize_query(query)
        if not clean_query:
            return []

        all_apps = cls.get_all_apps()
        if not all_apps:
            return []

        results: list[Gio.AppInfo] = []
        seen_ids: set[str] = set()

        def add_app(app: Gio.AppInfo):
            app_id = app.get_id() or app.get_name()
            if app_id not in seen_ids:
                seen_ids.add(app_id)
                results.append(app)

        # 1. Checa apelidos comuns (ex: 'navegador' -> Brave/Chrome/Firefox),
        #    com os aliases nativos do ambiente na frente da fila.
        if clean_query in COMMON_ALIASES:
            for alias in ordered_aliases(clean_query, cls._current_desktop()):
                for app in all_apps:
                    app_id = (app.get_id() or "").lower()
                    exe = (app.get_executable() or "").lower()
                    if alias in app_id or alias in exe:
                        add_app(app)

        # 2. Correspondência exata por nome
        for app in all_apps:
            name = (app.get_name() or "").lower()
            if name == clean_query:
                add_app(app)

        # 3. Nome do app começa com o termo digitado (prefix match)
        for app in all_apps:
            name = (app.get_name() or "").lower()
            if name.startswith(clean_query):
                add_app(app)

        # 4. Correspondência em limite de palavras (\btermo)
        pattern = r"\b" + re.escape(clean_query)
        for app in all_apps:
            name = (app.get_name() or "").lower()
            if re.search(pattern, name):
                add_app(app)

        # 5. Correspondência por substring no nome, executável ou display
        for app in all_apps:
            name = (app.get_name() or "").lower()
            exe = (app.get_executable() or "").lower()
            display = (app.get_display_name() or "").lower()
            if clean_query in name or clean_query in exe or clean_query in display:
                add_app(app)

        # 6. Fallback para Fuzzy matching se nada foi encontrado
        if not results:
            names = {app.get_name().lower(): app for app in all_apps if app.get_name()}
            matches = difflib.get_close_matches(clean_query, names.keys(), n=limit, cutoff=0.55)
            for m in matches:
                add_app(names[m])

        return results[:limit]

    @classmethod
    def find_app(cls, query: str) -> tuple[Gio.AppInfo | None, str]:
        """Busca o melhor aplicativo para a consulta do usuário. Devolve (app, nome_amigavel)."""
        suggestions = cls.suggest_apps(query, limit=1)
        if suggestions:
            top_app = suggestions[0]
            return top_app, top_app.get_name()

        return None, ""

    @classmethod
    def launch(cls, app: Gio.AppInfo) -> tuple[bool, str]:
        """Lança o aplicativo no ambiente gráfico do usuário."""
        try:
            ok = app.launch([], None)
            return True, f"Aplicativo '{app.get_name()}' iniciado."
        except Exception as exc:
            return False, f"Falha ao iniciar '{app.get_name()}': {exc}"

    @staticmethod
    def _current_desktop() -> str:
        """Ambiente detectado ('' quando a detecção falha)."""
        try:
            from .desktop.env import current_environment

            return current_environment().desktop or ""
        except Exception:
            return ""

    @staticmethod
    def _sanitize_query(text: str) -> str:
        t = text.lower().strip()
        # Remove prefixos comuns de solicitação
        prefixes = [
            "abrir ", "abre ", "iniciar ", "inicia ", "execute ", "executa ",
            "rodar ", "roda ", "lançar ", "lança ", "open ", "launch ", "start ",
            "o ", "a ", "os ", "as ", "meu ", "minha ", "o aplicativo ", "o app ",
        ]
        for p in prefixes:
            if t.startswith(p):
                t = t[len(p):].strip()
        return re.sub(r"[^\w\s-]", "", t).strip()
