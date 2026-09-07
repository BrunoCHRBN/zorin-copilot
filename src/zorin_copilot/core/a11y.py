# Decisão de design: inspeção semântica via AT-SPI2 — obtém a hierarquia de objetos sem capturar pixels pesados e opera de forma determinística.

"""Inspetor de acessibilidade AT-SPI2 para captura semântica do desktop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


@dataclass
class UIElement:
    name: str
    role: str
    uid: str = ""  # Identificador estável por inspeção (caminho de índices), usado pelo agente
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

    def to_summary(self, indent: int = 0, include_bounds: bool = False) -> str:
        """Gera uma representação textual compacta para enviar como contexto à IA.

        Quando `include_bounds` é True, cada linha traz a geometria (x, y, w, h) do
        elemento, permitindo à IA fundir a árvore semântica com o frame de vídeo.
        """
        prefix = "  " * indent
        actions_str = f" [ações: {', '.join(self.actions)}]" if self.actions else ""
        uid_str = f" [{self.uid}]" if self.uid else ""
        bounds_str = ""
        if include_bounds and self.bbox != (0, 0, 0, 0):
            bounds_str = f" @({self.bbox[0]},{self.bbox[1]} {self.bbox[2]}x{self.bbox[3]})"
        text = f"{prefix}-{uid_str} {self.role}: '{self.name}'{bounds_str}{actions_str}"
        lines = [text]
        for child in self.children:
            lines.append(child.to_summary(indent + 1, include_bounds=include_bounds))
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
                    return self._parse_node(app, max_depth=max_depth, uid_path=(i,))
        except Exception:
            pass
        return None

    def _parse_node(
        self, node: Any, depth: int = 0, max_depth: int = 4, uid_path: tuple[int, ...] = ()
    ) -> UIElement:
        try:
            name = node.get_name() or ""
            role_name = node.get_role_name() or "unknown"
            desc = node.get_description() or ""
        except Exception:
            return UIElement(name="error", role="unknown")

        # Geometria em coordenadas de tela (fusão vídeo + AT-SPI). Campo opcional:
        # controles sem superfície (ex: menus virtuais) ou sem suporte retornam (0,0,0,0).
        bbox = (0, 0, 0, 0)
        try:
            coord_type = getattr(self._atspi, "CoordType", None)
            screen = getattr(coord_type, "SCREEN", None) if coord_type else None
            if screen is not None and hasattr(node, "get_extents"):
                ext = node.get_extents(screen)
                if ext and len(ext) == 4:
                    bbox = tuple(int(v) for v in ext)
        except Exception:
            bbox = (0, 0, 0, 0)

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
                        children.append(
                            self._parse_node(child_node, depth + 1, max_depth, uid_path + (c,))
                        )
            except Exception:
                pass

        uid = ".".join(str(i) for i in uid_path) if uid_path else "0"
        return UIElement(
            name=name,
            role=role_name,
            uid=uid,
            description=desc,
            actions=tuple(actions),
            bbox=bbox,
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

    def focus_element(self, element: UIElement) -> bool:
        """Dá foco ao elemento (necessário antes de digitação via input virtual)."""
        if not element.raw_ref:
            return False
        try:
            return bool(element.raw_ref.grab_focus())
        except Exception:
            return False

    def text_insert(self, element: UIElement, text: str, append: bool = False) -> tuple[bool, str]:
        """Insere texto semanticamente num campo editável via interface de texto do AT-SPI.

        Caminho preferencial para digitação no Wayland: não depende de uinput nem de
        portais. Requer que o controle exponha ``EditableText``/``Text`` (GTK/Qt expõem).
        Retorna ``(ok, mensagem)``.
        """
        if not element.raw_ref:
            return False, "Elemento sem referência nativa (raw_ref vazio)."
        if not text:
            return False, "Nenhum texto para inserir."

        node = element.raw_ref
        try:
            # Garante foco antes de inserir
            try:
                node.grab_focus()
            except Exception:
                pass

            et_iface = node.get_editable_text_iface()
            if et_iface is not None:
                if append:
                    start = -1
                    try:
                        t_iface = node.get_text_iface()
                        if t_iface is not None:
                            start = t_iface.get_character_count()
                    except Exception:
                        start = -1
                    et_iface.insert_text(text, start)
                else:
                    et_iface.set_text_contents(text)
                return True, f"Texto inserido em '{element.name}' via AT-SPI EditableText."

            t_iface = node.get_text_iface()
            if t_iface is not None:
                t_iface.insert_text(0, text)
                return True, f"Texto inserido em '{element.name}' via AT-SPI Text."

            return False, f"'{element.name}' não expõe interface de texto editável (AT-SPI)."
        except Exception as exc:
            return False, f"Falha ao inserir texto via AT-SPI: {exc}"

    def get_focused_app(self) -> str | None:
        """Retorna o nome da aplicação que detém o foco de teclado no momento."""
        if not self._ensure_init() or not self._atspi:
            return None
        try:
            focused = self._atspi.get_focused_element()
            node = focused
            last = None
            while node is not None:
                last = node
                try:
                    if node.get_role_name() == "application":
                        return node.get_name() or None
                except Exception:
                    pass
                parent = node.get_parent()
                if parent is None:
                    break
                node = parent
            return last.get_name() if last else None
        except Exception:
            return None

    def get_ui_tree(self, app_name: str | None = None) -> UIElement | None:
        """Árvore de acessibilidade de um app (ou do app com foco, se omitido)."""
        if app_name:
            return self.inspect_application(app_name)
        focused = self.get_focused_app()
        if focused:
            return self.inspect_application(focused)
        return None

    @staticmethod
    def find_element_by_uid(root: UIElement, uid: str) -> UIElement | None:
        """Resolve um UID (gerado em `inspect_application`) de volta ao elemento."""
        if root.uid == uid:
            return root
        for child in root.children:
            found = DesktopInspector.find_element_by_uid(child, uid)
            if found:
                return found
        return None

    @staticmethod
    def element_at_point(root: UIElement, x: int, y: int) -> UIElement | None:
        """Dado um ponto em coordenadas de tela, devolve o elemento mais específico ali presente.

        Fundamental para a fusão vídeo + AT-SPI: o modelo vê um ponto no frame de vídeo,
        converte para coordenadas de tela e recebe o UID semântico do elemento.
        Empata sempre pelo menor contorno (mais aninhado / mais específico).
        """
        candidates: list[UIElement] = []

        def _walk(el: UIElement) -> None:
            bx, by, bw, bh = el.bbox
            if bw > 0 and bh > 0 and bx <= x <= bx + bw and by <= y <= by + bh:
                candidates.append(el)
            for child in el.children:
                _walk(child)

        _walk(root)
        if not candidates:
            return None
        candidates.sort(key=lambda e: e.bbox[2] * e.bbox[3])
        return candidates[0]
