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
        """Garante que o config principal inclui o snippet, sem destruir nada.

        Se já houver uma inclusão do snippet (ou um glob de ``conf.d`` que o
        cubra), não mexe em nada. Caso contrário acrescenta a linha ao final,
        guardando um backup.
        """
        main = self.main_config()
        if not main.exists():
            return False, f"{main} não existe; adicione `{self._source_line(snippet)}` manualmente."

        try:
            content = main.read_text(encoding="utf-8")
        except OSError:
            return False, f"Não foi possível ler {main}."

        if snippet.name in content or "conf.d" in content:
            return True, ""

        line = f"{self._source_line(snippet)}\n"
        try:
            backup = main.with_suffix(main.suffix + ".bak-copilot")
            if not backup.exists():
                backup.write_text(content, encoding="utf-8")
            with open(main, "a", encoding="utf-8") as handle:
                handle.write(f"\n{line}")
            return True, f"Linha adicionada em {main} (backup: {backup.name})."
        except OSError as exc:
            return False, f"Adicione manualmente `{line.strip()}` em {main}: {exc}"

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


def iter_backends(env: Environment | None = None) -> list[ShortcutBackend]:
    """Backends conhecidos, em ordem de preferência."""
    env = env or current_environment()
    return [
        GnomeShortcutBackend(env),
        HyprlandShortcutBackend(env),
        SwayShortcutBackend(env),
        KdeShortcutBackend(env),
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
