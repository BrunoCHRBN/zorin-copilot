"""Guarda de coleta: **nada** neste diretório é teste.

Os arquivos daqui são *harnesses de mutação* — eles **modificam arquivos de produção
em disco de propósito**, rodam a suíte para ver se ela acusa, e restauram em `finally`.
Um harness coletado como teste seria um teste que reescreve o código que está sendo
testado.

A proteção real é a nomenclatura (`mutate_*.py` não casa com o padrão `test_*.py` do
pytest nem com o `name.startswith("test_")` do `run_tests_safe.py`). Este arquivo é a
segunda tranca, explícita, para que a segurança não dependa de ninguém lembrar da
regra de nomes ao adicionar o próximo harness.
"""

from __future__ import annotations

collect_ignore_glob = ["*"]
