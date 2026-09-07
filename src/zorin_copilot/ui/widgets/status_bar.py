# Decisão de design: barra de status inferior (estilo Warp) com telemetria de sistema
# em tempo real — provedor/modelo, consumo de tokens da sessão, carga do sistema, RAM
# disponível e estatísticas do indexador RAG local. Os tokens são atualizados de forma
# pontual pelo app após cada resposta; o restante (carga/RAM/RAG) num tick periódico
# de 5s, já que variam independentemente da conversa.

"""Barra de status inferior do Zorin Copilot (estilo Warp)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GLib  # noqa: E402

from ...core.usage import format_tokens  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow

#: Ícones garantidos em qualquer tema Adwaita (verificados no ambiente de teste).
_ICON_MODEL = "network-transmit-receive-symbolic"
_ICON_TOKENS = "view-grid-symbolic"
_ICON_LOAD = "system-run-symbolic"
_ICON_RAM = "drive-harddisk-symbolic"
_ICON_RAG = "folder-documents-symbolic"

_REFRESH_INTERVAL_SECONDS = 5


def _read_mem_available_gb() -> float | None:
    """Lê ``MemAvailable`` de ``/proc/meminfo`` (kB) e devolve em GB, ou ``None``."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / (1024 * 1024)
    except OSError:
        return None
    return None


def _format_gb(value: float) -> str:
    # pt-BR: ponto decimal vira vírgula.
    return f"{value:.1f}".replace(".", ",")


class StatusBarWidget:
    """Barra de status inferior com telemetria de sistema e consumo de tokens.

    Recebe a janela como contexto (``ctx``) para ler configuração, o tracker de
    tokens do engine e as estatísticas do indexador RAG.
    """

    def __init__(self, ctx: "CopilotWindow"):
        self.ctx = ctx

        self.container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.container.add_css_class("status-bar")

        self.model_lbl = Gtk.Label()
        self.model_lbl.add_css_class("status-text")
        self.model_item = self._make_item(_ICON_MODEL, self.model_lbl)

        self.tokens_lbl = Gtk.Label()
        self.tokens_lbl.add_css_class("status-text")
        self.tokens_item = self._make_item(_ICON_TOKENS, self.tokens_lbl)

        self.load_lbl = Gtk.Label()
        self.load_lbl.add_css_class("status-text")
        self.load_item = self._make_item(_ICON_LOAD, self.load_lbl)

        self.ram_lbl = Gtk.Label()
        self.ram_lbl.add_css_class("status-text")
        self.ram_item = self._make_item(_ICON_RAM, self.ram_lbl)

        self.rag_lbl = Gtk.Label()
        self.rag_lbl.add_css_class("status-text")
        self.rag_item = self._make_item(_ICON_RAG, self.rag_lbl)

        # Monta os itens separados por divisórias verticais sutis.
        for first, item in enumerate(
            [self.model_item, self.tokens_item, self.load_item, self.ram_item, self.rag_item]
        ):
            if first:
                sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
                sep.set_margin_top(4)
                sep.set_margin_bottom(4)
                self.container.append(sep)
            self.container.append(item)

        self.refresh_all()
        self._disposed = False
        # O tick periódico se autocancela quando a janela é destruída, evitando
        # vazar o timer (e, com ele, a própria janela) após o fechamento.
        self.ctx.connect("destroy", self._on_dispose)
        GLib.timeout_add_seconds(_REFRESH_INTERVAL_SECONDS, self._on_tick)

    def _on_dispose(self, *_args) -> None:
        self._disposed = True

    # ------------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------------
    def _make_item(self, icon_name: str, label: Gtk.Label) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("status-item")
        img = Gtk.Image.new_from_icon_name(icon_name)
        img.set_pixel_size(14)
        img.add_css_class("status-icon")
        box.append(img)
        box.append(label)
        return box

    # ------------------------------------------------------------------
    # Atualização
    # ------------------------------------------------------------------
    def refresh_all(self) -> None:
        self.refresh_model()
        self.refresh_tokens()
        self.refresh_load()
        self.refresh_ram()
        self.refresh_rag()

    def _on_tick(self) -> bool:
        # Tick periódico: telemetria de sistema (carga/RAM/RAG) e modelo ativo.
        # Tokens são atualizados de forma pontual pelo app após cada resposta.
        if self._disposed:
            return GLib.SOURCE_REMOVE
        self.refresh_load()
        self.refresh_ram()
        self.refresh_rag()
        self.refresh_model()
        return GLib.SOURCE_CONTINUE

    def refresh_model(self) -> None:
        config = self.ctx.config
        if config.is_configured():
            model = {
                "gemini": config.gemini_model,
                "ollama": config.ollama_model,
                "openai": config.openai_model,
            }.get(config.provider, "")
            self.model_lbl.set_text(model)
            self.model_item.set_tooltip_text(f"Provedor de IA ativo: {config.provider}")
        else:
            self.model_lbl.set_text("sem IA")
            self.model_item.set_tooltip_text("Nenhum provedor de IA configurado")

    def refresh_tokens(self) -> None:
        tracker = self.ctx.engine.usage_tracker
        if tracker is None or tracker.session.total_tokens == 0:
            self.tokens_lbl.set_text("0 tokens")
            self.tokens_item.set_tooltip_text("Consumo de tokens desta conversa")
            return
        total = tracker.session.total_tokens
        reqs = tracker.requests
        self.tokens_lbl.set_text(f"{format_tokens(total)} tokens")
        last_model = tracker.last_model or ""
        suffix = f" · último: {last_model}" if last_model else ""
        self.tokens_item.set_tooltip_text(
            f"{total} tokens em {reqs} requisições{suffix}"
        )

    def refresh_load(self) -> None:
        try:
            load = os.getloadavg()
            ncpu = os.cpu_count() or 1
            norm = load[0] / ncpu
            self.load_lbl.set_text(f"Carga {load[0]:.2f}")
            self.load_item.set_tooltip_text(
                f"Carga do sistema (1/5/15 min): {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f} "
                f"— {ncpu} núcleos ({(norm * 100):.0f}% de uso)"
            )
        except OSError:
            self.load_lbl.set_text("Carga —")

    def refresh_ram(self) -> None:
        gb = _read_mem_available_gb()
        if gb is None:
            self.ram_lbl.set_text("RAM —")
            return
        self.ram_lbl.set_text(f"RAM {_format_gb(gb)} GB")
        self.ram_item.set_tooltip_text(f"Memória disponível: {gb:.2f} GB")

    def refresh_rag(self) -> None:
        try:
            stats = self.ctx.rag.get_stats()
            docs = stats.get("total_documents", 0)
            chunks = stats.get("total_chunks", 0)
            self.rag_lbl.set_text(f"{docs} docs")
            self.rag_item.set_tooltip_text(
                f"Indexador RAG local: {docs} documentos, {chunks} trechos indexados"
            )
        except Exception:
            self.rag_lbl.set_text("RAG —")
