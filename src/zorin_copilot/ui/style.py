# Decisão de design: Estilização Glassmorphism em GTK4 / Libadwaita com transparência acrílica,
# specular highlights (bordas translúcidas e reflexos internos), sombras suaves e sincronização
# dinâmica em tempo real entre modo claro (frosted light) e modo escuro (smoked glass).

"""Estilos visuais e temas Glassmorphism para a interface do Zorin Copilot."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402


GLASS_CSS = """
/* ====================================================================
   Zorin Copilot - Glassmorphism Aesthetic Stylesheet
   ==================================================================== */

/* --- Janela Principal Translúcida (Acrylic Frost) --- */
window.glass-window,
window.glass-window.background,
window.glass-window > contents {
    background-color: alpha(@window_bg_color, 0.84);
}

window.light-glass,
window.light-glass.background,
window.light-glass > contents {
    background-color: rgba(244, 246, 250, 0.84);
}

window.dark-glass,
window.dark-glass.background,
window.dark-glass > contents {
    background-color: rgba(20, 24, 33, 0.86);
}

/* --- HeaderBar Nativa Integrada ao Vidro Translúcido --- */
window.glass-window headerbar {
    background-color: transparent;
    box-shadow: none;
}

window.light-glass headerbar {
    border-bottom: 1px solid rgba(0, 0, 0, 0.05);
}

window.dark-glass headerbar {
    border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}

/* --- Containers Transparentes (Conteúdo flutua sobre o vidro) --- */
scrolledwindow,
scrolledwindow > viewport {
    background-color: transparent;
}

/* --- Indicador de Modelo / Status no HeaderBar (Glass Pill) --- */
.glass-pill {
    border-radius: 16px;
    padding: 3px 10px;
    transition: all 180ms ease;
}

window.light-glass .glass-pill {
    background-color: rgba(0, 0, 0, 0.04);
    border: 1px solid rgba(0, 0, 0, 0.06);
}

window.light-glass .glass-pill:hover {
    background-color: rgba(0, 0, 0, 0.08);
}

window.dark-glass .glass-pill {
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.1);
}

window.dark-glass .glass-pill:hover {
    background-color: rgba(255, 255, 255, 0.12);
}

/* --- Campo de Entrada Glassmorphic (Search Entry) --- */
.glass-entry {
    border-radius: 14px;
    padding: 8px 14px;
    font-size: 14px;
    transition: all 200ms ease;
}

window.light-glass .glass-entry,
.glass-entry {
    background-color: rgba(255, 255, 255, 0.76);
    border: 1px solid rgba(255, 255, 255, 0.95);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.03), inset 0 1px 0 rgba(255, 255, 255, 0.9);
    color: #1a1a1a;
}

window.light-glass .glass-entry:focus-within {
    background-color: rgba(255, 255, 255, 0.92);
    border: 1px solid rgba(53, 132, 228, 0.75);
    box-shadow: 0 0 0 3px rgba(53, 132, 228, 0.22), 0 4px 16px rgba(53, 132, 228, 0.08);
}

window.dark-glass .glass-entry {
    background-color: rgba(30, 36, 48, 0.76);
    border: 1px solid rgba(255, 255, 255, 0.14);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.08);
    color: #f6f6f6;
}

window.dark-glass .glass-entry:focus-within {
    background-color: rgba(36, 44, 58, 0.90);
    border: 1px solid rgba(98, 160, 234, 0.85);
    box-shadow: 0 0 0 3px rgba(98, 160, 234, 0.28), 0 4px 16px rgba(0, 0, 0, 0.4);
}

/* --- Botão Principal de Pedir / Submeter --- */
.glass-submit-btn {
    border-radius: 12px;
    padding: 8px 18px;
    font-weight: 600;
    transition: all 180ms ease;
}

/* --- Cards de Vidro (Resposta, Prévia de App, Detalhes) --- */
.glass-card {
    border-radius: 14px;
    transition: all 200ms ease;
}

window.light-glass .glass-card,
.glass-card {
    background-color: rgba(255, 255, 255, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.85);
    box-shadow: 0 8px 30px rgba(31, 38, 135, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.95);
}

window.dark-glass .glass-card {
    background-color: rgba(34, 40, 52, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.32), inset 0 1px 0 rgba(255, 255, 255, 0.12);
}

/* --- Botão Compacto de Abrir Agora (App Preview) --- */
.glass-launch-btn {
    border-radius: 12px;
    padding: 3px 10px;
    transition: all 150ms ease;
}

window.light-glass .glass-launch-btn,
.glass-launch-btn {
    background-color: rgba(53, 132, 228, 0.12);
    border: 1px solid rgba(53, 132, 228, 0.28);
    color: #1c71d8;
}

window.light-glass .glass-launch-btn:hover {
    background-color: rgba(53, 132, 228, 0.22);
    border-color: rgba(53, 132, 228, 0.45);
}

window.dark-glass .glass-launch-btn {
    background-color: rgba(98, 160, 234, 0.16);
    border: 1px solid rgba(98, 160, 234, 0.32);
    color: #78aeed;
}

window.dark-glass .glass-launch-btn:hover {
    background-color: rgba(98, 160, 234, 0.28);
    border-color: rgba(98, 160, 234, 0.5);
}

/* --- Chips de Sugestões Rápidas da Tela Inicial --- */
.glass-chip {
    border-radius: 20px;
    padding: 8px 16px;
    transition: all 180ms ease;
}

window.light-glass .glass-chip,
.glass-chip {
    background-color: rgba(255, 255, 255, 0.58);
    border: 1px solid rgba(255, 255, 255, 0.85);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.03), inset 0 1px 0 rgba(255, 255, 255, 0.9);
    color: #2e3436;
}

window.light-glass .glass-chip:hover {
    background-color: rgba(255, 255, 255, 0.92);
    border: 1px solid rgba(53, 132, 228, 0.38);
    box-shadow: 0 4px 14px rgba(53, 132, 228, 0.12);
}

window.dark-glass .glass-chip {
    background-color: rgba(42, 50, 64, 0.58);
    border: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.1);
    color: #eeeeec;
}

window.dark-glass .glass-chip:hover {
    background-color: rgba(54, 64, 82, 0.88);
    border: 1px solid rgba(98, 160, 234, 0.45);
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
}

/* --- Linhas de Ação Proposta no Desktop (Action Rows) --- */
.glass-row {
    border-radius: 12px;
    margin-bottom: 4px;
    transition: all 180ms ease;
}

window.light-glass .glass-row,
.glass-row {
    background-color: rgba(255, 255, 255, 0.62);
    border: 1px solid rgba(255, 255, 255, 0.8);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.02);
}

window.dark-glass .glass-row {
    background-color: rgba(34, 40, 52, 0.62);
    border: 1px solid rgba(255, 255, 255, 0.1);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

/* --- Botão Circular de Ícone (Copiar Resposta, etc.) --- */
.glass-icon-btn {
    border-radius: 9999px;
    transition: all 150ms ease;
}

window.light-glass .glass-icon-btn:hover {
    background-color: rgba(0, 0, 0, 0.06);
}

window.dark-glass .glass-icon-btn:hover {
    background-color: rgba(255, 255, 255, 0.12);
}

/* --- Barras de Rolagem Minimalistas --- */
scrollbar trough {
    background-color: transparent;
}

scrollbar slider {
    background-color: alpha(@window_fg_color, 0.2);
    border-radius: 8px;
    min-width: 6px;
    min-height: 6px;
}

scrollbar slider:hover {
    background-color: alpha(@window_fg_color, 0.4);
}
"""

_provider_installed = False


def apply_glass_theme(display: Gdk.Display | None = None) -> None:
    """Aplica o provedor de CSS global para a aplicação."""
    global _provider_installed
    if _provider_installed:
        return

    if display is None:
        display = Gdk.Display.get_default()

    if display is not None:
        provider = Gtk.CssProvider()
        provider.load_from_string(GLASS_CSS)
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        _provider_installed = True


def setup_glass_window(window: Adw.ApplicationWindow) -> None:
    """Configura o efeito glassmorphism na janela e sincroniza tema claro/escuro."""
    apply_glass_theme(window.get_display())
    window.add_css_class("glass-window")

    style_manager = Adw.StyleManager.get_default()

    def sync_color_scheme(*_):
        if style_manager.get_dark():
            window.add_css_class("dark-glass")
            window.remove_css_class("light-glass")
        else:
            window.add_css_class("light-glass")
            window.remove_css_class("dark-glass")

    style_manager.connect("notify::dark", sync_color_scheme)
    sync_color_scheme()
