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

    @classmethod
    def find_app(cls, query: str) -> tuple[Gio.AppInfo | None, str]:
        """Busca o melhor aplicativo para a consulta do usuário. Devolve (app, nome_amigavel)."""
        clean_query = cls._sanitize_query(query)
        if not clean_query:
            return None, ""

        all_apps = cls.get_all_apps()
        if not all_apps:
            return None, ""

        # 1. Checa apelidos comuns (ex: 'navegador' -> Firefox)
        if clean_query in COMMON_ALIASES:
            for alias in COMMON_ALIASES[clean_query]:
                for app in all_apps:
                    app_id = (app.get_id() or "").lower()
                    exe = (app.get_executable() or "").lower()
                    if alias in app_id or alias in exe:
                        return app, app.get_name()

        # 2. Correspondência exata por nome ou executável
        for app in all_apps:
            name = (app.get_name() or "").lower()
            exe = (app.get_executable() or "").lower()
            app_id = (app.get_id() or "").lower()
            if clean_query == name or clean_query == exe or clean_query == app_id:
                return app, app.get_name()

        # 3. Correspondência por contenção (substring)
        for app in all_apps:
            name = (app.get_name() or "").lower()
            exe = (app.get_executable() or "").lower()
            display = (app.get_display_name() or "").lower()
            if clean_query in name or clean_query in exe or clean_query in display:
                return app, app.get_name()

        # 4. Correspondência aproximada (Fuzzy matching)
        names = {app.get_name().lower(): app for app in all_apps if app.get_name()}
        matches = difflib.get_close_matches(clean_query, names.keys(), n=1, cutoff=0.55)
        if matches:
            best_match = matches[0]
            matched_app = names[best_match]
            return matched_app, matched_app.get_name()

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
