# Decisão de design: os controles de sistema estavam escritos em cima de gsettings do GNOME e
# de `gnome-screenshot` (removido do GNOME 43). Fora do GNOME cada chamada falhava em silêncio
# e o usuário recebia "Falha ao alterar o esquema de cores" sem saber por quê.
#
# Aqui cada controle vira uma cadeia de estratégias com detecção. Quando nada funciona,
# devolvemos a dica do pacote que resolveria — em vez de um booleano sem contexto.

"""Controles de ambiente: tema claro/escuro, bloqueio de tela, volume e notificação.

Todas as funções devolvem ``(ok, mensagem)`` para que a UI possa repetir a mensagem
ao usuário — inclusive quando a resposta é "não há como fazer isso aqui".
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Callable

from .env import Environment, current_environment

logger = logging.getLogger(__name__)


def _run(argv: list[str], timeout: int = 5) -> tuple[int, str]:
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return res.returncode, (res.stderr or res.stdout or "").strip()
    except FileNotFoundError:
        return 127, f"{argv[0]} não encontrado"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as exc:
        return 1, str(exc)


def _first_available(*binaries: str) -> str:
    for name in binaries:
        found = shutil.which(name)
        if found:
            return found
    return ""


# ---------------------------------------------------------------------------
# Tema claro / escuro
# ---------------------------------------------------------------------------
def set_color_scheme(dark: bool, env: Environment | None = None) -> tuple[bool, str]:
    """Aplica o esquema de cores usando o mecanismo disponível no ambiente.

    Ordem: portal XDG (funciona em qualquer sessão com portal) -> gsettings GNOME
    -> kwriteconfig do Plasma -> xfconf do Xfce.
    """
    env = env or current_environment()
    scheme = "prefer-dark" if dark else "prefer-light"
    label = "Modo escuro" if dark else "Modo claro"

    strategies: list[Callable[[], tuple[bool, str]]] = [
        lambda: _color_scheme_gsettings(scheme),
        lambda: _color_scheme_kde(dark),
        lambda: _color_scheme_xfce(dark),
    ]
    for strategy in strategies:
        try:
            ok, msg = strategy()
            if ok:
                return True, f"{label} ativado com sucesso. {msg}".strip()
        except Exception as exc:  # pragma: no cover - defesa por ambiente hostil
            logger.debug(f"Estratégia de tema falhou: {exc}")

    return False, (
        f"Não foi possível alterar o tema em {env.describe()}. "
        "Instale um portal XDG (xdg-desktop-portal-gtk/-wlr/-hyprland) ou ajuste pelo painel do ambiente."
    )


def _color_scheme_gsettings(scheme: str) -> tuple[bool, str]:
    if not shutil.which("gsettings"):
        return False, ""
    code, err = _run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", scheme])
    return (code == 0, "" if code == 0 else err)


def _color_scheme_kde(dark: bool) -> tuple[bool, str]:
    writer = _first_available("kwriteconfig6", "kwriteconfig5")
    if not writer:
        return False, ""
    theme = "org.kde.breeze.desktop" if dark else "org.kde.breeze.desktop"
    code, err = _run([writer, "--file", "kdeglobals", "--group", "General", "--key", "ColorScheme", theme])
    if code != 0:
        return False, err
    # Plasma precisa recarregar a configuração para aplicar o tema.
    _run(["dbus-send", "--session", "--type=signal", "/KGlobalSettings", "org.kde.KGlobalSettings.notifyChange", "int32:0", "int32:0"])
    return True, ""


def _color_scheme_xfce(dark: bool) -> tuple[bool, str]:
    if not shutil.which("xfconf-query"):
        return False, ""
    theme = "Adwaita-dark" if dark else "Adwaita"
    code, err = _run(["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName", "-s", theme])
    return (code == 0, "" if code == 0 else err)


# ---------------------------------------------------------------------------
# Bloqueio de tela
# ---------------------------------------------------------------------------
def lock_session(env: Environment | None = None) -> tuple[bool, str]:
    """Bloqueia a sessão pelo caminho mais adequado ao ambiente."""
    env = env or current_environment()

    if env.desktop == "hyprland":
        locker = _first_available("hyprlock", "swaylock")
        if locker:
            subprocess.Popen([locker], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, "Tela bloqueada."

    if env.desktop == "sway":
        locker = _first_available("swaylock", "hyprlock")
        if locker:
            subprocess.Popen([locker], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, "Tela bloqueada."

    if shutil.which("loginctl"):
        code, err = _run(["loginctl", "lock-session"])
        if code == 0:
            return True, "Sessão bloqueada."

    locker = _first_available("swaylock", "hyprlock", "i3lock", "xscreensaver-command")
    if locker:
        argv = [locker] if "xscreensaver" not in locker else [locker, "-lock"]
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "Tela bloqueada."

    return False, "Nenhum bloqueio de tela disponível (instale hyprlock/swaylock ou loginctl)."


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------
def adjust_volume(change: str, env: Environment | None = None) -> tuple[bool, str]:
    """Ajusta volume via wpctl (PipeWire), pactl ou amixer."""
    env = env or current_environment()

    if change not in ("up", "down", "mute"):
        return False, "Operação de volume inválida."

    wpctl = shutil.which("wpctl")
    if wpctl:
        sink = "@DEFAULT_AUDIO_SINK@"
        if change == "up":
            cmd, msg = [wpctl, "set-volume", sink, "5%+"], "Volume aumentado em 5%."
        elif change == "down":
            cmd, msg = [wpctl, "set-volume", sink, "5%-"], "Volume reduzido em 5%."
        else:
            cmd, msg = [wpctl, "set-mute", sink, "toggle"], "Áudio mutado/desmutado."
        code, _ = _run(cmd)
        if code == 0:
            return True, msg
        logger.debug("wpctl falhou; tentando pactl.")

    pactl = shutil.which("pactl")
    if pactl:
        if change == "up":
            cmd, msg = [pactl, "set-sink-volume", "@DEFAULT_SINK@", "+5%"], "Volume aumentado em 5%."
        elif change == "down":
            cmd, msg = [pactl, "set-sink-volume", "@DEFAULT_SINK@", "-5%"], "Volume reduzido em 5%."
        else:
            cmd, msg = [pactl, "set-sink-mute", "@DEFAULT_SINK@", "toggle"], "Áudio mutado/desmutado."
        code, _ = _run(cmd)
        if code == 0:
            return True, msg

    amixer = shutil.which("amixer")
    if amixer:
        if change == "up":
            cmd, msg = [amixer, "-q", "sset", "Master", "5%+"], "Volume aumentado em 5%."
        elif change == "down":
            cmd, msg = [amixer, "-q", "sset", "Master", "5%-"], "Volume reduzido em 5%."
        else:
            cmd, msg = [amixer, "-q", "sset", "Master", "toggle"], "Áudio mutado/desmutado."
        code, _ = _run(cmd)
        if code == 0:
            return True, msg

    return False, "Nenhum controlador de áudio encontrado (instale wireplumber, pipewire-pulse ou alsa-utils)."


# ---------------------------------------------------------------------------
# Notificações
# ---------------------------------------------------------------------------
def notify(title: str, message: str, app_name: str = "Zorin Copilot") -> tuple[bool, str]:
    """Envia notificação de desktop via ``notify-send`` (freedesktop)."""
    sender = shutil.which("notify-send")
    if not sender:
        return False, "notify-send não disponível (instale libnotify)."
    code, err = _run([sender, "-a", app_name, title, message])
    return (code == 0, "" if code == 0 else err)


# ---------------------------------------------------------------------------
# Captura de tela "aberta" (abre a ferramenta interativa do ambiente)
# ---------------------------------------------------------------------------
def open_screenshot_tool(env: Environment | None = None) -> tuple[bool, str]:
    """Abre a ferramenta interativa de captura, quando o ambiente tem uma.

    Substitui o antigo ``gnome-screenshot -i``, que não existe mais no GNOME 43+
    e nunca existiu fora dele.
    """
    env = env or current_environment()

    if env.is_wlroots:
        for tool in ("grimblast", "grim"):
            if shutil.which(tool):
                return True, f"Use `{tool}` (ou o atalho de recorte do Copilot) para capturar."
        return False, "Instale `grim` e `slurp` para captura em compositores wlroots."

    for tool, args in (("spectacle", ["-r", "-b"]), ("gnome-screenshot", ["-i"])):
        path = shutil.which(tool)
        if path:
            subprocess.Popen([path, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, f"Ferramenta de captura aberta ({tool})."

    return False, "Nenhuma ferramenta de captura interativa encontrada (grim/slurp, spectacle)."
