# Decisão de design: detecção de contexto ativo do desktop (janela em foco, aplicação,
# documento/arquivo aberto e conteúdo da área de transferência). Permite que o Zorin Copilot
# antecipe a intenção do usuário e ofereça ações inteligentes proativas ao ser invocado.

"""Módulo de detecção e classificação de contexto do desktop."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import logging
import re
import shutil
import subprocess
import time
from typing import Any, Tuple

from .a11y import DesktopInspector
from .clipboard import ClipboardService

logger = logging.getLogger(__name__)


class AppCategory(str, Enum):
    """Categorias funcionais de aplicativos de desktop."""
    CODE_EDITOR = "code_editor"
    BROWSER = "browser"
    TERMINAL = "terminal"
    DOCUMENT = "document"
    FILE_MANAGER = "file_manager"
    COMMUNICATION = "communication"
    MEDIA = "media"
    SYSTEM = "system"
    GENERAL = "general"


class ClipboardCategory(str, Enum):
    """Classificação semântica do conteúdo da área de transferência."""
    ERROR_TRACEBACK = "error_traceback"
    URL = "url"
    CODE_SNIPPET = "code_snippet"
    LONG_TEXT = "long_text"
    PLAIN_TEXT = "plain_text"
    IMAGE = "image"
    EMPTY = "empty"


@dataclass
class DesktopContext:
    """Snapshot estruturado do estado de trabalho do usuário no desktop."""
    app_name: str = ""
    window_title: str = ""
    category: AppCategory = AppCategory.GENERAL
    extracted_target: str = ""  # Nome do arquivo, URL, pasta ou comando relevante
    bbox: tuple[int, int, int, int] | None = None
    clipboard_category: ClipboardCategory = ClipboardCategory.EMPTY
    clipboard_preview: str = ""
    clipboard_text: str = ""
    has_active_window: bool = False
    git_repo: str = ""
    git_branch: str = ""
    git_has_diff: bool = False
    timestamp: float = 0.0

    @property
    def is_contextual(self) -> bool:
        """Indica se há contexto útil detectado (janela externa, git ou clipboard com dados)."""
        return (
            self.has_active_window
            or bool(self.git_repo)
            or self.clipboard_category not in (
                ClipboardCategory.EMPTY,
                ClipboardCategory.PLAIN_TEXT,
            )
        )


# Mapeamento de nomes de processos/apps e títulos para categorias
_APP_PATTERNS: list[tuple[re.Pattern, AppCategory]] = [
    # Editores de código e IDEs
    (
        re.compile(
            r"\b(?:code|vscodium|cursor|pycharm|intellij|idea|clion|webstorm|sublime_text|neovim|nvim|vim|emacs|gedit|gnome-text-editor|kate|geany)\b",
            re.IGNORECASE,
        ),
        AppCategory.CODE_EDITOR,
    ),
    # Navegadores Web
    (
        re.compile(
            r"\b(?:google-chrome|chrome|chromium|firefox|zen|brave|microsoft-edge|edge|epiphany|vivaldi|opera|waterfox|tor-browser)\b",
            re.IGNORECASE,
        ),
        AppCategory.BROWSER,
    ),
    # Terminais
    (
        re.compile(
            r"\b(?:ptyxis|alacritty|kitty|gnome-terminal|foot|konsole|wezterm|xterm|rxvt|tilix|terminator)\b",
            re.IGNORECASE,
        ),
        AppCategory.TERMINAL,
    ),
    # Documentos e visualizadores
    (
        re.compile(
            r"\b(?:soffice|libreoffice|writer|calc|impress|evince|okular|xreader|onlyoffice|mupdf|zathura)\b",
            re.IGNORECASE,
        ),
        AppCategory.DOCUMENT,
    ),
    # Gerenciadores de arquivos
    (
        re.compile(
            r"\b(?:nautilus|org\.gnome\.nautilus|thunar|dolphin|nemo|pcmanfm|caja)\b",
            re.IGNORECASE,
        ),
        AppCategory.FILE_MANAGER,
    ),
    # Comunicação
    (
        re.compile(
            r"\b(?:slack|discord|telegram|whatsapp|thunderbird|element|signal)\b",
            re.IGNORECASE,
        ),
        AppCategory.COMMUNICATION,
    ),
    # Mídia
    (
        re.compile(
            r"\b(?:spotify|vlc|totem|rhythmbox|celluloid|mpv|audacious)\b",
            re.IGNORECASE,
        ),
        AppCategory.MEDIA,
    ),
    # Configurações do Sistema
    (
        re.compile(
            r"\b(?:gnome-control-center|zorin-appearance|software-properties|gsettings)\b",
            re.IGNORECASE,
        ),
        AppCategory.SYSTEM,
    ),
]

_EXTENSIONS_CODE = re.compile(
    r"\b([a-zA-Z0-9_\-\./\\]+\.(?:py|rs|go|js|ts|jsx|tsx|html|css|scss|json|md|c|cpp|h|hpp|sh|bash|yml|yaml|sql|toml|xml|java|kt|dart))\b",
    re.IGNORECASE,
)

_EXTENSIONS_DOC = re.compile(
    r"\b([a-zA-Z0-9_\-\./\\]+\.(?:pdf|docx?|odt|xlsx?|ods|pptx?|odp|txt|epub|csv))\b",
    re.IGNORECASE,
)

_TRACEBACK_PATTERN = re.compile(
    r"(?:Traceback\s+\(most recent call last\)|(?:[A-Z]\w+Error|Exception):\s+.+|panic:\s+.+|exit status [1-9]|FAILED\s*\(|AssertionError:|SyntaxError:|TypeError:|ValueError:|ReferenceError:|NullPointerException|segmentation fault)",
    re.IGNORECASE,
)

_CODE_SNIPPET_PATTERN = re.compile(
    r"(?:^\s*(?:def|class|import|from|function|const|let|var|pub\s+fn|fn|func|public\s+class)\b|[{};]\s*$)",
    re.MULTILINE,
)

_URL_PATTERN = re.compile(
    r"^https?://[^\s/$.?#].[^\s]*$",
    re.IGNORECASE,
)


class DesktopContextDetector:
    """Detecta, classifica e enriquece o contexto de trabalho ativo no desktop."""

    def __init__(self, inspector: DesktopInspector | None = None):
        self.inspector = inspector or DesktopInspector()
        self._last_external_context: DesktopContext | None = None

    def detect_context(self) -> DesktopContext:
        """
        Coleta as informações da janela em foco e da área de transferência.

        Returns:
            Instância preenchida de DesktopContext.
        """
        app_name, title, bbox = self._get_raw_active_window()
        timestamp = time.time()

        # Se não encontrou janela nova ou se for o próprio Copilot, aproveita o último
        # contexto externo se recente (< 90 segundos)
        is_copilot = any(ign in (app_name or "").lower() for ign in ("zorin-copilot", "copilot"))
        if (not app_name or is_copilot) and self._last_external_context:
            if (timestamp - self._last_external_context.timestamp) < 90.0:
                # Atualiza com clipboard atualizado
                clip_cat, clip_prev, clip_text = self._analyze_clipboard()
                return DesktopContext(
                    app_name=self._last_external_context.app_name,
                    window_title=self._last_external_context.window_title,
                    category=self._last_external_context.category,
                    extracted_target=self._last_external_context.extracted_target,
                    bbox=self._last_external_context.bbox,
                    clipboard_category=clip_cat,
                    clipboard_preview=clip_prev,
                    clipboard_text=clip_text,
                    has_active_window=True,
                    git_repo=self._last_external_context.git_repo,
                    git_branch=self._last_external_context.git_branch,
                    git_has_diff=self._last_external_context.git_has_diff,
                    timestamp=timestamp,
                )

        has_active = bool(app_name and not is_copilot)
        category = self._classify_app(app_name, title) if has_active else AppCategory.GENERAL
        extracted_target = self._extract_target(category, title, app_name) if has_active else ""

        clip_cat, clip_prev, clip_text = self._analyze_clipboard()
        git_repo, git_branch, git_has_diff = self._detect_git_info()

        ctx = DesktopContext(
            app_name=app_name if has_active else "",
            window_title=title if has_active else "",
            category=category,
            extracted_target=extracted_target,
            bbox=bbox if has_active else None,
            clipboard_category=clip_cat,
            clipboard_preview=clip_prev,
            clipboard_text=clip_text,
            has_active_window=has_active,
            git_repo=git_repo,
            git_branch=git_branch,
            git_has_diff=git_has_diff,
            timestamp=timestamp,
        )

        if has_active:
            self._last_external_context = ctx

        return ctx

    def _detect_git_info(self) -> tuple[str, str, bool]:
        """Detecta se o ambiente de trabalho atual está em um repositório git."""
        if not shutil.which("git"):
            return "", "", False
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=0.35,
                check=False,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return "", "", False
            toplevel = res.stdout.strip()
            repo_name = toplevel.replace("\\", "/").rstrip("/").split("/")[-1]

            branch_res = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=0.25,
                check=False,
            )
            branch_name = branch_res.stdout.strip() if branch_res.returncode == 0 else ""
            if not branch_name:
                branch_name = "HEAD desanexado"

            status_res = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=0.35,
                check=False,
            )
            has_diff = bool(status_res.returncode == 0 and status_res.stdout.strip())

            return repo_name, branch_name, has_diff
        except Exception as exc:
            logger.debug(f"Detecção git falhou: {exc}")
            return "", "", False

    def _get_raw_active_window(self) -> tuple[str, str, tuple[int, int, int, int] | None]:
        """Tenta obter a janela ativa via AT-SPI2 com fallbacks para Hyprland, Sway e X11."""
        # 1. Caminho principal via AT-SPI2
        try:
            app, title, bbox = self.inspector.get_active_window_info()
            if app:
                return app, title, bbox
        except Exception as exc:
            logger.debug(f"Erro em inspector.get_active_window_info: {exc}")

        # 2. Fallback Hyprland (se rodando sob hyprland)
        if shutil.which("hyprctl"):
            try:
                proc = subprocess.run(
                    ["hyprctl", "activewindow", "-j"],
                    capture_output=True,
                    text=True,
                    timeout=0.6,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout)
                    cls_name = data.get("class") or data.get("initialClass") or ""
                    title = data.get("title") or ""
                    at = data.get("at", [0, 0])
                    size = data.get("size", [0, 0])
                    if cls_name:
                        bbox = (at[0], at[1], size[0], size[1]) if len(at) == 2 and len(size) == 2 else None
                        return cls_name, title, bbox
            except Exception as exc:
                logger.debug(f"Fallback hyprctl falhou: {exc}")

        # 3. Fallback Sway / i3
        if shutil.which("swaymsg"):
            try:
                proc = subprocess.run(
                    ["swaymsg", "-t", "get_tree"],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    tree = json.loads(proc.stdout)
                    focused = self._find_sway_focused(tree)
                    if focused:
                        app = focused.get("app_id") or focused.get("window_properties", {}).get("class") or ""
                        title = focused.get("name") or ""
                        rect = focused.get("rect", {})
                        bbox = (rect.get("x", 0), rect.get("y", 0), rect.get("width", 0), rect.get("height", 0))
                        if app:
                            return app, title, bbox
            except Exception as exc:
                logger.debug(f"Fallback swaymsg falhou: {exc}")

        return "", "", None

    def _find_sway_focused(self, node: dict[str, Any]) -> dict[str, Any] | None:
        if node.get("focused"):
            return node
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            found = self._find_sway_focused(child)
            if found:
                return found
        return None

    def _classify_app(self, app_name: str, window_title: str) -> AppCategory:
        """Classifica o aplicativo em uma categoria funcional."""
        combined = f"{app_name} {window_title}".lower()
        for pattern, cat in _APP_PATTERNS:
            if pattern.search(combined):
                return cat

        # Heurística adicional por extensões presentes no título
        if _EXTENSIONS_CODE.search(window_title):
            return AppCategory.CODE_EDITOR
        if _EXTENSIONS_DOC.search(window_title):
            return AppCategory.DOCUMENT

        return AppCategory.GENERAL

    def _extract_target(self, category: AppCategory, title: str, app_name: str) -> str:
        """Extrai um identificador ou alvo limpo (arquivo, domínio, pasta ou assunto)."""
        if not title:
            return ""

        if category == AppCategory.CODE_EDITOR:
            m = _EXTENSIONS_CODE.search(title)
            if m:
                # Retorna apenas o nome base do arquivo
                raw_filename = m.group(1).replace("\\", "/").rstrip("•* ")
                return raw_filename.split("/")[-1]
            # Se não encontrou extensão típica, pega primeira palavra/segmento antes de " - "
            first_part = title.split(" - ")[0].strip()
            if first_part and len(first_part) < 40:
                return first_part

        elif category == AppCategory.DOCUMENT:
            m = _EXTENSIONS_DOC.search(title)
            if m:
                raw_filename = m.group(1).replace("\\", "/").rstrip("•* ")
                return raw_filename.split("/")[-1]
            first_part = title.split(" - ")[0].strip()
            if first_part and len(first_part) < 50:
                return first_part

        elif category == AppCategory.BROWSER:
            # Limpa sufixos usuais de navegadores
            clean = re.sub(
                r"\s*[-—]\s*(?:Google Chrome|Mozilla Firefox|Zen Browser|Brave|Chromium|Edge|Vivaldi|Opera|Epiphany).*$",
                "",
                title,
                flags=re.IGNORECASE,
            ).strip()
            if clean and len(clean) > 55:
                clean = clean[:52] + "..."
            return clean

        elif category == AppCategory.FILE_MANAGER:
            # Em geral o título é a pasta atual (ex: "Downloads", "Documentos")
            folder = title.split(" - ")[0].strip()
            return folder if folder else "pasta de arquivos"

        elif category == AppCategory.TERMINAL:
            # Tenta pegar processo ou pasta
            first_part = title.split(" - ")[0].strip()
            if first_part and len(first_part) < 40:
                return first_part

        return ""

    def _analyze_clipboard(self) -> tuple[ClipboardCategory, str, str]:
        """Analisa a área de transferência usando ClipboardService."""
        try:
            content_type, data = ClipboardService.get_content()
            if content_type == "image":
                return ClipboardCategory.IMAGE, "Imagem copiada na área de transferência", ""
            if content_type == "text" and isinstance(data, str) and data.strip():
                text = data.strip()
                preview = text.splitlines()[0][:60] if text else ""
                if len(preview) == 60:
                    preview += "..."

                if _TRACEBACK_PATTERN.search(text):
                    return ClipboardCategory.ERROR_TRACEBACK, preview, text
                if _URL_PATTERN.match(text):
                    return ClipboardCategory.URL, preview, text
                if _CODE_SNIPPET_PATTERN.search(text) and ("\n" in text or len(text) > 40):
                    return ClipboardCategory.CODE_SNIPPET, preview, text
                if len(text) > 120:
                    return ClipboardCategory.LONG_TEXT, preview, text
                return ClipboardCategory.PLAIN_TEXT, preview, text
        except Exception as exc:
            logger.debug(f"Erro ao inspecionar clipboard no detector de contexto: {exc}")

        return ClipboardCategory.EMPTY, "", ""
