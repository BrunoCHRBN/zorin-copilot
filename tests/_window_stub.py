"""Religação de métodos reais de `CopilotWindow` num stub, sem subir a janela.

Técnica compartilhada por `test_ha_announce_wiring.py`, `test_consolidation_wiring.py`
e `test_attention_wiring.py`. Subir uma `CopilotWindow` de verdade custa **+109 MB e
+1,80 subprocessos** por instância (MCP, RAG) — ver a skill de GUI do projeto. Com o
gate de teste ligado o custo cai para +2,9 MB, mas ainda é caro para dezenas de casos.

O padrão: um objeto simples com os atributos que os métodos tocam, e os métodos reais
religados por `types.MethodType` — não vinculados, porque não há instância de verdade.

**As constantes de classe importam e são a parte sutil.** O stub não herda de
`CopilotWindow`, então um `self.CONSOLIDATION_TICK_SECONDS` legítimo não existiria: o
método quebraria ao rodar e a guarda de completude o reportaria como "colaborador
faltando", apontando para o lugar errado. A cópia é **derivada** de propósito — uma
lista escrita à mão envelheceria em silêncio, que é exatamente o defeito que a guarda
existe para pegar.
"""

from __future__ import annotations

import inspect
import re
import types

from zorin_copilot.ui.app import CopilotWindow


def bind_window_methods(stub: object, methods: tuple[str, ...]) -> None:
    """Religa `methods` no stub, e copia as constantes de classe da janela.

    Chamar **depois** dos atributos default do stub e **antes** dos overrides do
    chamador: a cópia das constantes sobrescreveria um override que viesse antes,
    e o override explícito é a última palavra.
    """
    for constante in dir(CopilotWindow):
        if constante.isupper():
            setattr(stub, constante, getattr(CopilotWindow, constante))
    for nome in methods:
        setattr(stub, nome, types.MethodType(getattr(CopilotWindow, nome), stub))


def missing_collaborators(stub: object, methods: tuple[str, ...]) -> set[str]:
    """Nomes que os métodos reais tocam via `self.` e o stub não tem.

    Lê o **código-fonte** dos métodos religados, em vez de esperar que alguém
    lembre de atualizar a lista à mão. Sem isto, um colaborador novo em `app.py`
    só aparece como `AttributeError` — e só no teste que exercita aquele caminho,
    nunca no arquivo que declara a lista.
    """
    faltando: set[str] = set()
    for nome in methods:
        fonte = inspect.getsource(getattr(CopilotWindow, nome))
        for attr in re.findall(r"self\.([A-Za-z_]\w*)", fonte):
            if not hasattr(stub, attr):
                faltando.add(f"{nome} usa self.{attr}")
    return faltando
