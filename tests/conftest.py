"""Configuração compartilhada da suíte de testes.

Garante isolamento completo do ambiente do usuário:
1. XDG_CONFIG_HOME e XDG_DATA_HOME são redirecionados para diretórios temporários,
   impedindo a leitura e gravação no config.json e nos servidores MCP reais do usuário.
2. Servidores MCP externos, threads de indexação RAG em disco e captura de microfone por Wake Word
   são estritamente desativados em modo de teste para impedir explosão de processos (OOM).
3. Janelas toplevel do GTK são destruídas automaticamente após cada teste para evitar leaks
   no GMainContext e popups indesejados no compositor gráfico.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# 1. Ativa imediatamente a flag de modo de teste para o processo
os.environ["ZORIN_TEST_MODE"] = "1"

# 2. Cria diretório temporário isolado para configurações e dados de teste
_TEST_SANDBOX_DIR = tempfile.mkdtemp(prefix="zorin_copilot_test_sandbox_")
_TEST_CONFIG_DIR = os.path.join(_TEST_SANDBOX_DIR, "config")
_TEST_DATA_DIR = os.path.join(_TEST_SANDBOX_DIR, "data")
_TEST_APP_CONFIG_DIR = os.path.join(_TEST_CONFIG_DIR, "zorin-copilot")
_TEST_APP_DATA_DIR = os.path.join(_TEST_DATA_DIR, "zorin-copilot")

os.makedirs(_TEST_APP_CONFIG_DIR, exist_ok=True)
os.makedirs(_TEST_APP_DATA_DIR, exist_ok=True)

# Escreve config mínima e segura para o ambiente de testes
with open(os.path.join(_TEST_APP_CONFIG_DIR, "config.json"), "w", encoding="utf-8") as _f:
    _f.write(
        '{\n'
        '  "provider": "gemini",\n'
        '  "gemini_api_key": "",\n'
        '  "mcp_enabled": true,\n'
        '  "mcp_auto_connect": false,\n'
        '  "wake_word_enabled": false,\n'
        '  "auto_execute_safe_actions": false,\n'
        '  "ghost_cursor_enabled": false,\n'
        '  "trusted_directories": [],\n'
        '  "quarantine_directories": []\n'
        '}\n'
    )

with open(os.path.join(_TEST_APP_CONFIG_DIR, "mcp_servers.json"), "w", encoding="utf-8") as _f:
    _f.write('{"mcpServers": {}}\n')

os.environ["XDG_CONFIG_HOME"] = _TEST_CONFIG_DIR
os.environ["XDG_DATA_HOME"] = _TEST_DATA_DIR


def pytest_sessionfinish(session, exitstatus):
    """Limpa a sandbox temporária após a suíte inteira de testes rodar."""
    try:
        shutil.rmtree(_TEST_SANDBOX_DIR, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture(autouse=True, scope="session")
def guard_heavy_background_services():
    """Trava qualquer tentativa acidental de iniciar processos externos pesados ou navegadores."""
    import subprocess
    from unittest.mock import MagicMock
    orig_popen = subprocess.Popen

    def safe_popen(args, *a, **kw):
        cmd_str = str(args)
        blocked = (
            "xdg-open", "xdg-email", "gio", "firefox", "chrome",
            "chromium", "google-chrome", "spotify", "pw-play", "pw-record", "arecord", "aplay"
        )
        if any(b in cmd_str for b in blocked):
            mock = MagicMock()
            mock.pid = 99999
            mock.poll.return_value = 0
            mock.returncode = 0
            mock.communicate.return_value = (b"", b"")
            mock.stdin = MagicMock()
            mock.stdout = MagicMock()
            mock.stderr = MagicMock()
            return mock
        return orig_popen(args, *a, **kw)

    with patch("zorin_copilot.mcp.manager.MCPManager.start_all_enabled", return_value=None), \
         patch("zorin_copilot.core.rag.LocalDocumentRAG.start_background_indexing", return_value=None), \
         patch("subprocess.Popen", side_effect=safe_popen):
        yield


@pytest.fixture(autouse=True)
def destroy_leftover_toplevels():
    """Destrói janelas que cada teste deixa vivas e drena eventos pendentes no GMainContext."""
    yield

    gtk = sys.modules.get("gi.repository.Gtk")
    glib = sys.modules.get("gi.repository.GLib")
    if gtk is not None:
        for window in list(gtk.Window.list_toplevels()):
            try:
                window.destroy()
            except Exception:  # pragma: no cover - janela já meio desmontada
                pass
    if glib is not None:
        try:
            ctx = glib.MainContext.default()
            while ctx.iteration(False):
                pass
        except Exception:
            pass
    import gc
    gc.collect()
