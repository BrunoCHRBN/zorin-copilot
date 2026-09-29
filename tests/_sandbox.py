"""Isolamento de ambiente da suíte de testes — independente do runner.

Por que este módulo existe
--------------------------
Antes, todo o isolamento vivia em `tests/conftest.py`. Só que `conftest.py` é um
arquivo **do pytest**: ele é importado pelo pytest e por mais ninguém. O README
documenta dois comandos de teste:

    python3 -m unittest discover -s tests      # núcleo
    xvfb-run -a python3 -m pytest -q           # suíte completa (UI)

No segundo, o `conftest.py` roda e tudo fica protegido. No primeiro — que é o
comando "oficial" do núcleo — **nenhuma** das proteções era aplicada:

1. `ZORIN_TEST_MODE` nunca era definido;
2. `XDG_CONFIG_HOME` / `XDG_DATA_HOME` nunca eram redirecionados, então a suíte
   lia e escrevia o `config.json` e o `memory.db` **reais do usuário**;
3. os bloqueios de MCP e de indexação RAG em disco não existiam;
4. as janelas GTK criadas pelos testes nunca eram destruídas.

O item 3 é o que derrubava a máquina. Medido: cada `CopilotWindow` sobe os
servidores MCP habilitados (subprocessos Node/Python via `uvx`/`npx`), o
indexador RAG varrendo ~/Documentos e ~/Downloads e o motor de wake word. Custo
por janela: **~130 MB e 2 processos**, nunca devolvidos na destruição. Há um
teste que cria 30 janelas de uma vez. Sem swap, o OOM killer entra e o sistema
reinicia no meio da execução.

Este módulo centraliza o isolamento para que ele valha **em qualquer runner**.

Como ativá-lo
-------------
- pytest: `tests/conftest.py` chama `activate()`.
- unittest: `tests/__init__.py` chama `activate()`; requer que a suíte seja
  descoberta como pacote, ou seja `python3 -m unittest discover -s tests -t .`
  (o `-t .` é o que faz o `__init__.py` ser importado).

`activate()` é idempotente e seguro de chamar mais de uma vez.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

_ACTIVATED = False
_SANDBOX_DIR: str | None = None


def activate() -> str:
    """Ativa o isolamento de ambiente e devolve o diretório sandbox criado.

    Idempotente: chamadas repetidas devolvem o mesmo diretório sem reescrever
    nada. É seguro chamar de `__init__.py` e de `conftest.py` na mesma execução.
    """
    global _ACTIVATED, _SANDBOX_DIR

    if _ACTIVATED and _SANDBOX_DIR:
        return _SANDBOX_DIR

    # 1. Flag de modo de teste, consumida por `_is_test_environment()` no app.
    #    É ela que impede MCP, RAG em disco e wake word de subirem por janela.
    os.environ["ZORIN_TEST_MODE"] = "1"

    # 2. Sandbox temporário para config e dados — nada toca o perfil do usuário.
    _SANDBOX_DIR = tempfile.mkdtemp(prefix="zorin_copilot_test_sandbox_")
    config_dir = os.path.join(_SANDBOX_DIR, "config")
    data_dir = os.path.join(_SANDBOX_DIR, "data")
    cache_dir = os.path.join(_SANDBOX_DIR, "cache")
    app_config_dir = os.path.join(config_dir, "zorin-copilot")
    app_data_dir = os.path.join(data_dir, "zorin-copilot")

    for path in (app_config_dir, app_data_dir, cache_dir):
        os.makedirs(path, exist_ok=True)

    # Config mínima e inerte: sem chave, sem MCP automático, sem wake word,
    # sem diretórios confiáveis (portanto sem varredura de disco).
    with open(os.path.join(app_config_dir, "config.json"), "w", encoding="utf-8") as fh:
        fh.write(
            "{\n"
            '  "provider": "gemini",\n'
            '  "gemini_api_key": "",\n'
            '  "mcp_enabled": true,\n'
            '  "mcp_auto_connect": false,\n'
            '  "wake_word_enabled": false,\n'
            '  "auto_execute_safe_actions": false,\n'
            '  "ghost_cursor_enabled": false,\n'
            '  "trusted_directories": [],\n'
            '  "quarantine_directories": []\n'
            "}\n"
        )

    with open(os.path.join(app_config_dir, "mcp_servers.json"), "w", encoding="utf-8") as fh:
        fh.write('{"mcpServers": {}}\n')

    os.environ["XDG_CONFIG_HOME"] = config_dir
    os.environ["XDG_DATA_HOME"] = data_dir
    os.environ["XDG_CACHE_HOME"] = cache_dir

    # 3. O diretório `tests/` precisa continuar importável como raiz simples,
    #    porque há módulos que fazem `from fakes import ...`. Quando a suíte é
    #    descoberta como pacote (`-t .`), o unittest insere a raiz do projeto —
    #    não `tests/` — então reafirmamos `tests/` aqui.
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)

    _ACTIVATED = True
    return _SANDBOX_DIR


def cleanup() -> None:
    """Remove o sandbox temporário. Chamado ao fim da sessão de testes."""
    global _SANDBOX_DIR
    if _SANDBOX_DIR:
        shutil.rmtree(_SANDBOX_DIR, ignore_errors=True)
        _SANDBOX_DIR = None


def destroy_leftover_toplevels() -> None:
    """Destrói janelas GTK sobreviventes e drena o `GMainContext`.

    Usado como tearDown pelos testes de UI: sem isso, cada janela viva mantém
    seu `GMainContext` e as referências de widget, e o processo cresce até o
    OOM. Só age se o GTK já estiver carregado — não força o carregamento.
    """
    gtk = sys.modules.get("gi.repository.Gtk")
    if gtk is not None:
        for window in list(gtk.Window.list_toplevels()):
            try:
                window.destroy()
            except Exception:  # pragma: no cover - janela já meio desmontada
                pass

    glib = sys.modules.get("gi.repository.GLib")
    if glib is not None:
        try:
            ctx = glib.MainContext.default()
            while ctx.iteration(False):
                pass
        except Exception:  # pragma: no cover
            pass

    import gc

    gc.collect()
