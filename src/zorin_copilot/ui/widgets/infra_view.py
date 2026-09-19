# Decisão de design: Visualização de Infraestrutura de Sistema, Containers e Sensores
# estritamente alinhada às diretrizes Anti-AI-Slop (tauri-anti-slop-PORTABLE.md):
#
# 1. Zero Emojis: Todas as representações visuais usam ícones simbólicos Freedesktop/Adwaita.
# 2. Sem Gradientes: Superfícies sólidas e neutras (@card_bg_color / @window_bg_color).
# 3. Numerais Tabulares (tnum): font-feature-settings: "tnum" 1 em temperaturas, RAM, MBs e PIDs.
# 4. Densidade de Desktop Compacta: Linhas de 26-28px com padding enxuto (4-6px).
# 5. Execução Não-Bloqueante: Todas as consultas de Docker e Hardware rodam em background.

"""Interface gráfica GTK4/Libadwaita para infraestrutura de containers e telemetria de hardware."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from ..gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ...core.docker_manager import ContainerInfo, DockerManager
from ...core.system_health import (
    JournalIncident,
    MemoryPressureMetrics,
    StorageHealthMetrics,
    SystemHealthInspector,
    ThermalMetrics,
)

if TYPE_CHECKING:
    from ..app import CopilotWindow

logger = logging.getLogger(__name__)


def _make_tnum_label(text: str = "", css_class: str = "") -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=0.0)
    lbl.add_css_class("numeric-stat")
    if css_class:
        lbl.add_css_class(css_class)
    return lbl


class InfraInspectorView(Gtk.Box):
    """Visualização integrada em abas para Hardware, Docker e Journalctl."""

    def __init__(self, ctx: "CopilotWindow" | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.ctx = ctx
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(16)
        self.set_margin_end(16)

        # Header com título e botão de refresh
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.append(header_box)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_lbl = Gtk.Label(label="Diagnóstico de Infraestrutura & Host", xalign=0.0)
        title_lbl.add_css_class("heading")
        sub_lbl = Gtk.Label(label="Telemetria de sensores, containers Docker e integridade de sistema", xalign=0.0)
        sub_lbl.add_css_class("caption")
        sub_lbl.add_css_class("dim-label")
        title_box.append(title_lbl)
        title_box.append(sub_lbl)
        header_box.append(title_box)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header_box.append(spacer)

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_btn.set_tooltip_text("Atualizar métricas agora")
        self.refresh_btn.add_css_class("flat")
        self.refresh_btn.connect("clicked", lambda _: self.refresh())
        header_box.append(self.refresh_btn)

        # ViewStack com ViewSwitcher
        self.view_stack = Adw.ViewStack()
        self.view_stack.set_vexpand(True)

        switcher_bar = Adw.ViewSwitcher(stack=self.view_stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        switcher_bar.set_halign(Gtk.Align.CENTER)
        self.append(switcher_bar)
        self.append(self.view_stack)

        # 1. Página de Hardware e Sensores
        self.hw_page = self._build_hardware_page()
        self.view_stack.add_titled_with_icon(
            self.hw_page, "hardware", "Hardware & Sensores", "utilities-system-monitor-symbolic"
        )

        # 2. Página de Containers Docker
        self.docker_page = self._build_docker_page()
        self.view_stack.add_titled_with_icon(
            self.docker_page, "docker", "Containers Docker", "system-search-symbolic"
        )

        # 3. Página de Incidentes do Journalctl
        self.journal_page = self._build_journal_page()
        self.view_stack.add_titled_with_icon(
            self.journal_page, "journal", "Diário do Sistema", "dialog-warning-symbolic"
        )

        # Inicia coleta inicial
        self.refresh()

    def _build_hardware_page(self) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(8)
        content.set_margin_bottom(8)
        scroll.set_child(content)

        # Grupo Térmico
        self.thermal_grp = Adw.PreferencesGroup(title="Termodinâmica e Ventilação")
        content.append(self.thermal_grp)

        self.cpu_row = Adw.ActionRow(title="Temperatura da CPU", subtitle="lm_sensors (Tctl / Core)")
        self.cpu_row.add_prefix(Gtk.Image.new_from_icon_name("utilities-system-monitor-symbolic"))
        self.cpu_val_lbl = _make_tnum_label("-- °C")
        self.cpu_row.add_suffix(self.cpu_val_lbl)
        self.thermal_grp.add(self.cpu_row)

        self.gpu_row = Adw.ActionRow(title="Temperatura da GPU", subtitle="amdgpu / nvidia")
        self.gpu_row.add_prefix(Gtk.Image.new_from_icon_name("video-display-symbolic"))
        self.gpu_val_lbl = _make_tnum_label("-- °C")
        self.gpu_row.add_suffix(self.gpu_val_lbl)
        self.thermal_grp.add(self.gpu_row)

        self.nvme_row = Adw.ActionRow(title="Temperatura do Armazenamento NVMe", subtitle="Composite Sensor")
        self.nvme_row.add_prefix(Gtk.Image.new_from_icon_name("drive-harddisk-symbolic"))
        self.nvme_val_lbl = _make_tnum_label("-- °C")
        self.nvme_row.add_suffix(self.nvme_val_lbl)
        self.thermal_grp.add(self.nvme_row)

        # Grupo Memória & PSI
        self.mem_grp = Adw.PreferencesGroup(title="Memória e Pressão do Host (PSI)")
        content.append(self.mem_grp)

        self.ram_row = Adw.ActionRow(title="Memória RAM em Uso", subtitle="Consumo em relação ao total")
        self.ram_row.add_prefix(Gtk.Image.new_from_icon_name("media-flash-symbolic"))
        self.ram_val_lbl = _make_tnum_label("-- %")
        self.ram_row.add_suffix(self.ram_val_lbl)
        self.mem_grp.add(self.ram_row)

        self.psi_row = Adw.ActionRow(title="Pressão de I/O de Memória (PSI)", subtitle="Stall médio nos últimos 10s")
        self.psi_row.add_prefix(Gtk.Image.new_from_icon_name("emblem-synchronizing-symbolic"))
        self.psi_val_lbl = _make_tnum_label("--")
        self.psi_row.add_suffix(self.psi_val_lbl)
        self.mem_grp.add(self.psi_row)

        # Grupo Armazenamento / SMART
        self.storage_grp = Adw.PreferencesGroup(title="Armazenamento e S.M.A.R.T.")
        content.append(self.storage_grp)

        self.smart_row = Adw.ActionRow(title="Status S.M.A.R.T. (Unidade Primária)", subtitle="/dev/nvme0n1")
        self.smart_row.add_prefix(Gtk.Image.new_from_icon_name("drive-harddisk-solidstate-symbolic"))
        self.smart_val_lbl = _make_tnum_label("Verificando...")
        self.smart_row.add_suffix(self.smart_val_lbl)
        self.storage_grp.add(self.smart_row)

        return scroll

    def _build_docker_page(self) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(8)
        content.set_margin_bottom(8)
        scroll.set_child(content)

        self.docker_grp = Adw.PreferencesGroup(
            title="Containers Locais em Execução",
            description="Socket /var/run/docker.sock direto com verificação de saúde",
        )
        content.append(self.docker_grp)

        self.docker_empty_lbl = Gtk.Label(label="Nenhum container Docker encontrado ou daemon inativo.", xalign=0.0)
        self.docker_empty_lbl.add_css_class("dim-label")
        self.docker_empty_lbl.set_margin_top(12)
        self.docker_grp.add(self.docker_empty_lbl)

        return scroll

    def _build_journal_page(self) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(8)
        content.set_margin_bottom(8)
        scroll.set_child(content)

        self.journal_grp = Adw.PreferencesGroup(
            title="Falhas Críticas Recentes (journalctl)",
            description="Filtradas por severidade (err..emerg) com deduplicação de mensagens em loop",
        )
        content.append(self.journal_grp)

        self.journal_empty_lbl = Gtk.Label(label="Nenhum erro recente no diário do sistema.", xalign=0.0)
        self.journal_empty_lbl.add_css_class("dim-label")
        self.journal_empty_lbl.set_margin_top(12)
        self.journal_grp.add(self.journal_empty_lbl)

        return scroll

    def refresh(self) -> None:
        """Dispara atualização assíncrona de todas as fontes de infraestrutura."""
        self.refresh_btn.set_sensitive(False)

        def worker() -> None:
            try:
                # 1. Hardware e Sensores
                report = SystemHealthInspector.get_complete_report()
                # 2. Docker
                dm = DockerManager()
                containers: list[ContainerInfo] = []
                if dm.is_available():
                    containers = dm.list_containers(all_containers=True)
                # 3. Journal
                incidents = SystemHealthInspector.get_journal_incidents(lines=25)

                def apply_ui() -> bool:
                    self._update_hardware_ui(report)
                    self._update_docker_ui(containers)
                    self._update_journal_ui(incidents)
                    self.refresh_btn.set_sensitive(True)
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(apply_ui)
            except Exception as exc:
                logger.error("Falha ao coletar dados de infraestrutura: %s", exc)

                def on_err() -> bool:
                    self.refresh_btn.set_sensitive(True)
                    return GLib.SOURCE_REMOVE

                GLib.idle_add(on_err)

        threading.Thread(target=worker, daemon=True).start()

    def _update_hardware_ui(self, report: dict[str, Any]) -> None:
        t = report.get("thermals", {})
        m = report.get("memory", {})
        s = report.get("storage", {})

        # Termodinâmica
        cpu = t.get("cpu_temp_c", 0.0)
        gpu = t.get("gpu_temp_c", 0.0)
        nvme = t.get("nvme_temp_c", 0.0)

        self.cpu_val_lbl.set_text(f"{cpu:.1f} °C")
        self.gpu_val_lbl.set_text(f"{gpu:.1f} °C")
        self.nvme_val_lbl.set_text(f"{nvme:.1f} °C" if nvme > 0 else "N/D")

        # Alerta de temperatura
        if t.get("throttle_warning"):
            self.cpu_row.set_subtitle(f"Atenção: {t.get('status_summary')}")
            self.cpu_val_lbl.add_css_class("error")
        else:
            self.cpu_row.set_subtitle("Temperatura estável (sem risco de throttling)")
            self.cpu_val_lbl.remove_css_class("error")

        # Memória
        used_p = m.get("used_percent", 0.0)
        used_mb = m.get("total_mb", 0.0) - m.get("available_mb", 0.0)
        total_mb = m.get("total_mb", 0.0)
        self.ram_val_lbl.set_text(f"{used_p:.1f} %")
        self.ram_row.set_subtitle(f"{used_mb/1024:.1f} GB de {total_mb/1024:.1f} GB utilizados")

        psi = m.get("psi_some_avg10", 0.0)
        press = m.get("pressure_level", "Baixa")
        self.psi_val_lbl.set_text(f"{psi:.2f} ({press})")

        # Storage
        notes = s.get("notes", "Íntegro")
        self.smart_val_lbl.set_text(notes)

    def _update_docker_ui(self, containers: list[ContainerInfo]) -> None:
        if not hasattr(self, "_docker_rows"):
            self._docker_rows = []

        for row in self._docker_rows:
            try:
                self.docker_grp.remove(row)
            except Exception:
                pass
        self._docker_rows.clear()

        if not containers:
            self.docker_empty_lbl.set_visible(True)
            return

        self.docker_empty_lbl.set_visible(False)

        for c in containers:
            row = Adw.ActionRow(
                title=c.name,
                subtitle=f"{c.image} · {c.status}" + (f" · Portas: {', '.join(c.ports)}" if c.ports else ""),
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("system-search-symbolic"))

            # Badge de saúde
            badge_lbl = _make_tnum_label(c.state.upper())
            if c.state == "running":
                badge_lbl.add_css_class("success")
            else:
                badge_lbl.add_css_class("dim-label")
            row.add_suffix(badge_lbl)

            # Botão Logs
            log_btn = Gtk.Button(icon_name="text-x-generic-symbolic")
            log_btn.set_tooltip_text(f"Ver logs destilados de {c.name}")
            log_btn.add_css_class("flat")
            log_btn.connect("clicked", lambda _, name=c.name: self._show_container_logs_dialog(name))
            row.add_suffix(log_btn)

            # Botão Reiniciar
            restart_btn = Gtk.Button(icon_name="view-refresh-symbolic")
            restart_btn.set_tooltip_text(f"Reiniciar container {c.name}")
            restart_btn.add_css_class("flat")
            restart_btn.connect("clicked", lambda _, name=c.name: self._on_restart_container(name))
            row.add_suffix(restart_btn)

            self.docker_grp.add(row)
            self._docker_rows.append(row)

    def _show_container_logs_dialog(self, container_name: str) -> None:
        """Exibe diálogo modal com logs destilados e sem ruído."""
        parent = self.get_root()
        if not isinstance(parent, Gtk.Window):
            return

        dialog = Adw.Dialog()
        dialog.set_title(f"Logs: {container_name}")
        dialog.set_content_width(640)
        dialog.set_content_height(480)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        text_view.add_css_class("card")
        scroll.set_child(text_view)
        box.append(scroll)
        dialog.set_child(box)

        # Coleta logs em background
        def worker() -> None:
            dm = DockerManager()
            res = dm.get_container_logs(container_name, tail=60)
            lines = res.get("distilled_lines", [])
            content_str = "\n".join(lines) if lines else res.get("summary", "Sem logs.")

            def apply() -> bool:
                buf = text_view.get_buffer()
                buf.set_text(content_str)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(apply)

        threading.Thread(target=worker, daemon=True).start()
        dialog.present(parent)

    def _on_restart_container(self, container_name: str) -> None:
        parent = self.get_root()
        if isinstance(parent, Gtk.Window) and hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog(
                heading=f"Reiniciar {container_name}?",
                body=f"Deseja enviar sinal de reinício para o container Docker '{container_name}'?",
            )
            dialog.add_response("cancel", "Cancelar")
            dialog.add_response("restart", "Reiniciar")
            dialog.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_response(dlg: Adw.AlertDialog, response: str) -> None:
                if response == "restart":
                    self._do_restart_container(container_name)

            dialog.choose(parent, None, on_response)
        else:
            self._do_restart_container(container_name)

    def _do_restart_container(self, container_name: str) -> None:
        if self.ctx and hasattr(self.ctx, "show_toast"):
            self.ctx.show_toast(f"Reiniciando container {container_name}...")

        def worker() -> None:
            dm = DockerManager()
            res = dm.restart_container(container_name)
            msg = res.get("message", "Operação concluída.")

            def finish() -> bool:
                if self.ctx and hasattr(self.ctx, "show_toast"):
                    self.ctx.show_toast(msg)
                self.refresh()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(finish)

        threading.Thread(target=worker, daemon=True).start()

    def _update_journal_ui(self, incidents: list[JournalIncident]) -> None:
        if not hasattr(self, "_journal_rows"):
            self._journal_rows = []

        for row in self._journal_rows:
            try:
                self.journal_grp.remove(row)
            except Exception:
                pass
        self._journal_rows.clear()

        if not incidents:
            self.journal_empty_lbl.set_visible(True)
            return

        self.journal_empty_lbl.set_visible(False)

        for inc in incidents[-15:]:  # Exibe os 15 incidentes mais recentes
            row = Adw.ActionRow(
                title=inc.unit,
                subtitle=inc.message[:130] + ("..." if len(inc.message) > 130 else ""),
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))

            if inc.repeat_count > 1:
                badge = _make_tnum_label(f"x{inc.repeat_count}", css_class="warning")
                row.add_suffix(badge)

            # Botão Copiar Mensagem de Erro
            copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
            copy_btn.set_tooltip_text("Copiar erro completo")
            copy_btn.add_css_class("flat")
            copy_btn.connect("clicked", lambda _, msg=inc.message: self._copy_text(msg))
            row.add_suffix(copy_btn)

            self.journal_grp.add(row)
            self._journal_rows.append(row)

    def _copy_text(self, text: str) -> None:
        try:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(text)
            if self.ctx and hasattr(self.ctx, "show_toast"):
                self.ctx.show_toast("Mensagem copiada para a área de transferência.")
        except Exception as exc:
            logger.warning("Falha ao copiar texto: %s", exc)


class InfraInspectorDialog(Adw.Dialog):
    """Diálogo modal com o diagnóstico completo de infraestrutura e hardware."""

    def __init__(self, ctx: "CopilotWindow") -> None:
        super().__init__()
        self.ctx = ctx
        self.set_title("Infraestrutura & Diagnóstico do Host")
        self.set_content_width(740)
        self.set_content_height(560)

        view = InfraInspectorView(ctx=ctx)
        self.set_child(view)
