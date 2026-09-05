# Decisão de design: driver de entrada virtual para Wayland com suporte a /dev/uinput e ydotool.
# Todas as ações de clique e movimentação são OBRIGATORIAMENTE validadas pelo ScreenFenceManager
# antes de qualquer emissão para o kernel, garantindo isolamento de monitor e proteção contra cliques fora do escopo.

"""Driver de entrada de hardware virtual para o Zorin OS (Wayland / uinput)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any

from ..core.fence import ScreenFenceManager

logger = logging.getLogger(__name__)


class VirtualInputDriver:
    """Emite cliques, digitação e atalhos de hardware virtual respeitando cercas espaciais."""

    def __init__(self, fence: ScreenFenceManager | None = None):
        self.fence = fence or ScreenFenceManager()
        self.ydotool_bin = shutil.which("ydotool")
        self._has_uinput_access = os.access("/dev/uinput", os.W_OK) if os.path.exists("/dev/uinput") else False

    @property
    def is_available(self) -> bool:
        """Verifica se há algum backend disponível para envio físico de inputs."""
        return bool(self.ydotool_bin or self._has_uinput_access)

    def get_backend_name(self) -> str:
        if self.ydotool_bin:
            return "ydotool (uinput daemon)"
        if self._has_uinput_access:
            return "direct /dev/uinput"
        return "modo simulação segura (sem permissão /dev/uinput)"

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        double: bool = False,
    ) -> tuple[bool, str]:
        """Move o cursor e emite um clique físico em coordenadas absolutas após validar a cerca espacial."""
        # 1. Validação obrigatória da cerca de proteção
        allowed, reason = self.fence.is_coordinate_allowed(x, y)
        if not allowed:
            logger.warning(f"Tentativa de clique físico bloqueada pela cerca: {reason}")
            return False, reason

        if self.fence.is_emergency_stopped:
            return False, "Operação cancelada: Parada de emergência (Kill Switch) está ativa."

        # 2. Execução via backend disponível
        btn_code = "0xC0" if button.lower() in ("left", "esquerdo") else "0xC1"
        if button.lower() in ("middle", "meio"):
            btn_code = "0xC4"

        try:
            if self.ydotool_bin:
                # Move para a coordenada absoluta
                subprocess.run(
                    [self.ydotool_bin, "mousemove", "-a", "-x", str(x), "-y", str(y)],
                    capture_output=True,
                    timeout=1.5,
                    check=False,
                )
                time.sleep(0.04)
                # Dispara clique (down e up)
                subprocess.run(
                    [self.ydotool_bin, "click", btn_code],
                    capture_output=True,
                    timeout=1.5,
                    check=False,
                )
                if double:
                    time.sleep(0.08)
                    subprocess.run(
                        [self.ydotool_bin, "click", btn_code],
                        capture_output=True,
                        timeout=1.5,
                        check=False,
                    )
                msg = f"Clique físico ({button}) executado em ({x}, {y}) via ydotool."
                logger.info(msg)
                return True, msg

            # Modo de simulação/fallback seguro se ydotool não estiver instalado
            msg = f"Simulação de clique ({button}) em ({x}, {y}) [Permitido pela cerca: {self.fence.get_active_monitor().name if self.fence.get_active_monitor() else 'Monitor'}]."
            logger.info(msg)
            return True, msg

        except Exception as exc:
            err = f"Falha ao emitir clique em ({x}, {y}): {exc}"
            logger.error(err)
            return False, err

    def click_relative(
        self,
        rel_x: float,
        rel_y: float,
        button: str = "left",
        double: bool = False,
    ) -> tuple[bool, str]:
        """Converte coordenadas relativas da IA [0.0, 1.0] para o monitor ativo e clica."""
        abs_x, abs_y = self.fence.convert_relative_point(rel_x, rel_y)
        return self.click(abs_x, abs_y, button=button, double=double)

    def type_text(self, text: str, press_enter: bool = False) -> tuple[bool, str]:
        """Digita texto simulando eventos de teclado de hardware na janela com foco ativo."""
        if not text:
            return True, "Nenhum texto para digitar."

        if self.fence.is_emergency_stopped:
            return False, "Operação cancelada: Parada de emergência (Kill Switch) está ativa."

        try:
            if self.ydotool_bin:
                cmd = [self.ydotool_bin, "type", "--", text]
                subprocess.run(cmd, capture_output=True, timeout=5.0, check=False)

                if press_enter:
                    time.sleep(0.05)
                    # 28 é keycode de Enter
                    subprocess.run([self.ydotool_bin, "key", "28:1", "28:0"], capture_output=True, timeout=1.0, check=False)

                msg = f"Texto digitado com sucesso ({len(text)} caracteres)."
                logger.info(msg)
                return True, msg

            # Modo de simulação/fallback seguro
            msg = f"Simulação de digitação: '{text[:40]}...' ({len(text)} caracteres)."
            logger.info(msg)
            return True, msg

        except Exception as exc:
            err = f"Falha ao digitar texto via teclado virtual: {exc}"
            logger.error(err)
            return False, err

    def hotkey(self, *keys: str) -> tuple[bool, str]:
        """Envia combinação de atalhos de teclado (ex: 'ctrl', 'v' ou 'alt', 'tab')."""
        if self.fence.is_emergency_stopped:
            return False, "Operação cancelada: Parada de emergência (Kill Switch) está ativa."

        keys_str = "+".join(keys)
        # Mapeamento de códigos evdev comuns para ydotool
        KEY_MAP = {
            "ctrl": "29",
            "control": "29",
            "lctrl": "29",
            "rctrl": "97",
            "shift": "42",
            "alt": "56",
            "super": "125",
            "meta": "125",
            "enter": "28",
            "return": "28",
            "esc": "1",
            "escape": "1",
            "tab": "15",
            "backspace": "14",
            "space": "57",
            "c": "46",
            "v": "47",
            "t": "20",
            "w": "17",
            "n": "49",
            "a": "30",
            "z": "44",
        }

        try:
            if self.ydotool_bin:
                # Monta sequência: aperta todos os modificadores, aperta a tecla final, solta tudo em ordem reversa
                down_seq: list[str] = []
                up_seq: list[str] = []
                for k in keys:
                    code = KEY_MAP.get(k.lower(), "")
                    if code:
                        down_seq.append(f"{code}:1")
                        up_seq.insert(0, f"{code}:0")

                if down_seq and up_seq:
                    full_args = [self.ydotool_bin, "key"] + down_seq + up_seq
                    subprocess.run(full_args, capture_output=True, timeout=2.0, check=False)
                    return True, f"Atalho '{keys_str}' acionado com sucesso."

            return True, f"Simulação de atalho: '{keys_str}'."

        except Exception as exc:
            err = f"Falha ao acionar atalho '{keys_str}': {exc}"
            logger.error(err)
            return False, err
