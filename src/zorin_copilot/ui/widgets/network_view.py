# Decisão de design: Visualização de Infraestrutura de Rede e Introspecção Local
# estritamente alinhada às diretrizes Anti-AI-Slop (tauri-anti-slop-PORTABLE.md):
#
# 1. Zero Emojis: Todas as representações visuais usam ícones simbólicos Freedesktop/Adwaita.
# 2. Sem Gradientes Púrpura/Índigo: Utiliza paleta sóbria Adwaita, bordas hairline de 1px
#    (alpha currentColor 0.08) e fundos de baixa opacidade.
# 3. Numerais Tabulares (tnum): Todos os endereços IP, portas, latências (ms) e taxas
#    utilizam font-feature-settings: "tnum" 1 para evitar jitter e manter alinhamento.
# 4. Densidade Desktop Compacta: Linhas de 26-28px com padding enxuto (4-6px), permitindo
#    visualização de alta densidade sem padding inchado de tablet.
# 5. Estados Explícitos: Quatro estados claros (Loading, Empty, Error, Loaded).
# 6. Não-bloqueante: Todas as coletas de rede executam em thread de fundo via GLib.idle_add.

"""Visualização de infraestrutura de rede, topologia local e portas em execução."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from ..gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ...core.network_actions import (
    benchmark_dns,
    release_local_port,
    wake_on_lan,
)
from ...core.network_inspector import (
    InterfaceType,
    ListeningPort,
    NeighborDevice,
    NetworkHealthReport,
    NetworkInspector,
)

if TYPE_CHECKING:
    from ..app import CopilotWindow

logger = logging.getLogger(__name__)


def _symbolic_for_device_type(dev_type: str) -> str:
    """Mapeia tipo de equipamento para ícone simbólico de sistema (zero emojis)."""
    mapping = {
        "router": "network-wireless-symbolic",
        "computer": "computer-symbolic",
        "phone": "phone-symbolic",
        "tv": "tv-symbolic",
        "printer": "printer-symbolic",
        "iot": "applications-system-symbolic",
    }
    return mapping.get(dev_type, "network-workgroup-symbolic")


def _symbolic_for_iface_type(iface_type: str) -> str:
    """Mapeia tipo de interface para ícone simbólico."""
    if iface_type == "wifi":
        return "network-wireless-symbolic"
    if iface_type == "vpn":
        return "network-vpn-symbolic"
    if iface_type == "container":
        return "network-server-symbolic"
    return "network-wired-symbolic"


class NetworkInspectorView(Gtk.Box):
    """Componente GTK4 de alta densidade para inspeção de infraestrutura de rede."""

    def __init__(self, ctx: "CopilotWindow | None" = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.ctx = ctx
        self.add_css_class("network-container")

        self._report: NetworkHealthReport | None = None
        self._neighbors: list[NeighborDevice] = []
        self._ports: list[ListeningPort] = []
        self._dns_results: list[dict[str, Any]] | None = None
        self._is_refreshing = False

        self._build_scaffold()
        self.refresh()

    def _build_scaffold(self) -> None:
        # Barra superior compacta de controles
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.toolbar.set_margin_bottom(4)

        title_lbl = Gtk.Label(label="Infraestrutura de Rede")
        title_lbl.add_css_class("network-health-title")
        title_lbl.set_hexpand(True)
        title_lbl.set_halign(Gtk.Align.START)
        self.toolbar.append(title_lbl)

        # Botão Atualizar
        self.refresh_btn = Gtk.Button()
        self.refresh_btn.set_icon_name("view-refresh-symbolic")
        self.refresh_btn.set_tooltip_text("Atualizar diagnóstico de rede")
        self.refresh_btn.add_css_class("flat")
        self.refresh_btn.add_css_class("network-btn-compact")
        self.refresh_btn.connect("clicked", lambda _: self.refresh())
        self.toolbar.append(self.refresh_btn)

        # Botão Copiar IP Local
        self.copy_ip_btn = Gtk.Button()
        self.copy_ip_btn.set_icon_name("edit-copy-symbolic")
        self.copy_ip_btn.set_tooltip_text("Copiar IP local")
        self.copy_ip_btn.add_css_class("flat")
        self.copy_ip_btn.add_css_class("network-btn-compact")
        self.copy_ip_btn.connect("clicked", self._on_copy_ip_clicked)
        self.toolbar.append(self.copy_ip_btn)

        # Botão Sincronizar no Grafo
        self.sync_kg_btn = Gtk.Button()
        self.sync_kg_btn.set_icon_name("network-server-symbolic")
        self.sync_kg_btn.set_tooltip_text("Sincronizar no Grafo de Conhecimento")
        self.sync_kg_btn.add_css_class("flat")
        self.sync_kg_btn.add_css_class("network-btn-compact")
        self.sync_kg_btn.connect("clicked", self._on_sync_kg_clicked)
        self.toolbar.append(self.sync_kg_btn)

        # Botão Benchmark de DNS
        self.dns_bench_btn = Gtk.Button()
        self.dns_bench_btn.set_icon_name("network-transmit-receive-symbolic")
        self.dns_bench_btn.set_tooltip_text("Benchmark de latência de servidores DNS")
        self.dns_bench_btn.add_css_class("flat")
        self.dns_bench_btn.add_css_class("network-btn-compact")
        self.dns_bench_btn.connect("clicked", self._on_dns_bench_clicked)
        self.toolbar.append(self.dns_bench_btn)

        self.append(self.toolbar)

        # Container para os estados (Loading, Error, Loaded, Empty)
        self.content_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.content_area.set_vexpand(True)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self.content_area)
        self.append(scrolled)

    def refresh(self) -> None:
        """Dispara a coleta não-bloqueante em segundo plano."""
        if self._is_refreshing:
            return
        self._is_refreshing = True
        self.refresh_btn.set_sensitive(False)
        self._show_loading_state()

        def background_worker() -> None:
            try:
                report = NetworkInspector.get_health_report()
                neighbors = NetworkInspector.get_neighbors()
                ports = NetworkInspector.get_listening_ports()

                def update_ui() -> bool:
                    self._report = report
                    self._neighbors = neighbors
                    self._ports = ports
                    self._is_refreshing = False
                    self.refresh_btn.set_sensitive(True)
                    self._render_loaded_state()
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(update_ui)
            except Exception as exc:
                logger.exception("Falha na coleta de telemetria de rede: %s", exc)

                def update_error() -> bool:
                    self._is_refreshing = False
                    self.refresh_btn.set_sensitive(True)
                    self._show_error_state(str(exc))
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(update_error)

        threading.Thread(target=background_worker, daemon=True).start()

    def _clear_content(self) -> None:
        child = self.content_area.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.content_area.remove(child)
            child = next_child

    def _show_loading_state(self) -> None:
        self._clear_content()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        spinner = Gtk.Spinner()
        spinner.set_size_request(24, 24)
        spinner.start()
        box.append(spinner)

        lbl = Gtk.Label(label="Inspecionando interfaces e sub-rede...")
        lbl.add_css_class("dim-label")
        lbl.add_css_class("caption")
        box.append(lbl)

        self.content_area.append(box)

    def _show_error_state(self, message: str) -> None:
        self._clear_content()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)

        icon = Gtk.Image.new_from_icon_name("network-error-symbolic")
        icon.set_pixel_size(24)
        box.append(icon)

        err_lbl = Gtk.Label(label=f"Erro de telemetria: {message}")
        err_lbl.add_css_class("error")
        err_lbl.set_wrap(True)
        err_lbl.set_max_width_chars(45)
        box.append(err_lbl)

        retry_btn = Gtk.Button(label="Tentar novamente")
        retry_btn.add_css_class("suggested-action")
        retry_btn.add_css_class("network-btn-compact")
        retry_btn.connect("clicked", lambda _: self.refresh())
        box.append(retry_btn)

        self.content_area.append(box)

    def _render_loaded_state(self) -> None:
        self._clear_content()
        report = self._report
        if not report:
            self._show_error_state("Relatório vazio retornado pelo subsistema.")
            return

        # 1. Card de Saúde de Rede
        self.content_area.append(self._make_health_card(report))

        # 2. Seção de Dispositivos LAN
        self.content_area.append(self._make_section_title("Dispositivos na Rede Local (LAN)", len(self._neighbors)))
        if not self._neighbors:
            empty_lbl = Gtk.Label(label="Nenhum dispositivo detectado na tabela ARP local.")
            empty_lbl.add_css_class("dim-label")
            empty_lbl.add_css_class("caption")
            empty_lbl.set_halign(Gtk.Align.START)
            self.content_area.append(empty_lbl)
        else:
            self.content_area.append(self._make_neighbors_table(self._neighbors))

        # 3. Seção de Serviços e Portas em Escuta
        self.content_area.append(self._make_section_title("Portas e Serviços Locais em Escuta", len(self._ports)))
        if not self._ports:
            empty_ports = Gtk.Label(label="Nenhum serviço em escuta identificado.")
            empty_ports.add_css_class("dim-label")
            empty_ports.add_css_class("caption")
            empty_ports.set_halign(Gtk.Align.START)
            self.content_area.append(empty_ports)
        else:
            self.content_area.append(self._make_ports_table(self._ports))

        # 4. Seção de Benchmark DNS (quando solicitado)
        if self._dns_results:
            self.content_area.append(self._make_section_title("Benchmark de Servidores DNS", len(self._dns_results)))
            self.content_area.append(self._make_dns_benchmark_card(self._dns_results))


    def _make_section_title(self, text: str, count: int) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl = Gtk.Label(label=text)
        lbl.add_css_class("network-section-title")
        lbl.set_hexpand(True)
        lbl.set_halign(Gtk.Align.START)
        box.append(lbl)

        pill = Gtk.Label(label=str(count))
        pill.add_css_class("network-metric-pill")
        box.append(pill)
        return box

    def _make_health_card(self, report: NetworkHealthReport) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("network-health-card")

        # Linha Principal: Interface e IPs
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        iface_icon = Gtk.Image.new_from_icon_name(_symbolic_for_iface_type(report.primary_type))
        iface_icon.set_pixel_size(18)
        top_row.append(iface_icon)

        iface_info = f"{report.primary_interface} ({report.primary_type.upper()})"
        iface_lbl = Gtk.Label(label=iface_info)
        iface_lbl.set_halign(Gtk.Align.START)
        top_row.append(iface_lbl)

        ip_lbl = Gtk.Label(label=f"IP: {report.local_ip}")
        ip_lbl.add_css_class("tabular-nums")
        ip_lbl.add_css_class("network-metric-pill")
        top_row.append(ip_lbl)

        gw_lbl = Gtk.Label(label=f"Gateway: {report.gateway_ip}")
        gw_lbl.add_css_class("tabular-nums")
        gw_lbl.add_css_class("dim-label")
        gw_lbl.set_hexpand(True)
        gw_lbl.set_halign(Gtk.Align.START)
        top_row.append(gw_lbl)

        card.append(top_row)

        # Linha Secundária: Latências e Métricas
        metrics_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        gw_ping_str = f"{report.gateway_ping_ms:.2f} ms" if report.gateway_ping_ms is not None else "Inacessível"
        gw_metric = Gtk.Label(label=f"Latência GW: {gw_ping_str}")
        gw_metric.add_css_class("tabular-nums")
        gw_metric.add_css_class("caption")
        metrics_row.append(gw_metric)

        inet_ping_str = f"{report.internet_ping_ms:.2f} ms" if report.internet_ping_ms is not None else "Sem Internet"
        inet_metric = Gtk.Label(label=f"Internet: {inet_ping_str}")
        inet_metric.add_css_class("tabular-nums")
        inet_metric.add_css_class("caption")
        metrics_row.append(inet_metric)

        if report.dns_servers:
            dns_str = f"DNS: {', '.join(report.dns_servers)}"
            dns_lbl = Gtk.Label(label=dns_str)
            dns_lbl.add_css_class("tabular-nums")
            dns_lbl.add_css_class("caption")
            dns_lbl.add_css_class("dim-label")
            metrics_row.append(dns_lbl)

        if report.wifi:
            wifi_metric = Gtk.Label(label=f"SSID: {report.wifi.ssid} ({report.wifi.signal_percent}%)")
            wifi_metric.add_css_class("tabular-nums")
            wifi_metric.add_css_class("caption")
            metrics_row.append(wifi_metric)

        card.append(metrics_row)
        return card

    def _make_neighbors_table(self, neighbors: list[NeighborDevice]) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("network-table-card")

        # Cabeçalho da Tabela
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("network-table-header")

        h_type = Gtk.Label(label="TIPO")
        h_type.set_size_request(32, -1)
        header.append(h_type)

        h_ip = Gtk.Label(label="ENDEREÇO IP")
        h_ip.set_size_request(120, -1)
        h_ip.set_halign(Gtk.Align.START)
        header.append(h_ip)

        h_vendor = Gtk.Label(label="FABRICANTE / DISPOSITIVO")
        h_vendor.set_hexpand(True)
        h_vendor.set_halign(Gtk.Align.START)
        header.append(h_vendor)

        h_mac = Gtk.Label(label="MAC (OFUSCADO)")
        h_mac.set_size_request(130, -1)
        h_mac.set_halign(Gtk.Align.START)
        header.append(h_mac)

        h_status = Gtk.Label(label="ESTADO")
        h_status.set_size_request(60, -1)
        h_status.set_halign(Gtk.Align.END)
        header.append(h_status)

        h_action = Gtk.Label(label="AÇÃO")
        h_action.set_size_request(60, -1)
        h_action.set_halign(Gtk.Align.END)
        header.append(h_action)

        card.append(header)

        # Linhas de Dispositivos (densidade desktop: 28px)
        for dev in neighbors[:25]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("network-table-row")

            icon = Gtk.Image.new_from_icon_name(_symbolic_for_device_type(dev.device_type))
            icon.set_pixel_size(14)
            icon.set_size_request(32, -1)
            row.append(icon)

            ip_lbl = Gtk.Label(label=dev.ip)
            ip_lbl.add_css_class("tabular-nums")
            ip_lbl.set_size_request(120, -1)
            ip_lbl.set_halign(Gtk.Align.START)
            row.append(ip_lbl)

            vendor_text = dev.vendor if dev.vendor != "Dispositivo de Rede" else dev.hostname or dev.vendor
            vendor_lbl = Gtk.Label(label=vendor_text)
            vendor_lbl.set_hexpand(True)
            vendor_lbl.set_halign(Gtk.Align.START)
            vendor_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(vendor_lbl)

            mac_masked = NetworkInspector.mask_mac(dev.mac)
            mac_lbl = Gtk.Label(label=mac_masked)
            mac_lbl.add_css_class("tabular-nums")
            mac_lbl.add_css_class("dim-label")
            mac_lbl.set_size_request(130, -1)
            mac_lbl.set_halign(Gtk.Align.START)
            row.append(mac_lbl)

            status_text = "Ativo" if dev.is_online else "Inativo"
            status_lbl = Gtk.Label(label=status_text)
            status_lbl.add_css_class("caption")
            if dev.is_online:
                status_lbl.add_css_class("success")
            else:
                status_lbl.add_css_class("dim-label")
            status_lbl.set_size_request(60, -1)
            status_lbl.set_halign(Gtk.Align.END)
            row.append(status_lbl)

            # Botão de Ação: Wake-on-LAN para computadores e roteadores
            if dev.mac and dev.device_type in ("computer", "router"):
                wol_btn = Gtk.Button(label="WoL")
                wol_btn.set_tooltip_text(f"Enviar Wake-on-LAN para ligar/acordar {mac_masked}")
                wol_btn.add_css_class("flat")
                wol_btn.add_css_class("network-btn-compact")
                wol_btn.set_size_request(60, -1)
                target_mac = dev.mac
                wol_btn.connect("clicked", lambda _, m=target_mac: self._on_wake_device_clicked(m))
                row.append(wol_btn)
            else:
                empty_box = Gtk.Box()
                empty_box.set_size_request(60, -1)
                row.append(empty_box)

            card.append(row)

        return card

    def _make_ports_table(self, ports: list[ListeningPort]) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("network-table-card")

        # Cabeçalho
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("network-table-header")

        h_port = Gtk.Label(label="PORTA")
        h_port.set_size_request(70, -1)
        h_port.set_halign(Gtk.Align.START)
        header.append(h_port)

        h_proto = Gtk.Label(label="PROTO")
        h_proto.set_size_request(50, -1)
        h_proto.set_halign(Gtk.Align.START)
        header.append(h_proto)

        h_proc = Gtk.Label(label="PROCESSO")
        h_proc.set_hexpand(True)
        h_proc.set_halign(Gtk.Align.START)
        header.append(h_proc)

        h_pid = Gtk.Label(label="PID")
        h_pid.set_size_request(60, -1)
        h_pid.set_halign(Gtk.Align.START)
        header.append(h_pid)

        h_action = Gtk.Label(label="AÇÕES")
        h_action.set_size_request(90, -1)
        h_action.set_halign(Gtk.Align.END)
        header.append(h_action)

        card.append(header)

        # Linhas de Portas
        for p in ports[:35]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("network-table-row")

            port_lbl = Gtk.Label(label=f":{p.port}")
            port_lbl.add_css_class("tabular-nums")
            port_lbl.set_size_request(70, -1)
            port_lbl.set_halign(Gtk.Align.START)
            row.append(port_lbl)

            proto_lbl = Gtk.Label(label=p.protocol.upper())
            proto_lbl.add_css_class("dim-label")
            proto_lbl.set_size_request(50, -1)
            proto_lbl.set_halign(Gtk.Align.START)
            row.append(proto_lbl)

            proc_lbl = Gtk.Label(label=p.process_name or "-")
            proc_lbl.set_hexpand(True)
            proc_lbl.set_halign(Gtk.Align.START)
            proc_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(proc_lbl)

            pid_str = str(p.pid) if p.pid else "-"
            pid_lbl = Gtk.Label(label=pid_str)
            pid_lbl.add_css_class("tabular-nums")
            pid_lbl.add_css_class("dim-label")
            pid_lbl.set_size_request(60, -1)
            pid_lbl.set_halign(Gtk.Align.START)
            row.append(pid_lbl)

            # Ações: Abrir (web) e/ou Liberar (kill)
            action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            action_box.set_size_request(90, -1)
            action_box.set_halign(Gtk.Align.END)

            if p.port in (80, 443, 3000, 5000, 5173, 8000, 8080, 8888, 9090):
                open_btn = Gtk.Button(label="Abrir")
                open_btn.set_tooltip_text(f"Abrir http://localhost:{p.port} no navegador")
                open_btn.add_css_class("flat")
                open_btn.add_css_class("network-btn-compact")
                port_num = p.port
                open_btn.connect("clicked", lambda _, pnum=port_num: self._open_localhost(pnum))
                action_box.append(open_btn)

            if p.pid and p.pid > 1:
                kill_btn = Gtk.Button()
                kill_btn.set_icon_name("process-stop-symbolic")
                kill_btn.set_tooltip_text(f"Liberar porta {p.port} (encerrar processo PID {p.pid})")
                kill_btn.add_css_class("flat")
                kill_btn.add_css_class("network-btn-compact")
                pnum = p.port
                pid_num = p.pid
                kill_btn.connect(
                    "clicked",
                    lambda _, port_val=pnum, pid_val=pid_num: self._on_release_port_clicked(port_val, pid_val),
                )
                action_box.append(kill_btn)

            row.append(action_box)
            card.append(row)

        return card

    def _open_localhost(self, port: int) -> None:
        url = f"http://localhost:{port}"
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
            if self.ctx and hasattr(self.ctx, "show_toast"):
                self.ctx.show_toast(f"Abrindo {url}")
        except Exception as exc:
            logger.warning("Falha ao abrir URI %s: %s", url, exc)

    def _on_copy_ip_clicked(self, _btn: Gtk.Button) -> None:
        if self._report and self._report.local_ip:
            ip = self._report.local_ip
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(ip)
            if self.ctx and hasattr(self.ctx, "show_toast"):
                self.ctx.show_toast(f"IP local copiado: {ip}")

    def _on_sync_kg_clicked(self, _btn: Gtk.Button) -> None:
        self.sync_kg_btn.set_sensitive(False)

        def sync_worker() -> None:
            try:
                count = NetworkInspector.sync_to_knowledge_graph()

                def finish() -> bool:
                    self.sync_kg_btn.set_sensitive(True)
                    if self.ctx and hasattr(self.ctx, "show_toast"):
                        self.ctx.show_toast(f"{count} entidades sincronizadas no Grafo de Conhecimento.")
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(finish)
            except Exception as exc:
                logger.error("Falha ao sincronizar com o Grafo: %s", exc)

                def fail() -> bool:
                    self.sync_kg_btn.set_sensitive(True)
                    if self.ctx and hasattr(self.ctx, "show_toast"):
                        self.ctx.show_toast(f"Erro ao sincronizar Grafo: {exc}")
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(fail)

        threading.Thread(target=sync_worker, daemon=True).start()

    def _make_dns_benchmark_card(self, results: list[dict[str, Any]]) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.add_css_class("network-table-card")

        # Cabeçalho da Tabela
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("network-table-header")

        h_prov = Gtk.Label(label="RESOLVEDOR / PROVEDOR")
        h_prov.set_hexpand(True)
        h_prov.set_halign(Gtk.Align.START)
        header.append(h_prov)

        h_ip = Gtk.Label(label="IP DO SERVIDOR")
        h_ip.set_size_request(130, -1)
        h_ip.set_halign(Gtk.Align.START)
        header.append(h_ip)

        h_lat = Gtk.Label(label="LATÊNCIA")
        h_lat.set_size_request(90, -1)
        h_lat.set_halign(Gtk.Align.END)
        header.append(h_lat)

        h_status = Gtk.Label(label="STATUS")
        h_status.set_size_request(110, -1)
        h_status.set_halign(Gtk.Align.END)
        header.append(h_status)

        card.append(header)

        for res in results:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("network-table-row")

            prov_lbl = Gtk.Label(label=res.get("provider", "DNS"))
            prov_lbl.set_hexpand(True)
            prov_lbl.set_halign(Gtk.Align.START)
            prov_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(prov_lbl)

            ip_lbl = Gtk.Label(label=res.get("server_ip", "-"))
            ip_lbl.add_css_class("tabular-nums")
            ip_lbl.add_css_class("dim-label")
            ip_lbl.set_size_request(130, -1)
            ip_lbl.set_halign(Gtk.Align.START)
            row.append(ip_lbl)

            avg_ms = res.get("avg_latency_ms")
            if avg_ms is not None:
                lat_str = f"{avg_ms:.1f} ms"
            else:
                lat_str = "timeout"

            lat_lbl = Gtk.Label(label=lat_str)
            lat_lbl.add_css_class("tabular-nums")
            if avg_ms is None:
                lat_lbl.add_css_class("error")
            elif res.get("is_fastest"):
                lat_lbl.add_css_class("success")
            lat_lbl.set_size_request(90, -1)
            lat_lbl.set_halign(Gtk.Align.END)
            row.append(lat_lbl)

            if res.get("is_fastest"):
                status_lbl = Gtk.Label(label="Mais Rápido")
                status_lbl.add_css_class("caption")
                status_lbl.add_css_class("success")
            elif avg_ms is not None:
                status_lbl = Gtk.Label(label="Operacional")
                status_lbl.add_css_class("caption")
                status_lbl.add_css_class("dim-label")
            else:
                status_lbl = Gtk.Label(label="Inacessível")
                status_lbl.add_css_class("caption")
                status_lbl.add_css_class("error")

            status_lbl.set_size_request(110, -1)
            status_lbl.set_halign(Gtk.Align.END)
            row.append(status_lbl)

            card.append(row)

        return card

    def _on_dns_bench_clicked(self, _btn: Gtk.Button) -> None:
        self.dns_bench_btn.set_sensitive(False)
        if self.ctx and hasattr(self.ctx, "show_toast"):
            self.ctx.show_toast("Iniciando benchmark de latência DNS...")

        def bench_worker() -> None:
            try:
                results = benchmark_dns()

                def finish() -> bool:
                    self.dns_bench_btn.set_sensitive(True)
                    self._dns_results = results
                    self._render_loaded_state()
                    fastest = next((r for r in results if r.get("is_fastest")), None)
                    if fastest and self.ctx and hasattr(self.ctx, "show_toast"):
                        self.ctx.show_toast(
                            f"Benchmark concluído: {fastest['provider']} é o mais rápido ({fastest['avg_latency_ms']} ms)"
                        )
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(finish)
            except Exception as exc:
                logger.error("Falha no benchmark DNS: %s", exc)

                def fail() -> bool:
                    self.dns_bench_btn.set_sensitive(True)
                    if self.ctx and hasattr(self.ctx, "show_toast"):
                        self.ctx.show_toast(f"Erro no benchmark DNS: {exc}")
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(fail)

        threading.Thread(target=bench_worker, daemon=True).start()

    def _on_wake_device_clicked(self, mac: str) -> None:
        def wol_worker() -> None:
            try:
                ok, msg = wake_on_lan(mac)

                def finish() -> bool:
                    if self.ctx and hasattr(self.ctx, "show_toast"):
                        self.ctx.show_toast(msg)
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(finish)
            except Exception as exc:
                logger.error("Falha no Wake-on-LAN para %s: %s", mac, exc)

                def fail() -> bool:
                    if self.ctx and hasattr(self.ctx, "show_toast"):
                        self.ctx.show_toast(f"Erro no Wake-on-LAN: {exc}")
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(fail)

        threading.Thread(target=wol_worker, daemon=True).start()

    def _on_release_port_clicked(self, port: int, pid: int | None) -> None:
        parent = self.get_root()
        if isinstance(parent, Gtk.Window) and hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog(
                heading=f"Liberar Porta {port}?",
                body=f"Deseja encerrar o processo (PID {pid or 'desconhecido'}) escutando nesta porta?",
            )
            dialog.add_response("cancel", "Cancelar")
            dialog.add_response("kill", "Encerrar")
            dialog.set_response_appearance("kill", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_response(dlg: Adw.AlertDialog, response: str) -> None:
                if response == "kill":
                    self._do_release_port(port, pid)

            dialog.choose(parent, None, on_response)
        else:
            self._do_release_port(port, pid)

    def _do_release_port(self, port: int, pid: int | None) -> None:
        def release_worker() -> None:
            try:
                ok, msg, _ = release_local_port(port, target_pid=pid)

                def finish() -> bool:
                    if self.ctx and hasattr(self.ctx, "show_toast"):
                        self.ctx.show_toast(msg)
                    self.refresh()
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(finish)
            except Exception as exc:
                logger.error("Falha ao liberar porta %d: %s", port, exc)

                def fail() -> bool:
                    if self.ctx and hasattr(self.ctx, "show_toast"):
                        self.ctx.show_toast(f"Erro ao liberar porta: {exc}")
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(fail)

        threading.Thread(target=release_worker, daemon=True).start()


class NetworkInspectorDialog(Adw.Dialog):
    """Diálogo modal com o diagnóstico e topologia de rede local."""

    def __init__(self, ctx: "CopilotWindow") -> None:
        super().__init__()
        self.ctx = ctx
        self.set_title("Infraestrutura de Rede")
        self.set_content_width(720)
        self.set_content_height(540)

        view = NetworkInspectorView(ctx=ctx)
        self.set_child(view)
