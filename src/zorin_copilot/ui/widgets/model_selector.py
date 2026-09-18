# Decisão de design: seletor de modelos e agentes implementado como Adw.Dialog moderno
# com busca em tempo real de modelos do Ollama/LM Studio em background thread,
# hot-swap instantâneo em memória (sem reiniciar) e preset de alta prioridade para o Dolphin 3.1 Uncensored.

"""Diálogo seletor de modelos e agentes de IA para o Zorin Copilot."""

from __future__ import annotations

import logging
import os
import threading
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

if TYPE_CHECKING:  # pragma: no cover
    from ..app import CopilotWindow

logger = logging.getLogger(__name__)

DOLPHIN_PRESET_MODEL = "dolphin3:latest"


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
        # 1. Agente em Destaque: Dolphin 3.1 (Uncensored)
        # -----------------------------------------------------------------
        self.featured_group = Adw.PreferencesGroup(
            title="Agente em Destaque",
            description="Modelo local sem censura e com alta autonomia para tarefas no sistema.",
        )
        self.dolphin_row = self._create_dolphin_card()
        self.featured_group.add(self.dolphin_row)
        content_box.append(self.featured_group)

        # -----------------------------------------------------------------
        # 2. Grupo Ollama (Modelos Locais)
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
        content_box.append(self.ollama_group)

        # -----------------------------------------------------------------
        # 3. Grupo LM Studio / Servidor OpenAI Local
        # -----------------------------------------------------------------
        self.lmstudio_group = Adw.PreferencesGroup(
            title="LM Studio / Servidor Local Compatível",
            description="Modelos carregados via servidor local (http://127.0.0.1:1234).",
        )
        self.lmstudio_group.set_visible(False)
        content_box.append(self.lmstudio_group)

        # -----------------------------------------------------------------
        # 4. Grupo Provedores em Nuvem
        # -----------------------------------------------------------------
        self.cloud_group = Adw.PreferencesGroup(
            title="Agentes em Nuvem",
            description="Processamento em servidores de alta capacidade para tarefas complexas.",
        )
        self._build_cloud_rows()
        content_box.append(self.cloud_group)

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

    def _create_dolphin_card(self) -> Adw.ActionRow:
        """Cria o card destacado do Agente Dolphin 3.1 Uncensored."""
        row = Adw.ActionRow()
        row.set_title("🐬 Dolphin 3.1 (Llama 3.1 8B)")
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

        tag_uncensored = Gtk.Label(label="Uncensored")
        tag_uncensored.add_css_class("caption")
        tag_uncensored.add_css_class("pill")
        tag_uncensored.add_css_class("accent")
        badge_box.append(tag_uncensored)

        tag_local = Gtk.Label(label="Local GPU")
        tag_local.add_css_class("caption")
        tag_local.add_css_class("pill")
        badge_box.append(tag_local)

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
                display_name="Dolphin 3.1 (Uncensored)",
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
            self.ollama_loading_row.set_visible(False)
            self.ollama_spinner.set_spinning(False)
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

                # Subtítulo explicativo
                sub_parts = []
                if param_size:
                    sub_parts.append(param_size)
                if quant:
                    sub_parts.append(quant)
                if size_str:
                    sub_parts.append(size_str)
                row.set_subtitle(" • ".join(sub_parts) if sub_parts else "Modelo local Ollama")

                # Ícone
                icon_name = "computer-symbolic"
                is_uncensored = "dolphin" in name.lower() or "uncensored" in name.lower()
                is_vision = any(v in name.lower() for v in ("vl", "vision", "minicpm", "llava"))
                if is_uncensored:
                    icon_name = "security-low-symbolic"
                elif is_vision:
                    icon_name = "camera-photo-symbolic"

                icon = Gtk.Image.new_from_icon_name(icon_name)
                icon.set_pixel_size(20)
                row.add_prefix(icon)

                # Badges no sufixo
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                box.set_valign(Gtk.Align.CENTER)

                if is_uncensored:
                    u_tag = Gtk.Label(label="Uncensored")
                    u_tag.add_css_class("caption")
                    u_tag.add_css_class("pill")
                    box.append(u_tag)

                if is_vision:
                    v_tag = Gtk.Label(label="Visão")
                    v_tag.add_css_class("caption")
                    v_tag.add_css_class("pill")
                    box.append(v_tag)

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
        if hasattr(self, "_dynamic_lm_rows"):
            for row in self._dynamic_lm_rows:
                self.lmstudio_group.remove(row)
        self._dynamic_lm_rows = []

        if lmstudio_models:
            self.lmstudio_group.set_visible(True)
            for item in lmstudio_models:
                m_id = item.get("id", "")
                if not m_id:
                    continue
                row = Adw.ActionRow()
                row.set_title(m_id)
                row.set_subtitle("Servidor local LM Studio (API OpenAI compatível)")
                row.set_activatable(True)

                icon = Gtk.Image.new_from_icon_name("computer-symbolic")
                icon.set_pixel_size(20)
                row.add_prefix(icon)

                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                box.set_valign(Gtk.Align.CENTER)
                tag = Gtk.Label(label="LM Studio")
                tag.add_css_class("caption")
                tag.add_css_class("pill")
                box.append(tag)

                if "uncensored" in m_id.lower():
                    u_tag = Gtk.Label(label="Uncensored")
                    u_tag.add_css_class("caption")
                    u_tag.add_css_class("pill")
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
        else:
            self.lmstudio_group.set_visible(False)

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
