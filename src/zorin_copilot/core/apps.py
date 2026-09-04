# Decisão de design: descoberta de aplicativos via Gio.AppInfo nativo — encontra qualquer app instalado (APT, Flatpak, Snap) sem listas hardcoded.

"""Gerenciador de aplicativos instalados no sistema Zorin OS."""

from __future__ import annotations

import difflib
import re
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio


COMMON_ALIASES = {
    "navegador": ["firefox", "brave", "google-chrome", "chromium"],
    "browser": ["firefox", "brave", "google-chrome", "chromium"],
    "terminal": ["gnome-terminal", "kgx", "alacritty", "xterm"],
    "arquivos": ["org.gnome.nautilus", "nautilus"],
    "explorer": ["org.gnome.nautilus", "nautilus"],
    "pasta": ["org.gnome.nautilus", "nautilus"],
    "calculadora": ["org.gnome.calculator", "calculator"],
    "configuracoes": ["gnome-control-center"],
    "configurações": ["gnome-control-center"],
    "ajustes": ["gnome-tweaks", "zorin-appearance"],
    "loja": ["gnome-software"],
    "software": ["gnome-software"],
    "editor": ["gnome-text-editor", "gedit", "code"],
    "codigo": ["code", "visual-studio-code"],
    "código": ["code", "visual-studio-code"],
    "musica": ["rhythmbox", "spotify"],
    "música": ["rhythmbox", "spotify"],
    "email": ["thunderbird", "geary"],
    "e-mail": ["thunderbird", "geary"],
}


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

        # 1. Checa apelidos comuns (ex: 'navegador' -> Brave/Chrome/Firefox)
        if clean_query in COMMON_ALIASES:
            for alias in COMMON_ALIASES[clean_query]:
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
