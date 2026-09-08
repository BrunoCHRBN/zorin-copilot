# Decisão de design: a captura deixou de falar direto com o portal XDG e passou por
# `core.desktop.screenshot`, que escolhe o backend pelo ambiente — grim/slurp em wlroots
# (Hyprland, Sway), spectacle no Plasma, portal no GNOME. A otimização (recorte, resize, JPEG)
# continua aqui, num único lugar.

"""Serviço de captura e visão computacional da tela para o Zorin Copilot."""

from __future__ import annotations

import io
import logging
import os
from typing import Any, Tuple

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

logger = logging.getLogger(__name__)


class ScreenCaptureService:
    """Serviço nativo para captura de tela completa ou seleção de área no Zorin OS."""

    @classmethod
    def capture(
        cls,
        interactive: bool = True,
        max_size: int = 1280,
        quality: int = 85,
        timeout_sec: int = 45,
        crop_rect: Tuple[int, int, int, int] | None = None,
    ) -> Tuple[bool, bytes | None, str]:
        """
        Captura a tela ou permite selecionar uma área interativamente.

        Args:
            interactive: Se True, abre o seletor nativo do GNOME para o usuário recortar uma área.
                         Se False, tira screenshot imediato da tela inteira.
            max_size: Dimensão máxima (largura/altura) para redimensionar e economizar tokens.
            quality: Qualidade JPEG (85 é o equilíbrio ideal entre legibilidade e tamanho).
            timeout_sec: Tempo máximo de espera pela interação do usuário.
            crop_rect: Bounding box opcional (x, y, largura, altura) para recortar a janela ativa.

        Returns:
            Tuple[sucesso, bytes_da_imagem, mensagem_ou_modo]
        """
        from .desktop.screenshot import select_backend

        backend = select_backend()
        logger.debug(f"Captura de tela via backend '{backend.name}' (interactive={interactive})")

        try:
            shot = backend.capture(interactive=interactive, timeout_sec=timeout_sec)
        except Exception as exc:
            logger.error(f"Backend de captura '{backend.name}' falhou: {exc}")
            return False, None, f"Falha na captura: {exc}"

        if not shot.ok or shot.path is None:
            return False, None, shot.message or "Captura cancelada pelo usuário."

        file_path = str(shot.path)

        if not os.path.isfile(file_path):
            return False, None, f"Arquivo de captura não encontrado: {file_path}"

        try:
            # Processa e otimiza a imagem em memória
            image_bytes = cls._optimize_image(
                file_path, max_size=max_size, quality=quality, crop_rect=crop_rect
            )
            mode_desc = "janela_ativa" if crop_rect else ("area_selecionada" if interactive else "tela_inteira")
            return True, image_bytes, mode_desc
        except Exception as exc:
            logger.error(f"Erro ao processar imagem capturada: {exc}")
            return False, None, f"Erro no processamento da imagem: {exc}"
        finally:
            # Remove o arquivo temporário da pasta do usuário para manter o sistema limpo
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError:
                pass

    @classmethod
    def _optimize_image(
        cls,
        file_path: str,
        max_size: int = 1280,
        quality: int = 85,
        crop_rect: Tuple[int, int, int, int] | None = None,
    ) -> bytes:
        """Lê a imagem, recorta região específica se solicitada, redimensiona e converte para JPEG otimizado."""
        if HAS_PIL:
            with Image.open(file_path) as img:
                # Converte RGBA para RGB se necessário
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                # Recorte na janela em foco se bounding box fornecido
                if crop_rect:
                    cx, cy, cw, ch = crop_rect
                    w_orig, h_orig = img.size
                    x1 = max(0, min(w_orig - 1, cx))
                    y1 = max(0, min(h_orig - 1, cy))
                    x2 = max(x1 + 10, min(w_orig, cx + cw))
                    y2 = max(y1 + 10, min(h_orig, cy + ch))
                    if (x2 - x1) >= 60 and (y2 - y1) >= 60:
                        img = img.crop((x1, y1, x2, y2))

                # Redimensiona mantendo proporção se exceder max_size
                w, h = img.size
                if w > max_size or h > max_size:
                    if w > h:
                        new_w = max_size
                        new_h = int(h * (max_size / w))
                    else:
                        new_h = max_size
                        new_w = int(w * (max_size / h))
                    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

                out_buf = io.BytesIO()
                img.save(out_buf, format="JPEG", quality=quality, optimize=True)
                return out_buf.getvalue()

        # Fallback: lê bytes brutos caso PIL não esteja disponível
        with open(file_path, "rb") as f:
            return f.read()

    @classmethod
    def get_privacy_shield_image(cls, width: int = 640, height: int = 360) -> bytes:
        """
        Gera um quadro elegante de proteção visual (tela preta com moldura e selo de privacidade)
        para envio seguro ao modelo quando o usuário estiver visualizando uma janela sensível.
        """
        if HAS_PIL:
            try:
                from PIL import ImageDraw
                img = Image.new("RGB", (width, height), color=(16, 16, 22))
                draw = ImageDraw.Draw(img)
                # Borda externa azul-ciano sutil estilo Zorin
                draw.rectangle([(6, 6), (width - 7, height - 7)], outline=(53, 132, 228), width=3)
                # Textos informativos centrais
                title = "🛡️ Zorin Copilot • Privacy Shield"
                sub = "Visao de tela pausada: Janela sensivel em foco"
                sub2 = "Protegendo senhas, dados bancarios e informacoes confidenciais"
                draw.text((width // 2 - 130, height // 2 - 28), title, fill=(255, 255, 255))
                draw.text((width // 2 - 165, height // 2 + 2), sub, fill=(180, 185, 200))
                draw.text((width // 2 - 210, height // 2 + 24), sub2, fill=(130, 135, 150))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                return buf.getvalue()
            except Exception as exc:
                logger.debug(f"Erro ao desenhar imagem do Privacy Shield: {exc}")

        return b""

    @classmethod
    def compute_frame_diff(cls, img1: bytes | Any | None, img2: bytes | Any | None) -> float:
        """
        Calcula a diferença perceptual normalizada [0.0..1.0] entre dois frames (bytes JPEG ou PIL Image).
        Retorna 1.0 se um dos frames for nulo ou se ocorrer falha na decodificação.
        """
        return compute_frame_diff(img1, img2)


def compute_frame_diff(img1: bytes | Any | None, img2: bytes | Any | None) -> float:
    """Calcula a diferença perceptual normalizada [0.0..1.0] entre dois frames."""
    if img1 is None or img2 is None or not HAS_PIL:
        return 1.0
    try:
        if isinstance(img1, bytes):
            im1 = Image.open(io.BytesIO(img1))
        else:
            im1 = img1

        if isinstance(img2, bytes):
            im2 = Image.open(io.BytesIO(img2))
        else:
            im2 = img2

        # Reduz para matriz 32x32 em escala de cinza para comparação ultrarrápida (~0.4ms)
        thumb1 = im1.resize((32, 32)).convert("L")
        thumb2 = im2.resize((32, 32)).convert("L")
        d1 = list(thumb1.getdata())
        d2 = list(thumb2.getdata())
        total_diff = sum(abs(a - b) for a, b in zip(d1, d2))
        return total_diff / (1024.0 * 255.0)
    except Exception as exc:
        logger.debug(f"Erro ao calcular diferença de frames: {exc}")
        return 1.0

