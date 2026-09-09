# Decisão de design: atalho global é a única forma de invocar o Copilot sem mouse, e era
# 100% dependente do GNOME Settings Daemon. Em Hyprland/Sway/KDE o registro antigo
# simplesmente não acontecia — e o app jurava que estava tudo registrado.
#
# Aqui cada compositor ganha um backend com o mesmo contrato. O formato de acelerador
# muda entre eles (GTK "<Super>c", Hyprland "SUPER, C", Sway "Mod4+Shift+s", KDE "Meta+S"),
# então a conversão vive numa função só, não espalhada.

"""Backends de atalho de teclado global por ambiente desktop.

Cada backend responde pelo mesmo contrato — :meth:`ShortcutBackend.register` recebe
um identificador de slot ("hud", "crop", "voice"), um acelerador GTK e o comando a
executar.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .env import Environment, current_environment

logger = logging.getLogger(__name__)

#: Slots de atalho que o Copilot registra no sistema.
SLOT_HUD: Final = "hud"
SLOT_CROP: Final = "crop"
SLOT_VOICE: Final = "voice"

_MODIFIER_ALIASES: Final[dict[str, str]] = {
    "super": "super",
    "meta": "super",
    "mod4": "super",
    "primary": "ctrl",
    "control": "ctrl",
    "ctrl": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "mod1": "alt",
}


def parse_gtk_accelerator(accelerator: str) -> tuple[list[str], str]:
    """Converte um acelerador GTK (``"<Super><Shift>s"``) em (modificadores, tecla).

    Devolve modificadores normalizados ("ctrl", "alt", "shift", "super") e a tecla
    final em minúsculas. Aceleradores malformados devolvem lista vazia — os
    backends recusam o registro em vez de gravar algo quebrado.
    """
    if not accelerator:
        return [], ""

    tokens = [a or b for a, b in re.findall(r"<([^<>]+)>|([^<>]+)", accelerator)]
    tokens = [t.strip() for t in tokens if t.strip()]
    if not tokens:
        return [], ""

    key = tokens[-1].lower()
    modifiers: list[str] = []
    for token in tokens[:-1]:
        mod = _MODIFIER_ALIASES.get(token.lower())
        if mod and mod not in modifiers:
            modifiers.append(mod)
    return modifiers, key


def to_hyprland_binding(accelerator: str) -> str:
    """``"<Super><Shift>s"`` -> ``"SUPER SHIFT, s"``."""
    modifiers, key = parse_gtk_accelerator(accelerator)
    if not key:
        return ""
    mods = " ".join(m.upper() for m in modifiers)
    return f"{mods}, {key}" if mods else f", {key}"


def to_sway_binding(accelerator: str) -> str:
    """``"<Super><Shift>s"`` -> ``"Mod4+Shift+s"``."""
    modifiers, key = parse_gtk_accelerator(accelerator)
    if not key:
        return ""
    names = {"super": "Mod4", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift"}
    mods = "+".join(names.get(m, m.capitalize()) for m in modifiers)
    return f"{mods}+{key}" if mods else key


def to_kde_binding(accelerator: str) -> str:
    """``"<Super><Shift>s"`` -> ``"Meta+Shift+S"``."""
    modifiers, key = parse_gtk_accelerator(accelerator)
    if not key:
        return ""
    names = {"super": "Meta", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift"}
    mods = "+".join(names.get(m, m.capitalize()) for m in modifiers)
    return f"{mods}+{key.upper()}" if mods else key.upper()


def resolve_binary(flag: str = "--toggle") -> str:
    """Localiza o executável do Copilot, preferindo o venv do pacote.

    Mantida aqui (e não em ``core.shortcuts``) porque os backends precisam dela e
    isso evita import circular entre as duas camadas.
    """
    venv_bin = Path.home() / ".local/share/zorin-copilot/venv/bin/zorin-copilot"
    if venv_bin.exists():
        return f"{venv_bin} {flag}"

    local_bin = Path.home() / ".local/bin/zorin-copilot"
    if local_bin.exists():
        return f"{local_bin} {flag}"

    which_bin = shutil.which("zorin-copilot")
    if which_bin:
        return f"{which_bin} {flag}"

    return f"zorin-copilot {flag}"


@dataclass
class ShortcutResult:
    """Resultado de um registro de atalho, com explicação utilizável na UI."""

    ok: bool
    message: str = ""
    #: Caminho do arquivo tocado, quando o backend grava em disco.
    config_path: str = ""

    def __bool__(self) -> bool:
        return self.ok


def _slot_label(slot: str) -> str:
    return {
        SLOT_HUD: "Zorin Copilot",
        SLOT_CROP: "Zorin Copilot - Recorte",
        SLOT_VOICE: "Zorin Copilot - Voz",
    }.get(slot, f"Zorin Copilot - {slot}")


class ShortcutBackend(ABC):
    """Contrato comum dos backends de atalho global."""

    #: Nome curto exibido em diagnóstico e logs.
    name: str = "base"

    #: O backend sobrevive a um reboot?
    persists: bool = True

    def __init__(self, env: Environment | None = None):
        self.env = env or current_environment()

    @abstractmethod
    def is_supported(self) -> bool:
        """O ambiente atual oferece os mecanismos que este backend precisa?"""

    @abstractmethod
    def register(self, slot: str, accelerator: str, command: str) -> ShortcutResult:
        """Registra (ou atualiza) o atalho do slot informado."""

    @abstractmethod
    def unregister(self, slot: str) -> ShortcutResult:
        """Remove o atalho do slot informado."""

    def is_registered(self, slot: str) -> bool:
        """Consulta booleana; a implementação padrão é conservadora."""
        return False

    def hint(self) -> str:
        """Dica mostrada quando o backend não está disponível."""
        return ""


class GnomeShortcutBackend(ShortcutBackend):
    """Atalhos via ``org.gnome.settings-daemon.plugins.media-keys``."""

    name = "gnome-media-keys"

    MEDIA_KEYS_SCHEMA: Final = "org.gnome.settings-daemon.plugins.media-keys"
    CUSTOM_KEY_SCHEMA: Final = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"

    _PATHS: Final[dict[str, str]] = {
        SLOT_HUD: "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot/",
        SLOT_CROP: "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot-crop/",
        SLOT_VOICE: "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/zorin-copilot-voice/",
    }

    @staticmethod
    def _gio():
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        return Gio

    def _schema_exists(self) -> bool:
        try:
            Gio = self._gio()
            source = Gio.SettingsSchemaSource.get_default()
            return source.lookup(self.MEDIA_KEYS_SCHEMA, True) is not None
        except Exception as exc:
            logger.warning(f"Não foi possível verificar o schema de atalhos do GNOME: {exc}")
            return False

    def is_supported(self) -> bool:
        return self.env.is_gnome and self._schema_exists()

    def _apply(self, path: str, name: str, command: str, binding: str) -> ShortcutResult:
        if not self._schema_exists():
            return ShortcutResult(False, "Schema de media-keys do GNOME indisponível.")
        try:
            Gio = self._gio()
            settings = Gio.Settings.new(self.MEDIA_KEYS_SCHEMA)
            existing = list(settings.get_strv("custom-keybindings"))
            if path not in existing:
                existing.append(path)
                settings.set_strv("custom-keybindings", existing)

            custom = Gio.Settings.new_with_path(self.CUSTOM_KEY_SCHEMA, path)
            custom.set_string("name", name)
            custom.set_string("command", command)
            custom.set_string("binding", binding)
            Gio.Settings.sync()
            return ShortcutResult(True, f"Atalho '{name}' registrado: {binding}")
        except Exception as exc:
            logger.error(f"Erro ao registrar atalho GNOME '{name}': {exc}")
            return ShortcutResult(False, f"Falha ao registrar no GNOME: {exc}")

    def register(self, slot: str, accelerator: str, command: str) -> ShortcutResult:
        path = self._PATHS.get(slot)
        if not path:
            return ShortcutResult(False, f"Slot desconhecido: {slot}")
        return self._apply(path, _slot_label(slot), command, accelerator)

    def unregister(self, slot: str) -> ShortcutResult:
        path = self._PATHS.get(slot)
        if not path:
            return ShortcutResult(False, f"Slot desconhecido: {slot}")
        if not self._schema_exists():
            return ShortcutResult(False, "Schema de media-keys do GNOME indisponível.")
        try:
            Gio = self._gio()
            settings = Gio.Settings.new(self.MEDIA_KEYS_SCHEMA)
            existing = [p for p in settings.get_strv("custom-keybindings") if p != path]
            settings.set_strv("custom-keybindings", existing)

            custom = Gio.Settings.new_with_path(self.CUSTOM_KEY_SCHEMA, path)
            for key in ("name", "command", "binding"):
                custom.set_string(key, "")
            Gio.Settings.sync()
            return ShortcutResult(True, "Atalho removido do GNOME.")
        except Exception as exc:
            return ShortcutResult(False, f"Falha ao remover atalho do GNOME: {exc}")

    def is_registered(self, slot: str) -> bool:
        path = self._PATHS.get(slot)
        if not path or not self._schema_exists():
            return False
        try:
            settings = self._gio().Settings.new(self.MEDIA_KEYS_SCHEMA)
            return path in settings.get_strv("custom-keybindings")
        except Exception:
            return False


class _ConfigFileBackend(ShortcutBackend, ABC):
    """Base para compositores configurados por arquivo texto (Hyprland, Sway).

    O padrão é o mesmo nos dois: aplicar em tempo real (para funcionar agora) e
    gravar um snippet separado incluído pelo config principal do usuário.
    """

    #: Nome do snippet gravado no diretório de configuração do compositor.
    snippet_name: str = "zorin-copilot.conf"

    def config_dir(self) -> Path:
        raise NotImplementedError

    def main_config(self) -> Path:
        raise NotImplementedError

    def directive(self, slot: str, accelerator: str, command: str) -> str:
        raise NotImplementedError

    def apply_runtime(self, directive: str) -> bool:
        """Aplica sem reiniciar o compositor. Opcional — nem sempre existe."""
        return False

    def header(self) -> str:
        return "# Gerado pelo Zorin Copilot — edite à vontade, este bloco é seu.\n"

    def snippet_path(self) -> Path:
        return self.config_dir() / self.snippet_name

    def register(self, slot: str, accelerator: str, command: str) -> ShortcutResult:
        directive = self.directive(slot, accelerator, command)
        if not directive:
            return ShortcutResult(False, f"Acelerador inválido para {self.name}: {accelerator!r}")

        path = self.snippet_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(self._rewrite_snippet(existing, slot, directive), encoding="utf-8")
        except OSError as exc:
            return ShortcutResult(False, f"Não foi possível gravar {path}: {exc}")

        applied = self.apply_runtime(directive)
        sourced, source_msg = self._ensure_sourced(path)

        if not sourced:
            return ShortcutResult(True, f"Snippet gravado em {path}. {source_msg}", config_path=str(path))
        suffix = " e aplicado na sessão atual." if applied else "; recarregue o compositor."
        return ShortcutResult(True, f"Atalho registrado em {path}{suffix}", config_path=str(path))

    def _begin_marker(self, slot: str) -> str:
        return f"# >>> zorin-copilot:{slot}"

    def _end_marker(self, slot: str) -> str:
        return f"# <<< zorin-copilot:{slot}"

    def _rewrite_snippet(self, existing: str, slot: str, directive: str) -> str:
        """Substitui apenas o bloco do slot, preservando o resto do arquivo."""
        begin, end = self._begin_marker(slot), self._end_marker(slot)
        pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
        block = f"{begin}\n{directive}\n{end}\n"
        if re.search(pattern, existing):
            return re.sub(pattern, block, existing)
        sep = "" if not existing or existing.endswith("\n") else "\n"
        prefix = "" if "zorin-copilot" in existing else self.header()
        return f"{existing}{sep}{prefix}{block}"

    def _source_line(self, snippet: Path) -> str:
        raise NotImplementedError

    def _ensure_sourced(self, snippet: Path) -> tuple[bool, str]:
        """Delega para :func:`ensure_snippet_sourced`.

        Atalhos e autostart gravam no *mesmo* snippet. A inclusão no config
        principal tem de ser garantida pelos dois caminhos, por isso a regra
        vive numa função só em vez de morar dentro desta classe.
        """
        return ensure_snippet_sourced(self.env.desktop, snippet)

    def unregister(self, slot: str) -> ShortcutResult:
        path = self.snippet_path()
        if not path.exists():
            return ShortcutResult(True, "Nada a remover.")
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ShortcutResult(False, str(exc))

        pattern = re.compile(
            re.escape(self._begin_marker(slot)) + r".*?" + re.escape(self._end_marker(slot)) + r"\n?",
            re.DOTALL,
        )
        path.write_text(re.sub(pattern, "", content), encoding="utf-8")
        return ShortcutResult(True, f"Bloco '{slot}' removido de {path}. Recarregue o compositor.")

    def is_registered(self, slot: str) -> bool:
        path = self.snippet_path()
        return path.exists() and self._begin_marker(slot) in path.read_text(encoding="utf-8", errors="ignore")


class HyprlandShortcutBackend(_ConfigFileBackend):
    """Atalhos via ``bind =`` no Hyprland, aplicados com ``hyprctl keyword``."""

    name = "hyprland"

    def config_dir(self) -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        return Path(base) / "hypr"

    def main_config(self) -> Path:
        return self.config_dir() / "hyprland.conf"

    def is_supported(self) -> bool:
        return self.env.desktop == "hyprland"

    def directive(self, slot: str, accelerator: str, command: str) -> str:
        binding = to_hyprland_binding(accelerator)
        return f"bind = {binding}, exec, {command}" if binding else ""

    def _source_line(self, snippet: Path) -> str:
        return f"source = {snippet}"

    def apply_runtime(self, directive: str) -> bool:
        if not self.env.has("hyprctl"):
            return False
        # `hyprctl keyword bind` recebe exatamente o argumento do `bind =` do arquivo.
        payload = directive.split("=", 1)[1].strip()
        try:
            res = subprocess.run(
                ["hyprctl", "keyword", "bind", payload],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return res.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug(f"hyprctl keyword falhou: {exc}")
            return False

    def hint(self) -> str:
        return "Confirme que `hyprctl` está no PATH ou registre o atalho manualmente no hyprland.conf."


class SwayShortcutBackend(_ConfigFileBackend):
    """Atalhos via ``bindsym`` no Sway, aplicados com ``swaymsg``."""

    name = "sway"

    def config_dir(self) -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        return Path(base) / "sway"

    def main_config(self) -> Path:
        return self.config_dir() / "config"

    def is_supported(self) -> bool:
        return self.env.desktop == "sway"

    def directive(self, slot: str, accelerator: str, command: str) -> str:
        binding = to_sway_binding(accelerator)
        return f"bindsym {binding} exec {command}" if binding else ""

    def _source_line(self, snippet: Path) -> str:
        return f"include {snippet}"

    def apply_runtime(self, directive: str) -> bool:
        if not self.env.has("swaymsg"):
            return False
        try:
            res = subprocess.run(["swaymsg", directive], capture_output=True, text=True, timeout=5, check=False)
            return res.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug(f"swaymsg falhou: {exc}")
            return False


class KdeShortcutBackend(ShortcutBackend):
    """Atalhos no KDE Plasma via ``kglobalshortcutsrc``.

    O Plasma recarrega o arquivo quando o ``kglobalaccel`` reinicia; gravar o
    arquivo é o caminho estável, então avisamos que pode exigir novo login.
    """

    name = "kde-kglobalaccel"

    _ACTIONS: Final[dict[str, str]] = {
        SLOT_HUD: "zorin-copilot-hud",
        SLOT_CROP: "zorin-copilot-crop",
        SLOT_VOICE: "zorin-copilot-voice",
    }

    def config_path(self) -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        return Path(base) / "kglobalshortcutsrc"

    def is_supported(self) -> bool:
        return self.env.is_kde

    def register(self, slot: str, accelerator: str, command: str) -> ShortcutResult:
        action = self._ACTIONS.get(slot)
        if not action:
            return ShortcutResult(False, f"Slot desconhecido: {slot}")

        binding = to_kde_binding(accelerator)
        if not binding:
            return ShortcutResult(False, f"Acelerador inválido: {accelerator!r}")

        path = self.config_path()
        entry = f"{action}={binding},none,{_slot_label(slot)}\n"
        try:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            lines = [ln for ln in existing.splitlines(keepends=True) if not ln.startswith(f"{action}=")]
            if not any(ln.strip() == "[Main]" for ln in lines):
                lines.insert(0, "[Main]\n")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(lines) + entry, encoding="utf-8")
        except OSError as exc:
            return ShortcutResult(False, f"Falha ao gravar {path}: {exc}")

        return ShortcutResult(
            True,
            f"Atalho gravado em {path}. No Plasma, aponte a ação para `{command}` em "
            "Configurações do Sistema > Atalhos, ou reinicie o kglobalaccel.",
            config_path=str(path),
        )

    def unregister(self, slot: str) -> ShortcutResult:
        action = self._ACTIONS.get(slot)
        path = self.config_path()
        if not action or not path.exists():
            return ShortcutResult(True, "Nada a remover.")
        try:
            kept = [
                ln for ln in path.read_text(encoding="utf-8").splitlines(keepends=True)
                if not ln.startswith(f"{action}=")
            ]
            path.write_text("".join(kept), encoding="utf-8")
            return ShortcutResult(True, "Entrada removida do kglobalshortcutsrc.")
        except OSError as exc:
            return ShortcutResult(False, str(exc))

    def is_registered(self, slot: str) -> bool:
        action = self._ACTIONS.get(slot)
        path = self.config_path()
        return bool(action) and path.exists() and f"{action}=" in path.read_text(encoding="utf-8", errors="ignore")

    def hint(self) -> str:
        return "No KDE, use Configurações do Sistema > Atalhos de Teclado > Atalhos Personalizados."


class GlobalShortcutsPortalBackend(ShortcutBackend):
    """Atalhos via portal ``org.freedesktop.portal.GlobalShortcuts``.

    É o caminho padronizado da freedesktop e o futuro para compositores sem
    mecanismo próprio (niri, river, labwc...). Hoje poucos o implementam — KDE 6
    e o GNOME 46+ têm suporte parcial — por isso ele entra **depois** dos backends
    nativos e antes do nulo.

    Ressalva honesta: a sessão do portal morre com o processo que a criou. Um
    ``copilot setup --shortcut`` cria o bind e o perde ao sair; o vínculo só
    sobrevive se o app estiver rodando em segundo plano (``--background``).
    """

    name = "global-shortcuts-portal"
    persists = False

    PORTAL_BUS: Final = "org.freedesktop.portal.Desktop"
    PORTAL_PATH: Final = "/org/freedesktop/portal/desktop"
    PORTAL_IFACE: Final = "org.freedesktop.portal.GlobalShortcuts"
    #: O trigger do portal usa o mesmo formato de acelerador do GTK.
    #: Ex.: "<Super><Shift>s".
    SESSION_IFACE: Final = "org.freedesktop.portal.Session"

    def __init__(self, env: Environment | None = None):
        super().__init__(env)
        self._proxy: Any = None
        self._session: str = ""
        self._commands: dict[str, str] = {}

    # ------------------------------------------------------------------ suporte
    def _gio(self) -> Any:
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            return Gio
        except (ImportError, ValueError):
            return None

    def _proxy_for(self, iface: str) -> Any:
        gio = self._gio()
        if gio is None:
            return None
        try:
            return gio.DBusProxy.new_for_bus_sync(
                gio.BusType.SESSION,
                gio.DBusProxyFlags.NONE,
                None,
                self.PORTAL_BUS,
                self.PORTAL_PATH,
                iface,
                None,
            )
        except Exception as exc:
            logger.debug(f"Portal {iface} indisponível: {exc}")
            return None

    def is_supported(self) -> bool:
        # Criar o proxy não prova nada: o Gio resolve nomes preguiçosamente e
        # aceita interfaces inexistentes. Introspecção é a única checagem honesta.
        gio = self._gio()
        if gio is None:
            return False
        try:
            conn = gio.bus_get_sync(gio.BusType.SESSION, None)
            reply = conn.call_sync(
                self.PORTAL_BUS,
                self.PORTAL_PATH,
                "org.freedesktop.DBus.Introspectable",
                "Introspect",
                None,
                None,
                gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            xml = reply.unpack()[0]
        except Exception as exc:
            logger.debug(f"Portal inalcançável: {exc}")
            return False

        if self.PORTAL_IFACE not in xml:
            return False

        self._proxy = self._proxy_for(self.PORTAL_IFACE)
        return self._proxy is not None

    # ----------------------------------------------------------------- registro
    def register(self, slot: str, accelerator: str, command: str) -> ShortcutResult:
        if self._proxy is None and not self.is_supported():
            return ShortcutResult(
                False, "Portal de atalhos globais ausente nesta sessão."
            )

        try:
            from gi.repository import GLib

            token = f"zorincopilot{os.getpid()}"
            options = {
                "session_handle_token": GLib.Variant("s", token),
                "handle_token": GLib.Variant("s", token),
            }
            reply = self._proxy.call_sync(
                "CreateSession",
                GLib.Variant("(a{sv})", (options,)),
                self._gio().DBusCallFlags.NONE,
                3000,
                None,
            )
            self._session = reply.unpack()[0]
            if self._session:
                # Mantém o proxy vivo: sem ele a sessão é coletada e o bind cai.
                _PORTAL_SESSIONS[self._session] = self
                self._proxy.connect("g-signal", self._on_signal)
        except Exception as exc:
            return ShortcutResult(False, f"Falha ao criar sessão no portal: {exc}")

        self._commands[slot] = command
        try:
            from gi.repository import GLib

            shortcuts = [
                (
                    slot,
                    {
                        "description": GLib.Variant("s", f"Zorin Copilot — {slot}"),
                        "preferred_trigger": GLib.Variant("s", accelerator),
                    },
                )
            ]
            self._proxy.call_sync(
                "BindShortcuts",
                GLib.Variant("(sa(sa{sv})sa{sv})", (self._session, shortcuts, "", {})),
                self._gio().DBusCallFlags.NONE,
                3000,
                None,
            )
        except Exception as exc:
            return ShortcutResult(False, f"Falha ao associar atalho no portal: {exc}")

        return ShortcutResult(
            True,
            f"Atalho '{slot}' associado via portal (válido enquanto o Copilot estiver rodando).",
        )

    def unregister(self, slot: str) -> ShortcutResult:
        self._commands.pop(slot, None)
        if not self._session:
            return ShortcutResult(True, "Nenhuma sessão de portal ativa.")
        try:
            session = self._proxy_for(self.SESSION_IFACE)
            if session is None:
                return ShortcutResult(False, "Não foi possível alcançar a sessão do portal.")
            from gi.repository import Gio

            session.call_sync(
                "Close", None, Gio.DBusCallFlags.NONE, 2000, None
            )
        except Exception as exc:
            logger.debug(f"Falha ao fechar sessão do portal: {exc}")
        _PORTAL_SESSIONS.pop(self._session, None)
        self._session = ""
        return ShortcutResult(True, "Sessão do portal encerrada.")

    def is_registered(self, slot: str) -> bool:
        return bool(self._session) and slot in self._commands

    def hint(self) -> str:
        return (
            "O portal GlobalShortcuts só mantém o atalho enquanto o processo do "
            "Copilot estiver vivo. Para algo persistente, registre o atalho no "
            "config do seu compositor."
        )

    # ------------------------------------------------------------------ sinais
    def _on_signal(self, _proxy, _sender, signal_name, params) -> None:
        """Dispara o comando quando o compositor reporta o atalho."""
        if signal_name != "Activated" or not params:
            return
        try:
            _session, shortcut_id, _timestamp, _options = params.unpack()
        except Exception:
            return
        command = self._commands.get(shortcut_id)
        if not command:
            return
        try:
            subprocess.Popen(command, shell=True, start_new_session=True)
        except Exception as exc:
            logger.error(f"Falha ao executar atalho '{shortcut_id}': {exc}")


#: Sessões vivas do portal, para que o GC não as destrua junto com o proxy.
_PORTAL_SESSIONS: dict[str, GlobalShortcutsPortalBackend] = {}


class NullShortcutBackend(ShortcutBackend):
    """Backend usado quando nenhum outro serve — falha com mensagem clara."""

    name = "none"
    persists = False

    def is_supported(self) -> bool:
        return True

    def register(self, slot: str, accelerator: str, command: str) -> ShortcutResult:
        return ShortcutResult(
            False,
            "Nenhum backend de atalho global disponível para este ambiente "
            f"({self.env.describe()}). O Copilot continua acessível pela bandeja ou pelo atalho da janela.",
        )

    def unregister(self, slot: str) -> ShortcutResult:
        return ShortcutResult(False, "Nenhum backend de atalho global disponível.")

    def hint(self) -> str:
        return "Registre manualmente no seu compositor um atalho para `zorin-copilot --toggle`."


def ensure_snippet_sourced(desktop: str, snippet: Path) -> tuple[bool, str]:
    """Garante que o config principal do compositor inclui ``snippet``.

    Hyprland e Sway leem apenas o config principal (``hyprland.conf`` /
    ``config``); um arquivo solto no diretório de configuração é ignorado
    silenciosamente. Gravar o snippet sem esta linha produz o pior tipo de
    falha: o ``setup`` responde "ativo" e nada acontece no próximo login.

    Atalhos e autostart gravam no mesmo arquivo, então a regra tem de viver
    fora das classes — os dois caminhos chamam esta função.

    Devolve ``(incluído, mensagem)``. A mensagem diz o que fazer quando não é
    possível tocar no config do usuário.
    """
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    if desktop == "hyprland":
        main = base / "hypr" / "hyprland.conf"
    elif desktop == "sway":
        main = base / "sway" / "config"
    else:
        return True, ""

    line = f"source = {snippet}" if desktop == "hyprland" else f"include {snippet}"

    if not main.exists():
        return False, f"{main} não existe; adicione `{line}` manualmente."

    try:
        content = main.read_text(encoding="utf-8")
    except OSError:
        return False, f"Não foi possível ler {main}."

    # Já incluído (direto ou por um glob de conf.d): não mexe em nada.
    if snippet.name in content or "conf.d" in content:
        return True, ""

    try:
        backup = main.with_suffix(main.suffix + ".bak-copilot")
        if not backup.exists():
            backup.write_text(content, encoding="utf-8")
        with open(main, "a", encoding="utf-8") as handle:
            handle.write(f"\n{line}\n")
        return True, f"Linha adicionada em {main} (backup: {backup.name})."
    except OSError as exc:
        return False, f"Adicione manualmente `{line}` em {main}: {exc}"


#: Slot do bloco de decoração (blur/rounding) no snippet do Hyprland.
SLOT_DECOR: Final = "decor"

#: app_id da janela principal (deve casar com `class:` do Hyprland).
_DECOR_APP_ID: Final = "io.github.bruno.ZorinCopilot"


def _decor_directive() -> str:
    """Bloco idempotente de regras de vidro/arredondamento para o Hyprland.

    Casa a janela principal pelo ``class:`` (app_id) e a pílula pelo ``namespace``
    da layer-shell. São regras do app próprio — não globais — e podem ser removidas
    sem afetar o resto do sistema.
    """
    ns = current_environment().blur_namespace()
    return (
        f"windowrule = blur,class:{_DECOR_APP_ID}\n"
        f"windowrule = rounding,class:{_DECOR_APP_ID}\n"
        f"layerrule = blur,{ns}\n"
        f"layerrule = rounding,{ns}\n"
    )


def _apply_decor_live(directive: str) -> bool:
    """Aplica cada regra na sessão atual via `hyprctl keyword` (sem reload global)."""
    ok = True
    for line in directive.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Formato "key = value" -> `hyprctl keyword key value`
        key, _, value = line.partition("=")
        args = [key.strip(), *value.strip().split()]
        try:
            res = subprocess.run(
                ["hyprctl", "keyword", *args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ok = ok and res.returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("hyprctl keyword %s falhou: %s", args, exc)
            ok = False
    return ok


def ensure_decor_rules(env: Environment | None = None) -> tuple[bool, str]:
    """Escreve (idempotente) e aplica as regras de vidro/arredondamento no Hyprland.

    Gerado no snippet ``zorin-copilot.conf`` (mesmo dos atalhos) e incluído no
    ``hyprland.conf`` via :func:`ensure_snippet_sourced`. Aplicado ao vivo com
    ``hyprctl keyword`` para valer na sessão atual. Sem Hyprland, é no-op seguro.
    """
    env = env or current_environment()
    if not env.is_hyprland:
        return False, "decoração de compositor só é suportada no Hyprland"
    backend = HyprlandShortcutBackend(env)
    snippet = backend.snippet_path()
    try:
        snippet.parent.mkdir(parents=True, exist_ok=True)
        existing = snippet.read_text(encoding="utf-8") if snippet.exists() else ""
        written = backend._rewrite_snippet(existing, SLOT_DECOR, _decor_directive())
        snippet.write_text(written, encoding="utf-8")
    except OSError as exc:
        return False, f"não foi possível gravar {snippet}: {exc}"

    sourced, source_msg = ensure_snippet_sourced(env.desktop, snippet)
    applied = _apply_decor_live(_decor_directive()) if env.has("hyprctl") else False
    msg = f"regras de decoração em {snippet}"
    if applied:
        msg += " (aplicadas na sessão)"
    if source_msg:
        msg += f"; {source_msg}"
    return True, msg


def iter_backends(env: Environment | None = None) -> list[ShortcutBackend]:
    """Backends conhecidos, em ordem de preferência."""
    env = env or current_environment()
    return [
        GnomeShortcutBackend(env),
        HyprlandShortcutBackend(env),
        SwayShortcutBackend(env),
        KdeShortcutBackend(env),
        # Portal por último: é padronizado, mas poucos compositores implementam e
        # a sessão morre com o processo. Melhor que nada, pior que o nativo.
        GlobalShortcutsPortalBackend(env),
    ]


def select_backend(env: Environment | None = None) -> ShortcutBackend:
    """Escolhe o backend adequado ao ambiente, caindo no nulo se nenhum servir."""
    for backend in iter_backends(env):
        try:
            if backend.is_supported():
                return backend
        except Exception as exc:
            logger.debug(f"Backend {backend.name} falhou na detecção: {exc}")
    return NullShortcutBackend(env)
