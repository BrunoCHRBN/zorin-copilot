# Decisão de design: driver de entrada virtual para Wayland com suporte a /dev/uinput e ydotool.
# Todas as ações de clique e movimentação são OBRIGATORIAMENTE validadas pelo ScreenFenceManager
# antes de qualquer emissão para o kernel, garantindo isolamento de monitor e proteção contra cliques fora do escopo.
#
# Regra de honestidade: sem backend, a operação FALHA. O driver nunca retorna
# sucesso por uma ação que não executou. Veja o comentário em `simulation`.

"""Driver de entrada de hardware virtual (Wayland / uinput)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Any

from ..core.fence import ScreenFenceManager

logger = logging.getLogger(__name__)

#: Liga o modo simulação sem tocar em código. Útil para demonstrações e
#: testes de integração — NUNCA para uso real, porque nada acontece de fato.
SIMULATION_ENV_VAR = "ZORIN_COPILOT_INPUT_SIMULATION"

_TRUTHY = {"1", "true", "yes", "on", "sim"}

INSTALL_HINT = (
    "Instale o backend de teclado virtual: 'sudo pacman -S wtype' (Wayland/Hyprland) ou "
    "'sudo pacman -S ydotool' (Arch) / 'sudo apt install ydotool' (Ubuntu), com o daemon ydotool ativo."
)

#: Diretórios conferidos quando o $PATH não resolve. Necessário porque o
#: copilot pode rodar sob um serviço systemd com PATH mínimo
#: (/usr/bin:/bin), enquanto o wtype/ydotool foi instalado em /usr/local/bin.
_EXTRA_BIN_DIRS: tuple[str, ...] = (
    "/usr/bin", "/usr/local/bin", "/bin", "/usr/local/sbin", "/usr/sbin", "/sbin",
)


def _find_binary(name: str) -> str | None:
    """Localiza `name`: override por env, depois $PATH, depois diretórios padrão."""
    override = os.environ.get(f"ZORIN_COPILOT_{name.upper()}_BIN", "").strip()
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override

    found = shutil.which(name)
    if found:
        return found

    for directory in _EXTRA_BIN_DIRS:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


class VirtualInputDriver:
    """Emite cliques, digitação e atalhos de hardware virtual respeitando cercas espaciais.

    Sem um backend real, todo método devolve ``(False, motivo)``. Isso é
    intencional: o resultado vai direto para o modelo (``live.py`` monta
    ``{"success": ok, "message": msg}``), então um falso positivo faz o agente
    *acreditar* que clicou ou digitou e continuar o plano a partir de uma
    premissa falsa. Falhar é melhor que mentir.
    """

    def __init__(self, fence: ScreenFenceManager | None = None, simulation: bool | None = None):
        self.fence = fence or ScreenFenceManager()
        # Presença de /dev/uinput gravável é só diagnóstico: não existe emissão
        # direta implementada, então não pode contar como backend disponível.
        self._has_uinput_access = os.access("/dev/uinput", os.W_OK) if os.path.exists("/dev/uinput") else False
        self.simulation = self._resolve_simulation(simulation)
        self.refresh_backends()

    def refresh_backends(self) -> bool:
        """(Re)detecta wtype/ydotool. Devolve True se algum backend apareceu.

        A detecção não pode acontecer só no `__init__`: é comum o usuário
        instalar o wtype *depois* de abrir o copilot (ele descobre que precisa
        justamente pelo erro). Preso ao probe inicial, o driver continuaria
        jurando que "nenhum backend existe" até o app ser reiniciado.
        """
        wtype = _find_binary("wtype")
        self.wtype_bin = wtype if wtype and "wtype" in os.path.basename(wtype) else None
        ydotool = _find_binary("ydotool")
        self.ydotool_bin = ydotool if ydotool and "ydotool" in os.path.basename(ydotool) else None
        return self.is_available

    @staticmethod
    def _resolve_simulation(override: bool | None) -> bool:
        if override is not None:
            return bool(override)
        return os.environ.get(SIMULATION_ENV_VAR, "").strip().lower() in _TRUTHY

    @property
    def is_available(self) -> bool:
        """Há backend capaz de emitir o input de verdade?

        Diferente de "o processo não vai quebrar". Modo simulação continua
        reportando False aqui — ele não executa nada.
        """
        return bool(self.wtype_bin or self.ydotool_bin)

    def get_backend_name(self) -> str:
        if self.wtype_bin:
            return "wtype (Wayland virtual keyboard)"
        if self.ydotool_bin:
            return "ydotool (uinput daemon)"
        if self.simulation:
            return "SIMULAÇÃO (nenhuma ação é executada)"
        return "indisponível (wtype ou ydotool não encontrado)"

    def _ensure_backend(self) -> None:
        """Re-procura os binários quando nenhum foi encontrado até agora.

        Chamado no início de cada ação: custa um `which` e cobre o caso de o
        backend ter sido instalado depois de o driver nascer.
        """
        if not self.is_available:
            self.refresh_backends()

    def _unavailable(self, action: str) -> tuple[bool, str]:
        """Falha padronizada quando não há backend. Respeita o modo simulação."""
        if self.simulation:
            msg = f"[SIMULAÇÃO] {action} NÃO foi executado de fato."
            logger.warning(msg)
            return True, msg
        msg = f"{action} não executado: nenhum backend de input disponível. {INSTALL_HINT}"
        logger.error(msg)
        return False, msg

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

        self._ensure_backend()

        # 2. Execução via backend disponível
        btn_code = "0xC0" if button.lower() in ("left", "esquerdo") else "0xC1"
        if button.lower() in ("middle", "meio"):
            btn_code = "0xC4"

        try:
            if self.ydotool_bin:
                # Move para a coordenada absoluta
                mv = subprocess.run(
                    [self.ydotool_bin, "mousemove", "-a", "-x", str(x), "-y", str(y)],
                    capture_output=True, text=True, timeout=1.5, check=False,
                )
                if mv.returncode != 0:
                    err = (mv.stderr or mv.stdout or "").strip()
                    msg = f"ydotool mousemove falhou (código {mv.returncode}" + (f": {err}" if err else "") + ")."
                    logger.error(msg)
                    return False, msg
                time.sleep(0.04)
                # Dispara clique (down e up)
                ck = subprocess.run(
                    [self.ydotool_bin, "click", btn_code],
                    capture_output=True, text=True, timeout=1.5, check=False,
                )
                if ck.returncode != 0:
                    err = (ck.stderr or ck.stdout or "").strip()
                    msg = f"ydotool click falhou (código {ck.returncode}" + (f": {err}" if err else "") + ")."
                    logger.error(msg)
                    return False, msg
                if double:
                    time.sleep(0.08)
                    ck2 = subprocess.run(
                        [self.ydotool_bin, "click", btn_code],
                        capture_output=True, text=True, timeout=1.5, check=False,
                    )
                    if ck2.returncode != 0:
                        err = (ck2.stderr or ck2.stdout or "").strip()
                        msg = f"ydotool click (2º) falhou (código {ck2.returncode}" + (f": {err}" if err else "") + ")."
                        logger.error(msg)
                        return False, msg
                msg = f"Clique físico ({button}) executado em ({x}, {y}) via ydotool."
                logger.info(msg)
                return True, msg

            # Sem backend: falha honesta (ou simulação explicitamente ligada).
            return self._unavailable(f"Clique ({button}) em ({x}, {y})")

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

        self._ensure_backend()

        try:
            if self.wtype_bin:
                cmd = [self.wtype_bin, "--", text]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0, check=False)
                if res.returncode != 0:
                    err = (res.stderr or res.stdout or "").strip()
                    msg = f"wtype falhou ao digitar (código {res.returncode}" + (f": {err}" if err else "") + ")."
                    logger.error(msg)
                    return False, msg

                if press_enter:
                    time.sleep(0.05)
                    res_k = subprocess.run(
                        [self.wtype_bin, "-k", "Return"],
                        capture_output=True, text=True, timeout=1.0, check=False,
                    )
                    if res_k.returncode != 0:
                        err = (res_k.stderr or res_k.stdout or "").strip()
                        msg = f"wtype falhou ao pressionar Enter (código {res_k.returncode}" + (f": {err}" if err else "") + ")."
                        logger.error(msg)
                        return False, msg

                msg = f"Texto digitado com sucesso ({len(text)} caracteres) via wtype."
                logger.info(msg)
                return True, msg

            if self.ydotool_bin:
                cmd = [self.ydotool_bin, "type", "--", text]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5.0, check=False)
                if res.returncode != 0:
                    err = (res.stderr or res.stdout or "").strip()
                    msg = f"ydotool type falhou (código {res.returncode}" + (f": {err}" if err else "") + ")."
                    logger.error(msg)
                    return False, msg

                if press_enter:
                    time.sleep(0.05)
                    # 28 é keycode de Enter
                    res_k = subprocess.run(
                        [self.ydotool_bin, "key", "28:1", "28:0"],
                        capture_output=True, text=True, timeout=1.0, check=False,
                    )
                    if res_k.returncode != 0:
                        err = (res_k.stderr or res_k.stdout or "").strip()
                        msg = f"ydotool key(Enter) falhou (código {res_k.returncode}" + (f": {err}" if err else "") + ")."
                        logger.error(msg)
                        return False, msg

                msg = f"Texto digitado com sucesso ({len(text)} caracteres)."
                logger.info(msg)
                return True, msg

            # Sem backend: falha honesta (ou simulação explicitamente ligada).
            return self._unavailable(f"Digitação de {len(text)} caracteres")

        except Exception as exc:
            err = f"Falha ao digitar texto via teclado virtual: {exc}"
            logger.error(err)
            return False, err

    def hotkey(self, *keys: str) -> tuple[bool, str]:
        """Envia combinação de atalhos de teclado (ex: 'ctrl', 'v' ou 'alt', 'tab')."""
        if self.fence.is_emergency_stopped:
            return False, "Operação cancelada: Parada de emergência (Kill Switch) está ativa."

        keys_str = "+".join(keys)

        self._ensure_backend()

        try:
            if self.wtype_bin:
                mod_map = {
                    "ctrl": "ctrl", "control": "ctrl", "lctrl": "ctrl", "rctrl": "ctrl",
                    "shift": "shift", "alt": "alt", "super": "logo", "meta": "logo", "win": "logo"
                }
                key_map_wtype = {
                    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
                    "tab": "Tab", "backspace": "BackSpace", "space": "space"
                }
                mods_down: list[str] = []
                mods_up: list[str] = []
                main_keys: list[str] = []
                for k in keys:
                    kl = k.lower()
                    if kl in mod_map:
                        mods_down.extend(["-M", mod_map[kl]])
                        mods_up.extend(["-m", mod_map[kl]])
                    else:
                        main_keys.extend(["-k", key_map_wtype.get(kl, k)])
                if mods_down or main_keys:
                    full_args = [self.wtype_bin] + mods_down + main_keys + mods_up
                    res = subprocess.run(full_args, capture_output=True, text=True, timeout=2.0, check=False)
                    if res.returncode != 0:
                        err = (res.stderr or res.stdout or "").strip()
                        msg = f"wtype falhou no atalho '{keys_str}' (código {res.returncode}" + (f": {err}" if err else "") + ")."
                        logger.error(msg)
                        return False, msg
                    return True, f"Atalho '{keys_str}' acionado com sucesso via wtype."

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
                    res = subprocess.run(full_args, capture_output=True, text=True, timeout=2.0, check=False)
                    if res.returncode != 0:
                        err = (res.stderr or res.stdout or "").strip()
                        msg = f"ydotool falhou no atalho '{keys_str}' (código {res.returncode}" + (f": {err}" if err else "") + ")."
                        logger.error(msg)
                        return False, msg
                    return True, f"Atalho '{keys_str}' acionado com sucesso."

                # Backend existe, mas nenhuma das teclas pedidas tem keycode
                # conhecido — isso é falha de mapeamento, não de backend.
                msg = (
                    f"Atalho '{keys_str}' não executado: nenhuma das teclas tem "
                    f"keycode mapeado. Mapeadas: {', '.join(sorted(KEY_MAP))}."
                )
                logger.error(msg)
                return False, msg

            return self._unavailable(f"Atalho '{keys_str}'")

        except Exception as exc:
            err = f"Falha ao acionar atalho '{keys_str}': {exc}"
            logger.error(err)
            return False, err
