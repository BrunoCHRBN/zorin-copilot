"""Configuração da suíte sob pytest.

O isolamento de ambiente em si mora em `_sandbox.py`, não aqui — porque o
`unittest` (o runner documentado no README) nunca importa `conftest.py`. Ver o
docstring de `_sandbox.py` para o histórico do problema.

Este arquivo só faz a ponte com o pytest: ativa a sandbox e registra as fixtures
de limpeza entre testes.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

try:  # importado como pacote (`tests.conftest`) quando há `tests/__init__.py`
    from tests import _sandbox
except ImportError:  # importado solto, conforme o rootdir do pytest
    import _sandbox  # type: ignore[no-redef]

_sandbox.activate()


def pytest_sessionfinish(session, exitstatus):
    """Limpa a sandbox temporária após a suíte inteira rodar."""
    _sandbox.cleanup()


@pytest.fixture(autouse=True, scope="session")
def guard_heavy_background_services():
    """Trava qualquer tentativa acidental de iniciar processos externos pesados ou navegadores."""
    orig_popen = subprocess.Popen

    def safe_popen(args, *a, **kw):
        cmd_str = str(args)
        blocked = (
            "xdg-open", "xdg-email", "gio", "firefox", "chrome",
            "chromium", "google-chrome", "spotify", "pw-play", "pw-record", "arecord", "aplay",
            # Lançadores de servidores MCP. O fixture abaixo blinda
            # `start_all_enabled`, mas `set_server_enabled`/`add_server` chamam
            # `start_server` direto e escapam dele: sem isto,
            # `test_mcp_manager_enable_disable_server` sobe um
            # `uvx mcp-server-git` de verdade. Com o cache do `uv` frio (o
            # sandbox redireciona `XDG_CACHE_HOME`), o `uvx` baixa o pacote do
            # PyPI e o servidor fica vivo até o fim da suíte.
            "uvx", "npx",
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
    _sandbox.destroy_leftover_toplevels()
