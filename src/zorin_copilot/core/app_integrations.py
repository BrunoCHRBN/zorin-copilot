# Decisão de design: Integrações profundas com aplicações desktop e web via Deep Linking e D-Bus MPRIS2.
# Permite ao assistente realizar ações específicas dentro de serviços como Spotify, Gmail, Google Drive/Docs,
# YouTube e Google Maps em 0ms com determinismo absoluto, além de orientar o LLM para geração estruturada de ações.

"""Módulo de integrações profundas com aplicações (Spotify, Gmail, Google Drive, etc.)."""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any, Optional

from ..ai.actions import ActionPlan, ActionType, DesktopAction

logger = logging.getLogger(__name__)


class SpotifyIntegration:
    """Integração profunda com o Spotify (desktop e web)."""

    @staticmethod
    def play_search(query: str) -> DesktopAction:
        clean_q = query.strip()
        encoded = urllib.parse.quote(clean_q)
        return DesktopAction(
            action_type=ActionType.MEDIA_CONTROL,
            target="search",
            params={
                "action": "search",
                "query": clean_q,
                "player": "spotify",
                "uri": f"spotify:search:{encoded}",
            },
            description=f"Tocar '{clean_q}' no Spotify",
        )

    @staticmethod
    def control(command: str) -> DesktopAction:
        return DesktopAction(
            action_type=ActionType.MEDIA_CONTROL,
            target=command,
            params={"action": command, "player": "spotify"},
            description=f"Spotify: {command}",
        )


class GmailIntegration:
    """Integração profunda com o Google Gmail (web)."""

    BASE_URL = "https://mail.google.com/mail/u/0"

    @classmethod
    def search(cls, query: str) -> DesktopAction:
        clean_q = query.strip()
        url = f"{cls.BASE_URL}/#search/{urllib.parse.quote(clean_q)}"
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=url,
            params={"app": "gmail", "action": "search", "query": clean_q},
            description=f"Buscar '{clean_q}' no Gmail",
        )

    @classmethod
    def compose(cls, to: str = "", subject: str = "", body: str = "") -> DesktopAction:
        params: dict[str, str] = {"view": "cm", "fs": "1"}
        if to:
            params["to"] = to
        if subject:
            params["su"] = subject
        if body:
            params["body"] = body
        url = f"{cls.BASE_URL}/?{urllib.parse.urlencode(params)}"
        desc = f"Escrever email para '{to}' no Gmail" if to else "Escrever novo email no Gmail"
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=url,
            params={"app": "gmail", "action": "compose", "to": to, "subject": subject},
            description=desc,
        )

    @classmethod
    def open_view(cls, view: str = "inbox") -> DesktopAction:
        view_map = {
            "inbox": "#inbox",
            "unread": "#search/is%3Aunread",
            "starred": "#starred",
            "sent": "#sent",
            "drafts": "#drafts",
            "trash": "#trash",
            "spam": "#spam",
        }
        hash_target = view_map.get(view.lower(), "#inbox")
        view_name_pt = {
            "inbox": "Caixa de Entrada",
            "unread": "Emails não lidos",
            "starred": "Com estrela",
            "sent": "Enviados",
            "drafts": "Rascunhos",
            "trash": "Lixeira",
            "spam": "Spam",
        }.get(view.lower(), view.capitalize())
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=f"{cls.BASE_URL}/{hash_target}",
            params={"app": "gmail", "action": "open_view", "view": view},
            description=f"Abrir {view_name_pt} no Gmail",
        )


class GoogleDriveIntegration:
    """Integração profunda com o Google Drive, Google Docs e Google Sheets (web)."""

    BASE_URL = "https://drive.google.com/drive"

    @classmethod
    def search(cls, query: str) -> DesktopAction:
        clean_q = query.strip()
        url = f"{cls.BASE_URL}/search?q={urllib.parse.quote(clean_q)}"
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=url,
            params={"app": "drive", "action": "search", "query": clean_q},
            description=f"Buscar '{clean_q}' no Google Drive",
        )

    @staticmethod
    def create_document(doc_type: str = "doc") -> DesktopAction:
        doc_type_clean = doc_type.lower().strip()
        url_map = {
            "doc": ("https://docs.google.com/document/create", "Novo Google Docs"),
            "document": ("https://docs.google.com/document/create", "Novo Google Docs"),
            "documento": ("https://docs.google.com/document/create", "Novo Google Docs"),
            "texto": ("https://docs.google.com/document/create", "Novo Google Docs"),
            "sheet": ("https://sheets.google.com/create", "Nova planilha Google Sheets"),
            "planilha": ("https://sheets.google.com/create", "Nova planilha Google Sheets"),
            "slide": ("https://slides.google.com/create", "Nova apresentação Google Slides"),
            "apresentacao": ("https://slides.google.com/create", "Nova apresentação Google Slides"),
            "apresentação": ("https://slides.google.com/create", "Nova apresentação Google Slides"),
            "form": ("https://forms.google.com/create", "Novo formulário Google Forms"),
            "formulario": ("https://forms.google.com/create", "Novo formulário Google Forms"),
            "formulário": ("https://forms.google.com/create", "Novo formulário Google Forms"),
        }
        url, desc = url_map.get(doc_type_clean, ("https://docs.google.com/document/create", "Novo Google Docs"))
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=url,
            params={"app": "drive", "action": "create", "type": doc_type_clean},
            description=f"Criar {desc}",
        )

    @classmethod
    def open_view(cls, view: str = "my-drive") -> DesktopAction:
        view_map = {
            "my-drive": "my-drive",
            "recent": "recent",
            "recentes": "recent",
            "shared": "shared-with-me",
            "compartilhados": "shared-with-me",
            "starred": "starred",
            "favoritos": "starred",
            "trash": "trash",
            "lixeira": "trash",
        }
        target_path = view_map.get(view.lower(), "my-drive")
        view_name_pt = {
            "my-drive": "Meu Drive",
            "recent": "Arquivos recentes",
            "recentes": "Arquivos recentes",
            "shared": "Compartilhados comigo",
            "compartilhados": "Compartilhados comigo",
            "starred": "Arquivos favoritos",
            "favoritos": "Arquivos favoritos",
            "trash": "Lixeira do Drive",
            "lixeira": "Lixeira do Drive",
        }.get(view.lower(), "Meu Drive")
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=f"{cls.BASE_URL}/{target_path}",
            params={"app": "drive", "action": "open_view", "view": view},
            description=f"Abrir {view_name_pt} no Google Drive",
        )


class YouTubeIntegration:
    """Integração com o YouTube."""

    @staticmethod
    def search(query: str) -> DesktopAction:
        clean_q = query.strip()
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_q)}"
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=url,
            params={"app": "youtube", "action": "search", "query": clean_q},
            description=f"Pesquisar '{clean_q}' no YouTube",
        )


class GoogleCalendarIntegration:
    """Integração com o Google Agenda / Calendar."""

    @staticmethod
    def view() -> DesktopAction:
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target="https://calendar.google.com/calendar/u/0/r",
            description="Abrir Google Agenda",
        )

    @staticmethod
    def create_event(title: str = "", details: str = "") -> DesktopAction:
        params: dict[str, str] = {}
        if title:
            params["text"] = title
        if details:
            params["details"] = details
        qs = f"?{urllib.parse.urlencode(params)}" if params else ""
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=f"https://calendar.google.com/calendar/u/0/r/eventedit{qs}",
            description=f"Criar evento '{title}' na Google Agenda" if title else "Criar evento na Google Agenda",
        )


class GoogleMapsIntegration:
    """Integração com o Google Maps."""

    @staticmethod
    def search(query: str) -> DesktopAction:
        clean_q = query.strip()
        url = f"https://www.google.com/maps/search/{urllib.parse.quote_plus(clean_q)}"
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=url,
            description=f"Buscar '{clean_q}' no Google Maps",
        )

    @staticmethod
    def directions(destination: str) -> DesktopAction:
        clean_d = destination.strip()
        url = f"https://www.google.com/maps/dir/?api=1&destination={urllib.parse.quote_plus(clean_d)}"
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=url,
            description=f"Traçar rota para '{clean_d}' no Google Maps",
        )


class WhatsAppIntegration:
    """Integração com o WhatsApp Web."""

    @staticmethod
    def open_chat(phone: str = "", text: str = "") -> DesktopAction:
        clean_phone = re.sub(r"[^\d+]", "", phone)
        if clean_phone:
            qs = f"?text={urllib.parse.quote(text)}" if text else ""
            url = f"https://web.whatsapp.com/send?phone={clean_phone}{qs}"
            desc = f"Conversa com {phone} no WhatsApp Web"
        else:
            url = "https://web.whatsapp.com"
            desc = "Abrir WhatsApp Web"
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=url,
            description=desc,
        )


class GitHubIntegration:
    """Integração com o GitHub."""

    @staticmethod
    def search(query: str) -> DesktopAction:
        clean_q = query.strip()
        url = f"https://github.com/search?q={urllib.parse.quote_plus(clean_q)}"
        return DesktopAction(
            action_type=ActionType.OPEN_URL,
            target=url,
            description=f"Buscar '{clean_q}' no GitHub",
        )


class AppIntentRouter:
    """Identifica padrões de intenção de interação profunda em aplicações (Spotify, Gmail, Drive, etc.)."""

    @classmethod
    def resolve_intent(cls, prompt: str) -> Optional[ActionPlan]:
        low = prompt.lower().strip()

        # -------------------------------------------------------------
        # 1. SPOTIFY
        # -------------------------------------------------------------
        if "spotify" in low:
            # Padrão 1: "toque no spotify bohemian rhapsody"
            m_spot_play = re.search(
                r"(?:toque|tocar|coloque|colocar|ouvir|ouça|reproduza|reproduzir|pesquise|pesquisar|procure|procurar|busca|buscar)\s+(?:no|pelo)\s+spotify\s+(?:por|a música|a musica)?\s*(.+)",
                low,
            )
            if not m_spot_play:
                # Padrão 2: "toque bohemian rhapsody no spotify"
                m_spot_play = re.search(
                    r"(?:toque|tocar|coloque|colocar|ouvir|ouça|reproduza|reproduzir|pesquise|pesquisar|procure|procurar|busca|buscar)\s+(.+?)\s+(?:no|pelo)\s+spotify\b",
                    low,
                )
            if not m_spot_play:
                # Padrão 3: "spotify toque bohemian rhapsody"
                m_spot_play = re.search(r"^spotify\s+(?:toque|tocar|coloque|toca|busque|buscar)\s+(.+)$", low)

            if m_spot_play:
                track = m_spot_play.group(1).strip()
                # Remove artigos e qualificadores comuns
                track = re.sub(r"^(?:a música|a musica|o artista|a banda|a faixa|a playlist|playlist|álbum|album)\s+", "", track).strip()
                if track:
                    action = SpotifyIntegration.play_search(track)
                    return ActionPlan(
                        thought=f"Buscando e reproduzindo '{track}' no Spotify.",
                        actions=[action],
                    )

        # -------------------------------------------------------------
        # 2. GMAIL
        # -------------------------------------------------------------
        if "gmail" in low:
            # Enviar / Escrever email
            if any(w in low for w in ["escreva", "escrever", "enviar", "envie", "redigir", "redija", "novo email", "mandar email"]):
                m_to = re.search(r"\bpara\s+([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7})\b", prompt)
                to_addr = m_to.group(1) if m_to else ""
                m_su = re.search(r"(?:com assunto|assunto)\s+[\"']?([^\"'\n]+)[\"']?", prompt, re.I)
                subject = m_su.group(1).strip() if m_su else ""
                action = GmailIntegration.compose(to=to_addr, subject=subject)
                dest_str = f" para {to_addr}" if to_addr else ""
                return ActionPlan(
                    thought=f"Abrindo novo email no Gmail{dest_str}.",
                    actions=[action],
                )

            # Emails não lidos
            if any(w in low for w in ["não lido", "nao lido", "não lidos", "nao lidos"]):
                action = GmailIntegration.open_view("unread")
                return ActionPlan(
                    thought="Abrindo emails não lidos no Gmail.",
                    actions=[action],
                )

            # Lixeira / Rascunhos / Enviados / Com estrela
            for v_key in ["lixeira", "rascunhos", "enviados", "starred", "spam"]:
                if v_key in low:
                    action = GmailIntegration.open_view(v_key)
                    return ActionPlan(
                        thought=f"Abrindo seção {v_key} no Gmail.",
                        actions=[action],
                    )

            # Busca no Gmail: "pesquise no gmail por faturas", "procure no gmail boletos"
            m_gsearch = re.search(r"(?:pesquise|pesquisar|procure|procurar|busque|buscar|encontre|encontrar)\s+(?:no\s+gmail\s+(?:por|sobre)?|emails?\s+(?:de|sobre|com)?\s*)\s*(.+)", low)
            if m_gsearch:
                gq = m_gsearch.group(1).replace("no gmail", "").strip()
                if gq:
                    action = GmailIntegration.search(gq)
                    return ActionPlan(
                        thought=f"Buscando por '{gq}' no Gmail.",
                        actions=[action],
                    )

            # Acesso geral ao Gmail
            if any(w in low for w in ["abra o gmail", "abrir gmail", "abrir o gmail", "caixa de entrada"]):
                action = GmailIntegration.open_view("inbox")
                return ActionPlan(
                    thought="Abrindo a Caixa de Entrada do Gmail.",
                    actions=[action],
                )

        # -------------------------------------------------------------
        # 3. GOOGLE DRIVE / DOCS / SHEETS
        # -------------------------------------------------------------
        if any(w in low for w in ["google docs", "google doc", "novo documento no docs", "criar doc no drive"]):
            action = GoogleDriveIntegration.create_document("doc")
            return ActionPlan(
                thought="Criando novo documento no Google Docs.",
                actions=[action],
            )
        if any(w in low for w in ["google sheets", "google sheet", "nova planilha", "criar planilha"]):
            action = GoogleDriveIntegration.create_document("sheet")
            return ActionPlan(
                thought="Criando nova planilha no Google Sheets.",
                actions=[action],
            )
        if any(w in low for w in ["google slides", "google slide", "nova apresentacao", "nova apresentação"]):
            action = GoogleDriveIntegration.create_document("slide")
            return ActionPlan(
                thought="Criando nova apresentação no Google Slides.",
                actions=[action],
            )

        if "drive" in low:
            # Recentes
            if any(w in low for w in ["recente", "recentes"]):
                action = GoogleDriveIntegration.open_view("recent")
                return ActionPlan(
                    thought="Abrindo arquivos recentes no Google Drive.",
                    actions=[action],
                )
            # Compartilhados
            if any(w in low for w in ["compartilhado", "compartilhados"]):
                action = GoogleDriveIntegration.open_view("shared")
                return ActionPlan(
                    thought="Abrindo arquivos compartilhados no Google Drive.",
                    actions=[action],
                )
            # Lixeira do Drive
            if "lixeira" in low:
                action = GoogleDriveIntegration.open_view("trash")
                return ActionPlan(
                    thought="Abrindo a lixeira do Google Drive.",
                    actions=[action],
                )
            # Busca no Drive: "procure no drive por notas", "pesquise no google drive orçamento"
            m_dsearch = re.search(r"(?:pesquise|pesquisar|procure|procurar|busque|buscar|encontre|encontrar)\s+(?:no\s+(?:google\s+)?drive\s+(?:por|sobre)?|arquivos?\s+(?:de|sobre)?\s*)\s*(.+)", low)
            if m_dsearch:
                dq = m_dsearch.group(1).replace("no drive", "").replace("no google drive", "").strip()
                if dq:
                    action = GoogleDriveIntegration.search(dq)
                    return ActionPlan(
                        thought=f"Pesquisando por '{dq}' no Google Drive.",
                        actions=[action],
                    )
            # Acesso ao Drive
            if any(w in low for w in ["abra o drive", "abrir drive", "abra o google drive", "abrir o google drive"]):
                action = GoogleDriveIntegration.open_view("my-drive")
                return ActionPlan(
                    thought="Abrindo Meu Drive no Google Drive.",
                    actions=[action],
                )

        # -------------------------------------------------------------
        # 4. YOUTUBE
        # -------------------------------------------------------------
        if "youtube" in low:
            # Padrão 1: "procure no youtube por X", "toque no youtube X"
            m_yt = re.search(
                r"(?:pesquise|pesquisar|procure|procurar|busque|buscar|toque|tocar|ouvir|ouça|veja|assistir|assista)\s+(?:no\s+youtube|pelo\s+youtube)\s+(?:por|sobre)?\s*(.+)",
                low,
            )
            if not m_yt:
                # Padrão 2: "procure X no youtube"
                m_yt = re.search(
                    r"(?:pesquise|pesquisar|procure|procurar|busque|buscar|toque|tocar|ouvir|ouça|veja|assistir|assista)\s+(.+?)\s+(?:no\s+youtube|pelo\s+youtube)\b",
                    low,
                )
            if not m_yt:
                # Padrão 3: "no youtube procure X"
                m_yt = re.search(r"(?:no\s+youtube|pelo\s+youtube)\s+(?:pesquise|procure|busque|toque)\s+(.+)", low)

            if m_yt:
                yq = m_yt.group(1).strip()
                if yq:
                    action = YouTubeIntegration.search(yq)
                    return ActionPlan(
                        thought=f"Buscando '{yq}' no YouTube.",
                        actions=[action],
                    )

        # -------------------------------------------------------------
        # 5. GOOGLE MAPS
        # -------------------------------------------------------------
        if any(w in low for w in ["google maps", "no maps", "pelo maps"]):
            m_maps = re.search(
                r"(?:procure|pesquise|busque|encontre|onde fica|como ir para)\s+(?:no\s+(?:google\s+)?maps|pelo\s+(?:google\s+)?maps)\s+(?:por|sobre)?\s*(.+)",
                low,
            )
            if not m_maps:
                m_maps = re.search(
                    r"(?:procure|pesquise|busque|encontre|onde fica|como ir para)\s+(.+?)\s+(?:no\s+(?:google\s+)?maps|pelo\s+(?:google\s+)?maps)\b",
                    low,
                )
            if m_maps:
                mq = m_maps.group(1).strip()
                action = GoogleMapsIntegration.search(mq)
                return ActionPlan(
                    thought=f"Buscando '{mq}' no Google Maps.",
                    actions=[action],
                )

        return None
