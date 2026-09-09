# Decisão de design: em Wayland o compositor é quem posiciona as janelas. `Gtk.Window.move()`
# é no-op e `set_keep_above()` depende de o WM aceitar a dica — o que Hyprland e Sway aceitam
# só se você declarar `windowrule` no config. Resultado: a pílula de voz aparecia onde o
# compositor quisesse.
#
# O jeito correto de ancorar uma superfície em wlroots é o protocolo **layer-shell**
# (wlr-layer-shell), e o binding para GTK4 é o `gtk4-layer-shell`. Ele precisa ser chamado
# **antes** de a janela ser realizada (realized), senão o compositor recusa.
#
# Onde ele não estiver instalado (GNOME não implementa layer-shell; X11 não tem nem
# protocolo) seguimos pelo caminho antigo, sem quebrar nada.

"""Ancoragem de janelas via ``gtk4-layer-shell`` (compositores wlroots).

Degradação explícita: todas as funções devolvem ``False`` quando a biblioteca não
está presente, para quem chama poder decidir o que fazer — aqui não há
``except Exception: pass`` silencioso.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

NAMESPACE: str = "Gtk4LayerShell"
VERSION: str = "1.0"

#: Camadas do protocolo, na ordem do wlr-layer-shell.
_LAYERS: dict[str, int] = {"background": 0, "bottom": 1, "top": 2, "overlay": 3}
_EDGES: dict[str, int] = {"left": 0, "right": 1, "top": 2, "bottom": 3}
_KEYBOARD_MODES: dict[str, int] = {"none": 0, "on_demand": 1, "exclusive": 2}

#: Canto escolhido pelo usuário -> bordas ancoradas. Um eixo sem âncora fica centrado.
_CORNER_ANCHORS: dict[str, tuple[str, ...]] = {
    "top-center": ("top",),
    "top-left": ("top", "left"),
    "top-right": ("top", "right"),
    "bottom-center": ("bottom",),
    "bottom-left": ("bottom", "left"),
    "bottom-right": ("bottom", "right"),
}

_module: Any = None
_loaded: bool = False


def _load() -> Any | None:
    """Importa o módulo uma vez e guarda o resultado (inclusive o fracasso)."""
    global _module, _loaded
    if _loaded:
        return _module
    _loaded = True
    try:
        import gi

        gi.require_version(NAMESPACE, VERSION)
        from gi.repository import Gtk4LayerShell  # type: ignore

        _module = Gtk4LayerShell
    except (ImportError, ValueError) as exc:
        logger.debug("gtk4-layer-shell indisponível: %s", exc)
        _module = None
    return _module


def is_supported() -> bool:
    """A biblioteca existe *e* o compositor fala wlr-layer-shell?"""
    mod = _load()
    if mod is None:
        return False
    # `is_supported()` só apareceu em versões recentes do binding; em versões
    # antigas a presença do módulo já implica suporte ao protocolo.
    check = getattr(mod, "is_supported", None)
    if check is None:
        return True
    try:
        return bool(check())
    except Exception as exc:
        logger.debug("gtk4-layer-shell.is_supported falhou: %s", exc)
        return False


def _enum(group: str, name: str, table: dict[str, int]) -> Any:
    """Resolve `Layer.TOP` sem assumir que o enum existe nessa versão."""
    mod = _load()
    if mod is None:
        raise RuntimeError("gtk4-layer-shell ausente")
    container = getattr(mod, group, None)
    if container is not None:
        member = getattr(container, name.upper(), None)
        if member is not None:
            return member
    return table.get(name.lower(), 0)


def init(window: Any) -> bool:
    """Transforma a janela em superfície de camada. Deve vir antes de `present()`."""
    mod = _load()
    if mod is None:
        return False
    try:
        mod.init_for_window(window)
        return True
    except Exception as exc:
        logger.warning("Falha ao iniciar layer-shell na janela: %s", exc)
        return False


def set_layer(window: Any, layer: str = "overlay") -> bool:
    mod = _load()
    if mod is None:
        return False
    try:
        mod.set_layer(window, _enum("Layer", layer, _LAYERS))
        return True
    except Exception as exc:
        logger.debug("set_layer(%s) falhou: %s", layer, exc)
        return False


def set_anchor(window: Any, edge: str, anchored: bool = True) -> bool:
    mod = _load()
    if mod is None:
        return False
    try:
        mod.set_anchor(window, _enum("Edge", edge, _EDGES), anchored)
        return True
    except Exception as exc:
        logger.debug("set_anchor(%s) falhou: %s", edge, exc)
        return False


def set_margin(window: Any, edge: str, pixels: int) -> bool:
    mod = _load()
    if mod is None:
        return False
    try:
        mod.set_margin(window, _enum("Edge", edge, _EDGES), int(pixels))
        return True
    except Exception as exc:
        logger.debug("set_margin(%s) falhou: %s", edge, exc)
        return False


def set_keyboard_mode(window: Any, mode: str = "none") -> bool:
    """``none`` deixa o teclado com o app focado — o certo para uma pílula de voz."""
    mod = _load()
    if mod is None:
        return False
    try:
        mod.set_keyboard_mode(window, _enum("KeyboardMode", mode, _KEYBOARD_MODES))
        return True
    except Exception as exc:
        logger.debug("set_keyboard_mode(%s) falhou: %s", mode, exc)
        return False


def set_exclusive_zone(window: Any, pixels: int) -> bool:
    """Reserva espaço para a superfície (0 = não empurra outras janelas)."""
    mod = _load()
    if mod is None:
        return False
    try:
        mod.set_exclusive_zone(window, int(pixels))
        return True
    except Exception as exc:
        logger.debug("set_exclusive_zone(%s) falhou: %s", pixels, exc)
        return False


def anchors_for_corner(corner: str) -> tuple[str, ...]:
    """Bordas que devem ser ancoradas para o canto pedido (padrão: top-center)."""
    return _CORNER_ANCHORS.get(corner, ("top",))


def anchor_corner(window: Any, corner: str, margin: int = 12, layer: str = "overlay") -> bool:
    """Ancora a janela no canto, já configurando camada e teclado.

    Falso quando não há layer-shell — aí o chamador segue com `move()`/`keep_above`.
    """
    if not is_supported():
        return False
    if not init(window):
        return False

    set_layer(window, layer)
    set_keyboard_mode(window, "none")
    set_exclusive_zone(window, 0)

    anchors = anchors_for_corner(corner)
    # Zera tudo primeiro: sem isso, reposicionar depois deixaria âncoras velhas.
    for edge in _EDGES:
        set_anchor(window, edge, edge in anchors)
    for edge in _EDGES:
        set_margin(window, edge, margin if edge in anchors else 0)

    logger.info("Pílula ancorada via layer-shell (%s).", ", ".join(anchors) or "centrado")
    return True


def reanchor(window: Any, corner: str, margin: int = 12) -> bool:
    """Reposiciona uma janela que já é superfície de camada."""
    mod = _load()
    if mod is None or not getattr(mod, "is_layer_window", lambda _w: False)(window):
        return False
    anchors = anchors_for_corner(corner)
    for edge in _EDGES:
        set_anchor(window, edge, edge in anchors)
    for edge in _EDGES:
        set_margin(window, edge, margin if edge in anchors else 0)
    return True


__all__ = [
    "NAMESPACE",
    "VERSION",
    "anchor_corner",
    "anchors_for_corner",
    "init",
    "is_supported",
    "reanchor",
    "set_anchor",
    "set_exclusive_zone",
    "set_keyboard_mode",
    "set_layer",
    "set_margin",
]
