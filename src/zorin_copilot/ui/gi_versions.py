# Decisão de design: o projeto usa `Gtk.FileDialog` (GTK 4.10), `Adw.ToolbarView`/
# `Adw.SwitchRow` (libadwaita 1.4) e `Adw.PreferencesDialog` (1.5). Os módulos, porém,
# só pediam `gi.require_version("Gtk", "4.0")` — o PyGObject aceita, e aí o código
# estoura com `AttributeError: 'gi.repository.Gtk' object has no attribute 'FileDialog'`
# (ou derruba o processo) numa máquina antiga.
#
# O detalhe não óbvio: **o namespace do GI não acompanha a versão do toolkit.**
# GTK 4.6 e GTK 4.18 publicam o mesmo `Gtk-4.0.typelib`, e libadwaita 1.1 e 1.7
# publicam o mesmo `Adw-1.typelib`. Ou seja, `gi.require_version("Gtk", "4.10")`
# não existe e nunca vai existir — ele levanta ValueError até no GTK mais recente.
# A versão real só é conhecida depois de carregar: `Gtk.get_minor_version()` e
# `Adw.MINOR_VERSION`. É isso que checamos aqui.
#
# Concentrar isso num só lugar troca um `AttributeError` opaco (ou um segfault) por
# uma mensagem que diz o que instalar.

"""Pinagem e verificação das versões mínimas de GTK4 / libadwaita.

Uso, no topo de qualquer módulo que precise de Gtk/Adw::

    from .gi_versions import require_gtk4   # (ou ..gi_versions, dentro de ui/widgets/)
    require_gtk4()
    from gi.repository import Adw, Gtk

A chamada é idempotente e barata depois da primeira (tudo fica em cache).
"""

from __future__ import annotations

import logging
import os

import gi

logger = logging.getLogger(__name__)

#: Versões mínimas de *runtime* exigidas pelo código.
#: Gtk.FileDialog -> 4.10; Adw.PreferencesDialog -> 1.5.
MIN_GTK: tuple[int, int] = (4, 10)
MIN_ADW: tuple[int, int] = (1, 5)

#: Versões de *namespace* do GI — congeladas pela API major, não mudam com o toolkit.
_GTK_NAMESPACE: str = "4.0"
_ADW_NAMESPACE: str = "1"
_GDK_NAMESPACE: str = "4.0"
_PANGO_NAMESPACE: str = "1.0"

#: Escape hatch para desenvolvedores presos num LTS antigo (ex.: Ubuntu 22.04 com
#: GTK 4.6). Não faz o código funcionar — troca o erro fatal por um aviso e segue.
#: Nunca deve ser usado em produção.
_FALLBACK_ENV: str = "ZORIN_COPILOT_ALLOW_OLD_TOOLKIT"

#: Cache: (gtk, adw) já resolvidos, ou None se ainda não foram.
_resolved: dict[str, tuple[int, ...] | None] = {"Gtk": None, "Adw": None}


class ToolkitTooOld(RuntimeError):
    """O sistema não expõe Gtk/Adw na versão mínima exigida."""

    def __init__(self, message: str, missing: list[tuple[str, str, str]] | None = None):
        super().__init__(message)
        self.missing = missing or []


def _namespace_available(namespace: str, version: str) -> bool:
    """O typelib existe em disco? Evita um ValueError feio antes do import."""
    try:
        versions = gi.Repository.get_default().enumerate_versions(namespace) or []
    except Exception:  # pragma: no cover - PyGObject muito antigo
        return True  # deixa o require_version dar a mensagem dele
    if not versions:
        return False
    return version in versions


def _load_gtk() -> tuple[int, ...]:
    gi.require_version("Gtk", _GTK_NAMESPACE)
    from gi.repository import Gtk

    return (
        int(Gtk.get_major_version()),
        int(Gtk.get_minor_version()),
        int(Gtk.get_micro_version()),
    )


def _load_adw() -> tuple[int, ...]:
    gi.require_version("Adw", _ADW_NAMESPACE)
    from gi.repository import Adw

    # Adw expõe as constantes desde 1.0; o getattr cobre builds exóticos.
    return (
        int(getattr(Adw, "MAJOR_VERSION", 1)),
        int(getattr(Adw, "MINOR_VERSION", 0)),
        int(getattr(Adw, "MICRO_VERSION", 0)),
    )


def _install_hint() -> str:
    """Comando de instalação para a distro detectada, ou dica genérica."""
    try:
        from ..core.desktop.env import current_environment

        env = current_environment()
    except Exception:
        env = None

    manager = getattr(env, "package_manager", "") if env else ""
    if manager == "pacman":
        return "  sudo pacman -S gtk4 libadwaita python-gobject"
    if manager == "apt":
        return (
            "  GTK 4.10+ exige Ubuntu 24.04 / Debian 13 ou mais novo.\n"
            "  sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-1"
        )
    if manager == "dnf":
        return "  sudo dnf install gtk4 libadwaita python3-gobject"
    return "  Instale gtk4 (>= 4.10), libadwaita (>= 1.5) e as typelibs Gir correspondentes."


def _allow_fallback() -> bool:
    return os.environ.get(_FALLBACK_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _format(missing: list[tuple[str, str, str]]) -> str:
    lines = ["Zorin Copilot precisa de um toolkit mais novo do que o deste sistema:"]
    for namespace, wanted, found in missing:
        lines.append(f"  {namespace}: instalado {found}, necessário {wanted}+")
    lines += ["", "Como resolver:", _install_hint()]
    return "\n".join(lines)


def require_gtk4(require_adw: bool = True) -> None:
    """Carrega Gtk (e Adw) conferindo a versão mínima de runtime.

    Levanta :class:`ToolkitTooOld` quando o sistema é antigo — a menos que
    ``ZORIN_COPILOT_ALLOW_OLD_TOOLKIT=1`` esteja no ambiente, caso em que apenas
    avisa e segue (a UI pode quebrar depois; a escolha é de quem está depurando).
    """
    wanted: list[tuple[str, tuple[int, int], str, str]] = [
        ("Gtk", MIN_GTK, _GTK_NAMESPACE, "Gtk"),
    ]
    if require_adw:
        wanted.append(("Adw", MIN_ADW, _ADW_NAMESPACE, ""))

    missing: list[tuple[str, str, str]] = []
    for key, minimum, namespace, _ in wanted:
        if _resolved[key] is None:
            if not _namespace_available(key, namespace):
                missing.append((key, ".".join(map(str, minimum)), "nenhuma"))
                continue
            try:
                _resolved[key] = _load_gtk() if key == "Gtk" else _load_adw()
            except (ImportError, ValueError) as exc:
                missing.append((key, ".".join(map(str, minimum)), f"indisponível ({exc})"))
                continue
        found = _resolved[key]
        if found is None:
            continue
        if found[:2] < minimum:
            missing.append((key, ".".join(map(str, minimum)), ".".join(map(str, found))))

    if not missing:
        return

    if _allow_fallback():
        logger.warning(
            "%s=1 — ignorando o requisito mínimo de toolkit; a interface pode falhar:\n%s",
            _FALLBACK_ENV,
            _format(missing),
        )
        return

    raise ToolkitTooOld(
        _format(missing) + f"\n\nPara forçar mesmo assim (desenvolvimento): {_FALLBACK_ENV}=1",
        missing,
    )


def require_gdk() -> None:
    """Fixa Gdk4 e Pango — namespaces congelados, sem requisito de runtime próprio."""
    gi.require_version("Gdk", _GDK_NAMESPACE)
    gi.require_version("Pango", _PANGO_NAMESPACE)


def toolkit_report() -> dict[str, object]:
    """Estado do toolkit para o `copilot doctor`. Nunca levanta."""
    details: dict[str, dict[str, object]] = {}
    ok = True
    for key, minimum in (("Gtk", MIN_GTK), ("Adw", MIN_ADW)):
        found: tuple[int, ...] | None = _resolved[key]
        if found is None:
            try:
                found = _load_gtk() if key == "Gtk" else _load_adw()
                _resolved[key] = found
            except Exception as exc:
                details[key] = {
                    "found": f"indisponível ({exc.__class__.__name__})",
                    "required": ".".join(map(str, minimum)),
                    "ok": False,
                }
                ok = False
                continue
        good = bool(found) and found[:2] >= minimum
        details[key] = {
            "found": ".".join(map(str, found)) if found else "—",
            "required": ".".join(map(str, minimum)),
            "ok": good,
        }
        ok = ok and good
    return {"ok": ok, "details": details}


__all__ = [
    "MIN_GTK",
    "MIN_ADW",
    "ToolkitTooOld",
    "require_gtk4",
    "require_gdk",
    "toolkit_report",
]
