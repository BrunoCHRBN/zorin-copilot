# Decisão de design: seletor de modelos e agentes implementado como Adw.Dialog moderno
# com busca em tempo real de modelos do Ollama/LM Studio em background thread,
# hot-swap instantâneo em memória (sem reiniciar) e preset de alta prioridade para o Dolphin 3.1 Uncensored.

"""Diálogo seletor de modelos e agentes de IA para o Zorin Copilot."""

from __future__ import annotations

import logging
import os
import re
import threading
import unicodedata
from typing import TYPE_CHECKING, Any

import requests

from ..gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from ...ai.providers import (
    DEFAULT_GEMINI_LIVE_MODEL,
    DEFAULT_GEMINI_LIVE_VOICE,
    GEMINI_LIVE_MODEL_CHOICES,
    GEMINI_LIVE_VOICE_CHOICES,
)
from ...core.config import CopilotConfig
from ...core.hardware_fit import describe_fit, detect_gpu, detect_ram_bytes, format_gb
from .command_palette import PaletteCommand, score_command

if TYPE_CHECKING:  # pragma: no cover
    from ..app import CopilotWindow

logger = logging.getLogger(__name__)

DOLPHIN_PRESET_MODEL = "dolphin3:latest"


def _label_texts(widget: Gtk.Widget) -> list[str]:
    """Texto de todos os `Gtk.Label` abaixo de `widget`.

    Usado para montar o texto de busca de uma linha: título, subtítulo e as
    tags do sufixo ("Visão", "Sem filtro") passam a ser todos pesquisáveis sem
    precisar registrar cada um à mão.
    """
    found: list[str] = []
    if isinstance(widget, Gtk.Label):
        text = widget.get_text()
        if text:
            found.append(text)
    child = widget.get_first_child()
    while child is not None:
        found.extend(_label_texts(child))
        child = child.get_next_sibling()
    return found


def _rows_of(group: Adw.PreferencesGroup) -> list[Adw.PreferencesRow]:
    """Linhas de conteúdo de um grupo, na ordem em que aparecem.

    **Não** são os filhos diretos. O Libadwaita empacota as linhas num
    `Gtk.ListBox` interno dentro de uma `Gtk.Box` (que também carrega o
    cabeçalho do grupo), então `group.get_first_child()` devolve um contêiner
    anônimo — foi exatamente esse o engano que fez o filtro não filtrar nada:
    a varredura achava um `Box` sem `get_title()` e concluía "não sei descrever,
    então mostro". A varredura tem de ser recursiva e ancorada no tipo da linha,
    não na posição dela na árvore.

    Ancora em `Adw.PreferencesRow` de propósito: `ActionRow`, `EntryRow`,
    `SwitchRow` e `ComboRow` derivam todos dele, então qualquer linha que o
    grupo possa renderizar entra na varredura sem precisar de registro manual —
    e uma linha removida do grupo (`group.remove(row)`) sai da varredura
    sozinha, sem lista paralela para manter em dia.
    """
    rows: list[Adw.PreferencesRow] = []

    def visit(widget: Gtk.Widget) -> None:
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Adw.PreferencesRow):
                rows.append(child)
            else:
                visit(child)
            child = child.get_next_sibling()

    visit(group)
    return rows


def _fold(text: str) -> str:
    """Minúsculas e sem acentos, **preservando a pontuação**.

    Não reusa `normalize_text` do wake word de propósito: lá a pontuação é
    ruído a remover ("OK, Copilot!" → "ok copilot"), aqui ela é conteúdo.
    Passar "qwen2.5vl:7b" por lá viraria "qwen2 5vl 7b" e o identificador
    deixaria de ser encontrável pelo que o usuário digita.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _use_case_tags(name: str, param_size: str) -> list[str]:
    """Tags de **caso de uso** de um modelo local, na ordem em que importam.

    O destaque vai para o que o modelo serve, não para o que ele não tem. Um
    "Sem filtro" em destaque diz pouco sobre a utilidade do modelo e soa como
    apelo; "Visão" ou "Rápido" responde à pergunta que o usuário tem.
    """
    low = name.lower()
    tags: list[str] = []

    if any(marker in low for marker in ("vl", "vision", "minicpm", "llava", "llama3.2-vision")):
        tags.append("Visão")

    # Faixa de tamanho: abaixo de 4B cabe com folga e responde rápido; a partir
    # de 13B já é um modelo que exige paciência e memória.
    size_match = re.search(r"([\d.]+)\s*b\b", param_size.lower())
    billions = float(size_match.group(1)) if size_match else 0.0
    if billions and billions <= 4.0:
        tags.append("Rápido")
    elif billions >= 13.0:
        tags.append("Grande")

    return tags


def _is_uncensored(name: str) -> bool:
    low = name.lower()
    return "dolphin" in low or "uncensored" in low or "sem-filtro" in low


def _format_size(size_bytes: int) -> str:
    """Formata bytes em GB/MB legível."""
    if not size_bytes or size_bytes <= 0:
        return ""
    gb = size_bytes / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.1f} GB"
    mb = size_bytes / (1024 ** 2)
    return f"{mb:.0f} MB"


class ModelSelectorDialog(Adw.Dialog):
    """Diálogo modal moderno para seleção rápida de modelo e agente de IA."""

    def __init__(self, ctx: "CopilotWindow"):
        super().__init__()
        self.ctx = ctx
        self.config = CopilotConfig.load()
        self._voice_preview_btns: dict[str, Gtk.Button] = {}
        # Linhas de estado que o filtro nunca esconde (ver `_apply_filter`).
        self._always_visible_rows: set[Gtk.Widget] = set()
        self.set_title("Selecionar Modelo / Agente")
        self.set_content_width(540)
        self.set_content_height(640)

        self._build_ui()
        self._load_models_async()
        self.connect("unmap", self._on_dialog_unmap)

    def _build_ui(self) -> None:
        self.toolbar_view = Adw.ToolbarView()
        self.set_child(self.toolbar_view)

        # HeaderBar com título e botão de atualização
        header = Adw.HeaderBar()
        title_widget = Adw.WindowTitle(
            title="Modelos e Agentes de IA",
            subtitle="Escolha o cérebro local ou em nuvem para o Copilot",
        )
        header.set_title_widget(title_widget)

        refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Atualizar modelos locais (Ollama e LM Studio)")
        refresh_btn.connect("clicked", lambda _: self._load_models_async())
        header.pack_end(refresh_btn)

        self.toolbar_view.add_top_bar(header)

        # Filtro numa barra fixa, não dentro da rolagem: com 7+ modelos o campo
        # precisa continuar alcançável depois de rolar, e era justamente a
        # rolagem longa o problema.
        self.toolbar_view.add_top_bar(self._build_filter_bar())

        # Conteúdo com barra de rolagem
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)
        content_box.set_margin_top(14)
        content_box.set_margin_bottom(20)

        # -----------------------------------------------------------------
        # 1. Provedores em Nuvem
        #
        # Vem primeiro de propósito. O subtítulo do diálogo promete "o cérebro
        # local ou em nuvem", mas a nuvem ficava abaixo dos três grupos locais:
        # numa janela de 640 px ela simplesmente não aparecia ao abrir, e a
        # promessa ficava por cumprir. Além disso o provedor padrão do app é a
        # nuvem, então esta é a seção do estado atual — o que o usuário veio
        # confirmar é o que ele vê primeiro.
        # -----------------------------------------------------------------
        self.cloud_group = Adw.PreferencesGroup(
            title="Agentes em Nuvem",
            description="Processamento em servidores de alta capacidade para tarefas complexas.",
        )
        self._build_cloud_rows()
        content_box.append(self.cloud_group)

        # -----------------------------------------------------------------
        # 2. Agente em Destaque: Dolphin 3.1
        # -----------------------------------------------------------------
        self.featured_group = Adw.PreferencesGroup(
            title="Agente em Destaque",
            description="Modelo local sem censura e com alta autonomia para tarefas no sistema.",
        )
        self.dolphin_row = self._create_dolphin_card()
        self.featured_group.add(self.dolphin_row)
        content_box.append(self.featured_group)

        # -----------------------------------------------------------------
        # 3. Grupo Ollama (Modelos Locais)
        # -----------------------------------------------------------------
        self.ollama_group = Adw.PreferencesGroup(
            title="Modelos Locais (Ollama)",
            description=f"Processamento 100% privado na GPU ({self.config.ollama_url}).",
        )
        self.ollama_loading_row = Adw.ActionRow(title="Buscando modelos locais...")
        self.ollama_spinner = Gtk.Spinner()
        self.ollama_spinner.set_spinning(True)
        self.ollama_loading_row.add_suffix(self.ollama_spinner)
        self.ollama_group.add(self.ollama_loading_row)
        self._always_visible_rows.add(self.ollama_loading_row)
        content_box.append(self.ollama_group)

        # -----------------------------------------------------------------
        # 4. Grupo LM Studio / Servidor OpenAI Local
        # -----------------------------------------------------------------
        self.lmstudio_group = Adw.PreferencesGroup(
            title="LM Studio / Servidor Local Compatível",
            description="Modelos carregados via servidor local (http://127.0.0.1:1234).",
        )
        content_box.append(self.lmstudio_group)

        # -----------------------------------------------------------------
        # 5. Grupo Modelos de Voz ao Vivo (Gemini 3.8 Live & Extended Thinking)
        # -----------------------------------------------------------------
        self.live_group = Adw.PreferencesGroup(
            title="Modelos de Voz ao Vivo (Gemini 3.8 Live)",
            description="Modelos oficiais de baixa latência e raciocínio estendido para voz multimodal.",
        )
        self._build_live_model_rows()
        content_box.append(self.live_group)

        # -----------------------------------------------------------------
        # 6. Grupo Voz do Assistente de IA
        # -----------------------------------------------------------------
        self.voices_group = Adw.PreferencesGroup(
            title="Voz do Assistente de IA",
            description="Escolha a personalidade e o timbre vocal para conversas de áudio em tempo real.",
        )
        self._build_voice_rows()
        content_box.append(self.voices_group)

        # -----------------------------------------------------------------
        # 7. Rodapé: Acesso a configurações completas
        # -----------------------------------------------------------------
        footer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer_box.set_halign(Gtk.Align.CENTER)
        footer_box.set_margin_top(8)

        settings_link_btn = Gtk.Button(label="Configurações Avançadas e Chaves de API...")
        settings_link_btn.add_css_class("flat")
        settings_link_btn.connect("clicked", self._on_open_full_settings)
        footer_box.append(settings_link_btn)
        content_box.append(footer_box)

        scrolled.set_child(content_box)
        self.toolbar_view.set_content(scrolled)

        # Normaliza o estado inicial: com a busca vazia nada é escondido, mas os
        # grupos sem linha (LM Studio offline) já saem ocultos por um caminho só,
        # em vez de cada construtor decidir por conta própria.
        self._apply_filter()

    def _build_filter_bar(self) -> Gtk.Widget:
        """Campo de busca dos modelos — a rolagem cresce, a busca não.

        Fica na barra de topo do `Adw.ToolbarView`, não dentro do
        `Gtk.ScrolledWindow`: assim a lista rola por baixo de um campo fixo, que
        é o padrão do Adw e o que `compare_dialog` já faz. Sem classe CSS
        própria — o `ToolbarView` já desenha a separação entre o que é fixo e o
        que rola, e uma classe sem regra só engana quem for ler depois.

        O casamento usa `score_command` do painel de comandos (`Ctrl+K`) para a
        parte difusa sobre o título, mas **não** o reusa inteiro: veja
        `_row_matches` para o motivo.
        """
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Filtrar modelos e vozes…")
        self.search_entry.set_hexpand(True)
        self.search_entry.set_margin_start(12)
        self.search_entry.set_margin_end(12)
        self.search_entry.set_margin_top(6)
        self.search_entry.set_margin_bottom(6)
        # `search-changed` já vem com o atraso do GTK, o que evita refiltrar a
        # cada tecla sem precisar de debounce próprio.
        self.search_entry.connect("search-changed", lambda _e: self._apply_filter())
        bar.append(self.search_entry)
        return bar

    def _filterable_groups(self) -> list[Adw.PreferencesGroup]:
        """Grupos varridos pelo filtro, na ordem em que aparecem."""
        return [
            self.cloud_group,
            self.featured_group,
            self.ollama_group,
            self.lmstudio_group,
            self.live_group,
            self.voices_group,
        ]

    def _apply_filter(self) -> None:
        """Mostra só as linhas que casam com a busca e esconde grupos vazios.

        Esconder o grupo vazio é o ponto: sem isso o filtro deixaria uma pilha
        de títulos de seção sem nada embaixo, que é pior que a rolagem original.
        """
        query = self.search_entry.get_text().strip().lower()

        for group in self._filterable_groups():
            rows = _rows_of(group)
            matching = 0
            has_status = False

            for row in rows:
                if row in self._always_visible_rows:
                    # Linhas de estado ("Buscando modelos…", "Ollama não
                    # encontrado") não são conteúdo a filtrar: se sumissem, o
                    # grupo ficaria vazio e o usuário perderia o diagnóstico.
                    #
                    # Só sustenta o grupo se estiver de fato à vista. A linha
                    # "Buscando modelos…" continua na árvore depois do render,
                    # apenas oculta; contá-la assim mesmo manteria o grupo
                    # aberto e vazio para qualquer busca — um cabeçalho de seção
                    # sem nada embaixo, exatamente o que o filtro devia evitar.
                    has_status = has_status or row.get_visible()
                    continue
                if self._row_matches(row, query):
                    row.set_visible(True)
                    matching += 1
                else:
                    row.set_visible(False)

            # Grupo sem linha alguma também cai aqui: `matching` fica 0 e
            # `has_status` falso, então ele se esconde. É o caso do LM Studio
            # quando o render marcou o grupo como visível antes de acrescentar
            # as linhas e nenhuma linha sobreviveu — um cabeçalho de seção
            # sozinho é pior que a rolagem original.
            group.set_visible(matching > 0 or has_status)

    @staticmethod
    def _row_matches(row: Adw.PreferencesRow, query: str) -> bool:
        """`True` se a linha casa com a busca (busca vazia casa com tudo).

        Duas camadas, e a segunda existe porque a primeira sozinha não serve
        aqui. `score_command` do painel de comandos termina numa
        correspondência por subsequência, que sobre um título curto é ótima
        ("nconv" acha "Nova conversa") mas sobre os subtítulos deste diálogo —
        frases inteiras em português — casa com quase tudo: com a busca "gemini"
        o card do Dolphin entrava, porque "gemini" é subsequência de "Agente
        local sem censura nem recusas corporativas". Um filtro que mostra cinco
        de seis seções não filtra.

        Então: difuso sobre o **título** (onde o identificador do modelo vive e
        onde o usuário digita abreviado) e substring simples sobre o **resto**
        (subtítulo, especificações, tags), sem acentos para "visao" achar
        "Visão". Um acerto fraco em prosa longa não basta para trazer a linha.

        Sem rede de proteção para "linha que não sei descrever": `_rows_of` só
        devolve `Adw.PreferencesRow`, que sempre tem título. A guarda
        `hasattr(row, "get_title")` que existia aqui devolvia `True` — e foi ela
        que transformou um erro de varredura em "o filtro não faz nada", sem
        sintoma nenhum. Melhor falhar alto do que mentir em silêncio.
        """
        if not query:
            return True

        if score_command(query, PaletteCommand(name="", title=row.get_title())) is not None:
            return True

        return _fold(query) in _fold(" ".join(_label_texts(row)))

    def _create_dolphin_card(self) -> Adw.ActionRow:
        """Cria o card destacado do Agente Dolphin 3.1.

        O título trazia um emoji (🐬) e a tag "Uncensored" vinha com a classe de
        destaque. Duas correções de §3.1: emoji em rótulo de UI viola a diretriz
        do projeto (ícone simbólico do Adwaita), e "sem filtro" é uma
        *característica*, não um argumento de venda — anunciá-la em destaque
        sugere vantagem onde só há uma escolha de treino. O destaque fica no que
        o modelo faz.
        """
        row = Adw.ActionRow()
        row.set_title("Dolphin 3.1 (Llama 3.1 8B)")
        row.set_subtitle(
            "Agente local sem censura nem recusas corporativas. "
            "Executa automações de desktop, comandos de terminal e análises com total autonomia."
        )
        row.set_activatable(True)

        # Ícone do card
        icon = Gtk.Image.new_from_icon_name("system-run-symbolic")
        icon.set_pixel_size(24)
        row.add_prefix(icon)

        # Badges
        badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        badge_box.set_valign(Gtk.Align.CENTER)

        # Caso de uso primeiro: é o que ajuda a escolher.
        tag_agent = Gtk.Label(label="Autônomo")
        tag_agent.add_css_class("caption")
        tag_agent.add_css_class("pill")
        badge_box.append(tag_agent)

        tag_local = Gtk.Label(label="Local GPU")
        tag_local.add_css_class("caption")
        tag_local.add_css_class("pill")
        badge_box.append(tag_local)

        # Característica, não vantagem: vem depois e sem destaque.
        tag_uncensored = Gtk.Label(label="Sem filtro")
        tag_uncensored.add_css_class("caption")
        tag_uncensored.add_css_class("dim-label")
        badge_box.append(tag_uncensored)

        is_active = (
            self.config.provider == "ollama"
            and "dolphin" in (self.config.ollama_model or "").lower()
        )
        if is_active:
            check_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            check_icon.set_tooltip_text("Agente Ativo")
            badge_box.append(check_icon)

        row.add_suffix(badge_box)
        row.connect(
            "activated",
            lambda _: self._select_model(
                provider="ollama",
                model_name=DOLPHIN_PRESET_MODEL,
                display_name="Dolphin 3.1 (Sem filtro)",
            ),
        )
        return row

    def _build_cloud_rows(self) -> None:
        """Cria os cards de provedores em nuvem (Gemini e WorkBuddy)."""
        # Google Gemini
        gemini_row = Adw.ActionRow()
        gemini_model = self.config.gemini_model or "gemini-3.6-flash"
        gemini_row.set_title(f"Google Gemini ({gemini_model})")
        gemini_row.set_subtitle("Multimodal em nuvem de alto desempenho e raciocínio avançado.")
        gemini_row.set_activatable(True)

        gemini_icon = Gtk.Image.new_from_icon_name("network-server-symbolic")
        gemini_icon.set_pixel_size(20)
        gemini_row.add_prefix(gemini_icon)

        gemini_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        gemini_box.set_valign(Gtk.Align.CENTER)
        gemini_tag = Gtk.Label(label="Nuvem")
        gemini_tag.add_css_class("caption")
        gemini_tag.add_css_class("pill")
        gemini_box.append(gemini_tag)

        if self.config.provider == "gemini":
            check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            gemini_box.append(check)
        gemini_row.add_suffix(gemini_box)

        gemini_row.connect(
            "activated",
            lambda _: self._select_model(
                provider="gemini",
                model_name=gemini_model,
                display_name=f"Gemini ({gemini_model})",
            ),
        )
        self.cloud_group.add(gemini_row)

        # WorkBuddy HY4
        workbuddy_model = getattr(self.config, "workbuddy_model", "hy4-preview") or "hy4-preview"
        wb_row = Adw.ActionRow()
        wb_row.set_title(f"WorkBuddy AI ({workbuddy_model})")
        wb_row.set_subtitle("Tencent Hunyuan MoE 770B com raciocínio profundo e visão.")
        wb_row.set_activatable(True)

        wb_icon = Gtk.Image.new_from_icon_name("preferences-system-network-symbolic")
        wb_icon.set_pixel_size(20)
        wb_row.add_prefix(wb_icon)

        wb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        wb_box.set_valign(Gtk.Align.CENTER)
        wb_tag = Gtk.Label(label="MoE 770B")
        wb_tag.add_css_class("caption")
        wb_tag.add_css_class("pill")
        wb_box.append(wb_tag)

        if self.config.provider == "workbuddy":
            check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            wb_box.append(check)
        wb_row.add_suffix(wb_box)

        wb_row.connect(
            "activated",
            lambda _: self._select_model(
                provider="workbuddy",
                model_name=workbuddy_model,
                display_name=f"WorkBuddy ({workbuddy_model})",
            ),
        )
        self.cloud_group.add(wb_row)

    def _build_live_model_rows(self) -> None:
        """Cria os cards de modelos de voz ao vivo (Gemini 3.8 Live e Extended Thinking)."""
        self._live_model_rows: list[Adw.ActionRow] = []
        self._live_model_checks: dict[str, Gtk.Widget] = {}

        current_live_model = getattr(self.config, "gemini_live_model", DEFAULT_GEMINI_LIVE_MODEL)

        for model_id, model_label in GEMINI_LIVE_MODEL_CHOICES:
            row = Adw.ActionRow()
            row.set_title(model_label.split("(")[0].strip())
            row.set_activatable(True)

            icon = Gtk.Image.new_from_icon_name("audio-speakers-symbolic")
            icon.set_pixel_size(20)
            row.add_prefix(icon)

            if "extended-thinking" in model_id:
                row.set_subtitle("Raciocínio contínuo em background, planejamento profundo e narração assíncrona de progresso.")
            elif "3.8-live" in model_id:
                row.set_subtitle("Ultra baixa latência, conversação contínua fluida e acionamento instantâneo de ferramentas.")
            else:
                row.set_subtitle("Modelo de voz ao vivo prévio.")

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_valign(Gtk.Align.CENTER)

            tag_live = Gtk.Label(label="Live Audio")
            tag_live.add_css_class("caption")
            tag_live.add_css_class("pill")
            box.append(tag_live)

            if "extended-thinking" in model_id:
                tag_think = Gtk.Label(label="Extended Thinking")
                tag_think.add_css_class("caption")
                tag_think.add_css_class("pill")
                tag_think.add_css_class("accent")
                box.append(tag_think)

            check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            check.set_visible(model_id == current_live_model)
            box.append(check)
            self._live_model_checks[model_id] = check

            row.add_suffix(box)

            target_id = model_id
            target_label = model_label
            row.connect("activated", lambda _, m=target_id, lbl=target_label: self._select_live_model(m, lbl))
            self.live_group.add(row)
            self._live_model_rows.append(row)

    def _select_live_model(self, model_id: str, display_name: str = "") -> None:
        """Aplica a troca do modelo de voz ao vivo e notifica o cliente ativo."""
        cfg = CopilotConfig.load()
        cfg.gemini_live_model = model_id
        cfg.save()
        self.config = cfg

        if hasattr(self.ctx, "live_client") and self.ctx.live_client:
            if hasattr(self.ctx.live_client, "set_model"):
                self.ctx.live_client.set_model(model_id)

        if hasattr(self.ctx, "_on_config_saved"):
            self.ctx._on_config_saved(cfg)

        self._refresh_live_model_checks()
        clean_name = display_name.split("(")[0].strip() if display_name else model_id
        if hasattr(self.ctx, "show_toast"):
            self.ctx.show_toast(f"Modelo Live alterado para {clean_name}")

    def _refresh_live_model_checks(self) -> None:
        """Atualiza a indicação visual de checkmark para o modelo Live ativo."""
        current = getattr(self.config, "gemini_live_model", DEFAULT_GEMINI_LIVE_MODEL)
        for m_id, check in self._live_model_checks.items():
            check.set_visible(m_id == current)

    def _build_voice_rows(self) -> None:
        """Cria os cards de seleção das 8 vozes oficiais do Gemini Live."""
        self._voice_rows: list[Adw.ActionRow] = []
        self._voice_checks: dict[str, Gtk.Widget] = {}

        current_voice = getattr(self.config, "gemini_live_voice", DEFAULT_GEMINI_LIVE_VOICE)

        for voice_id, voice_label in GEMINI_LIVE_VOICE_CHOICES:
            row = Adw.ActionRow()
            row.set_title(voice_id)
            row.set_activatable(True)

            desc = voice_label.split("(", 1)[1].rstrip(")") if "(" in voice_label else voice_label
            row.set_subtitle(desc)

            icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
            icon.set_pixel_size(18)
            row.add_prefix(icon)

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_valign(Gtk.Align.CENTER)

            first_attr = desc.split("·")[0].strip() if "·" in desc else "Voz"
            tag = Gtk.Label(label=first_attr)
            tag.add_css_class("caption")
            tag.add_css_class("pill")
            box.append(tag)

            # Botão de prévia auditiva da voz
            preview_btn = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
            preview_btn.add_css_class("flat")
            preview_btn.add_css_class("circular")
            preview_btn.set_valign(Gtk.Align.CENTER)
            preview_btn.set_tooltip_text(f"Ouvir demonstração da voz {voice_id}")
            self._voice_preview_btns[voice_id] = preview_btn
            target_v = voice_id
            preview_btn.connect("clicked", lambda _, v=target_v: self._toggle_voice_preview(v))
            box.append(preview_btn)

            check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
            check.set_visible(voice_id == current_voice)
            box.append(check)
            self._voice_checks[voice_id] = check

            row.add_suffix(box)

            target_voice = voice_id
            row.connect("activated", lambda _, v=target_voice, d=desc: self._select_live_voice(v, d))
            self.voices_group.add(row)
            self._voice_rows.append(row)

    def _toggle_voice_preview(self, voice_name: str) -> None:
        """Inicia ou interrompe a demonstração de áudio da voz solicitada."""
        from ...ai.voice_preview import VoicePreviewService
        service = VoicePreviewService.get_default()

        if service.is_playing(voice_name):
            service.stop()
            self._reset_voice_preview_icons()
            return

        service.stop()
        self._reset_voice_preview_icons()

        btn = self._voice_preview_btns.get(voice_name)
        if btn:
            btn.set_icon_name("media-playback-stop-symbolic")

        def _on_finished():
            self._reset_voice_preview_icons()

        service.play_voice(voice_name, on_finished=_on_finished)

    def _reset_voice_preview_icons(self) -> None:
        """Restaura o ícone de play em todos os botões de prévia."""
        for btn in self._voice_preview_btns.values():
            btn.set_icon_name("media-playback-start-symbolic")

    def _on_dialog_unmap(self, *_args) -> None:
        """Garante a parada do som quando o diálogo de modelos for fechado."""
        try:
            from ...ai.voice_preview import VoicePreviewService
            VoicePreviewService.get_default().stop()
        except Exception:
            pass
        self._reset_voice_preview_icons()

    def _select_live_voice(self, voice_name: str, display_desc: str = "") -> None:
        """Aplica a troca de voz do assistente e notifica o cliente ativo."""
        cfg = CopilotConfig.load()
        cfg.gemini_live_voice = voice_name
        cfg.save()
        self.config = cfg

        if hasattr(self.ctx, "live_client") and self.ctx.live_client:
            if hasattr(self.ctx.live_client, "set_voice"):
                self.ctx.live_client.set_voice(voice_name)

        if hasattr(self.ctx, "_on_config_saved"):
            self.ctx._on_config_saved(cfg)

        self._refresh_live_voice_checks()
        if hasattr(self.ctx, "show_toast"):
            self.ctx.show_toast(f"Voz alterada para {voice_name}")

    def _refresh_live_voice_checks(self) -> None:
        """Atualiza a indicação visual de checkmark para a voz ativa."""
        current = getattr(self.config, "gemini_live_voice", DEFAULT_GEMINI_LIVE_VOICE)
        for v_id, check in self._voice_checks.items():
            check.set_visible(v_id == current)

    def _load_models_async(self) -> None:
        """Dispara busca assíncrona dos modelos no Ollama e LM Studio."""
        if os.environ.get("ZORIN_TEST_MODE") == "1":
            # Sem busca real a linha de carregamento sai de cena — e quem decide
            # se o grupo do Ollama fica de pé é o `_apply_filter`, que roda no
            # fim do `_build_ui` e não vê esta mudança. Sem reaplicá-lo o grupo
            # ficaria aberto e vazio: um cabeçalho de seção sem nada embaixo.
            self.ollama_loading_row.set_visible(False)
            self.ollama_spinner.set_spinning(False)
            self._apply_filter()
            return

        self.ollama_loading_row.set_visible(True)
        self.ollama_spinner.set_spinning(True)

        def worker():
            ollama_models = []
            ollama_err = ""
            try:
                url = f"{self.config.ollama_url.rstrip('/')}/api/tags"
                resp = requests.get(url, timeout=2.0)
                if resp.status_code == 200:
                    data = resp.json()
                    ollama_models = data.get("models", [])
                else:
                    ollama_err = f"Status {resp.status_code}"
            except Exception as exc:
                ollama_err = str(exc)

            lmstudio_models = []
            try:
                resp_lm = requests.get("http://127.0.0.1:1234/v1/models", timeout=1.0)
                if resp_lm.status_code == 200:
                    data_lm = resp_lm.json()
                    lmstudio_models = data_lm.get("data", [])
            except Exception:
                pass

            GLib.idle_add(self._render_models_ui, ollama_models, ollama_err, lmstudio_models)

        threading.Thread(target=worker, daemon=True).start()

    def _render_models_ui(
        self,
        ollama_models: list[dict[str, Any]],
        ollama_err: str,
        lmstudio_models: list[dict[str, Any]],
    ) -> bool:
        """Renderiza na thread principal da interface os modelos detectados."""
        self.ollama_loading_row.set_visible(False)
        self.ollama_spinner.set_spinning(False)

        # Sondado uma vez para todas as linhas: `detect_gpu` já tem cache, mas
        # `detect_ram_bytes` relê /proc/meminfo — não faz sentido pagar isso por
        # modelo.
        gpu = detect_gpu()
        ram_bytes = detect_ram_bytes()

        # Remove linhas dinâmicas antigas de Ollama (se houver)
        if hasattr(self, "_dynamic_ollama_rows"):
            for row in self._dynamic_ollama_rows:
                self.ollama_group.remove(row)
        self._dynamic_ollama_rows: list[Adw.ActionRow] = []

        if ollama_err and not ollama_models:
            err_row = Adw.ActionRow()
            err_row.set_title("Ollama não encontrado")
            err_row.set_subtitle(f"Não foi possível conectar ao Ollama em {self.config.ollama_url}. Execute 'ollama serve'.")
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            err_row.add_prefix(icon)
            self.ollama_group.add(err_row)
            self._dynamic_ollama_rows.append(err_row)
            self._always_visible_rows.add(err_row)
        else:
            for item in ollama_models:
                name = item.get("name", "")
                if not name:
                    continue

                details = item.get("details", {})
                size_bytes = item.get("size", 0)
                size_str = _format_size(size_bytes)
                param_size = details.get("parameter_size", "")
                quant = details.get("quantization_level", "")

                row = Adw.ActionRow()
                row.set_title(name)
                row.set_activatable(True)

                verdict = describe_fit(size_bytes, gpu=gpu, ram_bytes=ram_bytes)

                # Números com números: o requisito de memória entra na mesma
                # linha das outras grandezas, em vez de virar prosa repetida.
                sub_parts = []
                if param_size:
                    sub_parts.append(param_size)
                if quant:
                    sub_parts.append(quant)
                if size_str:
                    sub_parts.append(size_str)
                if verdict.required_bytes:
                    sub_parts.append(f"~{format_gb(verdict.required_bytes)} GB em uso")
                specs = " • ".join(sub_parts) if sub_parts else "Modelo local Ollama"

                # A pergunta que o usuário realmente tem — "roda na minha GPU?" —
                # ganha linha própria, com espaço para ser lida.
                row.set_subtitle(f"{specs}\n{verdict.line}")
                row.set_subtitle_lines(2)

                # Ícone: o diferenciador primário (§0.3). `security-low-symbolic`
                # ficou de fora de propósito — lê-se como "inseguro", que é um
                # juízo, não uma categoria.
                is_uncensored = _is_uncensored(name)
                is_vision = "Visão" in _use_case_tags(name, param_size)
                if is_vision:
                    icon_name = "camera-photo-symbolic"
                elif is_uncensored:
                    icon_name = "system-run-symbolic"
                else:
                    icon_name = "computer-symbolic"

                icon = Gtk.Image.new_from_icon_name(icon_name)
                icon.set_pixel_size(20)
                row.add_prefix(icon)

                # Badges no sufixo — caso de uso primeiro, característica depois.
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                box.set_valign(Gtk.Align.CENTER)

                for tag_text in _use_case_tags(name, param_size):
                    tag = Gtk.Label(label=tag_text)
                    tag.add_css_class("caption")
                    tag.add_css_class("pill")
                    box.append(tag)

                if is_uncensored:
                    u_tag = Gtk.Label(label="Sem filtro")
                    u_tag.add_css_class("caption")
                    u_tag.add_css_class("dim-label")
                    box.append(u_tag)

                is_active = (
                    self.config.provider == "ollama"
                    and self.config.ollama_model.strip() == name.strip()
                )
                if is_active:
                    check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
                    box.append(check)

                row.add_suffix(box)

                # Evento de clique
                m_name = name
                row.connect(
                    "activated",
                    lambda _, m=m_name: self._select_model(
                        provider="ollama",
                        model_name=m,
                        display_name=f"Ollama ({m})",
                    ),
                )
                self.ollama_group.add(row)
                self._dynamic_ollama_rows.append(row)

        # LM Studio
        #
        # A visibilidade do grupo **não** é decidida aqui: quem decide é o
        # `_apply_filter()` no fim deste método, a partir das linhas que
        # existirem de fato. Marcar o grupo como visível antes de acrescentar as
        # linhas era uma segunda fonte de verdade — e quando nenhum item trazia
        # `id` a marcação sobrevivia e deixava um cabeçalho de seção sozinho na
        # tela, com o filtro já tendo dito que o grupo estava vazio.
        if hasattr(self, "_dynamic_lm_rows"):
            for row in self._dynamic_lm_rows:
                self.lmstudio_group.remove(row)
        self._dynamic_lm_rows = []

        if lmstudio_models:
            for item in lmstudio_models:
                m_id = item.get("id", "")
                if not m_id:
                    continue
                row = Adw.ActionRow()
                row.set_title(m_id)

                # O servidor OpenAI-compatível não informa o tamanho do modelo,
                # então não há veredito de adequação a dar aqui. Dizer "roda na
                # GPU" sem dado seria inventar — fica só a origem.
                row.set_subtitle("Servidor local LM Studio (API OpenAI compatível)")
                row.set_activatable(True)

                is_uncensored = "uncensored" in m_id.lower()
                icon_name = "system-run-symbolic" if is_uncensored else "computer-symbolic"
                icon = Gtk.Image.new_from_icon_name(icon_name)
                icon.set_pixel_size(20)
                row.add_prefix(icon)

                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                box.set_valign(Gtk.Align.CENTER)
                tag = Gtk.Label(label="LM Studio")
                tag.add_css_class("caption")
                tag.add_css_class("pill")
                box.append(tag)

                if is_uncensored:
                    u_tag = Gtk.Label(label="Sem filtro")
                    u_tag.add_css_class("caption")
                    u_tag.add_css_class("dim-label")
                    box.append(u_tag)

                is_active = (
                    self.config.provider == "openai"
                    and self.config.openai_url.startswith("http://127.0.0.1:1234")
                    and self.config.openai_model == m_id
                )
                if is_active:
                    check = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
                    box.append(check)
                row.add_suffix(box)

                target_id = m_id
                row.connect(
                    "activated",
                    lambda _, m=target_id: self._select_model(
                        provider="openai",
                        model_name=m,
                        display_name=f"LM Studio ({m})",
                        custom_url="http://127.0.0.1:1234/v1",
                    ),
                )
                self.lmstudio_group.add(row)
                self._dynamic_lm_rows.append(row)

        # Reaplica o filtro: as linhas foram recriadas agora, e sem isto uma
        # busca já digitada seria ignorada pelas linhas novas.
        self._apply_filter()

        return GLib.SOURCE_REMOVE

    def _select_model(
        self,
        provider: str,
        model_name: str,
        display_name: str = "",
        custom_url: str = "",
    ) -> None:
        """Aplica a troca de modelo/agente imediatamente e salva a configuração."""
        cfg = CopilotConfig.load()
        cfg.provider = provider

        if provider == "ollama":
            cfg.ollama_model = model_name
        elif provider == "gemini":
            cfg.gemini_model = model_name
        elif provider == "workbuddy":
            cfg.workbuddy_model = model_name
        elif provider == "openai":
            cfg.openai_model = model_name
            if custom_url:
                cfg.openai_url = custom_url

        cfg.save()
        self.config = cfg

        if hasattr(self.ctx, "_on_config_saved"):
            self.ctx._on_config_saved(cfg)

        self.close()

        name = display_name or model_name
        if hasattr(self.ctx, "show_toast"):
            self.ctx.show_toast(f"Modelo alterado para {name}")

    def _on_open_full_settings(self, _btn: Gtk.Button) -> None:
        """Fecha o seletor rápido e abre o diálogo completo de preferências."""
        self.close()
        if hasattr(self.ctx, "_open_settings"):
            self.ctx._open_settings()
