# Decisão de design: Gerenciador de área de transferência (Clipboard Inteligente)
# integrando suporte a Wayland (wl-clipboard) e X11/GTK com detecção automática de texto
# e imagens, permitindo análise contextual direta, tradução, correção e explicação de código.

"""Serviço de gerenciamento inteligente da área de transferência (Clipboard)."""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
from typing import Tuple

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

logger = logging.getLogger(__name__)


class ClipboardService:
    """Serviço para leitura, prévia e manipulação segura da área de transferência."""

    @classmethod
    def is_available(cls) -> bool:
        """Verifica se ferramentas de clipboard (wl-paste ou xclip) estão presentes."""
        return bool(shutil.which("wl-paste") or shutil.which("xclip"))

    @classmethod
    def get_content(cls) -> Tuple[str, str | bytes | None]:
        """
        Detecta e extrai o conteúdo atual da área de transferência.

        Returns:
            Tuple[tipo, dados]:
                - ("text", string_do_texto)
                - ("image", bytes_da_imagem_jpeg)
                - ("empty", None)
        """
        # 1. Tenta via wl-paste (nativo no Zorin OS Wayland)
        if shutil.which("wl-paste"):
            try:
                types_proc = subprocess.run(
                    ["wl-paste", "--list-types"],
                    capture_output=True,
                    text=True,
                    timeout=1.2,
                )
                if types_proc.returncode == 0:
                    lines = [l.strip() for l in types_proc.stdout.splitlines() if l.strip()]

                    # Prioridade 1: Texto plano
                    text_mime = next(
                        (m for m in lines if "text/plain" in m or m in ("UTF8_STRING", "STRING", "TEXT")),
                        None,
                    )
                    if text_mime:
                        text_proc = subprocess.run(
                            ["wl-paste", "--type", text_mime, "--no-newline"],
                            capture_output=True,
                            text=True,
                            timeout=2.0,
                        )
                        if text_proc.returncode == 0 and text_proc.stdout.strip():
                            return "text", text_proc.stdout.strip()

                    # Prioridade 2: Imagem
                    img_mime = next(
                        (m for m in lines if "image/png" in m or "image/jpeg" in m or "image/" in m),
                        None,
                    )
                    if img_mime:
                        img_proc = subprocess.run(
                            ["wl-paste", "--type", img_mime],
                            capture_output=True,
                            timeout=3.0,
                        )
                        if img_proc.returncode == 0 and img_proc.stdout:
                            return "image", cls._optimize_image_bytes(img_proc.stdout)
            except Exception as exc:
                logger.debug(f"Erro ao acessar wl-paste: {exc}")

        # 2. Fallback via xclip (se em sessão X11 ou sob XWayland)
        if shutil.which("xclip"):
            try:
                x_proc = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                )
                if x_proc.returncode == 0 and x_proc.stdout.strip():
                    return "text", x_proc.stdout.strip()
            except Exception as exc:
                logger.debug(f"Erro ao acessar xclip: {exc}")

        return "empty", None

    @classmethod
    def get_text(cls) -> str | None:
        """Retorna o texto copiado, se houver."""
        kind, val = cls.get_content()
        return val if kind == "text" and isinstance(val, str) else None

    @classmethod
    def get_preview(cls, max_len: int = 50) -> str:
        """Retorna uma linha de resumo do que está na área de transferência."""
        kind, val = cls.get_content()
        if kind == "text" and isinstance(val, str):
            clean = " ".join(val.split())
            if len(clean) > max_len:
                return f"{clean[:max_len]}..."
            return clean
        elif kind == "image":
            return "[Imagem copiada na área de transferência]"
        return "[Área de transferência vazia]"

    @classmethod
    def set_text(cls, text: str) -> bool:
        """Copia um texto para a área de transferência do sistema."""
        if shutil.which("wl-copy"):
            try:
                res = subprocess.run(
                    ["wl-copy", text],
                    check=False,
                    timeout=2.0,
                )
                if res.returncode == 0:
                    return True
            except Exception as exc:
                logger.debug(f"Erro ao executar wl-copy: {exc}")

        if shutil.which("xclip"):
            try:
                proc = subprocess.Popen(
                    ["xclip", "-selection", "clipboard"],
                    stdin=subprocess.PIPE,
                )
                proc.communicate(input=text.encode("utf-8"), timeout=2.0)
                if proc.returncode == 0:
                    return True
            except Exception as exc:
                logger.debug(f"Erro ao executar xclip: {exc}")

        return False

    @classmethod
    def _optimize_image_bytes(cls, raw_bytes: bytes, max_size: int = 1280, quality: int = 85) -> bytes:
        """Redimensiona e converte bytes de imagem para JPEG leve."""
        if HAS_PIL:
            try:
                with Image.open(io.BytesIO(raw_bytes)) as img:
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    w, h = img.size
                    if w > max_size or h > max_size:
                        if w > h:
                            new_w = max_size
                            new_h = int(h * (max_size / w))
                        else:
                            new_h = max_size
                            new_w = int(w * (max_size / h))
                        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    out = io.BytesIO()
                    img.save(out, format="JPEG", quality=quality, optimize=True)
                    return out.getvalue()
            except Exception as e:
                logger.debug(f"Falha ao comprimir imagem do clipboard com PIL: {e}")
        return raw_bytes
