# Decisão de design: um StatusNotifierItem sem menu expõe `Menu = "/"`, e aí a barra
# degenera para "clicar chama Activate". Isso deixa a bandeja com uma ação só e enterra
# recorte, RAG, preferências e kill switch.
#
# O protocolo que as barras realmente leem é o `com.canonical.dbusmenu` — o mesmo que
# o Plasma, a waybar e o snixembed consomem. Ele é só D-Bus: uma árvore de itens com
# propriedades e um método `Event`. Não precisa de GTK.
#
# Implementamos o subconjunto que importa: layout, propriedades, `clicked`, e os sinais
# de atualização. `icon-data` e menus dinâmicos (AboutToShow com lazy build) ficam de
# fora porque o Copilot não precisa deles.

"""Menu D-Bus (``com.canonical.dbusmenu``) para o ícone de bandeja.

Uso::

    menu = DbusMenu([
        MenuItem(id=1, label="Abrir Copilot", on_clicked=abrir),
        MenuItem(id=2, type="separator"),
        MenuItem(id=3, label="Sair", on_clicked=sair),
    ])
    tray = StatusNotifierTray(menu=menu)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

DBUSMENU_IFACE: str = "com.canonical.dbusmenu"
DBUSMENU_PATH: str = "/Menu"

#: Propriedades devolvidas quando a barra não especifica quais quer.
_DEFAULT_PROPS: tuple[str, ...] = (
    "type",
    "label",
    "enabled",
    "visible",
    "icon-name",
    "toggle-type",
    "toggle-state",
    "children-display",
)

DBUSMENU_XML: str = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="a(ib)" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(i)" name="removedProps"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
  </interface>
</node>
"""


@dataclass
class MenuItem:
    """Um item do menu da bandeja.

    ``id`` precisa ser único e diferente de 0 — o zero é reservado pelo protocolo
    para designar a raiz da árvore.
    """

    id: int
    label: str = ""
    icon_name: str = ""
    enabled: bool = True
    visible: bool = True
    type: str = "standard"  # "standard" | "separator"
    toggle_type: str = ""  # "" | "checkmark" | "radio"
    toggle_state: int = 0  # -1 indeterminado, 0 desligado, 1 ligado
    children_display: str = ""  # "" | "submenu"
    children: list[MenuItem] = field(default_factory=list)
    on_clicked: Callable[[], None] | None = None

    def find(self, item_id: int) -> MenuItem | None:
        if self.id == item_id:
            return self
        for child in self.children:
            hit = child.find(item_id)
            if hit is not None:
                return hit
        return None


def _v_string(text: str) -> Any:
    from gi.repository import GLib

    return GLib.Variant.new_string(text)


def _v_bool(value: bool) -> Any:
    from gi.repository import GLib

    return GLib.Variant.new_boolean(bool(value))


def _v_int(value: int) -> Any:
    from gi.repository import GLib

    return GLib.Variant.new_int32(int(value))


def _property_variant(item: MenuItem, name: str) -> Any | None:
    """Variante de uma única propriedade, ou None se ela não existir.

    Fica separada de :func:`_pack_properties` porque `GetProperty` precisa da
    variante **embrulhada** — e `_pack_properties(...).unpack()` devolve o valor
    já desembrulhado (o PyGObject resolve o `v` sozinho), o que não serve para
    montar a resposta `(v)`.
    """
    factories: dict[str, Callable[[], Any]] = {
        "type": lambda: _v_string(item.type),
        "label": lambda: _v_string(item.label),
        "enabled": lambda: _v_bool(item.enabled),
        "visible": lambda: _v_bool(item.visible),
        "icon-name": lambda: _v_string(item.icon_name),
        "toggle-type": lambda: _v_string(item.toggle_type),
        "toggle-state": lambda: _v_int(item.toggle_state),
        "children-display": lambda: _v_string(item.children_display),
    }
    factory = factories.get(name)
    return factory() if factory else None


def _pack_properties(item: MenuItem, names: Sequence[str]) -> Any:
    """Empacota as propriedades pedidas num ``a{sv}``."""
    from gi.repository import GLib

    wanted = tuple(names) if names else _DEFAULT_PROPS
    builder = GLib.VariantDict.new(None)
    for name in wanted:
        value = _property_variant(item, name)
        if value is not None:
            builder.insert_value(name, value)
    return builder.end()


def _pack_node(item: MenuItem, names: Sequence[str], depth: int) -> Any:
    """Monta o nó ``(ia{sv}av)`` exigido pelo GetLayout.

    ``depth`` segue a convenção do protocolo: ``-1`` recursão irrestrita,
    ``0`` devolve o nó sem filhos.
    """
    from gi.repository import GLib

    builder = GLib.VariantBuilder.new(GLib.VariantType.new("(ia{sv}av)"))
    builder.add_value(_v_int(item.id))
    builder.add_value(_pack_properties(item, names))

    children = GLib.VariantBuilder.new(GLib.VariantType.new("av"))
    if depth != 0:
        for child in item.children:
            # `av` guarda *variantes*: o nó precisa ser embrulhado, senão o
            # GLib reclama (e a barra recebe um layout fora do tipo).
            children.add_value(
                GLib.Variant.new_variant(_pack_node(child, names, depth - 1 if depth > 0 else -1))
            )
    builder.add_value(children.end())
    return builder.end()


def _error_domain() -> Any:
    """Quark de erro D-Bus, resolvido tarde para não exigir Gio no import."""
    from gi.repository import Gio

    return Gio.dbus_error_quark()


class DbusMenu:
    """Exporta uma árvore de :class:`MenuItem` como ``com.canonical.dbusmenu``.

    A árvore é estática: montada uma vez e republicada com :meth:`set_items`, que
    incrementa a revisão e emite ``LayoutUpdated`` para as barras redesenharem.
    """

    VERSION: int = 3

    def __init__(self, items: Sequence[MenuItem] | None = None, path: str = DBUSMENU_PATH):
        self.path = path
        self.items: list[MenuItem] = list(items or [])
        self._revision: int = 1
        self._conn: Any = None
        self._registration_id: int = 0

    # ------------------------------------------------------------------ setup
    def register(self, conn: Any) -> bool:
        """Publica o objeto no caminho configurado. Falso se o D-Bus falhar."""
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except (ImportError, ValueError) as exc:
            logger.info(f"dbusmenu indisponível (sem Gio): {exc}")
            return False

        self._conn = conn
        try:
            node_info = Gio.DBusNodeInfo.new_for_xml(DBUSMENU_XML)
            iface_info = node_info.lookup_interface(DBUSMENU_IFACE)
            self._registration_id = conn.register_object(
                self.path,
                iface_info,
                self._handle_method,
                self._handle_get_property,
                None,
            )
        except Exception as exc:
            logger.warning(f"Falha ao registrar menu da bandeja: {exc}")
            return False
        return True

    def unregister(self) -> None:
        if self._registration_id and self._conn is not None:
            try:
                self._conn.unregister_object(self._registration_id)
            except Exception:
                pass
        self._registration_id = 0
        self._conn = None

    @property
    def is_registered(self) -> bool:
        return self._registration_id != 0

    # ----------------------------------------------------------------- árvore
    def set_items(self, items: Sequence[MenuItem]) -> None:
        """Troca a árvore inteira e avisa as barras."""
        self.items = list(items)
        self._revision += 1
        self._emit_layout_updated(0)

    def set_item_property(self, item_id: int, name: str, value: Any) -> None:
        """Atualiza uma propriedade (ex.: alternar ``toggle_state``)."""
        item = self.find(item_id)
        if item is None:
            return
        attr = {
            "label": "label",
            "enabled": "enabled",
            "visible": "visible",
            "icon-name": "icon_name",
            "toggle-state": "toggle_state",
        }.get(name)
        if attr is None:
            return
        setattr(item, attr, value)
        self._emit_properties_updated([item])

    def find(self, item_id: int) -> MenuItem | None:
        for item in self.items:
            hit = item.find(item_id)
            if hit is not None:
                return hit
        return None

    # --------------------------------------------------------------- handlers
    def _handle_method(self, conn, sender, path, iface, method, params, invocation, *args) -> None:
        from gi.repository import GLib

        try:
            if method == "GetLayout":
                parent_id, depth, names = params.unpack()
                root = self._root_for(parent_id)
                if root is None:
                    invocation.return_error_literal(_error_domain(), "Item não encontrado")
                    return
                invocation.return_value(
                    GLib.Variant.new_tuple(
                        GLib.Variant.new_uint32(self._revision),
                        _pack_node(root, list(names), depth),
                    )
                )
                return

            if method == "GetGroupProperties":
                ids, names = params.unpack()
                outer = GLib.VariantBuilder.new(GLib.VariantType.new("a(ia{sv})"))
                for item_id in ids:
                    item = self.find(item_id)
                    if item is None:
                        continue
                    entry = GLib.VariantBuilder.new(GLib.VariantType.new("(ia{sv})"))
                    entry.add_value(_v_int(item.id))
                    entry.add_value(_pack_properties(item, list(names)))
                    outer.add_value(entry.end())
                invocation.return_value(GLib.Variant.new_tuple(outer.end()))
                return

            if method == "GetProperty":
                item_id, name = params.unpack()
                item = self.find(item_id)
                if item is None:
                    invocation.return_error_literal(_error_domain(), "Item não encontrado")
                    return
                value = _property_variant(item, name)
                if value is None:
                    invocation.return_error_literal(
                        _error_domain(), f"Propriedade desconhecida: {name}"
                    )
                    return
                invocation.return_value(GLib.Variant.new_tuple(value))
                return

            if method == "Event":
                item_id, event_id, _data, _timestamp = params.unpack()
                if event_id == "clicked":
                    self._activate(item_id)
                invocation.return_value(None)
                return

            if method == "AboutToShow":
                # Árvore estática: nada a recalcular antes de exibir.
                invocation.return_value(GLib.Variant.new_tuple(GLib.Variant.new_boolean(False)))
                return

            if method == "AboutToShowGroup":
                ids = params.unpack()[0]
                updates = GLib.VariantBuilder.new(GLib.VariantType.new("a(ib)"))
                for item_id in ids:
                    entry = GLib.VariantBuilder.new(GLib.VariantType.new("(ib)"))
                    entry.add_value(_v_int(item_id))
                    entry.add_value(GLib.Variant.new_boolean(False))
                    updates.add_value(entry.end())
                invocation.return_value(
                    GLib.Variant.new_tuple(
                        updates.end(),
                        GLib.Variant.new_array(GLib.VariantType.new("i"), []),
                    )
                )
                return
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning(f"Erro ao tratar {method} do dbusmenu: {exc}")
            try:
                invocation.return_error_literal(_error_domain(), str(exc))
            except Exception:
                pass
            return

        invocation.return_value(None)

    def _handle_get_property(self, conn, sender, path, iface, prop, *args):
        from gi.repository import GLib

        if prop == "Version":
            return GLib.Variant.new_uint32(self.VERSION)
        if prop == "Status":
            return GLib.Variant.new_string("normal")
        if prop == "TextDirection":
            return GLib.Variant.new_string("ltr")
        if prop == "IconThemePath":
            return GLib.Variant.new_array(GLib.VariantType.new("s"), [])
        return None

    # --------------------------------------------------------------- internos
    def _root_for(self, parent_id: int) -> MenuItem | None:
        """O protocolo usa parentId 0 para "a raiz"; nossos ids começam em 1."""
        if parent_id == 0:
            return MenuItem(
                id=0,
                type="standard",
                children_display="submenu",
                children=list(self.items),
            )
        return self.find(parent_id)

    def _activate(self, item_id: int) -> None:
        item = self.find(item_id)
        if item is None or item.on_clicked is None:
            return
        try:
            item.on_clicked()
        except Exception as exc:
            logger.error(f"Erro na ação do menu da bandeja: {exc}")

    def _emit_signal(self, name: str, body: Any) -> None:
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(None, self.path, DBUSMENU_IFACE, name, body)
        except Exception as exc:
            logger.debug(f"Falha ao emitir {name}: {exc}")

    def _emit_layout_updated(self, parent: int) -> None:
        from gi.repository import GLib

        self._emit_signal(
            "LayoutUpdated",
            GLib.Variant.new_tuple(
                GLib.Variant.new_uint32(self._revision),
                GLib.Variant.new_int32(parent),
            ),
        )

    def _emit_properties_updated(self, items: Sequence[MenuItem]) -> None:
        from gi.repository import GLib

        updated = GLib.VariantBuilder.new(GLib.VariantType.new("a(ia{sv})"))
        for item in items:
            entry = GLib.VariantBuilder.new(GLib.VariantType.new("(ia{sv})"))
            entry.add_value(_v_int(item.id))
            entry.add_value(_pack_properties(item, _DEFAULT_PROPS))
            updated.add_value(entry.end())
        removed = GLib.VariantBuilder.new(GLib.VariantType.new("ai"))
        self._emit_signal(
            "ItemsPropertiesUpdated",
            GLib.Variant.new_tuple(updated.end(), removed.end()),
        )


def build_menu(spec: Sequence[dict[str, Any]]) -> list[MenuItem]:
    """Conveniência: monta ``MenuItem``s a partir de dicts (usado pela UI).

    Chaves aceitas: ``id``, ``label``, ``icon``, ``enabled``, ``separator``,
    ``toggle`` (estado inicial, bool), ``on_clicked``, ``children``.
    """
    next_id = [1]

    def build(entry: dict[str, Any]) -> MenuItem:
        item_id = int(entry.get("id") or next_id[0])
        next_id[0] = max(next_id[0], item_id + 1)
        if entry.get("separator"):
            return MenuItem(id=item_id, type="separator")
        toggle = entry.get("toggle")
        children = [build(child) for child in (entry.get("children") or [])]
        return MenuItem(
            id=item_id,
            label=str(entry.get("label", "")),
            icon_name=str(entry.get("icon") or entry.get("icon_name") or ""),
            enabled=bool(entry.get("enabled", True)),
            toggle_type="checkmark" if toggle is not None else "",
            toggle_state=1 if toggle else 0,
            children_display="submenu" if children else "",
            children=children,
            on_clicked=entry.get("on_clicked"),
        )

    return [build(entry) for entry in spec]


__all__ = [
    "DBUSMENU_IFACE",
    "DBUSMENU_PATH",
    "DBUSMENU_XML",
    "DbusMenu",
    "MenuItem",
    "build_menu",
]
