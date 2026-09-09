# Decisão de design: descoberta de aplicativos via Gio.AppInfo nativo — encontra qualquer app instalado (APT, Flatpak, Snap) sem listas hardcoded.

"""Gerenciador de aplicativos instalados no sistema Zorin OS."""

from __future__ import annotations

import difflib
import logging
import os
import re
import shutil
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

logger = logging.getLogger(__name__)


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


#: Terminais conhecidos. Serve como último recurso: se o usuário pediu um
#: terminal específico que não está nesta máquina (o modelo mandou "kitty",
#: "konsole"...), abrimos *qualquer* terminal do ambiente em vez de responder
#: "não encontrado" e travar o resto do plano.
TERMINAL_BINS: frozenset[str] = frozenset(COMMON_ALIASES["terminal"])

#: Formas de pedir "um terminal, qualquer um". Inclui o próprio "terminal"
#: (que é a *chave* em COMMON_ALIASES, não um valor) porque o modelo às vezes
#: manda o termo genérico e queremos cair no fallback mesmo assim.
TERMINAL_REQUEST_WORDS: frozenset[str] = frozenset(
    TERMINAL_BINS | {"terminal", "emulador de terminal", "console", "shell"}
)


def is_terminal_request(query: str) -> bool:
    """O pedido é por um terminal (não importa qual)?"""
    return (query or "").strip().lower() in TERMINAL_REQUEST_WORDS


#: Binários que existem no $PATH mas nunca são "aplicativo". O índice do Gio
#: nos protegia disso naturalmente — ele só enxerga .desktop, e ninguém
#: empacota `shutdown.desktop`. Ao abrir o fallback pelo $PATH, a proteção
#: precisa ser explícita: sem ela, um "abrir o shutdown" dito por voz (ou
#: alucinado pelo modelo) chegaria ao Popen.
NEVER_LAUNCH_BINS: frozenset[str] = frozenset({
    # energia / init
    "shutdown", "reboot", "halt", "poweroff", "init", "systemctl",
    # destrutivos
    "rm", "dd", "mkfs", "mkfs.ext4", "mkfs.vfat", "fdisk", "parted",
    "wipefs", "shred", "format",
    # contas e privilégio
    "chown", "chmod", "chgrp", "passwd", "useradd", "userdel", "usermod",
    "su", "sudo", "doas", "pkexec",
    # processos
    "kill", "killall", "pkill",
    # gerenciadores de pacote — instalar é `run_command` (com portão de risco),
    # não `launch_app`; abrir "o pacman" como app não faz sentido.
    "pacman", "apt", "apt-get", "dpkg", "yay", "paru", "dnf", "zypper",
})


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

    @staticmethod
    def find_binary(query: str) -> str:
        """Resolve `query` direto no $PATH. Devolve '' quando não existe.

        Por que isso existe: o índice do Gio só conhece apps que tenham
        .desktop válido **e** `should_show()` verdadeiro. Num ambiente
        minimalista (Hyprland/Sway/Niri) isso deixa de fora boa parte do que
        o usuário instalou — kitty, foot e wezterm entram por pacote e, sem
        entrada de menu indexada, o Gio simplesmente não os vê. O $PATH não
        tem essa opinião: se o binário está lá, o app existe.

        Só aceitamos um nome simples. Caminho, espaços ou flags vindos do
        modelo são rejeitados — não executamos argumento arbitrário. Nomes em
        `NEVER_LAUNCH_BINS` também: existem no $PATH, mas não são aplicativo.
        """
        name = (query or "").strip()
        if not name or "/" in name or " " in name or "\t" in name or name.startswith("-"):
            return ""
        if name.lower() in NEVER_LAUNCH_BINS:
            logger.warning("find_binary: '%s' está na lista de nunca-lançar; ignorado.", name)
            return ""
        try:
            return shutil.which(name) or ""
        except Exception:
            return ""

    @classmethod
    def find_terminal_in_path(cls) -> str:
        """Primeiro terminal disponível no $PATH, com os nativos do ambiente na frente."""
        for alias in ordered_aliases("terminal", cls._current_desktop()):
            found = cls.find_binary(alias)
            if found:
                return found
        return ""

    @staticmethod
    def is_executable(path: str) -> bool:
        """`path` existe e é executável? (usado antes de dar Popen num binário do $PATH)"""
        return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)

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
