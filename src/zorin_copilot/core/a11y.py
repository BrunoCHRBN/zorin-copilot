# Decisão de design: inspeção semântica via AT-SPI2 — obtém a hierarquia de objetos sem capturar pixels pesados e opera de forma determinística.

"""Inspetor de acessibilidade AT-SPI2 para captura semântica do desktop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


@dataclass
class UIElement:
    name: str
    role: str
    description: str = ""
    states: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, width, height
    children: list[UIElement] = field(default_factory=list)
    raw_ref: Any = field(default=None, repr=False)

    @property
    def is_interactive(self) -> bool:
        """Determina se o elemento aceita interação do usuário (clique, escrita, seleção)."""
        interactive_roles = {
            "push_button",
            "check_box",
            "radio_button",
            "text",
            "entry",
            "menu_item",
            "combo_box",
            "slider",
            "link",
            "list_item",
        }
        return self.role.lower() in interactive_roles or bool(self.actions)

    def find(self, predicate: Callable[[UIElement], bool]) -> list[UIElement]:
        matches: list[UIElement] = []
        if predicate(self):
            matches.append(self)
        for child in self.children:
            matches.extend(child.find(predicate))
        return matches

    def to_summary(self, indent: int = 0) -> str:
        """Gera uma representação textual compacta para enviar como contexto à IA."""
        prefix = "  " * indent
        actions_str = f" [ações: {', '.join(self.actions)}]" if self.actions else ""
        text = f"{prefix}- {self.role}: '{self.name}'{actions_str}"
        lines = [text]
        for child in self.children:
            lines.append(child.to_summary(indent + 1))
        return "\n".join(lines)


class DesktopInspector:
    """Interage com a árvore de acessibilidade do GNOME/Wayland via Atspi."""

    def __init__(self, atspi_module: Any | None = None):
        self._atspi = atspi_module
        self._initialized = False

    def _ensure_init(self) -> bool:
        if self._initialized:
            return True
        if self._atspi is None:
            try:
                import gi
                gi.require_version("Atspi", "2.0")
                from gi.repository import Atspi
                self._atspi = Atspi
            except (ImportError, ValueError):
                return False
        try:
            self._atspi.init()
            self._initialized = True
            return True
        except Exception:
            return False

    def list_applications(self) -> list[str]:
        """Lista nomes das aplicações registradas na árvore de acessibilidade."""
        if not self._ensure_init() or not self._atspi:
            return []
        try:
            desktop = self._atspi.get_desktop(0)
            count = desktop.get_child_count()
            apps = []
            for i in range(count):
                app = desktop.get_child_at_index(i)
                if app:
                    name = app.get_name()
                    if name:
                        apps.append(name)
            return apps
        except Exception:
            return []

    def inspect_application(self, app_name: str, max_depth: int = 4) -> UIElement | None:
        """Extrai a árvore estruturada da janela de uma aplicação específica."""
        if not self._ensure_init() or not self._atspi:
            return None
        try:
            desktop = self._atspi.get_desktop(0)
            count = desktop.get_child_count()
            for i in range(count):
                app = desktop.get_child_at_index(i)
                if app and app.get_name().lower() == app_name.lower():
                    return self._parse_node(app, max_depth=max_depth)
        except Exception:
            pass
        return None

    def _parse_node(self, node: Any, depth: int = 0, max_depth: int = 4) -> UIElement:
        try:
            name = node.get_name() or ""
            role_name = node.get_role_name() or "unknown"
            desc = node.get_description() or ""
        except Exception:
            return UIElement(name="error", role="unknown")

        actions: list[str] = []
        try:
            action_iface = node.get_action_iface()
            if action_iface:
                act_count = action_iface.get_n_actions()
                for k in range(min(act_count, 6)):
                    act_name = action_iface.get_action_name(k)
                    if act_name:
                        actions.append(act_name)
        except Exception:
            pass

        children: list[UIElement] = []
        if depth < max_depth:
            try:
                child_count = node.get_child_count()
                for c in range(min(child_count, 30)):
                    child_node = node.get_child_at_index(c)
                    if child_node:
                        children.append(self._parse_node(child_node, depth + 1, max_depth))
            except Exception:
                pass

        return UIElement(
            name=name,
            role=role_name,
            description=desc,
            actions=tuple(actions),
            children=children,
            raw_ref=node,
        )

    def do_action(self, element: UIElement, action_index: int = 0) -> bool:
        """Executa a ação semântica direta no elemento (ex: clique ou ativação)."""
        if not element.raw_ref:
            return False
        try:
            action_iface = element.raw_ref.get_action_iface()
            if action_iface and action_index < action_iface.get_n_actions():
                return bool(action_iface.do_action(action_index))
        except Exception:
            pass
        return False
