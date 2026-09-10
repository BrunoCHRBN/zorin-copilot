# Decisão de design: o Zorin Copilot nasceu casado com Zorin OS 18 (Ubuntu 24.04 + GNOME 46).
# Em vez de espalhar `if gnome` pelo código, toda decisão que depende do ambiente passa a
# consultar um único retrato do sistema (`Environment`). Quem roda em Arch/Hyprland recebe
# os mesmos recursos por outros backends; o que não existe degrada com mensagem honesta.

"""Detecção do ambiente desktop: distribuição, sessão, compositor e capacidades.

Este módulo é deliberadamente puro — não importa ``gi``, não toca em D-Bus e não
faz rede. Isso o mantém testável em qualquer máquina (incluindo CI sem servidor
gráfico) e permite que os adapters decidam o que fazer com as informações.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Final

# Marcadores de compositor que não aparecem (ou aparecem tortos) em XDG_CURRENT_DESKTOP.
# Cada par (variável de ambiente, identificador do compositor).
_COMPOSITOR_ENV_MARKERS: Final[tuple[tuple[str, str], ...]] = (
    ("HYPRLAND_INSTANCE_SIGNATURE", "hyprland"),
    ("SWAYSOCK", "sway"),
    ("WAYFIRE_SOCKET", "wayfire"),
    ("LABWC_PID", "labwc"),
    ("RIVER_SOCKET", "river"),
    ("NIRI_SOCKET", "niri"),
    ("HYPRLAND_CMD", "hyprland"),
)

# Valores conhecidos de XDG_CURRENT_DESKTOP -> identificador normalizado.
_DESKTOP_ALIASES: Final[dict[str, str]] = {
    "gnome": "gnome",
    "gnome-classic": "gnome",
    "ubuntu:gnome": "gnome",
    "zorin:gnome": "gnome",
    "pop:gnome": "gnome",
    "kde": "kde",
    "plasma": "kde",
    "kde plasma": "kde",
    "xfce": "xfce",
    "x-cinnamon": "cinnamon",
    "mate": "mate",
    "lxqt": "lxqt",
    "hyprland": "hyprland",
    "sway": "sway",
    "wlroots": "wlroots",
}

#: Compositores que implementam a família de protocolos wlroots (grim/slurp, layer-shell).
WLROOTS_COMPOSITORS: Final[frozenset[str]] = frozenset(
    {"hyprland", "sway", "wayfire", "labwc", "river", "niri", "wlroots"}
)

#: Rótulos legíveis para o prompt de sistema — o modelo precisa saber em que
#: ambiente está para sugerir comandos que existam de fato.
DESKTOP_LABELS: Final[dict[str, str]] = {
    "gnome": "GNOME",
    "kde": "KDE Plasma",
    "hyprland": "Hyprland",
    "sway": "Sway",
    "wayfire": "Wayfire",
    "labwc": "labwc",
    "niri": "Niri",
    "river": "River",
    "wlroots": "wlroots",
    "xfce": "Xfce",
    "cinnamon": "Cinnamon",
    "mate": "MATE",
    "lxqt": "LXQt",
}


#: Barras que aparecem em compositores wlroots. Diferente de GNOME/KDE, aqui a
#: barra é opcional — por isso precisa ser detectada em execução, não assumida.
BAR_PROCESSES: Final[tuple[str, ...]] = (
    "waybar",
    "polybar",
    "yambar",
    "eww",
    "ags",
    "swaybar",
    "xfce4-panel",
    "tint2",
    "lemonbar",
)


def running_processes() -> frozenset[str]:
    """Nomes dos processos em execução, lidos direto de ``/proc``.

    Evita depender de ``pgrep``/``ps`` — que não existem em imagens mínimas de
    container — e é trivial de simular em teste.
    """
    names: set[str] = set()
    try:
        import os

        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/comm", "rb") as handle:
                    name = handle.read().decode("utf-8", "replace").strip()
            except OSError:
                continue
            if name:
                names.add(name)
    except OSError:
        return frozenset()
    return frozenset(names)


#: A partir desta versão o Hyprland aceita ``hyprland.lua`` — e, se ele existir,
#: o ``hyprland.conf`` **não é lido**. hyprlang segue suportado por ~1-2 releases.
HYPRLAND_LUA_MIN_VERSION: Final[tuple[int, int]] = (0, 55)

_VERSION_RE: Final = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text: str) -> tuple[int, ...]:
    """Extrai a primeira versão ``X.Y[.Z]`` do texto. Vazia se não houver."""
    match = _VERSION_RE.search(text or "")
    if not match:
        return ()
    return tuple(int(part) for part in match.groups() if part is not None)


def parse_hyprland_version(text: str) -> str:
    """Interpreta a saída de ``hyprctl version`` e devolve ``"X.Y.Z"`` (ou ``""``).

    O comando imprime várias linhas e a versão aparece em mais de um formato
    (``Hyprland 0.55.3 built from...`` e ``Tag: v0.55.3``). Aceita os dois.
    """
    for line in (text or "").splitlines():
        stripped = line.strip().lower()
        if not (stripped.startswith("hyprland") or stripped.startswith("tag:")):
            continue
        parts = list(parse_version(stripped))
        if len(parts) >= 2:
            while len(parts) < 3:
                parts.append(0)
            return ".".join(str(part) for part in parts)
    return ""


def probe_hyprland_version() -> str:
    """Versão do Hyprland via ``hyprctl version``; ``""`` se não for possível."""
    if not shutil.which("hyprctl"):
        return ""
    try:
        result = subprocess.run(
            ["hyprctl", "version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return parse_hyprland_version(result.stdout)


def hypr_config_dir(environ: dict[str, str] | None = None) -> Path:
    """Diretório de configuração do Hyprland (respeita ``XDG_CONFIG_HOME``)."""
    environ = dict(os.environ if environ is None else environ)
    base = environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "hypr"


def describe_for_prompt(env: Environment) -> str:
    """Descreve o ambiente numa frase curta, para o prompt de sistema.

    O modelo recebe isso e passa a sugerir comandos coerentes com a máquina real:
    `pacman` em Arch, `bind` em Hyprland, `grim` para captura.
    """
    system = env.distro.pretty_name or env.distro.id or "Linux"
    desktop = DESKTOP_LABELS.get(env.desktop, env.desktop.capitalize() if env.desktop else "")
    session = env.session_type.capitalize() if env.session_type else ""

    parts = [f"{system} (Linux"]
    if desktop:
        parts.append(f" / {desktop}")
    if session:
        parts.append(f" no {session}")
    parts.append(")")
    return "".join(parts)


@dataclass(frozen=True)
class DistroInfo:
    """Identidade da distribuição lida de ``/etc/os-release``."""

    id: str = ""
    id_like: tuple[str, ...] = ()
    pretty_name: str = ""
    version_id: str = ""

    @property
    def is_arch_like(self) -> bool:
        return self.id in {"arch", "endeavouros", "manjaro", "artix"} or "arch" in self.id_like

    @property
    def is_debian_like(self) -> bool:
        return self.id in {"debian", "ubuntu", "zorin", "linuxmint", "pop"} or "debian" in self.id_like

    @property
    def package_manager(self) -> str:
        """Gerenciador de pacotes nativo (sem helpers de AUR/PPA)."""
        if self.is_arch_like:
            return "pacman"
        if self.id in {"fedora", "nobara"} or "fedora" in self.id_like:
            return "dnf"
        if self.is_debian_like:
            return "apt"
        return ""


@dataclass(frozen=True)
class HyprlandConfig:
    """Qual arquivo de configuração o Hyprland **de fato** carrega.

    Desde a 0.55 o Hyprland aceita ``hyprland.lua``. A regra do compositor é
    excludente e checada uma única vez na inicialização: se o ``.lua`` existe, o
    ``hyprland.conf`` é ignorado por inteiro — inclusive qualquer ``source =``
    que a gente tenha escrito nele. É por isso que o sabor precisa ser detectado
    em vez de assumido: errar aqui produz silêncio, não erro.
    """

    flavor: str = "hyprlang"  # "hyprlang" | "lua"
    path: Path | None = None
    version: str = ""

    @property
    def is_lua(self) -> bool:
        return self.flavor == "lua"

    @property
    def version_tuple(self) -> tuple[int, ...]:
        return parse_version(self.version)

    @property
    def supports_lua(self) -> bool:
        """O Hyprland instalado entende config em Lua?"""
        return self.version_tuple[:2] >= HYPRLAND_LUA_MIN_VERSION


def detect_hyprland_config(
    config_dir: Path | None = None,
    version: str = "",
) -> HyprlandConfig:
    """Descobre o sabor e o caminho do config do Hyprland.

    Precedência, seguindo o comportamento real do compositor:

    1. ``hyprland.lua`` existe  -> lua (o ``.conf`` é ignorado)
    2. ``hyprland.conf`` existe -> hyprlang
    3. nenhum dos dois         -> lua se a versão for >= 0.55 (é o que o
       Hyprland gera hoje num perfil novo), senão hyprlang
    """
    directory = config_dir if config_dir is not None else hypr_config_dir()
    lua_path = directory / "hyprland.lua"
    conf_path = directory / "hyprland.conf"

    if lua_path.exists():
        return HyprlandConfig(flavor="lua", path=lua_path, version=version)
    if conf_path.exists():
        return HyprlandConfig(flavor="hyprlang", path=conf_path, version=version)

    if parse_version(version)[:2] >= HYPRLAND_LUA_MIN_VERSION:
        return HyprlandConfig(flavor="lua", path=lua_path, version=version)
    return HyprlandConfig(flavor="hyprlang", path=conf_path, version=version)


@dataclass(frozen=True)
class Environment:
    """Retrato do ambiente em que o Copilot está rodando.

    Construído uma vez por processo (:func:`detect_environment`) e consultado por
    todos os adapters. ``capabilities`` responde "existe ferramenta X?" para que a
    UI possa explicar por que um recurso está indisponível em vez de falhar mudo.
    """

    distro: DistroInfo = field(default_factory=DistroInfo)
    session_type: str = ""  # "wayland" | "x11" | ""
    desktop: str = ""  # "gnome" | "kde" | "hyprland" | "sway" | "xfce" | ...
    package_manager: str = ""

    # Binários relevantes descobertos no PATH. Guardar aqui evita repetir
    # shutil.which em cada adapter e torna a detecção testável por injeção.
    binaries: frozenset[str] = frozenset()

    # Config do Hyprland: sabor (hyprlang/lua), caminho e versão. Só preenchido
    # quando o desktop é Hyprland — nos demais fica no padrão e é ignorado.
    hyprland: HyprlandConfig = field(default_factory=HyprlandConfig)

    @property
    def hyprland_uses_lua(self) -> bool:
        """O Hyprland desta sessão carrega ``hyprland.lua``?"""
        return self.hyprland.is_lua

    @property
    def is_wayland(self) -> bool:
        return self.session_type == "wayland"

    @property
    def is_x11(self) -> bool:
        return self.session_type == "x11"

    @property
    def is_wlroots(self) -> bool:
        """Compositor da família wlroots (Hyprland, Sway, labwc, niri, ...)."""
        return self.desktop in WLROOTS_COMPOSITORS

    @property
    def is_gnome(self) -> bool:
        return self.desktop == "gnome"

    @property
    def is_kde(self) -> bool:
        return self.desktop == "kde"

    @property
    def is_hyprland(self) -> bool:
        """Roda sobre o Hyprland? É o único compositor alvo do blur real (layerrule)."""
        return self.desktop == "hyprland"

    @property
    def supports_compositor_blur(self) -> bool:
        """O compositor consegue desfocar esta janela por app_id/namespace?

        Só wlroots com ``hyprctl`` expõe isso de forma confiável hoje (Hyprland via
        ``layerrule``/``windowrule``). GNOME/Mutter e X11 não desfocam janelas de
        app por id — nesses casos a UI recorre a um fallback CSS de opacidade.
        """
        return self.is_wlroots and self.has("hyprctl")

    @staticmethod
    def blur_namespace() -> str:
        """Namespace estável da pílula de voz para ``layerrule`` do Hyprland.

        Precisa ser constante entre execuções: o ``layerrule blur,<ns>`` é casado
        pelo namespace da superfície layer-shell, não por título.
        """
        return "zorin-copilot-pill"

    def has(self, *binaries: str) -> bool:
        """Algum dos binários informados está disponível?"""
        return any(b in self.binaries for b in binaries)

    def is_running(self, *processes: str) -> bool:
        """Algum dos processos informados está em execução agora?"""
        running = running_processes()
        return any(p in running for p in processes)

    def describe(self) -> str:
        """Frase curta para logs e para o rodapé de diagnóstico."""
        parts = [self.distro.pretty_name or self.distro.id or "distro desconhecida"]
        if self.desktop:
            parts.append(self.desktop)
        if self.session_type:
            parts.append(self.session_type)
        return " · ".join(parts)


def parse_os_release(text: str) -> DistroInfo:
    """Interpreta o conteúdo de ``/etc/os-release``.

    Aceita texto cru para poder ser testada sem tocar no disco. ``ID_LIKE`` vem
    separado por espaços na especificação, mas alguns fornecedores usam aspas.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip().strip('"').strip("'")

    id_like = tuple(v for v in values.get("ID_LIKE", "").replace(",", " ").split() if v)
    return DistroInfo(
        id=values.get("ID", "").lower(),
        id_like=tuple(v.lower() for v in id_like),
        pretty_name=values.get("PRETTY_NAME", ""),
        version_id=values.get("VERSION_ID", ""),
    )


def _read_os_release() -> DistroInfo:
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as handle:
            return parse_os_release(handle.read())
    except OSError:
        return DistroInfo()


def _detect_desktop(environ: dict[str, str]) -> str:
    """Normaliza o identificador do ambiente gráfico.

    A ordem importa: Hyprland/Sway às vezes deixam ``XDG_CURRENT_DESKTOP`` vazio
    (ou com valor genérico como "wlroots"), então as variáveis específicas do
    compositor têm precedência — elas são inequívocas.
    """
    for env_var, compositor in _COMPOSITOR_ENV_MARKERS:
        if environ.get(env_var):
            return compositor

    raw = (environ.get("XDG_CURRENT_DESKTOP") or "").strip().lower()
    if raw:
        # KDE costuma vir como "KDE"; GNOME como "ubuntu:GNOME". O primeiro
        # componente antes de ':' é o mais estável entre distribuições.
        return _DESKTOP_ALIASES.get(raw) or _DESKTOP_ALIASES.get(raw.split(":")[0]) or raw

    wayland_display = environ.get("WAYLAND_DISPLAY", "").lower()
    for marker in ("hyprland", "sway", "wayfire", "labwc", "niri"):
        if marker in wayland_display:
            return marker

    if environ.get("DISPLAY") and not environ.get("WAYLAND_DISPLAY"):
        return "x11-generic"

    return ""


#: Binários que os adapters consultam. Centralizar a lista faz a detecção virar
#: uma única varredura de PATH em vez de dezenas espalhadas pelo código.
_PROBED_BINARIES: Final[tuple[str, ...]] = (
    # captura de tela
    "grim",
    "slurp",
    "grimblast",
    "wayshot",
    "spectacle",
    "gnome-screenshot",
    "import",  # ImageMagick, último recurso em X11
    # entrada / automação
    "ydotool",
    "dotool",
    "xdotool",
    "wtype",
    # notificação
    "notify-send",
    "makoctl",
    "swaync-client",
    # área de transferência
    "wl-copy",
    "wl-paste",
    "xclip",
    "xsel",
    # áudio
    "wpctl",
    "pactl",
    "pw-record",
    "pw-play",
    "arecord",
    # controle de ambiente
    "gsettings",
    "kwriteconfig6",
    "kwriteconfig5",
    "hyprctl",
    "swaymsg",
    "dbus-send",
    # documentos
    "pdftotext",
    "evince",
    "zathura",
)


def detect_environment(
    environ: dict[str, str] | None = None,
    probe: bool = True,
) -> Environment:
    """Monta o retrato do ambiente.

    Args:
        environ: variáveis de ambiente a considerar (padrão: ``os.environ``).
            Existe para os testes simularem Hyprland, Sway, GNOME etc.
        probe: quando ``False``, não varre o PATH — útil em testes para manter
            o resultado determinístico.
    """
    environ = dict(os.environ if environ is None else environ)
    distro = _read_os_release()

    session = (environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    if not session:
        session = "wayland" if environ.get("WAYLAND_DISPLAY") else ("x11" if environ.get("DISPLAY") else "")

    found = frozenset(b for b in _PROBED_BINARIES if shutil.which(b)) if probe else frozenset()
    desktop = _detect_desktop(environ)

    # Só faz sentido perguntar ao Hyprland quando ele é o compositor — e só com
    # `probe` ligado, para que os testes continuem determinísticos.
    hyprland = HyprlandConfig()
    if desktop == "hyprland" and probe:
        version = probe_hyprland_version()
        hyprland = detect_hyprland_config(hypr_config_dir(environ), version)

    return Environment(
        distro=distro,
        session_type=session,
        desktop=desktop,
        package_manager=distro.package_manager,
        binaries=found,
        hyprland=hyprland,
    )


@lru_cache(maxsize=1)
def current_environment() -> Environment:
    """Retrato do ambiente atual, calculado uma única vez por processo.

    O cache é intencional: o compositor não muda no meio da sessão, e os adapters
    consultam isso em caminho quente. Testes que precisam de outro ambiente devem
    chamar :func:`detect_environment` com ``environ`` explícito.
    """
    return detect_environment()
