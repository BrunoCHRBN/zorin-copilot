"""Testes unitários para o gerenciador de desenvolvimento com VS Code (VSCodeManager)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zorin_copilot.ai.agent_tools import ToolRegistry
from zorin_copilot.core.vscode import VSCodeManager
from zorin_copilot.shell.risk import RiskLevel, RiskPolicy


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Cria um workspace temporário simulado com arquivos de teste."""
    ws = tmp_path / "meu_projeto"
    ws.mkdir()
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("print('hello world')\n", encoding="utf-8")
    (ws / "README.md").write_text("# Meu Projeto\n", encoding="utf-8")
    (ws / ".env").write_text("SECRET_KEY=supersecret123\n", encoding="utf-8")
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("dummy git config", encoding="utf-8")
    (ws / ".venv").mkdir()
    (ws / ".venv" / "pyvenv.cfg").write_text("dummy venv", encoding="utf-8")
    return ws


# --------------------------------------------------------------------------- #
# Detecção de Binário e Instalação
# --------------------------------------------------------------------------- #


def test_vscode_is_installed_true(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/code" if cmd == "code" else None)
    assert VSCodeManager.is_installed() is True
    assert VSCodeManager.get_binary() == "/usr/bin/code"


def test_vscode_is_installed_codium_fallback(monkeypatch):
    def fake_which(cmd):
        if cmd == "codium":
            return "/usr/bin/codium"
        return None

    monkeypatch.setattr("shutil.which", fake_which)
    assert VSCodeManager.is_installed() is True
    assert VSCodeManager.get_binary() == "/usr/bin/codium"


def test_vscode_is_installed_false(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert VSCodeManager.is_installed() is False
    assert VSCodeManager.get_binary() is None


# --------------------------------------------------------------------------- #
# Detecção de Workspace Ativo
# --------------------------------------------------------------------------- #


def test_get_active_workspace_from_storage(tmp_path, monkeypatch):
    code_storage = tmp_path / ".config" / "Code" / "User" / "workspaceStorage"
    sub_dir = code_storage / "abcdef12345"
    sub_dir.mkdir(parents=True)
    target_project = tmp_path / "projetos" / "backend"
    target_project.mkdir(parents=True)

    workspace_json = sub_dir / "workspace.json"
    workspace_json.write_text(json.dumps({"folder": target_project.as_uri()}), encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("zorin_copilot.core.window_manager.WindowManager.get_active_or_last_window", lambda: None)

    active_ws = VSCodeManager.get_active_workspace()
    assert active_ws is not None
    assert active_ws.resolve() == target_project.resolve()


def test_get_active_workspace_matched_with_window_title(tmp_path, monkeypatch):
    code_storage = tmp_path / ".config" / "Code" / "User" / "workspaceStorage"
    p1 = tmp_path / "work" / "proj_alpha"
    p2 = tmp_path / "work" / "proj_beta"
    p1.mkdir(parents=True)
    p2.mkdir(parents=True)

    dir1 = code_storage / "dir1"
    dir1.mkdir(parents=True)
    (dir1 / "workspace.json").write_text(json.dumps({"folder": p1.as_uri()}), encoding="utf-8")

    dir2 = code_storage / "dir2"
    dir2.mkdir(parents=True)
    (dir2 / "workspace.json").write_text(json.dumps({"folder": p2.as_uri()}), encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    mock_win = MagicMock()
    mock_win.app = "code"
    mock_win.title = "main.py - proj_beta - Visual Studio Code"
    monkeypatch.setattr("zorin_copilot.core.window_manager.WindowManager.get_active_or_last_window", lambda: mock_win)

    active_ws = VSCodeManager.get_active_workspace()
    assert active_ws is not None
    assert active_ws.name == "proj_beta"


def test_get_active_workspace_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("zorin_copilot.core.window_manager.WindowManager.get_active_or_last_window", lambda: None)

    assert VSCodeManager.get_active_workspace() is None


# --------------------------------------------------------------------------- #
# Abertura de Workspace e Arquivos
# --------------------------------------------------------------------------- #


def test_open_workspace_success(temp_workspace, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "get_binary", lambda: "/usr/bin/code")
    mock_run = MagicMock()
    monkeypatch.setattr("subprocess.run", mock_run)
    monkeypatch.setattr("zorin_copilot.core.window_manager.WindowManager.focus_window", lambda query: True)

    res = VSCodeManager.open_workspace(temp_workspace, new_window=False)
    assert res["success"] is True
    assert "aberto no vs code" in res["message"].lower()
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0] == "/usr/bin/code"
    assert "-r" in cmd
    assert str(temp_workspace) in cmd


def test_open_workspace_new_window(temp_workspace, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "get_binary", lambda: "/usr/bin/code")
    mock_run = MagicMock()
    monkeypatch.setattr("subprocess.run", mock_run)

    res = VSCodeManager.open_workspace(temp_workspace, new_window=True, focus=False)
    assert res["success"] is True
    cmd = mock_run.call_args[0][0]
    assert "-n" in cmd


def test_open_workspace_not_installed(temp_workspace, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "get_binary", lambda: None)
    res = VSCodeManager.open_workspace(temp_workspace)
    assert res["success"] is False
    assert "não encontrado" in res["message"].lower()


def test_open_file_with_line_and_col(temp_workspace, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "get_binary", lambda: "/usr/bin/code")
    mock_run = MagicMock()
    monkeypatch.setattr("subprocess.run", mock_run)

    target_file = temp_workspace / "src" / "app.py"
    res = VSCodeManager.open_file(target_file, line=15, column=4, focus=False)
    assert res["success"] is True
    cmd = mock_run.call_args[0][0]
    assert "-g" in cmd
    assert f"{target_file}:15:4" in cmd


# --------------------------------------------------------------------------- #
# Criação de Projetos (Scaffolding)
# --------------------------------------------------------------------------- #


def test_create_project_fastapi(tmp_path, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "get_binary", lambda: "/usr/bin/code")
    monkeypatch.setattr(VSCodeManager, "open_workspace", lambda *args, **kwargs: {"success": True, "message": "ok"})
    monkeypatch.setattr("subprocess.run", MagicMock())

    res = VSCodeManager.create_project(
        project_name="api_pedidos",
        template="fastapi",
        base_dir=tmp_path,
        open_editor=False,
        setup_env=False,
    )

    assert res["success"] is True
    proj_dir = Path(res["project_dir"])
    assert proj_dir.exists()
    assert (proj_dir / "main.py").exists()
    assert (proj_dir / "requirements.txt").exists()
    assert (proj_dir / ".gitignore").exists()
    assert (proj_dir / ".vscode" / "settings.json").exists()
    assert (proj_dir / ".vscode" / "launch.json").exists()
    assert (proj_dir / ".env.example").exists()

    content = (proj_dir / "main.py").read_text(encoding="utf-8")
    assert "FastAPI" in content


def test_create_project_node(tmp_path, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "get_binary", lambda: "/usr/bin/code")

    res = VSCodeManager.create_project(
        project_name="microservice_node",
        template="node",
        base_dir=tmp_path,
        open_editor=False,
        setup_env=False,
    )

    assert res["success"] is True
    proj_dir = Path(res["project_dir"])
    assert (proj_dir / "package.json").exists()
    assert (proj_dir / "index.js").exists()
    assert (proj_dir / ".gitignore").exists()


# --------------------------------------------------------------------------- #
# Escrita e Backup Seguro de Arquivos de Código
# --------------------------------------------------------------------------- #


def test_write_code_file_new_file(temp_workspace, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "open_file", lambda *a, **kw: {"success": True, "message": "ok"})

    new_file = temp_workspace / "src" / "services" / "payment.py"
    code = "def process_payment():\n    return True\n"

    res = VSCodeManager.write_code_file(new_file, code, workspace_path=temp_workspace, open_in_editor=False)
    assert res["success"] is True
    assert new_file.exists()
    assert new_file.read_text(encoding="utf-8") == code
    assert res.get("backup_created") is False


def test_write_code_file_existing_creates_backup(temp_workspace, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "open_file", lambda *a, **kw: {"success": True, "message": "ok"})

    app_file = temp_workspace / "src" / "app.py"
    original_code = app_file.read_text(encoding="utf-8")
    new_code = "print('version 2.0')\n"

    res = VSCodeManager.write_code_file(app_file, new_code, workspace_path=temp_workspace, open_in_editor=False)
    assert res["success"] is True
    assert res.get("backup_created") is True
    backup_path = Path(res["backup_path"])
    assert backup_path.exists()
    assert backup_path.read_text(encoding="utf-8") == original_code
    assert app_file.read_text(encoding="utf-8") == new_code


# --------------------------------------------------------------------------- #
# Leitura e Proteção de Segredos
# --------------------------------------------------------------------------- #


def test_read_code_file_success(temp_workspace):
    app_file = temp_workspace / "src" / "app.py"
    res = VSCodeManager.read_code_file(app_file)
    assert res["success"] is True
    assert "hello world" in res["content"]


def test_read_code_file_secret_blocked(temp_workspace):
    env_file = temp_workspace / ".env"
    res = VSCodeManager.read_code_file(env_file)
    assert res["success"] is False
    assert "segurança" in res["message"].lower()
    assert "content" not in res


def test_read_code_file_pem_secret_blocked(temp_workspace):
    key_file = temp_workspace / "server.key"
    key_file.write_text("FAKE RSA PRIVATE KEY", encoding="utf-8")
    res = VSCodeManager.read_code_file(key_file)
    assert res["success"] is False
    assert "segurança" in res["message"].lower()


# --------------------------------------------------------------------------- #
# Inspeção de Estrutura do Workspace
# --------------------------------------------------------------------------- #


def test_read_workspace_structure(temp_workspace):
    res = VSCodeManager.read_workspace_structure(temp_workspace)
    assert res["success"] is True
    struct = res["structure"]
    assert "src/" in struct
    assert "app.py" in struct
    assert "README.md" in struct
    # .git e .venv devem ser ignorados por padrão
    assert ".git/" not in struct
    assert ".venv/" not in struct


# --------------------------------------------------------------------------- #
# Integração com RiskPolicy
# --------------------------------------------------------------------------- #


def test_risk_policy_vscode_workspace(temp_workspace):
    policy = RiskPolicy()

    # Leitura ou listagem de projeto: SAFE
    level, _ = policy.classify("vscode_workspace", {"action": "get_active_project"})
    assert level == RiskLevel.SAFE

    level, _ = policy.classify("vscode_workspace", {"action": "read_file"})
    assert level == RiskLevel.SAFE

    level, _ = policy.classify("vscode_workspace", {"action": "get_structure"})
    assert level == RiskLevel.SAFE

    # Abertura ou novo projeto: SAFE
    level, _ = policy.classify("vscode_workspace", {"action": "open_workspace"})
    assert level == RiskLevel.SAFE

    level, _ = policy.classify("vscode_workspace", {"action": "create_project"})
    assert level == RiskLevel.SAFE

    # Escrita de arquivo novo: SAFE
    new_file = temp_workspace / "brand_new.py"
    level, _ = policy.classify("vscode_workspace", {"action": "write_code", "file_path": str(new_file)})
    assert level == RiskLevel.SAFE

    # Sobrescrita de arquivo existente com mais de 50 bytes: CONFIRM
    big_file = temp_workspace / "src" / "app.py"
    big_file.write_text("x" * 100, encoding="utf-8")
    level, desc = policy.classify("vscode_workspace", {"action": "write_code", "file_path": str(big_file)})
    assert level == RiskLevel.CONFIRM
    assert "app.py" in desc

    # Modificação cirúrgica (patch_code) de arquivo existente com mais de 50 bytes: CONFIRM
    level, desc = policy.classify("vscode_workspace", {"action": "patch_code", "file_path": str(big_file)})
    assert level == RiskLevel.CONFIRM
    assert "app.py" in desc


# --------------------------------------------------------------------------- #
# Modificação Cirúrgica de Código (patch_code_file)
# --------------------------------------------------------------------------- #


def test_patch_code_file_exact_match(temp_workspace, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "open_file", lambda *a, **kw: {"success": True, "message": "ok"})

    app_file = temp_workspace / "src" / "app.py"
    app_file.write_text("def run():\n    print('hello world')\n    return 0\n", encoding="utf-8")

    res = VSCodeManager.patch_code_file(
        app_file,
        target_code="print('hello world')",
        replacement_code="print('zorin copilot active')",
        workspace_path=temp_workspace,
        open_in_editor=False,
    )

    assert res["success"] is True
    assert "print('zorin copilot active')" in app_file.read_text(encoding="utf-8")
    assert "print('hello world')" not in app_file.read_text(encoding="utf-8")
    assert res.get("backup_created") is True
    backup_path = Path(res["backup_path"])
    assert backup_path.exists()
    assert "print('hello world')" in backup_path.read_text(encoding="utf-8")


def test_patch_code_file_whitespace_tolerance(temp_workspace, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "open_file", lambda *a, **kw: {"success": True, "message": "ok"})

    app_file = temp_workspace / "src" / "calc.py"
    app_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    # Alvo com espaços em branco ligeiramente diferentes
    res = VSCodeManager.patch_code_file(
        app_file,
        target_code="def add( a, b ):\n    return a + b",
        replacement_code="def add(a: int, b: int) -> int:\n    return a + b",
        workspace_path=temp_workspace,
        open_in_editor=False,
    )

    assert res["success"] is True
    assert "def add(a: int, b: int) -> int:" in app_file.read_text(encoding="utf-8")


def test_patch_code_file_not_found(temp_workspace):
    app_file = temp_workspace / "src" / "app.py"
    res = VSCodeManager.patch_code_file(
        app_file,
        target_code="trecho_inexistente_xyz_123",
        replacement_code="novo_trecho",
        workspace_path=temp_workspace,
        open_in_editor=False,
    )

    assert res["success"] is False
    assert "não encontrado" in res["message"].lower()


def test_patch_code_file_secret_blocked(temp_workspace):
    env_file = temp_workspace / ".env"
    res = VSCodeManager.patch_code_file(
        env_file,
        target_code="SECRET_KEY=supersecret123",
        replacement_code="SECRET_KEY=newsecret",
    )

    assert res["success"] is False
    assert "segurança" in res["message"].lower()


# --------------------------------------------------------------------------- #
# Integração com ToolRegistry
# --------------------------------------------------------------------------- #


def test_tool_registry_vscode_workspace(temp_workspace, monkeypatch):
    monkeypatch.setattr(VSCodeManager, "get_active_workspace", lambda: temp_workspace)

    registry = ToolRegistry()
    res = registry.call("vscode_workspace", {"action": "get_active_project"})
    assert res["ok"] is True
    assert res["workspace_path"] == str(temp_workspace)
    assert res["project_name"] == temp_workspace.name

    # Teste de dry_run
    dry_registry = registry.for_dry_run()
    dry_res = dry_registry.call("vscode_workspace", {"action": "create_project", "project_name": "teste_dry"})
    assert dry_res["ok"] is True
    assert dry_res.get("dry_run") is True

    # Teste de dry_run para patch_code
    dry_patch = dry_registry.call(
        "vscode_workspace",
        {
            "action": "patch_code",
            "file_path": str(temp_workspace / "src" / "app.py"),
            "target_code": "print('hello world')",
            "replacement_code": "print('novo')",
        },
    )
    assert dry_patch["ok"] is True
    assert dry_patch.get("dry_run") is True

