# Decisão de design: Grounding visual multimodal de baixa latência e OCR espacial para Linux/Wayland.
# Permite que o Zorin Copilot localize e interaja com botões, campos e textos em QUALQUER aplicativo
# (mesmo navegadores, apps Electron, jogos, LibreOffice ou telas onde o AT-SPI é cego ou incompleto).
#
# FLUXO DE EXECUÇÃO:
# 1. Captura rápida do monitor ativo em resolução 1:1 sem redimensionamento distorcido.
# 2. Varredura OCR por Tesseract local com extração de bounding boxes e agrupamento de frases (120-250ms).
# 3. Pontuação inteligente de casamento (exato > substring > similaridade difflib para tolerar ruídos de OCR).
# 4. Fallback para Grounding Multimodal via modelo de visão se o alvo for um ícone ou elemento gráfico.
# 5. Deslocamento do Ghost Cursor e emissão de clique virtual seguro via VirtualInputDriver.

"""Serviço de Grounding Visual e Localização Espacial de Elementos de Interface."""

from __future__ import annotations

import difflib
import json
import logging
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Sequence

from .fence import FenceMode, ScreenFenceManager
from .vision import ScreenCaptureService

logger = logging.getLogger(__name__)


@dataclass
class VisualElement:
    """Elemento visual localizado na tela via OCR ou Grounding Multimodal."""

    text: str
    x: int  # centro X absoluto na tela
    y: int  # centro Y absoluto na tela
    bbox: tuple[int, int, int, int]  # (x, y, largura, altura) em coordenadas de tela
    confidence: float
    is_phrase: bool = False
    source: str = "ocr"  # "ocr" | "vision_model"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "bbox": list(self.bbox),
            "confidence": round(self.confidence, 2),
            "is_phrase": self.is_phrase,
            "source": self.source,
        }


def get_tesseract_languages() -> str:
    """Detecta os modelos de linguagem instalados para o Tesseract."""
    try:
        res = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        langs = [line.strip() for line in res.stdout.splitlines()[1:] if line.strip()]
        selected = []
        if "por" in langs:
            selected.append("por")
        if "eng" in langs:
            selected.append("eng")
        return "+".join(selected) if selected else (langs[0] if langs else "por")
    except Exception:
        return "por"


def parse_tesseract_tsv(
    tsv_content: str,
    offset_x: int = 0,
    offset_y: int = 0,
    min_confidence: float = 25.0,
) -> list[VisualElement]:
    """Interpreta a saída TSV do Tesseract, extraindo palavras e agrupando linhas em frases."""
    elements: list[VisualElement] = []
    lines = tsv_content.strip().splitlines()
    if len(lines) <= 1:
        return elements

    # Dicionário para agrupar palavras por linha: (block_num, par_num, line_num) -> list[word_data]
    lines_dict: dict[tuple[int, int, int], list[dict[str, Any]]] = {}

    for row in lines[1:]:
        parts = row.split("\t")
        if len(parts) < 12:
            continue

        raw_text = parts[11].strip()
        if not raw_text:
            continue

        try:
            level = int(parts[0])
            block_num = int(parts[2])
            par_num = int(parts[3])
            line_num = int(parts[4])
            left = int(parts[6])
            top = int(parts[7])
            width = int(parts[8])
            height = int(parts[9])
            conf = float(parts[10])
        except (ValueError, IndexError):
            continue

        if conf < min_confidence or level != 5:  # level 5 = word
            continue

        # Coordenadas absolutas na tela
        abs_x = offset_x + left
        abs_y = offset_y + top
        center_x = abs_x + width // 2
        center_y = abs_y + height // 2

        word_el = VisualElement(
            text=raw_text,
            x=center_x,
            y=center_y,
            bbox=(abs_x, abs_y, width, height),
            confidence=conf,
            is_phrase=False,
            source="ocr",
        )
        elements.append(word_el)

        line_key = (block_num, par_num, line_num)
        if line_key not in lines_dict:
            lines_dict[line_key] = []
        lines_dict[line_key].append({
            "text": raw_text,
            "x": abs_x,
            "y": abs_y,
            "w": width,
            "h": height,
            "conf": conf,
        })

    # Agrupa palavras consecutivas em frases para busca de termos compostos
    for _key, words in lines_dict.items():
        if len(words) >= 2:
            phrase_text = " ".join(w["text"] for w in words)
            min_x = min(w["x"] for w in words)
            min_y = min(w["y"] for w in words)
            max_x = max(w["x"] + w["w"] for w in words)
            max_y = max(w["y"] + w["h"] for w in words)
            p_w = max_x - min_x
            p_h = max_y - min_y
            avg_conf = sum(w["conf"] for w in words) / len(words)

            elements.append(
                VisualElement(
                    text=phrase_text,
                    x=min_x + p_w // 2,
                    y=min_y + p_h // 2,
                    bbox=(min_x, min_y, p_w, p_h),
                    confidence=avg_conf,
                    is_phrase=True,
                    source="ocr",
                )
            )

    return elements


def _normalize_text(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s.strip().lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def score_text_match(query: str, target: str) -> float:
    """Calcula pontuação de compatibilidade [0.0..1.0] entre a consulta e o texto detectado."""
    q_raw = query.strip().lower()
    t_raw = target.strip().lower()
    if not q_raw or not t_raw:
        return 0.0

    # 1. Casamento exato (bruto ou com acentuação normalizada)
    if q_raw == t_raw:
        return 1.0

    q_norm = _normalize_text(q_raw)
    t_norm = _normalize_text(t_raw)
    if q_norm == t_norm:
        return 1.0

    # 2. Casamento de substring
    # Caso A: A query inteira é substring do alvo (ex: "Salvar" em "Salvar Como...", "Buscar produtos" em "Buscar produtos, marcas e mais")
    for q, t in ((q_raw, t_raw), (q_norm, t_norm)):
        if q in t:
            if len(q) < 3:
                # Letras curtas (1-2 caracteres como 'a', 'o', 'ok') só dão match se o alvo for quase do mesmo tamanho
                if len(t) <= len(q) + 1:
                    return 0.90
                continue

            # Verifica se q aparece como palavra inteira/frase delimitada
            pattern = rf"(?:\b|^){re.escape(q)}(?:\b|$)"
            is_word_boundary = bool(re.search(pattern, t))
            ratio = len(q) / max(len(t), 1)

            if is_word_boundary:
                # Palavra/frase inteira presente: alta confiança proporcional ao tamanho
                return min(1.0, 0.85 + 0.12 * ratio)
            else:
                # Apenas substring interna sem limite de palavra (ex: "car" em "scarce")
                return 0.50 * ratio

    # Caso B: O alvo detectado é substring da query (ex: "Configurac" em "Configuracoes")
    # CUIDADO CRÍTICO: Letras isoladas ou palavras minúsculas NUNCA devem casar com consultas longas
    # (ex: 'o' em 'Máximo', 'e' em 'Preço', '1.' em 'Até R$ 1.500').
    for q, t in ((q_raw, t_raw), (q_norm, t_norm)):
        if t in q:
            if len(t) < 3:
                # Fragmento mínimo de OCR (1-2 chars) jamais deve pontuar alto para queries >= 3 chars
                continue

            coverage = len(t) / max(len(q), 1)
            # Se cobre quase toda a query (>= 80%, ex: pequeno truncamento de OCR na borda)
            if coverage >= 0.80:
                return min(1.0, 0.70 + 0.20 * coverage)
            # Se for apenas uma fração da query, a pontuação é baixa e NUNCA atinge o threshold de 0.65
            return 0.30 * coverage

    # 3. Tolerância a ruídos de OCR via SequenceMatcher (difflib)
    matcher_raw = difflib.SequenceMatcher(None, q_raw, t_raw).ratio()
    matcher_norm = difflib.SequenceMatcher(None, q_norm, t_norm).ratio()
    return max(matcher_raw, matcher_norm)


class UIGroundingService:
    """Motor de localização espacial e OCR de baixa latência para o desktop."""

    @classmethod
    def scan_screen(
        cls,
        fence: ScreenFenceManager | None = None,
        image_bytes: bytes | None = None,
        crop_rect: tuple[int, int, int, int] | None = None,
        exclude_self: bool = True,
    ) -> list[VisualElement]:
        """Captura a tela ou janela e extrai todos os elementos textuais visíveis com coordenadas."""
        tess_bin = shutil.which("tesseract")
        if not tess_bin:
            logger.debug("Tesseract não encontrado no sistema.")
            return []

        offset_x = 0
        offset_y = 0

        # Se imagem não fornecida, captura o alvo ativo (janela, monitor ou área solicitada)
        if image_bytes is None:
            f = fence or ScreenFenceManager()
            if crop_rect:
                target_crop = crop_rect
                offset_x, offset_y = crop_rect[0], crop_rect[1]
            elif f.mode in (FenceMode.ACTIVE_WINDOW, FenceMode.CHOSEN_WINDOW) and f.get_target_window():
                win = f.get_target_window()
                assert win is not None
                target_crop = (win.x, win.y, win.width, win.height)
                offset_x, offset_y = win.x, win.y
            else:
                active_m = f.get_active_monitor()
                if active_m:
                    # Recorta no monitor autorizado para acelerar e manter coordenadas exatas
                    target_crop = (active_m.x, active_m.y, active_m.width, active_m.height)
                    offset_x, offset_y = active_m.x, active_m.y
                else:
                    target_crop = None

            # max_size=0 preserva a resolução 1:1 original para precisão pixel-perfect
            ok, img_data, _mode = ScreenCaptureService.capture(
                interactive=False, max_size=0, crop_rect=target_crop
            )
            if not ok or not img_data:
                logger.warning("Falha ao capturar imagem da tela para OCR.")
                return []
            image_bytes = img_data

        lang = get_tesseract_languages()
        try:
            cmd = [tess_bin, "stdin", "stdout", "-l", lang, "--dpi", "96", "tsv"]
            proc = subprocess.run(
                cmd,
                input=image_bytes,
                capture_output=True,
                timeout=4.0,
                check=False,
            )
            if proc.returncode != 0:
                logger.warning("Tesseract falhou com código %d: %s", proc.returncode, proc.stderr[:100])
                return []

            tsv_text = proc.stdout.decode("utf-8", errors="replace")
            elements = parse_tesseract_tsv(tsv_text, offset_x=offset_x, offset_y=offset_y)

            # Exclui elementos que se encontram dentro da janela do próprio Copilot
            if exclude_self:
                f = fence or ScreenFenceManager()
                excluded_rects = f.get_all_excluded_rects()
                if excluded_rects:
                    elements = [
                        el for el in elements
                        if not any(rx <= el.x < rx + rw and ry <= el.y < ry + rh for rx, ry, rw, rh in excluded_rects)
                    ]

            return elements

        except Exception as exc:
            logger.error(f"Erro ao processar OCR da tela: {exc}")
            return []

    @classmethod
    def find_elements(
        cls,
        query: str,
        elements: Sequence[VisualElement] | None = None,
        threshold: float = 0.65,
        fence: ScreenFenceManager | None = None,
    ) -> list[tuple[VisualElement, float]]:
        """Localiza candidatos na tela que correspondem ao rótulo ou texto procurado."""
        if not query.strip():
            return []

        all_elements = elements if elements is not None else cls.scan_screen(fence=fence)

        # Se elementos foram passados externamente, garante exclusão da janela do Copilot
        f = fence or ScreenFenceManager()
        excluded_rects = f.get_all_excluded_rects()
        if excluded_rects and elements is not None:
            all_elements = [
                el for el in all_elements
                if not any(rx <= el.x < rx + rw and ry <= el.y < ry + rh for rx, ry, rw, rh in excluded_rects)
            ]

        candidates: list[tuple[VisualElement, float]] = []

        for el in all_elements:
            score = score_text_match(query, el.text)
            if score >= threshold:
                candidates.append((el, score))

        # Ordena: melhor pontuação primeiro, desempatando por maior confiança de OCR
        candidates.sort(key=lambda item: (item[1], item[0].confidence), reverse=True)
        return candidates

    @classmethod
    def ground_with_vision_model(
        cls,
        query: str,
        image_bytes: bytes,
        fence: ScreenFenceManager | None = None,
    ) -> tuple[int, int] | None:
        """Fallback: solicita ao modelo de visão as coordenadas [0..1000] de um elemento não textual ou ícone."""
        try:
            from ..ai.providers import ProviderFactory
            from ..core.config import CopilotConfig

            cfg = CopilotConfig.load()
            provider = ProviderFactory.create(cfg)

            prompt = (
                f"Você é um operador de computador no Linux. Localize o seguinte elemento visual na tela: '{query}'. "
                "Responda ESTRITAMENTE em formato JSON com as coordenadas normalizadas do centro do elemento "
                "na escala de 0 a 1000: {\"point\": [y, x], \"confidence\": 0.95}"
            )

            # Usa o método de visão do provider
            res = provider.analyze_image(prompt, image_bytes)
            # Extrai coordenadas JSON
            match = re.search(r"\{\s*\"point\"\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]", res)
            if match:
                y_norm = int(match.group(1))
                x_norm = int(match.group(2))
                f = fence or ScreenFenceManager()
                abs_x, abs_y = f.convert_relative_point(x_norm / 1000.0, y_norm / 1000.0)
                logger.info("Elemento '%s' localizado via Visão Multimodal em (%d, %d)", query, abs_x, abs_y)
                return abs_x, abs_y

        except Exception as exc:
            logger.debug("Falha no Grounding Multimodal via modelo de visão: %s", exc)

        return None

    @classmethod
    def click_visual_element(
        cls,
        query: str,
        button: str = "left",
        double: bool = False,
        driver: Any | None = None,
        fence: ScreenFenceManager | None = None,
    ) -> tuple[bool, str, tuple[int, int] | None]:
        """Localiza um elemento visualmente e executa o clique com animação do Ghost Cursor."""
        f = fence or ScreenFenceManager()
        if f.is_emergency_stopped:
            return False, "Operação cancelada: Parada de emergência (Kill Switch) ativa.", None

        # 1. Varredura rápida local por OCR (baixa latência ~150-250ms)
        candidates = cls.find_elements(query, fence=f)
        if candidates:
            best_el, score = candidates[0]
            cx, cy = best_el.x, best_el.y
            logger.info("Elemento '%s' encontrado via OCR: '%s' (score %.2f) em (%d, %d)", query, best_el.text, score, cx, cy)

            allowed, reason = f.is_coordinate_allowed(cx, cy)
            if not allowed:
                return False, f"Clique bloqueado pela cerca digital: {reason}", (cx, cy)

            # Executa com feedback do Ghost Cursor
            from ..shell.input_driver import VirtualInputDriver
            input_drv = driver or VirtualInputDriver(fence=f)
            ok, msg = input_drv.click(
                cx,
                cy,
                button=button,
                double=double,
                label=f"Clicando em '{best_el.text}'",
            )
            return ok, msg, (cx, cy)

        # 2. Fallback: Grounding Multimodal via Modelo de Visão
        ok_shot, img_bytes, _mode = ScreenCaptureService.capture(interactive=False, max_size=1280)
        if ok_shot and img_bytes:
            coords = cls.ground_with_vision_model(query, img_bytes, fence=f)
            if coords:
                cx, cy = coords
                allowed, reason = f.is_coordinate_allowed(cx, cy)
                if not allowed:
                    return False, f"Clique bloqueado pela cerca digital: {reason}", (cx, cy)

                from ..shell.input_driver import VirtualInputDriver
                input_drv = driver or VirtualInputDriver(fence=f)
                ok, msg = input_drv.click(
                    cx,
                    cy,
                    button=button,
                    double=double,
                    label=f"Clicando em '{query}'",
                )
                return ok, msg, (cx, cy)

        return False, f"Elemento visual com rótulo '{query}' não localizado na tela.", None
