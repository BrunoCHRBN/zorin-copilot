# Decisão de design: canal de percepção que funde AT-SPI2 (semântica, determinística) com
# visão (screenshot + grounding VLM). Imports pesados (gi/Atspi, Pillow, requests) são
# feitos SOB DEMANDA dentro dos métodos para manter o módulo importável em headless/CI e
# permitir injeção de dobles nos testes. O loop do agente consome um `PerceptionChannel`
# qualquer — o desktop real ou um fake de teste.

"""Percepção fusionada do desktop: árvore semântica + screenshot + grounding visual."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class ScreenState:
    """Estado observável da tela num instante."""

    ui_tree: Any = None  # UIElement | None — árvore AT-SPI2
    screenshot_bytes: bytes | None = None
    screenshot_mime: str = "image/jpeg"
    app: str | None = None
    timestamp: float = field(default_factory=time.monotonic)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_summary(self, include_bounds: bool = False) -> str:
        """Texto compacto para o prompt do planejador (semântica + presença de imagem)."""
        parts = [f"[app={self.app or '?'} screenshot={'sim' if self.screenshot_bytes else 'nao'}]"]
        tree = self.ui_tree
        if tree is not None and hasattr(tree, "to_summary"):
            parts.append(tree.to_summary(include_bounds=include_bounds))
        return "\n".join(parts)


class PerceptionChannel(Protocol):
    """Fonte de percepção injetável: AT-SPI, visão, ou dublê de teste."""

    def observe(self) -> ScreenState:
        """Lê o estado atual da tela (semântica + imagem)."""
        ...

    def ground(self, query: str, mode: str = "bbox") -> list[dict[str, Any]]:
        """Localiza `query` na tela via VLM e devolve caixas delimitadoras."""
        ...


class VisionPerceptionChannel:
    """Percepção que combina árvore AT-SPI2, screenshot e grounding VLM.

    - `inspector`  -> DesktopInspector (semântica: roles, labels, coords, ações)
    - `capture`    -> ScreenCaptureService (imagem para o VLM)
    - `grounding`  -> LocalVLMGroundingClient (traduz "primeiro link" em bbox)
    Qualquer um pode ser None (ex.: headless) e é ignorado com log debug.
    """

    def __init__(
        self,
        *,
        inspector: Any | None = None,
        capture_service: Any | None = None,
        grounding_client: Any | None = None,
        app: str | None = None,
    ) -> None:
        self.inspector = inspector
        self.capture_service = capture_service
        self.grounding_client = grounding_client
        self.app = app
        self._last_screenshot: bytes | None = None

    def observe(self) -> ScreenState:
        ui_tree = None
        try:
            if self.inspector is not None:
                ui_tree = self.inspector.get_ui_tree(self.app)
        except Exception as exc:  # percepção nunca derruba o loop
            logger.debug("Falha ao ler arvore AT-SPI: %s", exc)

        screenshot_bytes = None
        try:
            if self.capture_service is not None:
                ok, data, _err = self.capture_service.capture(
                    interactive=False, max_size=1280, quality=80
                )
                if ok and data:
                    screenshot_bytes = data
                    self._last_screenshot = data
        except Exception as exc:
            logger.debug("Falha ao capturar tela: %s", exc)

        return ScreenState(ui_tree=ui_tree, screenshot_bytes=screenshot_bytes, app=self.app)

    def ground(self, query: str, mode: str = "bbox") -> list[dict[str, Any]]:
        if self.grounding_client is None or self._last_screenshot is None:
            return []
        try:
            result = self.grounding_client.ground(query, self._last_screenshot)
            if result is None:
                return []
            boxes = getattr(result, "boxes", None) or []
            out: list[dict[str, Any]] = []
            for box in boxes:
                if hasattr(box, "__dataclass_fields__"):
                    import dataclasses

                    out.append(dataclasses.asdict(box))
                elif isinstance(box, dict):
                    out.append(box)
                else:
                    out.append(getattr(box, "__dict__", {}))
            return out
        except Exception as exc:
            logger.debug("Grounding visual falhou para '%s': %s", query, exc)
            return []


def build_vision_perception(*, app: str | None = None) -> VisionPerceptionChannel:
    """Monta a percepção real (AT-SPI + screenshot + VLM) com imports lazy.

    Usado pelo CLI/UI para ligar o runtime a um desktop de verdade; os módulos
    pesados (gi/Atspi, Pillow, requests) só são importados neste momento.
    """
    from ..core.a11y import DesktopInspector
    from ..core.vision import ScreenCaptureService

    grounding_client = None
    try:
        from ..core.vlm_grounding import LocalVLMGroundingClient

        grounding_client = LocalVLMGroundingClient()
    except Exception as exc:  # VLM opcional: sem ele, a percepção fica só semântica
        logger.debug("Cliente de grounding VLM indisponível: %s", exc)

    return VisionPerceptionChannel(
        inspector=DesktopInspector(),
        capture_service=ScreenCaptureService(),
        grounding_client=grounding_client,
        app=app,
    )


def hash_state(state: ScreenState) -> str:
    """Assinatura barata do estado para a memória anti-loop.

    Domina a semântica (resumo da árvore, determinístico) e entra o screenshot
    apenas como checksum leve. Dois estados visualmente distintos mas com a mesma
    árvore caem no mesmo bucket — o que é desejado para 'não repita a mesma ação
    no mesmo layout'.
    """
    tree_sig = ""
    if state.ui_tree is not None and hasattr(state.ui_tree, "to_summary"):
        tree_sig = state.ui_tree.to_summary(include_bounds=False)
    img_sig = "noimg"
    if state.screenshot_bytes:
        img_sig = hashlib.sha1(state.screenshot_bytes).hexdigest()[:12]
    raw = f"{state.app or '?'}|{tree_sig}|{img_sig}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]
