# Decisão de design: Captura de tela via XDG Desktop Portal (D-Bus) para compatibilidade nativa
# total com Wayland e GNOME Shell no Zorin OS, com suporte a recorte de área (interactive=True)
# e compressão inteligente via Pillow para otimizar tokens de API do Gemini.

"""Serviço de captura e visão computacional da tela para o Zorin Copilot."""

from __future__ import annotations

import io
import logging
import os
import time
from urllib.parse import unquote, urlparse
from typing import Tuple

import gi
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

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
    ) -> Tuple[bool, bytes | None, str]:
        """
        Captura a tela ou permite selecionar uma área interativamente.

        Args:
            interactive: Se True, abre o seletor nativo do GNOME para o usuário recortar uma área.
                         Se False, tira screenshot imediato da tela inteira.
            max_size: Dimensão máxima (largura/altura) para redimensionar e economizar tokens.
            quality: Qualidade JPEG (85 é o equilíbrio ideal entre legibilidade e tamanho).
            timeout_sec: Tempo máximo de espera pela interação do usuário.

        Returns:
            Tuple[sucesso, bytes_da_imagem, mensagem_ou_modo]
        """
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:
            logger.error(f"Não foi possível conectar ao D-Bus de sessão: {exc}")
            return False, None, f"Erro D-Bus: {exc}"

        loop = GLib.MainLoop()
        result: dict[str, str | int | None] = {"uri": None, "code": -1}
        expected_handle: list[str | None] = [None]

        def on_response(conn, sender, path, iface, signal, params, user_data):
            try:
                # Garante que só processa o sinal associado a este pedido específico
                if expected_handle[0] and path != expected_handle[0]:
                    return
                res_code, results = params.unpack()
                result["code"] = res_code
                if res_code == 0 and "uri" in results:
                    result["uri"] = results["uri"]
            except Exception as e:
                logger.error(f"Erro ao processar sinal do portal de screenshot: {e}")
            finally:
                loop.quit()

        # Inscreve-se no sinal ANTES de disparar a chamada para evitar race conditions
        sub_id = bus.signal_subscribe(
            "org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.Request",
            "Response",
            None,
            None,
            Gio.DBusSignalFlags.NONE,
            on_response,
            None,
        )

        try:
            # Opções do Portal XDG Screenshot passadas como dicionário nativo Python
            portal_options = {
                "interactive": GLib.Variant("b", interactive),
                "modal": GLib.Variant("b", False),
            }

            val = bus.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Screenshot",
                "Screenshot",
                GLib.Variant("(sa{sv})", ("", portal_options)),
                GLib.VariantType("(o)"),
                Gio.DBusCallFlags.NONE,
                15000,
                None,
            )
            expected_handle[0] = val.unpack()[0]
        except Exception as exc:
            bus.signal_unsubscribe(sub_id)
            logger.error(f"Falha ao chamar org.freedesktop.portal.Screenshot: {exc}")
            return False, None, f"Falha no portal de screenshot: {exc}"

        # Timeout de segurança generoso para o usuário desenhar a seleção com calma
        timeout_source = GLib.timeout_add_seconds(timeout_sec, loop.quit)
        try:
            loop.run()
        finally:
            bus.signal_unsubscribe(sub_id)
            GLib.source_remove(timeout_source)

        if result["code"] != 0 or not result["uri"]:
            logger.info("Captura cancelada pelo usuário ou tempo esgotado.")
            return False, None, "Captura cancelada pelo usuário."

        # Extrai caminho do arquivo local
        uri_str = str(result["uri"])
        parsed = urlparse(uri_str)
        file_path = unquote(parsed.path)

        if not os.path.isfile(file_path):
            return False, None, f"Arquivo de captura não encontrado: {file_path}"

        try:
            # Processa e otimiza a imagem em memória
            image_bytes = cls._optimize_image(file_path, max_size=max_size, quality=quality)
            mode_desc = "area_selecionada" if interactive else "tela_inteira"
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
    def _optimize_image(cls, file_path: str, max_size: int = 1280, quality: int = 85) -> bytes:
        """Lê a imagem, redimensiona se necessário e converte para JPEG otimizado."""
        if HAS_PIL:
            with Image.open(file_path) as img:
                # Converte RGBA para RGB se necessário
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

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
