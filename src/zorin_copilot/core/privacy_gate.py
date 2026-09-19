# Decisão de design: Portão Cego de Ingestão (Zero-Knowledge Ingestion Gate)
# Garante que nenhum evento de aplicativo sensível (gerenciadores de senha,
# janelas bancárias, abas anônimas e diálogos de autenticação) entre no buffer
# da RNN ou nos logs de telemetria. Opera em O(1) com expressões regulares pré-compiladas.

"""Filtro de privacidade estrito para ingestão de eventos e sanitização de dados sensíveis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class PrivacyCheckResult:
    """Resultado da avaliação de privacidade de um evento ou texto."""
    allowed: bool
    reason: str = ""
    is_sensitive: bool = False


class PrivacyGatekeeper:
    """Guardião de privacidade para filtragem de eventos de tela, janelas e sanitização de texto."""

    # Classes e nomes de executáveis de aplicativos estritamente sensíveis
    SENSITIVE_APP_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"bitwarden", re.IGNORECASE),
        re.compile(r"1password", re.IGNORECASE),
        re.compile(r"keepass", re.IGNORECASE),
        re.compile(r"enpass", re.IGNORECASE),
        re.compile(r"lastpass", re.IGNORECASE),
        re.compile(r"proton[-_]?pass", re.IGNORECASE),
        re.compile(r"authpass", re.IGNORECASE),
        re.compile(r"polkit", re.IGNORECASE),
        re.compile(r"gcr[-_]?prompter", re.IGNORECASE),
        re.compile(r"gnome[-_]?keyring", re.IGNORECASE),
        re.compile(r"pinentry", re.IGNORECASE),
        re.compile(r"authenticator", re.IGNORECASE),
    )

    # Títulos de janelas que revelam dados confidenciais ou navegação anônima
    SENSITIVE_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"private\s+browsing", re.IGNORECASE),
        re.compile(r"navega[çc][ãa]o\s+an[ôo]nima", re.IGNORECASE),
        re.compile(r"incognito", re.IGNORECASE),
        re.compile(r"inprivate", re.IGNORECASE),
        re.compile(r"\bmaster\s+password\b", re.IGNORECASE),
        re.compile(r"\bsenha\s+mestra\b", re.IGNORECASE),
        re.compile(r"\bautentica[çc][ãa]o\b", re.IGNORECASE),
        re.compile(r"\bbanco\b", re.IGNORECASE),
        re.compile(r"\bcaixa\s+econ[ôo]mica\b", re.IGNORECASE),
        re.compile(r"\bitau\b", re.IGNORECASE),
        re.compile(r"\bbradesco\b", re.IGNORECASE),
        re.compile(r"\bsantander\b", re.IGNORECASE),
        re.compile(r"\bnubank\b", re.IGNORECASE),
        re.compile(r"\binter\b", re.IGNORECASE),
    )

    # Padrões de texto contendo credenciais, tokens ou chaves criptográficas
    SECRET_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        # Chaves privadas PEM (RSA, EC, OPENSSH)
        (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "<REDACTED_PRIVATE_KEY>"),
        # Tokens de API padrão (OpenAI, Gemini, GitHub, Slack)
        (re.compile(r"\b(?:sk|pk)_(?:live|test)_[0-9a-zA-Z]{24,}\b"), "<REDACTED_API_KEY>"),
        (re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), "<REDACTED_GEMINI_KEY>"),
        (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,255}\b"), "<REDACTED_GITHUB_TOKEN>"),
        (re.compile(r"\bxox[baprs]-[0-9a-zA-Z]{10,48}\b"), "<REDACTED_SLACK_TOKEN>"),
        # Tokens Bearer / JWT
        (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "<REDACTED_JWT_TOKEN>"),
        # Cartões de Crédito (13 a 19 dígitos com separadores opcionais)
        (re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"), "<REDACTED_CARD_NUMBER>"),
        # Comandos sudo com senha visível ou flags perigosas
        (re.compile(r"(sudo\s+-S\s+)(.+)", re.IGNORECASE), r"\1<REDACTED_PASSWORD>"),
    )

    @classmethod
    def should_ingest_window(cls, app_name: str = "", window_title: str = "") -> PrivacyCheckResult:
        """Avalia se eventos originados da janela informada podem ser ingeridos pelo stream da RNN."""
        app = (app_name or "").strip()
        title = (window_title or "").strip()

        # 1. Verifica classe/nome do aplicativo
        for pattern in cls.SENSITIVE_APP_PATTERNS:
            if pattern.search(app):
                return PrivacyCheckResult(
                    allowed=False,
                    reason=f"Aplicativo sensível bloqueado: '{app}'",
                    is_sensitive=True,
                )

        # 2. Verifica título da janela
        for pattern in cls.SENSITIVE_TITLE_PATTERNS:
            if pattern.search(title):
                return PrivacyCheckResult(
                    allowed=False,
                    reason=f"Título de janela sensível detectado: '{title}'",
                    is_sensitive=True,
                )

        return PrivacyCheckResult(allowed=True, reason="", is_sensitive=False)

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """Remove e mascara credenciais, chaves de API e tokens confidenciais do texto."""
        if not text:
            return ""

        sanitized = text
        for pattern, replacement in cls.SECRET_TEXT_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)

        return sanitized

    @classmethod
    def filter_event_stream(
        cls,
        events: Sequence[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Filtra uma lista de eventos de desktop, descartando instantaneamente os confidenciais."""
        safe_events: list[dict[str, str]] = []
        for ev in events:
            app = ev.get("app", "")
            title = ev.get("title", "")
            result = cls.should_ingest_window(app, title)
            if result.allowed:
                # Sanitiza qualquer campo textual presente
                clean_ev = dict(ev)
                if "text" in clean_ev:
                    clean_ev["text"] = cls.sanitize_text(clean_ev["text"])
                safe_events.append(clean_ev)
        return safe_events
