"""Suíte de testes do Zorin Copilot.

Este `__init__.py` existe por um motivo de segurança, não por organização: ele
é o único gancho que o `unittest` oferece para executar código **antes** de
qualquer módulo de teste ser importado. É aqui que o isolamento de ambiente
entra — redirecionamento de XDG, `ZORIN_TEST_MODE` e bloqueio de serviços
pesados (MCP, RAG em disco, wake word).

Descoberta como pacote, então:

    python3 -m unittest discover -s tests -t .

O `-t .` é obrigatório: sem ele o `unittest` trata `tests/` como raiz simples,
importa os módulos como top-level e **não** importa este arquivo — a suíte
rodaria sem nenhuma proteção, lendo e escrevendo o config e o banco de memória
reais do usuário.

Ver `_sandbox.py` para a explicação completa do que é isolado e por quê.
"""

from __future__ import annotations

from . import _sandbox

_sandbox.activate()
