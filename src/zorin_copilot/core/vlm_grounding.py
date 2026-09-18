# Decisão de design: Grounding visual local via VLM (Vision-Language Models) para Linux/Wayland.
# Permite localizar com precisão milimétrica elementos gráficos, ícones, controles de canvas,
# jogos, portais educacionais (AVA/SCORM) e interfaces onde a árvore de acessibilidade (AT-SPI) é cega
# e onde o OCR tradicional é insuficiente.
#
# FLUXO DE EXECUÇÃO:
# 1. Captura da tela ou janela alvo via ScreenCaptureService mantendo aspect ratio e resolução adequada.
# 2. Varredura via VLM local (Ollama API: qwen2.5vl, minicpm-v, llava, moondream) com fallback para Gemini.
# 3. Extração e normalização de coordenadas [0..1000] -> [0.0..1.0] e mapeamento para coordenadas de tela.
# 4. Retorno estruturado de VisualElement com bounding box, centro absoluto e grau de confiança.

"""Módulo de Grounding Visual e Localização Espacial via VLM (Vision-Language Model)."""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Sequence

from .config import CopilotConfig
from .fence import FenceMode, ScreenFenceManager

logger = logging.getLogger(__name__)

KNOWN_VISION_FAMILIES: set[str] = {
    "qwen25vl",
    "qwen2-vl",
    "minicpm-v",
    "llava",
    "llama-vision",
    "mllama",
    "moondream",
    "bakllava",
    "granite-vision",
}

KNOWN_VISION_NAME_PARTS: tuple[str, ...] = (
    "vl",
    "vision",
    "minicpm-v",
    "llava",
    "moondream",
    "bakllava",
)


@dataclass
class VLMBoundingBox:
    """Caixa delimitadora normalizada [0.0..1.0] e utilitários de projeção em pixels."""

    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @classmethod
    def from_raw(
        cls,
        coords: Sequence[float | int],
        point: Sequence[float | int] | None = None,
        is_gemini: bool = False,
    ) -> VLMBoundingBox:
        """Normaliza 4 coordenadas brutas para xmin, ymin, xmax, ymax em [0.0, 1.0]."""
        if len(coords) < 4:
            raise ValueError(f"Coordenadas insuficientes para bounding box: {coords}")

        c0, c1, c2, c3 = [float(v) for v in coords[:4]]
        if any(v > 1.0 for v in (c0, c1, c2, c3)):
            c0, c1, c2, c3 = c0 / 1000.0, c1 / 1000.0, c2 / 1000.0, c3 / 1000.0

        c0 = max(0.0, min(1.0, c0))
        c1 = max(0.0, min(1.0, c1))
        c2 = max(0.0, min(1.0, c2))
        c3 = max(0.0, min(1.0, c3))

        # Desambiguação de eixos usando o ponto central se disponível
        if point and len(point) >= 2:
            p0, p1 = float(point[0]), float(point[1])
            if p0 > 1.0 or p1 > 1.0:
                p0, p1 = p0 / 1000.0, p1 / 1000.0

            # Opção A: c0, c2 é X; c1, c3 é Y (estilo Qwen2.5-VL)
            in_a = (min(c0, c2) <= p0 <= max(c0, c2)) and (min(c1, c3) <= p1 <= max(c1, c3))
            # Opção B: c0, c2 é Y; c1, c3 é X (estilo Gemini / Google)
            in_b = (min(c0, c2) <= p1 <= max(c0, c2)) and (min(c1, c3) <= p0 <= max(c1, c3))

            if in_a and not in_b:
                return cls(xmin=min(c0, c2), ymin=min(c1, c3), xmax=max(c0, c2), ymax=max(c1, c3))
            elif in_b and not in_a:
                return cls(xmin=min(c1, c3), ymin=min(c0, c2), xmax=max(c1, c3), ymax=max(c0, c2))

        # Se não foi possível desambiguar pelo ponto, usa a convenção do provedor
        if is_gemini:
            # Gemini padrão: [ymin, xmin, ymax, xmax]
            return cls(xmin=min(c1, c3), ymin=min(c0, c2), xmax=max(c1, c3), ymax=max(c0, c2))
        else:
            # Padrão desktop / Qwen-VL: [xmin, ymin, xmax, ymax]
            return cls(xmin=min(c0, c2), ymin=min(c1, c3), xmax=max(c0, c2), ymax=max(c1, c3))

    @property
    def center_rel(self) -> tuple[float, float]:
        """Ponto central normalizado (rel_x, rel_y) em [0.0..1.0]."""
        return ((self.xmin + self.xmax) / 2.0, (self.ymin + self.ymax) / 2.0)

    @property
    def width_rel(self) -> float:
        return max(0.0, self.xmax - self.xmin)

    @property
    def height_rel(self) -> float:
        return max(0.0, self.ymax - self.ymin)

    def to_screen_rect(
        self,
        base_x: int,
        base_y: int,
        width: int,
        height: int,
    ) -> tuple[int, int, int, int]:
        """Converte coordenadas relativas para retângulo absoluto na tela (x, y, w, h)."""
        abs_x = base_x + int(round(self.xmin * width))
        abs_y = base_y + int(round(self.ymin * height))
        abs_w = max(1, int(round(self.width_rel * width)))
        abs_h = max(1, int(round(self.height_rel * height)))
        return abs_x, abs_y, abs_w, abs_h


@dataclass
class VLMGroundingResult:
    """Resultado estruturado da detecção visual por VLM."""

    found: bool
    x: int  # Centro X absoluto na tela
    y: int  # Centro Y absoluto na tela
    bbox: tuple[int, int, int, int]  # (x, y, largura, altura) na tela
    confidence: float
    query: str
    label: str = ""
    description: str = ""
    source: str = "vlm_local"
    model_name: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "x": self.x,
            "y": self.y,
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 2),
            "query": self.query,
            "label": self.label,
            "description": self.description,
            "source": self.source,
            "model_name": self.model_name,
            "error": self.error,
        }

    def to_visual_element(self) -> Any:
        """Converte para a classe VisualElement do ui_grounding."""
        from .ui_grounding import VisualElement

        return VisualElement(
            text=self.label or self.query,
            x=self.x,
            y=self.y,
            bbox=self.bbox,
            confidence=self.confidence * 100.0,
            is_phrase=True,
            source="vision_model",
        )


class LocalVLMGroundingClient:
    """Cliente de Grounding Visual que orquestra modelos locais no Ollama com fallback em nuvem."""

    def __init__(
        self,
        ollama_url: str | None = None,
        model: str | None = None,
        timeout_sec: float = 18.0,
        fallback_to_gemini: bool = True,
    ) -> None:
        cfg = CopilotConfig.load()
        self.ollama_url = (ollama_url or cfg.ollama_url or "http://127.0.0.1:11434").rstrip("/")
        self.preferred_model = model or getattr(cfg, "vlm_grounding_model", "") or cfg.ollama_vision_model or ""
        self.timeout_sec = timeout_sec
        self.fallback_to_gemini = fallback_to_gemini
        self._cached_vision_model: str | None = None

    def list_available_vision_models(self) -> list[str]:
        """Consulta o Ollama local e retorna a lista de modelos que possuem capacidade de visão."""
        try:
            req = urllib.request.Request(
                f"{self.ollama_url}/api/tags",
                headers={"User-Agent": "Zorin-Copilot-VLM/1.0"},
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            models = data.get("models", [])
            vision_models: list[str] = []

            for m in models:
                name = str(m.get("name", "")).strip()
                caps = m.get("capabilities", []) or []
                details = m.get("details", {}) or {}
                families = details.get("families", []) or []

                is_vision = False
                if "vision" in caps:
                    is_vision = True
                elif any(fam.lower() in KNOWN_VISION_FAMILIES for fam in families):
                    is_vision = True
                elif any(part in name.lower() for part in KNOWN_VISION_NAME_PARTS):
                    is_vision = True

                if is_vision and name not in vision_models:
                    vision_models.append(name)

            return vision_models
        except Exception as exc:
            logger.debug("Falha ao listar modelos do Ollama em %s: %s", self.ollama_url, exc)
            return []

    def select_vision_model(self, preferred: str | None = None) -> str | None:
        """Seleciona o modelo de visão mais apropriado instalado no Ollama."""
        target = preferred or self.preferred_model
        available = self.list_available_vision_models()
        if not available:
            return None

        # 1. Se especificado e disponível exatamente
        if target:
            for m in available:
                if m == target or m.split(":")[0] == target.split(":")[0]:
                    self._cached_vision_model = m
                    return m

        # 2. Ranking de preferência para modelos estado-da-arte conhecidos
        priority_keywords = ["qwen2.5vl:7b", "qwen2.5vl:3b", "qwen2.5vl", "minicpm-v", "llava", "moondream"]
        for kw in priority_keywords:
            for m in available:
                if kw in m.lower():
                    self._cached_vision_model = m
                    return m

        # 3. Retorna o primeiro modelo de visão detectado
        self._cached_vision_model = available[0]
        return available[0]

    def ground(
        self,
        query: str,
        image_bytes: bytes,
        fence: ScreenFenceManager | None = None,
        crop_rect: tuple[int, int, int, int] | None = None,
        model: str | None = None,
    ) -> VLMGroundingResult:
        """Executa a localização visual do elemento query na imagem fornecida."""
        if not query.strip():
            return VLMGroundingResult(
                found=False,
                x=0,
                y=0,
                bbox=(0, 0, 0, 0),
                confidence=0.0,
                query=query,
                error="Query de busca vazia.",
            )

        # 1. Tenta Grounding via VLM local (Ollama)
        local_model = self.select_vision_model(preferred=model)
        if local_model:
            try:
                res = self._ground_with_ollama(
                    query=query,
                    image_bytes=image_bytes,
                    model=local_model,
                    fence=fence,
                    crop_rect=crop_rect,
                )
                if res.found:
                    return res
                # Se não encontrou ou houve erro de parse, loga e prossegue para fallback se permitido
                logger.info("VLM local (%s) não localizou '%s': %s", local_model, query, res.description)
            except Exception as exc:
                logger.warning("Falha durante grounding com VLM local (%s): %s", local_model, exc)

        # 2. Fallback: Provedor em Nuvem (Gemini Vision)
        if self.fallback_to_gemini:
            cfg = CopilotConfig.load()
            if cfg.gemini_api_key.strip():
                try:
                    res_cloud = self._ground_with_gemini(
                        query=query,
                        image_bytes=image_bytes,
                        fence=fence,
                        crop_rect=crop_rect,
                    )
                    if res_cloud.found:
                        return res_cloud
                except Exception as exc:
                    logger.debug("Fallback Gemini Vision falhou: %s", exc)

        return VLMGroundingResult(
            found=False,
            x=0,
            y=0,
            bbox=(0, 0, 0, 0),
            confidence=0.0,
            query=query,
            source="none",
            error=f"Elemento '{query}' não localizado por VLM local nem nuvem.",
        )

    def _ground_with_ollama(
        self,
        query: str,
        image_bytes: bytes,
        model: str,
        fence: ScreenFenceManager | None = None,
        crop_rect: tuple[int, int, int, int] | None = None,
    ) -> VLMGroundingResult:
        """Executa chamada ao endpoint /api/generate do Ollama com schema JSON rígido."""
        b64_img = base64.b64encode(image_bytes).decode("utf-8")

        prompt = (
            "Você é um motor especializado de Grounding Visual e Localização de Controles de Interface (GUI Agent).\n"
            "Analise a imagem da tela fornecida e localize com precisão a posição do elemento correspondente à consulta:\n"
            f'"{query}"\n\n'
            "Responda EXCLUSIVAMENTE em formato JSON puro com a seguinte estrutura:\n"
            "{\n"
            '  "found": true,\n'
            '  "bbox": [xmin, ymin, xmax, ymax],\n'
            '  "point": [x, y],\n'
            '  "confidence": 0.95,\n'
            '  "label": "nome curto do elemento",\n'
            '  "description": "descrição objetiva do que foi localizado"\n'
            "}\n\n"
            "Diretrizes de Coordenadas:\n"
            "- As coordenadas devem estar normalizadas na escala de 0 a 1000.\n"
            "- xmin e xmax são horizontais (0 à esquerda, 1000 à direita).\n"
            "- ymin e ymax são verticais (0 no topo, 1000 na base).\n"
            "- point [x, y] é o centro clicável do elemento.\n"
            "- Se o elemento não estiver visível na imagem, responda: "
            '{"found": false, "confidence": 0.0, "description": "Elemento não visível"}'
        )

        payload = {
            "model": model,
            "prompt": prompt,
            "images": [b64_img],
            "format": "json",
            "stream": False,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
            },
        }

        req = urllib.request.Request(
            f"{self.ollama_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Zorin-Copilot-VLM/1.0"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            raw_body = resp.read().decode("utf-8")
            data = json.loads(raw_body)

        resp_text = str(data.get("response", "")).strip()
        found, bbox_obj, point_rel, conf, label, desc, raw_json = self._parse_vlm_output(
            resp_text, is_gemini=False
        )

        if not found or not bbox_obj:
            return VLMGroundingResult(
                found=False,
                x=0,
                y=0,
                bbox=(0, 0, 0, 0),
                confidence=conf,
                query=query,
                label=label,
                description=desc or "Não encontrado pelo modelo.",
                source="vlm_local",
                model_name=model,
                raw_response=raw_json,
            )

        cx, cy, screen_bbox = self._map_coordinates_to_screen(
            bbox_obj=bbox_obj,
            point_rel=point_rel,
            fence=fence,
            crop_rect=crop_rect,
        )

        return VLMGroundingResult(
            found=True,
            x=cx,
            y=cy,
            bbox=screen_bbox,
            confidence=conf,
            query=query,
            label=label or query,
            description=desc,
            source="vlm_local",
            model_name=model,
            raw_response=raw_json,
        )

    def _ground_with_gemini(
        self,
        query: str,
        image_bytes: bytes,
        fence: ScreenFenceManager | None = None,
        crop_rect: tuple[int, int, int, int] | None = None,
    ) -> VLMGroundingResult:
        """Fallback: chamada direta à API do Google Gemini com Grounding Visual."""
        cfg = CopilotConfig.load()
        api_key = cfg.gemini_api_key.strip()
        if not api_key:
            return VLMGroundingResult(
                found=False,
                x=0,
                y=0,
                bbox=(0, 0, 0, 0),
                confidence=0.0,
                query=query,
                source="gemini",
                error="Chave Gemini não configurada.",
            )

        b64_img = base64.b64encode(image_bytes).decode("utf-8")
        model = "gemini-2.5-flash"

        prompt = (
            "You are a GUI grounding assistant on Linux desktop. "
            f"Locate the UI element, icon, button or control described as: '{query}'.\n"
            "Return strictly a JSON object with schema:\n"
            "{\n"
            '  "found": true,\n'
            '  "box_2d": [ymin, xmin, ymax, xmax],\n'
            '  "point": [y, x],\n'
            '  "confidence": 0.95,\n'
            '  "label": "element name",\n'
            '  "description": "brief description"\n'
            "}\n"
            "Coordinates must be normalized to scale 0 to 1000. If element is absent, set found=false."
        )

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": b64_img,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=12.0) as resp:
            raw_body = resp.read().decode("utf-8")
            data = json.loads(raw_body)

        candidates = data.get("candidates", [])
        if not candidates:
            return VLMGroundingResult(
                found=False,
                x=0,
                y=0,
                bbox=(0, 0, 0, 0),
                confidence=0.0,
                query=query,
                source="gemini",
                error="Sem candidatos retornados pelo Gemini.",
            )

        text_part = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        found, bbox_obj, point_rel, conf, label, desc, raw_json = self._parse_vlm_output(
            text_part, is_gemini=True
        )

        if not found or not bbox_obj:
            return VLMGroundingResult(
                found=False,
                x=0,
                y=0,
                bbox=(0, 0, 0, 0),
                confidence=conf,
                query=query,
                source="gemini",
                model_name=model,
                raw_response=raw_json,
                error="Elemento não localizado pelo Gemini.",
            )

        cx, cy, screen_bbox = self._map_coordinates_to_screen(
            bbox_obj=bbox_obj,
            point_rel=point_rel,
            fence=fence,
            crop_rect=crop_rect,
        )

        return VLMGroundingResult(
            found=True,
            x=cx,
            y=cy,
            bbox=screen_bbox,
            confidence=conf,
            query=query,
            label=label or query,
            description=desc,
            source="gemini",
            model_name=model,
            raw_response=raw_json,
        )

    def _parse_vlm_output(
        self,
        raw_text: str,
        is_gemini: bool = False,
    ) -> tuple[bool, VLMBoundingBox | None, tuple[float, float] | None, float, str, str, dict[str, Any]]:
        """Interpreta robustamente respostas JSON de múltiplos modelos de visão."""
        s = raw_text.strip()
        # Remove blocos markdown ```json ... ``` se presentes
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s)
        if m:
            s = m.group(1).strip()

        data: dict[str, Any] = {}
        try:
            data = json.loads(s)
        except Exception:
            m_json = re.search(r"(\{[\s\S]*\})", s)
            if m_json:
                try:
                    data = json.loads(m_json.group(1))
                except Exception:
                    pass

        if not data:
            return False, None, None, 0.0, "", "Falha ao decodificar JSON", {}

        found = bool(data.get("found", True))
        conf = float(data.get("confidence", 0.90 if found else 0.0))
        label = str(data.get("label", ""))
        desc = str(data.get("description", ""))

        if not found:
            return False, None, None, conf, label, desc, data

        raw_coords = (
            data.get("bbox")
            or data.get("box_2d")
            or data.get("rect")
            or data.get("bounding_box")
        )

        raw_point = data.get("point") or data.get("center") or data.get("point_2d")
        point_coords: list[float] | None = None
        if isinstance(raw_point, (list, tuple)) and len(raw_point) >= 2:
            point_coords = [float(raw_point[0]), float(raw_point[1])]
        elif isinstance(raw_point, dict):
            px = float(raw_point.get("x", 0.0))
            py = float(raw_point.get("y", 0.0))
            point_coords = [px, py]

        bbox_obj: VLMBoundingBox | None = None
        if isinstance(raw_coords, (list, tuple)) and len(raw_coords) >= 4:
            try:
                bbox_obj = VLMBoundingBox.from_raw(
                    raw_coords,
                    point=point_coords,
                    is_gemini=is_gemini,
                )
            except Exception as exc:
                logger.debug("Erro ao criar VLMBoundingBox: %s", exc)

        # Se não temos bounding box mas temos ponto [x, y], cria uma pequena bounding box em torno do ponto
        if not bbox_obj and point_coords:
            p0, p1 = point_coords[0], point_coords[1]
            if p0 > 1.0 or p1 > 1.0:
                p0, p1 = p0 / 1000.0, p1 / 1000.0
            # Heurística: no Gemini point é [y, x], no local é [x, y]
            rx = p1 if is_gemini else p0
            ry = p0 if is_gemini else p1
            bbox_obj = VLMBoundingBox(
                xmin=max(0.0, rx - 0.02),
                ymin=max(0.0, ry - 0.02),
                xmax=min(1.0, rx + 0.02),
                ymax=min(1.0, ry + 0.02),
            )

        point_rel: tuple[float, float] | None = None
        if bbox_obj:
            point_rel = bbox_obj.center_rel
        elif point_coords:
            p0, p1 = point_coords[0], point_coords[1]
            if p0 > 1.0 or p1 > 1.0:
                p0, p1 = p0 / 1000.0, p1 / 1000.0
            point_rel = (p1, p0) if is_gemini else (p0, p1)

        return found, bbox_obj, point_rel, conf, label, desc, data

    def _map_coordinates_to_screen(
        self,
        bbox_obj: VLMBoundingBox,
        point_rel: tuple[float, float] | None,
        fence: ScreenFenceManager | None = None,
        crop_rect: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int, tuple[int, int, int, int]]:
        """Mapeia coordenadas relativas [0.0..1.0] para coordenadas absolutas da tela respeitando a cerca digital."""
        if crop_rect:
            base_x, base_y, base_w, base_h = crop_rect
        else:
            f = fence or ScreenFenceManager()
            if f.mode in (FenceMode.ACTIVE_WINDOW, FenceMode.CHOSEN_WINDOW) and f.get_target_window():
                win = f.get_target_window()
                assert win is not None
                base_x, base_y, base_w, base_h = win.x, win.y, win.width, win.height
            else:
                active_m = f.get_active_monitor()
                if active_m:
                    base_x, base_y, base_w, base_h = active_m.x, active_m.y, active_m.width, active_m.height
                else:
                    base_x, base_y, base_w, base_h = 0, 0, 1920, 1080

        screen_bbox = bbox_obj.to_screen_rect(base_x, base_y, base_w, base_h)

        if point_rel:
            rel_x, rel_y = point_rel
            cx = base_x + int(round(rel_x * base_w))
            cy = base_y + int(round(rel_y * base_h))
        else:
            cx = screen_bbox[0] + screen_bbox[2] // 2
            cy = screen_bbox[1] + screen_bbox[3] // 2

        # Clamping de segurança dentro da área alvo
        cx = max(base_x, min(cx, base_x + base_w - 1))
        cy = max(base_y, min(cy, base_y + base_h - 1))

        return cx, cy, screen_bbox
