"""Introspecção da árvore de widgets GTK — apoio aos testes de UI.

Existe porque os testes da reformulação da GUI (`docs/PROPOSTA_REFORMULACAO_GUI.md`)
precisam afirmar coisas sobre a **forma** dos widgets — que ícone uma linha usa,
que rótulos ela contém, se um botão destrutivo está escondido até o hover. Isso
não é acessível pela API pública das linhas do Libadwaita, então os testes
descem a árvore.

Detalhe que engana: `Adw.ActionRow.add_prefix()` não anexa o widget como filho
direto — o Libadwaita empacota prefixos e sufixos em caixas internas. Por isso a
busca é recursiva, e não uma varredura dos irmãos.
"""

from __future__ import annotations

from typing import Iterator

from gi.repository import Gtk


def descendants(widget: Gtk.Widget) -> Iterator[Gtk.Widget]:
    """Todos os widgets abaixo de `widget`, em profundidade, sem incluí-lo."""
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


def icon_names(widget: Gtk.Widget) -> list[str]:
    """Nomes de ícone de todos os `Gtk.Image` abaixo de `widget`."""
    found: list[str] = []
    for node in descendants(widget):
        if isinstance(node, Gtk.Image):
            name = node.get_icon_name()
            if name:
                found.append(name)
    return found


def labels(widget: Gtk.Widget) -> list[str]:
    """Texto de todos os `Gtk.Label` abaixo de `widget`."""
    return [node.get_text() for node in descendants(widget) if isinstance(node, Gtk.Label)]


def first_controller(widget: Gtk.Widget, kind: type) -> object | None:
    """Primeiro controlador de eventos de `widget` do tipo `kind`, ou None.

    Necessário para exercitar hover nos testes: `Gtk.EventControllerMotion` é
    anexado ao widget, não exposto por atributo, e o único jeito de simular a
    entrada do ponteiro é emitir o sinal no controlador.
    """
    controllers = widget.observe_controllers()
    for index in range(controllers.get_n_items()):
        controller = controllers.get_item(index)
        if isinstance(controller, kind):
            return controller
    return None
