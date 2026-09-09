"""Configuração compartilhada da suíte de testes."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def destroy_leftover_toplevels():
    """Destrói as janelas que cada teste deixa vivas.

    A suíte constrói um `CopilotWindow` por teste e nunca os fechava. Toda
    janela viva mantém fontes no `GMainContext`: o frame clock do GDK roda em
    `G_PRIORITY_HIGH_IDLE` (100) e os ticks de telemetria em
    `G_PRIORITY_DEFAULT` (0). O `GMainContext` só despacha uma fonte quando não
    há nenhuma de prioridade mais alta pronta — portanto algumas centenas de
    janelas acumuladas bastam para que **nenhum** `GLib.idle_add`
    (`G_PRIORITY_DEFAULT_IDLE`, 200) volte a rodar. E é por idle que o app
    entrega a resposta da IA à interface: na prática, a janela ficava presa no
    estado "ocupada" para sempre.

    O sintoma era traiçoeiro: o teste passava isolado e falhava no fim da suíte,
    sem nenhum erro no meio do caminho.

    A limpeza só roda se algum teste já tiver importado o Gtk — testes de núcleo
    puro não devem precisar de toolkit para rodar.
    """
    yield

    gtk = sys.modules.get("gi.repository.Gtk")
    if gtk is None:
        return
    for window in gtk.Window.list_toplevels():
        try:
            window.destroy()
        except Exception:  # pragma: no cover - janela já meio desmontada
            pass
