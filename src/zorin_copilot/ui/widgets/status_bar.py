# Decisão de design: barra de status inferior (estilo Warp) com telemetria de sistema
# em tempo real — provedor/modelo, consumo de tokens da sessão, carga do sistema, RAM
# disponível e estatísticas do indexador RAG local. Os tokens são atualizados de forma
# pontual pelo app após cada resposta; o restante (carga/RAM/RAG) num tick periódico
# de 5s, já que variam independentemente da conversa.

"""Barra de status inferior do Zorin Copilot (estilo Warp)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ..gi_versions import require_gtk4  # noqa: E402
require_gtk4()
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
_ICON_NET = "network-workgroup-symbolic"

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
        self.tokens_lbl.add_css_class("tabular-nums")
        self.tokens_lbl.add_css_class("status-bar-tokens")
        self.tokens_item = self._make_item(_ICON_TOKENS, self.tokens_lbl)

        self.load_lbl = Gtk.Label()
        self.load_lbl.add_css_class("status-text")
        self.load_lbl.add_css_class("tabular-nums")
        self.load_item = self._make_item(_ICON_LOAD, self.load_lbl)

        self.ram_lbl = Gtk.Label()
        self.ram_lbl.add_css_class("status-text")
        self.ram_lbl.add_css_class("tabular-nums")
        self.ram_item = self._make_item(_ICON_RAM, self.ram_lbl)

        self.rag_lbl = Gtk.Label()
        self.rag_lbl.add_css_class("status-text")
        self.rag_lbl.add_css_class("tabular-nums")
        self.rag_item = self._make_item(_ICON_RAG, self.rag_lbl)

        self.net_lbl = Gtk.Label(label="Rede —")
        self.net_lbl.add_css_class("status-text")
        self.net_lbl.add_css_class("tabular-nums")
        self.net_item = self._make_item(_ICON_NET, self.net_lbl)
        self.net_item.set_tooltip_text("Infraestrutura de Rede local (clique para ver detalhes)")

        net_click = Gtk.GestureClick()
        net_click.connect(
            "pressed",
            lambda *_: getattr(self.ctx, "_open_network_inspector", lambda: None)(),
        )
        self.net_item.add_controller(net_click)

        # Três regiões com significados distintos, em vez de seis itens de peso
        # idêntico enfileirados. Comando (o que a IA é) à esquerda; o que ela
        # indexa no centro; saúde do SO à direita. O agrupamento é o que torna a
        # barra escaneável — sem ele, "0 tokens" e "Carga 0.87" parecem a mesma
        # coisa.
        self.region_model = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.region_model.add_css_class("status-region")
        self.region_model.append(self.model_item)
        self.region_model.append(self.tokens_item)

        self.region_context = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.region_context.add_css_class("status-region")
        self.region_context.append(self.rag_item)

        self.region_system = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.region_system.add_css_class("status-region")
        # A cerca espacial (monitor/janela onde as automações agem) é estado do
        # ambiente e entra aqui — não no topo, onde espremia o título.
        self._fence_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.region_system.append(self._fence_slot)
        self.region_system.append(self.load_item)
        self.region_system.append(self.ram_item)
        self.region_system.append(self.net_item)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.container.append(self.region_model)
        self.container.append(self._region_divider())
        self.container.append(self.region_context)
        self.container.append(spacer)
        self.container.append(self.region_system)

        self.refresh_all()
        self._disposed = False
        # O tick só existe enquanto a janela está visível. Antes ele era criado
        # aqui e só "se cancelava" devolvendo SOURCE_REMOVE na passada
        # seguinte — ou seja, continuava vivo por até um intervalo inteiro
        # depois do fechamento, e para sempre se a janela nunca fosse destruída
        # (o `destroy` do GTK4 não emite o sinal para janela nunca apresentada).
        self._timer_id = 0
        self.ctx.connect("map", self._on_map)
        self.ctx.connect("unmap", self._on_unmap)
        self.ctx.connect("destroy", self._on_dispose)

    def _start_tick(self) -> None:
        if self._timer_id or self._disposed:
            return
        # G_PRIORITY_LOW de propósito: telemetria de fundo não deve competir
        # com a entrega de resposta da IA, que chega por GLib.idle_add.
        # Ver "Inanição de prioridade" em docs/ENDEAVOUROS_PORT.md.
        self._timer_id = GLib.timeout_add_seconds(
            _REFRESH_INTERVAL_SECONDS, self._on_tick, priority=GLib.PRIORITY_LOW
        )

    def _stop_tick(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

    def _on_map(self, *_args) -> None:
        self._start_tick()

    def _on_unmap(self, *_args) -> None:
        self._stop_tick()

    def _on_dispose(self, *_args) -> None:
        self._disposed = True
        self._stop_tick()

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

    def _region_divider(self) -> Gtk.Separator:
        """Divisória vertical sutil entre as regiões de significado distinto."""
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.set_margin_top(4)
        sep.set_margin_bottom(4)
        return sep

    def set_fence_widget(self, widget: Gtk.Widget) -> None:
        """Hospeda o botão da cerca espacial (construído pelo header).

        O widget é criado em `HeaderBarWidget` porque o popover e a lógica de
        cerca moram lá; a barra de status só o posiciona junto das demais
        leituras de ambiente.
        """
        while child := self._fence_slot.get_first_child():
            self._fence_slot.remove(child)
        self._fence_slot.append(widget)

    # ------------------------------------------------------------------
    # Atualização
    # ------------------------------------------------------------------
    def refresh_all(self) -> None:
        self.refresh_model()
        self.refresh_tokens()
        self.refresh_load()
        self.refresh_ram()
        self.refresh_rag()
        self.refresh_net()

    def _on_tick(self) -> bool:
        # Tick periódico: telemetria de sistema (carga/RAM/RAG/Rede) e modelo ativo.
        # Tokens são atualizados de forma pontual pelo app após cada resposta.
        # Por construção o tick só roda com a janela visível; se chegou aqui
        # invisível ou já descartada, a fonte perdeu o sentido e sai de cena.
        if self._disposed or not self.ctx.get_visible():
            self._stop_tick()
            return GLib.SOURCE_REMOVE
        self.refresh_load()
        self.refresh_ram()
        self.refresh_rag()
        self.refresh_model()
        self.refresh_net()
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
        """Mostra o consumo da sessão só depois do primeiro token.

        Antes, exibia "0 tokens" numa conversa nova — o que parece erro de
        contador, não estado. Enquanto não há uso, o item some em vez de mentir.
        """
        tracker = self.ctx.engine.usage_tracker
        if tracker is None or tracker.session.total_tokens == 0:
            self.tokens_item.set_visible(False)
            return
        self.tokens_item.set_visible(True)
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

    def refresh_net(self) -> None:
        import threading

        def bg() -> None:
            try:
                from ...core.network_inspector import NetworkInspector

                gw_ip, gw_dev = NetworkInspector.get_default_gateway()
                if not gw_ip:
                    def update_offline() -> bool:
                        if not self._disposed:
                            self.net_lbl.set_text("Offline")
                            self.net_item.set_tooltip_text("Sem conexão de rede ativa")
                        return GLib.SOURCE_REMOVE

                    GLib.idle_add(update_offline)
                    return

                ping = NetworkInspector.ping_host(gw_ip, count=1, timeout_sec=0.25)
                ping_str = f"{ping:.1f} ms" if ping is not None else "GW ok"

                def update_online() -> bool:
                    if not self._disposed:
                        self.net_lbl.set_text(f"Rede {ping_str}")
                        self.net_item.set_tooltip_text(
                            f"Rede local: {gw_dev} (GW {gw_ip})\nClique para abrir o diagnóstico completo."
                        )
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(update_online)
            except Exception:
                pass

        threading.Thread(target=bg, daemon=True).start()

