# Decisão de design: Estilização Glassmorphism em GTK4 / Libadwaita com transparência acrílica,
# opacidade equilibrada em 96% para impedir vazamento de textos de janelas em segundo plano,
# specular highlights (bordas translúcidas e reflexos internos), alto contraste tipográfico e
# sincronização dinâmica em tempo real entre modo claro (frosted light) e modo escuro (smoked glass).

"""Estilos visuais e temas Glassmorphism para a interface do Zorin Copilot."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402


GLASS_CSS = """
/* ====================================================================
   Zorin Copilot - Calibrated Frosted Glassmorphism Stylesheet (96% Opacity)
   ==================================================================== */

/* --- Janela Principal Translúcida (Equilíbrio entre vidro e legibilidade) --- */
window.glass-window,
window.glass-window.background,
window.glass-window > contents {
    background-color: alpha(@window_bg_color, 0.96);
}

window.light-glass,
window.light-glass.background,
window.light-glass > contents {
    background-color: rgba(245, 247, 251, 0.96);
}

window.dark-glass,
window.dark-glass.background,
window.dark-glass > contents {
    background-color: rgba(20, 24, 33, 0.96);
}

/* --- HeaderBar Nativa com Alto Contraste Integrada ao Vidro --- */
window.glass-window headerbar {
    background-color: transparent;
    box-shadow: none;
}

window.light-glass headerbar {
    border-bottom: 1px solid rgba(18, 51, 84, 0.08);
}

window.light-glass headerbar windowtitle .title {
    color: #123354;
    font-weight: 600;
}

window.light-glass headerbar windowtitle .subtitle {
    color: #4a607a;
}

window.dark-glass headerbar {
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

window.dark-glass headerbar windowtitle .title {
    color: #ffffff;
    font-weight: 600;
}

window.dark-glass headerbar windowtitle .subtitle {
    color: #9aa7b5;
}

/* --- Containers Transparentes Scoped Apenas na Janela Principal --- */
window.glass-window > contents scrolledwindow,
window.glass-window > contents scrolledwindow > viewport {
    background-color: transparent;
}

/* Protege o diálogo de preferências contra transparências indesejadas */
preferencesdialog,
preferencesdialog > contents {
    background-color: @dialog_bg_color;
}

/* --- Indicador de Modelo / Status no HeaderBar (Glass Pill) --- */
.glass-pill {
    border-radius: 16px;
    padding: 3px 12px;
    transition: all 180ms ease;
}

window.light-glass .glass-pill {
    background-color: rgba(18, 51, 84, 0.06);
    border: 1px solid rgba(18, 51, 84, 0.12);
    color: #123354;
    font-weight: 500;
}

window.light-glass .glass-pill:hover {
    background-color: rgba(18, 51, 84, 0.12);
    border-color: rgba(18, 51, 84, 0.22);
}

window.dark-glass .glass-pill {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    color: #e4ecf5;
    font-weight: 500;
}

window.dark-glass .glass-pill:hover {
    background-color: rgba(255, 255, 255, 0.16);
    border-color: rgba(255, 255, 255, 0.25);
}

/* --- Campo de Entrada Glassmorphic (Search Entry) --- */
.glass-entry {
    border-radius: 14px;
    padding: 8px 14px;
    font-size: 14px;
    transition: all 180ms ease;
}

window.light-glass .glass-entry,
.glass-entry {
    background-color: rgba(255, 255, 255, 0.92);
    border: 1px solid rgba(18, 51, 84, 0.14);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.03), inset 0 1px 0 rgba(255, 255, 255, 0.95);
    color: #123354;
}

window.light-glass .glass-entry:focus-within {
    background-color: #ffffff;
    border: 1px solid #15a6f0;
    box-shadow: 0 0 0 2px rgba(21, 166, 240, 0.28), 0 2px 10px rgba(21, 166, 240, 0.12);
    color: #123354;
}

window.dark-glass .glass-entry {
    background-color: rgba(30, 36, 48, 0.88);
    border: 1px solid rgba(255, 255, 255, 0.14);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.08);
    color: #f6f6f6;
}

window.dark-glass .glass-entry:focus-within {
    background-color: rgba(36, 44, 58, 0.96);
    border: 1px solid #62a0ea;
    box-shadow: 0 0 0 2px rgba(98, 160, 234, 0.32), 0 2px 10px rgba(0, 0, 0, 0.4);
    color: #ffffff;
}

/* --- Botão Principal 'Pedir' (Sempre Azul Zorin com Alto Destaque) --- */
.glass-submit-btn {
    background-color: #15a6f0;
    color: #ffffff;
    border: 1px solid rgba(13, 143, 209, 0.4);
    border-radius: 12px;
    padding: 8px 20px;
    font-weight: 600;
    box-shadow: 0 2px 8px rgba(21, 166, 240, 0.35);
    transition: all 180ms ease;
}

.glass-submit-btn:hover {
    background-color: #0d8fd1;
    box-shadow: 0 4px 12px rgba(21, 166, 240, 0.45);
}

.glass-submit-btn:active {
    background-color: #0c7eb9;
}

/* --- Títulos da Tela Inicial --- */
window.light-glass .welcome-title {
    color: #123354;
}

window.light-glass .welcome-subtitle {
    color: #4a607a;
}

window.dark-glass .welcome-title {
    color: #ffffff;
}

window.dark-glass .welcome-subtitle {
    color: #9aa7b5;
}

/* --- Cards de Vidro (Resposta, Prévia de App, Detalhes) --- */
.glass-card {
    border-radius: 14px;
    transition: all 200ms ease;
}

window.light-glass .glass-card,
.glass-card {
    background-color: rgba(255, 255, 255, 0.88);
    border: 1px solid rgba(18, 51, 84, 0.09);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.95);
}

window.dark-glass .glass-card {
    background-color: rgba(34, 40, 52, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.12);
}

/* --- Botão Compacto de Abrir Agora (App Preview) --- */
.glass-launch-btn {
    border-radius: 12px;
    padding: 3px 10px;
    transition: all 150ms ease;
}

window.light-glass .glass-launch-btn,
.glass-launch-btn {
    background-color: rgba(21, 166, 240, 0.12);
    border: 1px solid rgba(21, 166, 240, 0.30);
    color: #0d8fd1;
}

window.light-glass .glass-launch-btn:hover {
    background-color: rgba(21, 166, 240, 0.22);
    border-color: rgba(21, 166, 240, 0.50);
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

/* --- Botão de Fixar Tópico no Header do Card (Pin) --- */
.glass-pin-btn {
    border-radius: 12px;
    padding: 3px 10px;
    transition: all 180ms ease;
}

window.light-glass .glass-pin-btn {
    background-color: rgba(18, 51, 84, 0.06);
    border: 1px solid rgba(18, 51, 84, 0.12);
    color: #123354;
}

window.light-glass .glass-pin-btn:hover {
    background-color: rgba(18, 51, 84, 0.12);
    border-color: rgba(18, 51, 84, 0.22);
}

window.dark-glass .glass-pin-btn {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.14);
    color: #e4ecf5;
}

window.dark-glass .glass-pin-btn:hover {
    background-color: rgba(255, 255, 255, 0.16);
    border-color: rgba(255, 255, 255, 0.25);
}

/* --- Chips de Sugestões Rápidas da Tela Inicial --- */
.glass-chip {
    border-radius: 20px;
    padding: 8px 16px;
    transition: all 180ms ease;
}

window.light-glass .glass-chip,
.glass-chip {
    background-color: rgba(255, 255, 255, 0.82);
    border: 1px solid rgba(18, 51, 84, 0.10);
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.03), inset 0 1px 0 rgba(255, 255, 255, 0.95);
    color: #123354;
    font-weight: 500;
}

window.light-glass .glass-chip:hover {
    background-color: #ffffff;
    border: 1px solid #15a6f0;
    box-shadow: 0 4px 12px rgba(21, 166, 240, 0.18);
    color: #0d8fd1;
}

window.dark-glass .glass-chip {
    background-color: rgba(42, 50, 64, 0.78);
    border: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.1);
    color: #e4ecf5;
}

window.dark-glass .glass-chip:hover {
    background-color: rgba(54, 64, 82, 0.95);
    border: 1px solid #62a0ea;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
    color: #ffffff;
}

/* --- Linhas de Ação Proposta no Desktop (Action Rows) --- */
.glass-row {
    border-radius: 12px;
    margin-bottom: 4px;
    transition: all 180ms ease;
}

window.light-glass .glass-row,
.glass-row {
    background-color: rgba(255, 255, 255, 0.84);
    border: 1px solid rgba(18, 51, 84, 0.08);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.02);
}

window.dark-glass .glass-row {
    background-color: rgba(34, 40, 52, 0.82);
    border: 1px solid rgba(255, 255, 255, 0.1);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

/* --- Botão Circular de Ícone (Copiar Resposta, Configurações, etc.) --- */
.glass-icon-btn {
    border-radius: 9999px;
    transition: all 150ms ease;
}

window.light-glass .glass-icon-btn:hover {
    background-color: rgba(18, 51, 84, 0.08);
}

window.dark-glass .glass-icon-btn:hover {
    background-color: rgba(255, 255, 255, 0.14);
}

/* --- Barras de Rolagem Minimalistas --- */
scrollbar trough {
    background-color: transparent;
}

scrollbar slider {
    background-color: alpha(@window_fg_color, 0.25);
    border-radius: 8px;
    min-width: 6px;
    min-height: 6px;
}

scrollbar slider:hover {
    background-color: alpha(@window_fg_color, 0.45);
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
