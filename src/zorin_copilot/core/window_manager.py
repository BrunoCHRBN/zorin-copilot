# Decisão de design: Gerenciador unificado de janelas e foco dinâmico para Linux (Wayland e X11).
# Permite ao Zorin Copilot rastrear em tempo real a janela ativa ou mais recente (focus history),
# listar janelas abertas e alternar o foco do compositor para a janela desejada.

"""Gerenciador de janelas, foco dinâmico e inspeção de aplicativos abertos."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

IGNORED_APP_CLASSES = {
    "zorin-copilot",
    "zorin_copilot",
    "io.github.bruno.zorincopilot",
    "org.zorin.copilot",
    "zorin-copilot-pill",
}


@dataclass
class WindowInfo:
    """Informações geométricas e metadados de uma janela aberta."""

    id: str  # address no Hyprland, wid no X11, con_id no Sway
    app: str  # class / app_id / nome do executável
    title: str
    x: int
    y: int
    width: int
    height: int
    monitor_index: int = 0
    workspace: str = ""
    focus_history: int = 999  # 0 = mais recente, 1 = anterior...
    is_active: bool = False

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        """Retorna (x, y, largura, altura)."""
        return self.x, self.y, self.width, self.height

    @property
    def max_x(self) -> int:
        return self.x + self.width

    @property
    def max_y(self) -> int:
        return self.y + self.height

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.max_x and self.y <= py < self.max_y

    def display_name(self, max_title_chars: int = 35) -> str:
        """Formata um nome amigável para exibição em listas e badges da UI."""
        clean_app = self.app.capitalize() if self.app else "Janela"
        if not self.title:
            return clean_app
        t = self.title.strip()
        if len(t) > max_title_chars:
            t = t[:max_title_chars].rstrip() + "…"
        return f"{clean_app} — {t}"


class WindowManager:
    """Serviço de consulta e controle de foco de janelas do desktop."""

    @classmethod
    def list_windows(cls, exclude_copilot: bool = True) -> list[WindowInfo]:
        """Lista todas as janelas visíveis no desktop ordenadas pela mais recente (focus history)."""
        my_pid = os.getpid()

        # 1. Backend Hyprland (nativo e preciso)
        if shutil.which("hyprctl"):
            try:
                proc = subprocess.run(
                    ["hyprctl", "clients", "-j"],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    clients = json.loads(proc.stdout)
                    windows: list[WindowInfo] = []
                    for c in clients:
                        if not c.get("mapped", True) or c.get("hidden", False):
                            continue
                        cls_name = str(c.get("class") or c.get("initialClass") or "").strip()
                        title = str(c.get("title") or c.get("initialTitle") or "").strip()
                        c_pid = c.get("pid")

                        if exclude_copilot:
                            low_cls = cls_name.lower()
                            if any(ign in low_cls for ign in IGNORED_APP_CLASSES) or (
                                c_pid and c_pid == my_pid
                            ):
                                continue

                        at = c.get("at", [0, 0])
                        size = c.get("size", [0, 0])
                        w = int(size[0]) if len(size) > 0 else 0
                        h = int(size[1]) if len(size) > 1 else 0
                        if w < 100 or h < 80:
                            # Ignora popups minúsculos ou widgets invisíveis
                            continue

                        address = str(c.get("address") or "")
                        hist_id = c.get("focusHistoryID", c.get("focusHistoryId", 999))
                        try:
                            hist_int = int(hist_id)
                        except (TypeError, ValueError):
                            hist_int = 999

                        ws = str(c.get("workspace", {}).get("name") or "")
                        mon = int(c.get("monitor", 0))

                        windows.append(
                            WindowInfo(
                                id=address,
                                app=cls_name,
                                title=title,
                                x=int(at[0]),
                                y=int(at[1]),
                                width=w,
                                height=h,
                                monitor_index=mon,
                                workspace=ws,
                                focus_history=hist_int,
                                is_active=hist_int == 0,
                            )
                        )

                    # Ordena: foco mais recente primeiro (focus_history: 0, 1, 2...)
                    windows.sort(key=lambda w: w.focus_history)
                    return windows
            except Exception as exc:
                logger.debug("Falha ao listar janelas no Hyprland: %s", exc)

        # 2. Backend Sway / i3
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
                    windows = []

                    def _walk(node: dict[str, Any]) -> None:
                        rect = node.get("rect", {})
                        w = rect.get("width", 0)
                        h = rect.get("height", 0)
                        app = str(
                            node.get("app_id")
                            or node.get("window_properties", {}).get("class")
                            or ""
                        ).strip()
                        title = str(node.get("name") or "").strip()
                        node_pid = node.get("pid")

                        if w >= 100 and h >= 80 and app:
                            if not (
                                exclude_copilot
                                and (
                                    any(ign in app.lower() for ign in IGNORED_APP_CLASSES)
                                    or (node_pid and node_pid == my_pid)
                                )
                            ):
                                windows.append(
                                    WindowInfo(
                                        id=str(node.get("id") or ""),
                                        app=app,
                                        title=title,
                                        x=int(rect.get("x", 0)),
                                        y=int(rect.get("y", 0)),
                                        width=int(w),
                                        height=int(h),
                                        focus_history=0 if node.get("focused") else 99,
                                        is_active=bool(node.get("focused")),
                                    )
                                )
                        for child in node.get("nodes", []) + node.get("floating_nodes", []):
                            _walk(child)

                    _walk(tree)
                    windows.sort(key=lambda w: (not w.is_active, w.app))
                    return windows
            except Exception as exc:
                logger.debug("Falha ao listar janelas no Sway: %s", exc)

        # 3. Backend X11 via wmctrl
        if shutil.which("wmctrl"):
            try:
                proc = subprocess.run(
                    ["wmctrl", "-l", "-G", "-p"],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    windows = []
                    for line in proc.stdout.splitlines():
                        parts = line.split(None, 8)
                        if len(parts) >= 9:
                            wid, _desktop, pid_str, x_str, y_str, w_str, h_str, _host, title = (
                                parts
                            )
                            try:
                                win_pid = int(pid_str)
                                wx, wy, ww, wh = int(x_str), int(y_str), int(w_str), int(h_str)
                            except ValueError:
                                continue
                            if exclude_copilot and (
                                win_pid == my_pid
                                or any(ign in title.lower() for ign in IGNORED_APP_CLASSES)
                            ):
                                continue
                            if ww >= 100 and wh >= 80:
                                windows.append(
                                    WindowInfo(
                                        id=wid,
                                        app=title.split(" - ")[-1] if " - " in title else title,
                                        title=title,
                                        x=wx,
                                        y=wy,
                                        width=ww,
                                        height=wh,
                                    )
                                )
                    return windows
            except Exception as exc:
                logger.debug("Falha ao listar janelas via wmctrl: %s", exc)

        return []

    @classmethod
    def get_active_or_last_window(cls) -> WindowInfo | None:
        """Obtém a janela atualmente em foco ou a última janela ativa (anterior ao Copilot)."""
        windows = cls.list_windows(exclude_copilot=True)
        if not windows:
            return None

        # 1. Se estiver sob Hyprland, verifica a janela ativa atual no compositor
        if shutil.which("hyprctl"):
            try:
                proc = subprocess.run(
                    ["hyprctl", "activewindow", "-j"],
                    capture_output=True,
                    text=True,
                    timeout=0.5,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout)
                    addr = str(data.get("address") or "")
                    cls_name = str(data.get("class") or "").lower()
                    if addr and not any(ign in cls_name for ign in IGNORED_APP_CLASSES):
                        for w in windows:
                            if w.id == addr:
                                return w
            except Exception:
                pass

        # 2. Retorna a primeira da lista (que está ordenada pelo focusHistoryID)
        return windows[0]

    @classmethod
    def focus_window(cls, identifier: str) -> bool:
        """Altera o foco do sistema operacional para a janela indicada (por address, class ou busca)."""
        if not identifier:
            return False

        target = identifier.strip()

        # 1. Backend Hyprland
        if shutil.which("hyprctl"):
            try:
                # Se for endereço hexadecimal (ex: 0x55d2b7cf3520)
                if target.startswith("0x"):
                    res = subprocess.run(
                        ["hyprctl", "dispatch", "focuswindow", f"address:{target}"],
                        capture_output=True,
                        text=True,
                        timeout=0.6,
                        check=False,
                    )
                    if res.returncode == 0:
                        logger.info("Janela '%s' focada via hyprctl address.", target)
                        return True

                # Se for nome de classe ou busca
                clean = re.sub(r"[^\w\-.]", "", target)
                res = subprocess.run(
                    ["hyprctl", "dispatch", "focuswindow", f"class:{clean}"],
                    capture_output=True,
                    text=True,
                    timeout=0.6,
                    check=False,
                )
                if res.returncode == 0:
                    logger.info("Janela '%s' focada via hyprctl class.", clean)
                    return True
            except Exception as exc:
                logger.debug("hyprctl focuswindow falhou: %s", exc)

        # 2. Backend Sway
        if shutil.which("swaymsg"):
            try:
                res = subprocess.run(
                    ["swaymsg", f"[con_id={target}] focus"],
                    capture_output=True,
                    text=True,
                    timeout=0.6,
                    check=False,
                )
                if res.returncode == 0:
                    return True
                # Tenta por app_id
                res2 = subprocess.run(
                    ["swaymsg", f"[app_id={target}] focus"],
                    capture_output=True,
                    text=True,
                    timeout=0.6,
                    check=False,
                )
                if res2.returncode == 0:
                    return True
            except Exception as exc:
                logger.debug("swaymsg focus falhou: %s", exc)

        # 3. Backend X11 via xdotool / wmctrl
        if shutil.which("xdotool"):
            try:
                if target.startswith("0x"):
                    proc = subprocess.run(
                        ["xdotool", "windowactivate", target],
                        capture_output=True,
                        text=True,
                        timeout=0.6,
                        check=False,
                    )
                    if proc.returncode == 0:
                        return True
            except Exception:
                pass

        return False

    @classmethod
    def find_window(cls, query: str) -> WindowInfo | None:
        """Busca uma janela aberta pelo nome do aplicativo ou trecho do título."""
        q = query.strip().lower()
        if not q:
            return None
        windows = cls.list_windows(exclude_copilot=True)
        # Correspondência exata de app
        for w in windows:
            if w.app.lower() == q:
                return w
        # Substring de app ou título
        for w in windows:
            if q in w.app.lower() or q in w.title.lower():
                return w
        return None

    @classmethod
    def list_monitors(cls) -> list[dict[str, Any]]:
        """Lista os monitores conectados e suas geometrias e espaços de trabalho."""
        # 1. Backend Hyprland
        if shutil.which("hyprctl"):
            try:
                proc = subprocess.run(
                    ["hyprctl", "monitors", "-j"],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout)
                    monitors = []
                    for m in data:
                        ws_info = m.get("activeWorkspace") or {}
                        ws_id = str(ws_info.get("id") or ws_info.get("name") or "1")
                        monitors.append(
                            {
                                "id": int(m.get("id", len(monitors))),
                                "name": str(m.get("name") or f"Monitor {len(monitors)}"),
                                "description": str(m.get("description") or m.get("model") or ""),
                                "x": int(m.get("x", 0)),
                                "y": int(m.get("y", 0)),
                                "width": int(m.get("width", 1920)),
                                "height": int(m.get("height", 1080)),
                                "is_primary": bool(m.get("focused") or int(m.get("id", 0)) == 0),
                                "active_workspace": ws_id,
                                "scale": float(m.get("scale", 1.0)),
                            }
                        )
                    if monitors:
                        return monitors
            except Exception as exc:
                logger.debug("Falha ao listar monitores no Hyprland: %s", exc)

        # 2. Backend Sway
        if shutil.which("swaymsg"):
            try:
                proc = subprocess.run(
                    ["swaymsg", "-t", "get_outputs"],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout)
                    monitors = []
                    for i, o in enumerate(data):
                        rect = o.get("rect", {})
                        monitors.append(
                            {
                                "id": int(o.get("id", i)),
                                "name": str(o.get("name") or f"Monitor {i}"),
                                "description": str(o.get("model") or ""),
                                "x": int(rect.get("x", 0)),
                                "y": int(rect.get("y", 0)),
                                "width": int(rect.get("width", 1920)),
                                "height": int(rect.get("height", 1080)),
                                "is_primary": bool(o.get("primary") or i == 0),
                                "active_workspace": str(o.get("current_workspace", str(i + 1))),
                                "scale": float(o.get("scale", 1.0)),
                            }
                        )
                    if monitors:
                        return monitors
            except Exception as exc:
                logger.debug("Falha ao listar monitores no Sway: %s", exc)

        # 3. Fallback via ScreenFenceManager / GDK
        try:
            from .fence import ScreenFenceManager
            fence_monitors = ScreenFenceManager.detect_monitors()
            res = []
            for m in fence_monitors:
                res.append(
                    {
                        "id": m.index,
                        "name": m.name,
                        "description": m.model,
                        "x": m.x,
                        "y": m.y,
                        "width": m.width,
                        "height": m.height,
                        "is_primary": m.is_primary,
                        "active_workspace": str(m.index + 1),
                        "scale": m.scale,
                    }
                )
            if res:
                return res
        except Exception:
            pass

        return [
            {
                "id": 0,
                "name": "Monitor 0",
                "description": "Monitor Principal",
                "x": 0,
                "y": 0,
                "width": 1920,
                "height": 1080,
                "is_primary": True,
                "active_workspace": "1",
                "scale": 1.0,
            }
        ]

    @classmethod
    def resolve_monitor(cls, query: str | int, current_monitor_id: Any | None = None) -> dict[str, Any] | None:
        """Resolve o monitor alvo a partir de termos em linguagem natural ou identificadores."""
        monitors = cls.list_monitors()
        if not monitors:
            return None

        q = str(query).strip().lower()

        # Se for índice numérico direto
        try:
            val_int = int(q)
            for m in monitors:
                if m["id"] == val_int:
                    return m
        except ValueError:
            pass

        # Termos em linguagem natural
        if q in ("principal", "primaria", "primario", "main", "primary"):
            for m in monitors:
                if m["is_primary"]:
                    return m
            return monitors[0]

        if q in ("secundario", "secundaria", "segundo", "segunda", "auxiliar", "secondary"):
            for m in monitors:
                if not m["is_primary"]:
                    return m
            return monitors[1] if len(monitors) > 1 else monitors[0]

        if q in ("outro", "outra", "other", "proximo", "proxima", "next", "oposto"):
            if current_monitor_id is not None:
                for m in monitors:
                    if str(m["id"]) != str(current_monitor_id) and m["name"] != str(current_monitor_id):
                        return m
            for m in monitors:
                if not m["is_primary"]:
                    return m
            return monitors[1] if len(monitors) > 1 else monitors[0]

        if q in ("direita", "right"):
            return max(monitors, key=lambda m: m["x"])

        if q in ("esquerda", "left"):
            return min(monitors, key=lambda m: m["x"])

        if q in ("cima", "topo", "top", "above"):
            return min(monitors, key=lambda m: m["y"])

        if q in ("baixo", "bottom", "below"):
            return max(monitors, key=lambda m: m["y"])

        # Busca por nome ou descrição
        for m in monitors:
            if q in m["name"].lower() or q in m["description"].lower():
                return m

        return None

    @classmethod
    def move_window(
        cls,
        window_or_id: WindowInfo | str,
        target_monitor: dict[str, Any] | str | int,
        drag_visual: bool = True,
    ) -> dict[str, Any]:
        """Move uma janela para o monitor indicado com animação visual de arrastar."""
        # 1. Resolve a janela
        win: WindowInfo | None = None
        if isinstance(window_or_id, WindowInfo):
            win = window_or_id
        else:
            q = str(window_or_id).strip()
            if not q or q.lower() in ("current", "active", "atual", "ativa", "foco"):
                win = cls.get_active_or_last_window()
            else:
                windows = cls.list_windows(exclude_copilot=True)
                for w in windows:
                    if w.id == q:
                        win = w
                        break
                if win is None:
                    win = cls.find_window(q)

        if win is None:
            return {"success": False, "message": f"Janela '{window_or_id}' não encontrada para mover."}

        # 2. Resolve o monitor alvo
        target_mon: dict[str, Any] | None = None
        if isinstance(target_monitor, dict):
            target_mon = target_monitor
        else:
            target_mon = cls.resolve_monitor(target_monitor, current_monitor_id=win.monitor_index)

        if target_mon is None:
            return {"success": False, "message": f"Monitor destino '{target_monitor}' não encontrado."}

        # 3. Animação do Cursor Fantasma (Drag suave cruzando a tela)
        if drag_visual:
            try:
                from ..ui.ghost_cursor import GhostCursorOverlay

                start_x = float(win.x + max(10, win.width // 2))
                start_y = float(win.y + min(36, max(15, win.height // 4)))
                end_x = float(target_mon["x"] + target_mon["width"] // 2)
                end_y = float(target_mon["y"] + target_mon["height"] // 2)
                GhostCursorOverlay.get_default().animate_drag(
                    start_x=start_x,
                    start_y=start_y,
                    end_x=end_x,
                    end_y=end_y,
                    duration_ms=450,
                    label=f"Movendo {win.app}...",
                    wait_glide=False,
                )
            except Exception as exc:
                logger.debug("Falha ao animar drag do cursor: %s", exc)

        # 4. Execução física no compositor
        moved = False
        target_ws = str(target_mon.get("active_workspace", "1"))
        target_name = str(target_mon.get("name", ""))

        # 4.1. Hyprland
        if shutil.which("hyprctl"):
            try:
                cls.focus_window(win.id)
                p1 = subprocess.run(
                    ["hyprctl", "dispatch", "movetoworkspacesilent", f"{target_ws},address:{win.id}"],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                    check=False,
                )
                if p1.returncode == 0:
                    moved = True
                if target_name:
                    subprocess.run(
                        ["hyprctl", "dispatch", "movewindow", f"mon:{target_name}"],
                        capture_output=True,
                        text=True,
                        timeout=0.6,
                        check=False,
                    )
                cls.focus_window(win.id)
            except Exception as exc:
                logger.debug("hyprctl movetoworkspacesilent falhou: %s", exc)

        # 4.2. Sway
        elif shutil.which("swaymsg"):
            try:
                p = subprocess.run(
                    ["swaymsg", f"[con_id={win.id}]", "move", "workspace", target_ws],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                    check=False,
                )
                if p.returncode == 0:
                    moved = True
                elif target_name:
                    p2 = subprocess.run(
                        ["swaymsg", f"[con_id={win.id}]", "move", "output", target_name],
                        capture_output=True,
                        text=True,
                        timeout=0.8,
                        check=False,
                    )
                    moved = (p2.returncode == 0)
            except Exception as exc:
                logger.debug("swaymsg move falhou: %s", exc)

        # 4.3. X11 via wmctrl
        elif shutil.which("wmctrl"):
            try:
                dest_x = target_mon["x"] + max(0, (target_mon["width"] - win.width) // 2)
                dest_y = target_mon["y"] + max(0, (target_mon["height"] - win.height) // 2)
                p = subprocess.run(
                    ["wmctrl", "-i", "-r", win.id, "-e", f"0,{dest_x},{dest_y},{win.width},{win.height}"],
                    capture_output=True,
                    text=True,
                    timeout=0.8,
                    check=False,
                )
                moved = (p.returncode == 0)
            except Exception as exc:
                logger.debug("wmctrl move falhou: %s", exc)
        else:
            moved = True

        name = win.display_name()
        return {
            "success": moved,
            "message": f"Janela '{name}' movida para o monitor '{target_mon['name']}'.",
            "window": win.app,
            "window_id": win.id,
            "target_monitor": target_mon["name"],
        }

    @classmethod
    def move_windows(
        cls,
        windows_query: str = "current",
        target_monitor: str | int = "outro",
        drag_visual: bool = True,
    ) -> dict[str, Any]:
        """Move uma janela, múltiplas janelas ou todas as janelas para o monitor indicado."""
        q = str(windows_query).strip()

        # Mover todas as janelas
        if q.lower() in ("all", "todas", "tudo", "todas as janelas", "*"):
            windows = cls.list_windows(exclude_copilot=True)
            if not windows:
                return {"success": False, "message": "Nenhuma janela aberta encontrada para mover."}

            target_mon = cls.resolve_monitor(target_monitor)
            if target_mon is None:
                return {"success": False, "message": f"Monitor destino '{target_monitor}' não encontrado."}

            moved_count = 0
            moved_names = []
            for w in windows:
                if w.monitor_index == target_mon["id"]:
                    continue
                res = cls.move_window(w, target_mon, drag_visual=drag_visual and (moved_count == 0))
                if res.get("success"):
                    moved_count += 1
                    moved_names.append(w.app)

            if moved_count == 0:
                return {
                    "success": True,
                    "message": f"Todas as janelas já estavam no monitor '{target_mon['name']}'.",
                    "moved_count": 0,
                    "target_monitor": target_mon["name"],
                }

            return {
                "success": True,
                "message": f"{moved_count} janela(s) ({', '.join(moved_names[:4])}) movida(s) para o monitor '{target_mon['name']}'.",
                "moved_count": moved_count,
                "target_monitor": target_mon["name"],
            }

        # Múltiplas janelas separadas por vírgula
        if "," in q:
            parts = [p.strip() for p in q.split(",") if p.strip()]
            moved_count = 0
            for part in parts:
                res = cls.move_window(part, target_monitor, drag_visual=drag_visual and (moved_count == 0))
                if res.get("success"):
                    moved_count += 1
            return {
                "success": moved_count > 0,
                "message": f"{moved_count} de {len(parts)} janela(s) movida(s) para o monitor destino.",
                "moved_count": moved_count,
            }

        # Janela única
        return cls.move_window(q, target_monitor, drag_visual=drag_visual)

