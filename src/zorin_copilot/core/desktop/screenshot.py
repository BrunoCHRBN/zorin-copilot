# Decisão de design: a captura era exclusiva do portal XDG (org.freedesktop.portal.Screenshot).
# Em Hyprland/Sway o portal só responde se houver xdg-desktop-portal-wlr/-hyprland instalado e
# configurado — quando não há, a chamada D-Bus estoura e o usuário vê "Falha no portal".
#
# Em compositores wlroots o caminho nativo é grim (+slurp para recorte), que além de mais
# confiável não depende de nenhum serviço extra de pé. O portal continua sendo a primeira
# escolha onde existe (GNOME), mas deixa de ser a única.

"""Backends de captura de tela por ambiente.

Todos devolvem um :class:`CaptureResult`. Backends que precisam de arquivo temporário
o criam e informam o caminho; :mod:`zorin_copilot.core.vision` otimiza e apaga,
centralizando o tratamento de imagem num lugar só.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .env import Environment, current_environment

logger = logging.getLogger(__name__)


@dataclass
class CaptureResult:
    """Resultado bruto de uma captura."""

    ok: bool
    #: Arquivo temporário com a imagem (o chamador deve apagá-lo).
    path: Path | None = None
    #: Bytes já em memória, quando o backend não precisa de arquivo.
    data: bytes | None = None
    #: "tela_inteira" | "area_selecionada"
    mode: str = ""
    message: str = ""


class CaptureBackend(ABC):
    """Contrato dos backends de captura."""

    name: str = "base"

    def __init__(self, env: Environment | None = None):
        self.env = env or current_environment()

    @abstractmethod
    def is_supported(self) -> bool:
        """O backend consegue capturar neste ambiente?"""

    @abstractmethod
    def capture(self, interactive: bool, timeout_sec: int) -> CaptureResult:
        """Captura a tela. ``interactive=True`` pede seleção de área."""

    @staticmethod
    def _tmp() -> Path:
        fd, raw = tempfile.mkstemp(prefix="zorin-copilot-shot-", suffix=".png")
        os.close(fd)
        return Path(raw)

    def _run(self, argv: list[str], timeout: int) -> tuple[int, str]:
        try:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
            return res.returncode, (res.stderr or "").strip()
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        except OSError as exc:
            return 127, str(exc)


class PortalCaptureBackend(CaptureBackend):
    """Portal XDG (GNOME, e qualquer sessão com portal de screenshot ativo)."""

    name = "xdg-portal"

    def is_supported(self) -> bool:
        # O portal é o caminho universal *quando existe*. Não há como saber sem
        # tentar, então assumimos suporte e deixamos o erro falar mais alto.
        return True

    def capture(self, interactive: bool, timeout_sec: int) -> CaptureResult:
        try:
            import gi

            gi.require_version("Gio", "2.0")
            gi.require_version("GLib", "2.0")
            from gi.repository import Gio, GLib
        except (ImportError, ValueError) as exc:
            return CaptureResult(False, message=f"Gio/GLib indisponível: {exc}")

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:
            return CaptureResult(False, message=f"Sem D-Bus de sessão: {exc}")

        loop = GLib.MainLoop()
        result: dict[str, object] = {"uri": None, "code": -1}
        expected: list[str | None] = [None]

        def on_response(conn, sender, path, iface, signal, params, user_data):
            try:
                if expected[0] and path != expected[0]:
                    return
                res_code, results = params.unpack()
                result["code"] = res_code
                if res_code == 0 and "uri" in results:
                    result["uri"] = results["uri"]
            except Exception as exc:
                logger.error(f"Erro no sinal do portal de screenshot: {exc}")
            finally:
                loop.quit()

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
            options = {
                "interactive": GLib.Variant("b", interactive),
                "modal": GLib.Variant("b", False),
            }
            val = bus.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Screenshot",
                "Screenshot",
                GLib.Variant("(sa{sv})", ("", options)),
                GLib.VariantType("(o)"),
                Gio.DBusCallFlags.NONE,
                15000,
                None,
            )
            expected[0] = val.unpack()[0]
        except Exception as exc:
            bus.signal_unsubscribe(sub_id)
            return CaptureResult(False, message=f"Portal de screenshot indisponível: {exc}")

        timeout_source = GLib.timeout_add_seconds(timeout_sec, loop.quit)
        try:
            loop.run()
        finally:
            bus.signal_unsubscribe(sub_id)
            GLib.source_remove(timeout_source)

        if result["code"] != 0 or not result["uri"]:
            return CaptureResult(False, message="Captura cancelada ou portal sem resposta.")

        from urllib.parse import unquote, urlparse

        file_path = unquote(urlparse(str(result["uri"])).path)
        if not os.path.isfile(file_path):
            return CaptureResult(False, message=f"Arquivo do portal não encontrado: {file_path}")

        return CaptureResult(
            ok=True,
            path=Path(file_path),
            mode="area_selecionada" if interactive else "tela_inteira",
        )


class GrimCaptureBackend(CaptureBackend):
    """``grim`` + ``slurp`` — o caminho nativo em compositores wlroots."""

    name = "grim-slurp"

    def is_supported(self) -> bool:
        return self.env.has("grim")

    def capture(self, interactive: bool, timeout_sec: int) -> CaptureResult:
        target = self._tmp()

        geometry = ""
        if interactive:
            if not self.env.has("slurp"):
                target.unlink(missing_ok=True)
                return CaptureResult(False, message="`slurp` não encontrado (necessário para recortar a área).")
            code, geometry, err = self._slurp(timeout_sec)
            if code != 0 or not geometry:
                target.unlink(missing_ok=True)
                # slurp sai com 1 quando o usuário aperta Esc — não é erro.
                if code == 1:
                    return CaptureResult(False, message="Seleção de área cancelada.")
                return CaptureResult(False, message=f"slurp falhou: {err}")

        argv = ["grim"]
        if geometry:
            argv += ["-g", geometry]
        argv.append(str(target))

        code, err = self._run(argv, timeout=30)
        if code != 0:
            target.unlink(missing_ok=True)
            return CaptureResult(False, message=f"grim falhou: {err or f'código {code}'}")

        return CaptureResult(ok=True, path=target, mode="area_selecionada" if geometry else "tela_inteira")

    @staticmethod
    def _slurp(timeout: int) -> tuple[int, str, str]:
        try:
            res = subprocess.run(["slurp"], capture_output=True, text=True, timeout=timeout, check=False)
            return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        except OSError as exc:
            return 127, "", str(exc)


class GrimblastCaptureBackend(CaptureBackend):
    """``grimblast`` — wrapper do Hyprland que já conhece monitor e área ativa."""

    name = "grimblast"

    def is_supported(self) -> bool:
        return self.env.has("grimblast")

    def capture(self, interactive: bool, timeout_sec: int) -> CaptureResult:
        target = self._tmp()
        scope = "area" if interactive else "screen"
        code, err = self._run(["grimblast", "save", scope, str(target)], timeout=timeout_sec + 20)
        if code != 0:
            target.unlink(missing_ok=True)
            return CaptureResult(False, message=f"grimblast falhou: {err or f'código {code}'}")
        return CaptureResult(ok=True, path=target, mode="area_selecionada" if interactive else "tela_inteira")


class SpectacleCaptureBackend(CaptureBackend):
    """``spectacle`` — utilitário de captura do KDE Plasma."""

    name = "spectacle"

    def is_supported(self) -> bool:
        return self.env.has("spectacle")

    def capture(self, interactive: bool, timeout_sec: int) -> CaptureResult:
        target = self._tmp()
        # -r: modo retângulo (interativo); -f: tela inteira; -b: sem GUI; -n: sem notificação
        argv = ["spectacle", "-b", "-n", "-r" if interactive else "-f", "-o", str(target)]
        code, err = self._run(argv, timeout=timeout_sec + 20)
        if code != 0:
            target.unlink(missing_ok=True)
            return CaptureResult(False, message=f"spectacle falhou: {err or f'código {code}'}")
        return CaptureResult(ok=True, path=target, mode="area_selecionada" if interactive else "tela_inteira")


class ImagemagickCaptureBackend(CaptureBackend):
    """``import`` do ImageMagick — último recurso em sessões X11."""

    name = "imagemagick"

    def is_supported(self) -> bool:
        return self.env.is_x11 and self.env.has("import")

    def capture(self, interactive: bool, timeout_sec: int) -> CaptureResult:
        target = self._tmp()
        argv = ["import"]
        if not interactive:
            argv += ["-window", "root"]
        argv.append(str(target))
        code, err = self._run(argv, timeout=timeout_sec + 20)
        if code != 0:
            target.unlink(missing_ok=True)
            return CaptureResult(False, message=f"import falhou: {err or f'código {code}'}")
        return CaptureResult(ok=True, path=target, mode="area_selecionada" if interactive else "tela_inteira")


def iter_backends(env: Environment | None = None) -> list[CaptureBackend]:
    """Ordem de preferência.

    Em wlroots, grimblast/grim vêm antes do portal porque não dependem de um
    serviço de portal estar de pé; no GNOME o portal vence.
    """
    env = env or current_environment()
    native: list[CaptureBackend] = []
    if env.is_wlroots:
        native = [GrimblastCaptureBackend(env), GrimCaptureBackend(env)]
    elif env.is_kde:
        native = [SpectacleCaptureBackend(env)]

    return native + [
        PortalCaptureBackend(env),
        GrimCaptureBackend(env),
        ImagemagickCaptureBackend(env),
    ]


def select_backend(env: Environment | None = None) -> CaptureBackend:
    """Primeiro backend suportado; o portal entra como rede de segurança universal."""
    env = env or current_environment()
    for backend in iter_backends(env):
        try:
            if backend.is_supported():
                return backend
        except Exception as exc:
            logger.debug(f"Backend de captura {backend.name} falhou na detecção: {exc}")
    return PortalCaptureBackend(env)


def missing_dependencies(env: Environment | None = None) -> tuple[str, ...]:
    """Binários que o usuário deveria instalar para ter captura decente.

    Separado da captura em si para que a UI/CLI possa sugerir a instalação em vez
    de deixar o usuário diante de um erro opaco.
    """
    env = env or current_environment()
    if env.is_wlroots or env.is_x11:
        needed = ("grim", "slurp")
    elif env.is_kde:
        needed = ("spectacle",)
    else:
        needed = ()  # GNOME: o portal já vem com a sessão
    # Usa o inventário do ambiente em vez de shutil.which: mantém a função
    # determinística e testável sem depender do PATH da máquina.
    return tuple(n for n in needed if not env.has(n))
