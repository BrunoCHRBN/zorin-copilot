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

/* --- Isolamento de Cores e Blindagem contra Temas Externos do SO --- */
@define-color accent_color #15a6f0;
@define-color accent_bg_color #15a6f0;
@define-color accent_fg_color #ffffff;

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

/* --- Unificação e Blindagem de Cores dos Ícones (Monocromático Neutro) --- */
/* Modo Claro: Grafite / Ardósia (#3a4759) | Hover: Azul Profundo (#123354) */
window.light-glass image,
window.light-glass image.symbolic,
window.light-glass .glass-icon-btn,
window.light-glass .glass-icon-btn image,
window.light-glass button.flat,
window.light-glass button.flat image,
window.light-glass headerbar button,
window.light-glass headerbar button image,
window.light-glass headerbar windowcontrols button,
window.light-glass headerbar windowcontrols button image,
window.light-glass entry image,
window.light-glass .glass-chip image,
window.light-glass .welcome-icon,
window.light-glass popover image,
window.light-glass popover button image,
window.light-glass .glass-card image,
window.light-glass .glass-menu-item image,
window.light-glass adwactionrow image {
    color: #3a4759;
}

window.light-glass button.flat:hover,
window.light-glass button.flat:hover image,
window.light-glass .glass-icon-btn:hover,
window.light-glass .glass-icon-btn:hover image,
window.light-glass headerbar button:hover,
window.light-glass headerbar button:hover image,
window.light-glass headerbar windowcontrols button:hover,
window.light-glass headerbar windowcontrols button:hover image,
window.light-glass .glass-chip:hover image,
window.light-glass .glass-menu-item:hover image {
    color: #123354;
}

/* Modo Escuro: Prata / Gelo (#e4ecf5) | Hover: Branco Puro (#ffffff) */
window.dark-glass image,
window.dark-glass image.symbolic,
window.dark-glass .glass-icon-btn,
window.dark-glass .glass-icon-btn image,
window.dark-glass button.flat,
window.dark-glass button.flat image,
window.dark-glass headerbar button,
window.dark-glass headerbar button image,
window.dark-glass headerbar windowcontrols button,
window.dark-glass headerbar windowcontrols button image,
window.dark-glass entry image,
window.dark-glass .glass-chip image,
window.dark-glass .welcome-icon,
window.dark-glass popover image,
window.dark-glass popover button image,
window.dark-glass .glass-card image,
window.dark-glass .glass-menu-item image,
window.dark-glass adwactionrow image {
    color: #e4ecf5;
}

window.dark-glass button.flat:hover,
window.dark-glass button.flat:hover image,
window.dark-glass .glass-icon-btn:hover,
window.dark-glass .glass-icon-btn:hover image,
window.dark-glass headerbar button:hover,
window.dark-glass headerbar button:hover image,
window.dark-glass headerbar windowcontrols button:hover,
window.dark-glass headerbar windowcontrols button:hover image,
window.dark-glass .glass-chip:hover image,
window.dark-glass .glass-menu-item:hover image {
    color: #ffffff;
}

/* Exceções funcionais: Destrutivo (Lixeira / Deletar) */
window.glass-window button.destructive-action,
window.glass-window button.destructive-action image,
window.glass-window .destructive-action image {
    color: #e01b24;
}

/* Botão 'Pedir' e Ações Sugeridas (Sempre Azul Zorin com texto e ícone brancos) */
window.glass-window button.suggested-action,
window.glass-window .glass-submit-btn,
button.suggested-action.glass-submit-btn {
    background-color: #15a6f0;
    background-image: none;
    color: #ffffff;
    border: 1px solid rgba(13, 143, 209, 0.4);
}

window.glass-window button.suggested-action:hover,
window.glass-window .glass-submit-btn:hover,
button.suggested-action.glass-submit-btn:hover {
    background-color: #0d8fd1;
    color: #ffffff;
}

window.glass-window button.suggested-action image,
window.glass-window .glass-submit-btn image {
    color: #ffffff;
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

/* --- Estado final de uma ação já executada (rastro de sucesso/falha) --- */
.glass-row.action-done {
    border-left: 3px solid #33d17a;
}

.glass-row.action-failed {
    border-left: 3px solid #e01b24;
}

window.light-glass .glass-row.action-failed {
    background-color: rgba(224, 27, 36, 0.07);
}

window.dark-glass .glass-row.action-failed {
    background-color: rgba(224, 27, 36, 0.14);
}

/* Ícone de estado colorido explicitamente (não depende das classes de acento) */
.glass-row.action-done image {
    color: #33d17a;
    margin-right: 2px;
}

.glass-row.action-failed image {
    color: #e01b24;
    margin-right: 2px;
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

/* --- Painel Lateral de Conversas (Estilo Gemini) --- */
.sidebar-panel {
    padding: 10px;
    transition: all 200ms ease;
}

window.light-glass .sidebar-panel {
    background-color: rgba(243, 246, 251, 0.94);
    border-right: 1px solid rgba(18, 51, 84, 0.10);
}

window.dark-glass .sidebar-panel {
    background-color: rgba(18, 22, 30, 0.94);
    border-right: 1px solid rgba(255, 255, 255, 0.08);
}

/* Linhas de chat na barra lateral */
.sidebar-chat-row {
    border-radius: 10px;
    padding: 6px 10px;
    margin-bottom: 3px;
    transition: all 150ms ease;
}

window.light-glass .sidebar-chat-row:hover {
    background-color: rgba(18, 51, 84, 0.06);
}

window.dark-glass .sidebar-chat-row:hover {
    background-color: rgba(255, 255, 255, 0.08);
}

window.light-glass .sidebar-chat-row.active {
    background-color: rgba(21, 166, 240, 0.12);
    border-left: 3px solid #15a6f0;
}

window.dark-glass .sidebar-chat-row.active {
    background-color: rgba(98, 160, 234, 0.18);
    border-left: 3px solid #62a0ea;
}

/* --- Bolha de Mensagem do Usuário (Estilo Gemini) --- */
.user-chat-bubble {
    border-radius: 18px 18px 4px 18px;
    padding: 10px 16px;
    margin-bottom: 4px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.03);
}

window.light-glass .user-chat-bubble {
    background-color: rgba(21, 166, 240, 0.09);
    border: 1px solid rgba(21, 166, 240, 0.22);
    color: #123354;
}

window.dark-glass .user-chat-bubble {
    background-color: rgba(21, 166, 240, 0.16);
    border: 1px solid rgba(21, 166, 240, 0.30);
    color: #ffffff;
}

/* --- Card de Resposta do Assistente (Estilo Gemini) --- */
.assistant-message-card {
    border-radius: 18px;
    padding: 14px 18px;
    margin-bottom: 6px;
}

window.light-glass .assistant-message-card {
    background-color: rgba(255, 255, 255, 0.90);
    border: 1px solid rgba(18, 51, 84, 0.09);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.03);
}

window.dark-glass .assistant-message-card {
    background-color: rgba(30, 36, 48, 0.88);
    border: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
}

/* --- Barra de Entrada Inferior (Estilo Gemini) --- */
.prompt-bar-card {
    border-radius: 28px;
    padding: 4px 8px 4px 12px;
    transition: all 180ms ease;
}

window.light-glass .prompt-bar-card {
    background-color: rgba(255, 255, 255, 0.96);
    border: 1px solid rgba(18, 51, 84, 0.14);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.95);
}

window.light-glass .prompt-bar-card:focus-within {
    border: 1px solid #15a6f0;
    box-shadow: 0 0 0 2px rgba(21, 166, 240, 0.25), 0 4px 20px rgba(21, 166, 240, 0.12);
}

window.dark-glass .prompt-bar-card {
    background-color: rgba(32, 38, 50, 0.94);
    border: 1px solid rgba(255, 255, 255, 0.14);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.08);
}

window.dark-glass .prompt-bar-card:focus-within {
    border: 1px solid #62a0ea;
    box-shadow: 0 0 0 2px rgba(98, 160, 234, 0.30), 0 4px 20px rgba(0, 0, 0, 0.4);
}

.prompt-bar-card entry {
    background-color: transparent;
    border: none;
    box-shadow: none;
    padding: 6px 8px;
    font-size: 14px;
}

.disclaimer-caption {
    font-size: 11px;
    opacity: 0.65;
    margin-top: 4px;
    margin-bottom: 6px;
}
"""

_provider_installed = False


def apply_glass_theme(display: Gdk.Display | None = None) -> None:
    """Aplica o provedor de CSS global para a aplicação com prioridade de usuário."""
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
            Gtk.STYLE_PROVIDER_PRIORITY_USER,
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
