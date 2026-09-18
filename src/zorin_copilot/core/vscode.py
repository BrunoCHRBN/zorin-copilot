# Decisão de design: Integração de desenvolvimento com o Visual Studio Code (VSCodeManager).
# Fornece inspeção do projeto/workspace ativo, abertura de pastas e arquivos diretamente em abas,
# scaffold de novos projetos estruturados (.gitignore, .vscode/settings.json, virtualenv),
# e escrita segura de arquivos de código com snapshot/backup automático e proteção contra vazamento de segredos (.env).

"""Gerenciador e orquestrador de desenvolvimento com Visual Studio Code."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# Diretórios e padrões ignorados ao escanear a estrutura de um projeto
IGNORED_PROJECT_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cargo",
    "target",
    "dist",
    "build",
    ".idea",
    ".cache",
    ".next",
    ".nuxt",
}

# Arquivos sensíveis que JAMAIS devem ser lidos ou expostos ao modelo
PROTECTED_SECRET_PATTERNS = {
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r"^id_rsa(\..+)?$", re.IGNORECASE),
    re.compile(r"^id_ed25519(\..+)?$", re.IGNORECASE),
    re.compile(r".*\.(pem|key|pfx|p12|kdbx)$", re.IGNORECASE),
    re.compile(r".*credentials.*\.json$", re.IGNORECASE),
}


class VSCodeManager:
    """Controlador de integração do Zorin Copilot com o Visual Studio Code."""

    @classmethod
    def get_binary(cls) -> str | None:
        """Localiza o executável do VS Code no sistema operacional."""
        for candidate in ("code", "codium", "code-oss"):
            path = shutil.which(candidate)
            if path:
                return path
        return None

    @classmethod
    def is_installed(cls) -> bool:
        """Verifica se o VS Code está instalado."""
        return cls.get_binary() is not None

    @classmethod
    def get_active_workspace(cls) -> Path | None:
        """Detecta o diretório do projeto atualmente aberto no VS Code.

        Estratégia híbrida:
        1. Inspeciona o título da janela ativa no compositor (ex: 'main.py - meu-projeto - Visual Studio Code').
        2. Consulta os registros recentes em ~/.config/Code/User/workspaceStorage/.
        """
        # 1. Tenta obter o nome da pasta a partir da janela em foco no WindowManager
        target_folder_name: str | None = None
        try:
            from .window_manager import WindowManager

            win = WindowManager.get_active_or_last_window()
            if win and ("code" in win.app.lower() or "visual studio code" in win.title.lower()):
                title = win.title.replace(" - Visual Studio Code", "").replace(" — Visual Studio Code", "")
                parts = [p.strip() for p in title.split(" - ") if p.strip()]
                if not parts:
                    parts = [p.strip() for p in title.split(" — ") if p.strip()]
                if len(parts) >= 2:
                    # Formato: arquivo - pasta
                    target_folder_name = parts[-1]
                elif len(parts) == 1 and parts[0] != "Visual Studio Code":
                    target_folder_name = parts[0]
        except Exception as exc:
            logger.debug("Falha ao inspecionar janela do VS Code: %s", exc)

        # 2. Varre o workspaceStorage do VS Code / VSCodium ordenado pelo mais recente
        config_dirs = [
            Path.home() / ".config" / "Code" / "User" / "workspaceStorage",
            Path.home() / ".config" / "VSCodium" / "User" / "workspaceStorage",
            Path.home() / ".config" / "Code - OSS" / "User" / "workspaceStorage",
        ]

        workspace_entries: list[tuple[float, Path]] = []
        for ws_dir in config_dirs:
            if not ws_dir.exists():
                continue
            for json_file in ws_dir.glob("*/workspace.json"):
                try:
                    mtime = json_file.stat().st_mtime
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    folder_uri = data.get("folder")
                    if folder_uri and folder_uri.startswith("file://"):
                        parsed_path = Path(unquote(urlparse(folder_uri).path))
                        if parsed_path.exists() and parsed_path.is_dir():
                            workspace_entries.append((mtime, parsed_path))
                except Exception:
                    continue

        if not workspace_entries:
            # Se não encontrou no storage, verifica se a pasta atual existe no sistema
            if target_folder_name:
                common_paths = [
                    Path.home() / "Projetos" / target_folder_name,
                    Path.home() / target_folder_name,
                    Path.cwd(),
                ]
                for cp in common_paths:
                    if cp.exists() and cp.is_dir() and cp.name.lower() == target_folder_name.lower():
                        return cp
            return None

        # Ordena: workspace modificado mais recentemente primeiro
        workspace_entries.sort(key=lambda item: item[0], reverse=True)

        # Se identificamos o nome da pasta da janela ativa, busca correspondência exata
        if target_folder_name:
            for _mtime, path in workspace_entries:
                if path.name.lower() == target_folder_name.lower():
                    return path

        # Caso contrário, retorna o workspace mais recente
        return workspace_entries[0][1]

    @classmethod
    def open_workspace(
        cls,
        folder_path: str | Path,
        new_window: bool = False,
        focus: bool = True,
    ) -> dict[str, Any]:
        """Abre uma pasta como workspace no VS Code."""
        binary = cls.get_binary()
        if not binary:
            return {"success": False, "message": "Visual Studio Code não encontrado no sistema."}

        target = Path(folder_path).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)

        cmd = [binary]
        if new_window:
            cmd.append("-n")
        else:
            cmd.append("-r")
        cmd.append(str(target))

        try:
            subprocess.run(cmd, check=False, timeout=5.0)
            if focus:
                try:
                    from .window_manager import WindowManager
                    WindowManager.focus_window("code")
                except Exception:
                    pass

            return {
                "success": True,
                "workspace_path": str(target),
                "message": f"Workspace '{target.name}' aberto no VS Code.",
            }
        except Exception as exc:
            logger.error("Erro ao abrir workspace no VS Code: %s", exc)
            return {"success": False, "message": f"Falha ao abrir VS Code: {exc}"}

    @classmethod
    def open_file(
        cls,
        file_path: str | Path,
        line: int = 1,
        column: int = 1,
        focus: bool = True,
    ) -> dict[str, Any]:
        """Abre um arquivo específico em uma aba do editor posicionado na linha indicada."""
        binary = cls.get_binary()
        if not binary:
            return {"success": False, "message": "Visual Studio Code não encontrado no sistema."}

        target = Path(file_path).expanduser().resolve()
        if not target.exists():
            return {"success": False, "message": f"Arquivo '{target}' não existe."}

        loc_arg = f"{target}:{max(1, line)}:{max(1, column)}"
        cmd = [binary, "-r", "-g", loc_arg]

        try:
            subprocess.run(cmd, check=False, timeout=5.0)
            if focus:
                try:
                    from .window_manager import WindowManager
                    WindowManager.focus_window("code")
                except Exception:
                    pass

            return {
                "success": True,
                "file_path": str(target),
                "line": line,
                "column": column,
                "message": f"Arquivo '{target.name}' aberto no VS Code (linha {line}).",
            }
        except Exception as exc:
            logger.error("Erro ao abrir arquivo no VS Code: %s", exc)
            return {"success": False, "message": f"Falha ao abrir arquivo no VS Code: {exc}"}

    @classmethod
    def create_project(
        cls,
        project_name: str,
        template: str = "python",
        base_dir: str | Path | None = None,
        open_editor: bool = True,
        setup_env: bool = True,
    ) -> dict[str, Any]:
        """Cria uma pasta estruturada de projeto completa com arquivos base, .gitignore e .vscode."""
        clean_name = re.sub(r"[^\w\-.]", "_", project_name.strip())
        if not clean_name:
            clean_name = "novo_projeto"

        if base_dir:
            parent = Path(base_dir).expanduser().resolve()
        else:
            parent = Path.home() / "Projetos"
        parent.mkdir(parents=True, exist_ok=True)

        project_dir = parent / clean_name
        project_dir.mkdir(parents=True, exist_ok=True)

        created_files: list[str] = []
        tmpl = template.lower().strip()

        # 1. Configuração do .vscode/settings.json
        vscode_dir = project_dir / ".vscode"
        vscode_dir.mkdir(parents=True, exist_ok=True)

        settings_json = {
            "editor.formatOnSave": True,
            "editor.tabSize": 4 if "python" in tmpl else 2,
            "files.autoSave": "afterDelay",
        }

        if tmpl in ("python", "fastapi", "flask"):
            settings_json["python.defaultInterpreterPath"] = "${workspaceFolder}/.venv/bin/python"
            settings_json["python.analysis.typeCheckingMode"] = "basic"

        (vscode_dir / "settings.json").write_text(
            json.dumps(settings_json, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        created_files.append(".vscode/settings.json")

        # 2. .gitignore apropriado
        gitignore_content = cls._get_gitignore_template(tmpl)
        (project_dir / ".gitignore").write_text(gitignore_content, encoding="utf-8")
        created_files.append(".gitignore")

        # 3. .env.example seguro
        env_example = (
            "# Exemplo de variáveis de ambiente (NUNCA versione arquivos .env com senhas reais)\n"
            "APP_ENV=development\n"
            "APP_PORT=8000\n"
            "DEBUG=True\n"
        )
        (project_dir / ".env.example").write_text(env_example, encoding="utf-8")
        created_files.append(".env.example")

        # 4. Arquivos específicos do template
        if tmpl in ("python", "fastapi"):
            main_code = (
                "\"\"\"Ponto de entrada da aplicação FastAPI.\"\"\"\n\n"
                "from fastapi import FastAPI\n\n"
                "app = FastAPI(title=\"" + clean_name.replace("_", " ").title() + "\", version=\"0.1.0\")\n\n\n"
                "@app.get(\"/\")\n"
                "async def root():\n"
                "    return {\"message\": \"Servidor online!\", \"projeto\": \"" + clean_name + "\"}\n\n\n"
                "@app.get(\"/health\")\n"
                "async def healthcheck():\n"
                "    return {\"status\": \"healthy\"}\n\n"
                "if __name__ == \"__main__\":\n"
                "    import uvicorn\n"
                "    uvicorn.run(\"main:app\", host=\"127.0.0.1\", port=8000, reload=True)\n"
            )
            (project_dir / "main.py").write_text(main_code, encoding="utf-8")
            created_files.append("main.py")

            reqs = "fastapi>=0.115.0\nuvicorn[standard]>=0.30.0\npydantic>=2.8.0\npytest>=8.0.0\n"
            (project_dir / "requirements.txt").write_text(reqs, encoding="utf-8")
            created_files.append("requirements.txt")

            # Debug F5
            launch_json = {
                "version": "0.2.0",
                "configurations": [
                    {
                        "name": "FastAPI: Iniciar Servidor",
                        "type": "debugpy",
                        "request": "launch",
                        "module": "uvicorn",
                        "args": ["main:app", "--reload", "--port", "8000"],
                        "jinja": True,
                    }
                ],
            }
            (vscode_dir / "launch.json").write_text(
                json.dumps(launch_json, indent=2) + "\n", encoding="utf-8"
            )
            created_files.append(".vscode/launch.json")

            # Criação do virtualenv em segundo plano se solicitado
            if setup_env:
                cls._setup_python_virtualenv(project_dir)

        elif tmpl == "flask":
            app_code = (
                "\"\"\"Ponto de entrada da aplicação Flask.\"\"\"\n\n"
                "from flask import Flask, jsonify\n\n"
                "app = Flask(__name__)\n\n\n"
                "@app.route(\"/\")\n"
                "def index():\n"
                "    return jsonify({\"message\": \"Servidor Flask online!\", \"projeto\": \"" + clean_name + "\"})\n\n\n"
                "if __name__ == \"__main__\":\n"
                "    app.run(host=\"127.0.0.1\", port=5000, debug=True)\n"
            )
            (project_dir / "app.py").write_text(app_code, encoding="utf-8")
            created_files.append("app.py")

            reqs = "flask>=3.0.0\npytest>=8.0.0\n"
            (project_dir / "requirements.txt").write_text(reqs, encoding="utf-8")
            created_files.append("requirements.txt")

            if setup_env:
                cls._setup_python_virtualenv(project_dir)

        elif tmpl in ("node", "typescript", "express"):
            pkg_json = {
                "name": clean_name.lower(),
                "version": "1.0.0",
                "description": f"Projeto {clean_name}",
                "main": "index.js",
                "scripts": {
                    "start": "node index.js",
                    "dev": "nodemon index.js",
                },
                "dependencies": {
                    "express": "^4.19.2",
                },
            }
            (project_dir / "package.json").write_text(
                json.dumps(pkg_json, indent=2) + "\n", encoding="utf-8"
            )
            created_files.append("package.json")

            index_code = (
                "const express = require('express');\n"
                "const app = express();\n"
                "const PORT = process.env.PORT || 3000;\n\n"
                "app.use(express.json());\n\n"
                "app.get('/', (req, res) => {\n"
                f"  res.json({{ message: 'Servidor Express ativo!', projeto: '{clean_name}' }});\n"
                "});\n\n"
                "app.listen(PORT, () => {\n"
                "  console.log(`Servidor rodando em http://localhost:${PORT}`);\n"
                "});\n"
            )
            (project_dir / "index.js").write_text(index_code, encoding="utf-8")
            created_files.append("index.js")

        elif tmpl in ("react", "web", "html"):
            index_html = (
                "<!DOCTYPE html>\n"
                "<html lang=\"pt-BR\">\n"
                "<head>\n"
                "  <meta charset=\"UTF-8\" />\n"
                "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n"
                f"  <title>{clean_name.replace('_', ' ').title()}</title>\n"
                "  <script src=\"https://cdn.tailwindcss.com\"></script>\n"
                "</head>\n"
                "<body class=\"bg-slate-900 text-slate-100 flex items-center justify-center min-h-screen\">\n"
                "  <div class=\"max-w-md p-8 bg-slate-800 rounded-2xl shadow-xl border border-slate-700 text-center\">\n"
                f"    <h1 class=\"text-2xl font-bold text-cyan-400 mb-2\">{clean_name}</h1>\n"
                "    <p class=\"text-slate-400 text-sm mb-4\">Projeto inicializado com sucesso pelo Zorin Copilot.</p>\n"
                "    <button id=\"btn\" class=\"px-4 py-2 bg-cyan-500 hover:bg-cyan-400 text-slate-900 font-semibold rounded-lg transition-colors\">\n"
                "      Clique aqui\n"
                "    </button>\n"
                "  </div>\n"
                "  <script src=\"app.js\"></script>\n"
                "</body>\n"
                "</html>\n"
            )
            (project_dir / "index.html").write_text(index_html, encoding="utf-8")
            created_files.append("index.html")

            app_js = (
                "document.getElementById('btn').addEventListener('click', () => {\n"
                "  alert('Zorin Copilot funcionando perfeitamente no VS Code!');\n"
                "});\n"
            )
            (project_dir / "app.js").write_text(app_js, encoding="utf-8")
            created_files.append("app.js")

        # README.md
        readme = (
            f"# {clean_name.replace('_', ' ').title()}\n\n"
            f"Projeto criado automaticamente pelo **Zorin Copilot** utilizando o template `{tmpl}`.\n\n"
            "## Estrutura do Projeto\n"
            + "\n".join(f"- `{f}`" for f in created_files)
            + "\n\n## Como Executar\n"
            + (
                "```bash\nsource .venv/bin/activate\npip install -r requirements.txt\npython main.py\n```\n"
                if tmpl in ("python", "fastapi")
                else "```bash\nnpm install\nnpm start\n```\n"
                if tmpl in ("node", "typescript")
                else "Abra o arquivo `index.html` em seu navegador.\n"
            )
        )
        (project_dir / "README.md").write_text(readme, encoding="utf-8")
        created_files.append("README.md")

        # 5. Abre no VS Code
        if open_editor:
            cls.open_workspace(project_dir, focus=True)

        return {
            "success": True,
            "project_name": clean_name,
            "path": str(project_dir),
            "project_dir": str(project_dir),
            "template": tmpl,
            "created_files": created_files,
            "message": f"Projeto '{clean_name}' criado com sucesso em '{project_dir}' com {len(created_files)} arquivos.",
        }

    @classmethod
    def _setup_python_virtualenv(cls, project_dir: Path) -> None:
        """Cria o ambiente virtual .venv se uv ou python3 estiver disponível."""
        venv_path = project_dir / ".venv"
        if venv_path.exists():
            return

        def _worker():
            try:
                if shutil.which("uv"):
                    subprocess.run(
                        ["uv", "venv", str(venv_path)],
                        cwd=str(project_dir),
                        capture_output=True,
                        timeout=15.0,
                        check=False,
                    )
                else:
                    subprocess.run(
                        ["python3", "-m", "venv", str(venv_path)],
                        cwd=str(project_dir),
                        capture_output=True,
                        timeout=30.0,
                        check=False,
                    )
                logger.info("Ambiente virtual .venv criado em %s", venv_path)
            except Exception as exc:
                logger.debug("Falha ao criar .venv: %s", exc)

        import threading
        th = threading.Thread(target=_worker, daemon=True, name="setup-venv")
        th.start()

    @classmethod
    def _get_gitignore_template(cls, template: str) -> str:
        """Gera o conteúdo padrão de .gitignore para o template."""
        common = ".env\n.env.local\n*.bak\n.DS_Store\nThumbs.db\n"
        if "python" in template:
            return (
                common
                + ".venv/\nvenv/\nenv/\n__pycache__/\n*.py[cod]\n*$py.class\n"
                + ".pytest_cache/\n.ruff_cache/\n.mypy_cache/\ndist/\nbuild/\n*.egg-info/\n"
            )
        elif "node" in template or "react" in template or "typescript" in template:
            return common + "node_modules/\ndist/\nbuild/\n.next/\n.nuxt/\nnpm-debug.log*\n"
        return common

    @classmethod
    def is_secret_file(cls, path_or_name: str | Path) -> bool:
        """Verifica se o arquivo é protegido contra leitura ou exposição de segredos."""
        filename = Path(path_or_name).name
        for pat in PROTECTED_SECRET_PATTERNS:
            if pat.match(filename):
                return True
        return False

    @classmethod
    def write_code_file(
        cls,
        file_path: str | Path,
        content: str,
        workspace_path: Path | None = None,
        open_in_editor: bool = True,
        line: int = 1,
        backup: bool = True,
    ) -> dict[str, Any]:
        """Escreve código em um arquivo do projeto com proteção de backup e abertura no editor."""
        raw_path = Path(file_path).expanduser()
        if not raw_path.is_absolute():
            base = workspace_path or cls.get_active_workspace() or Path.cwd()
            target = (base / raw_path).resolve()
        else:
            target = raw_path.resolve()

        # Segurança: impede escrita fora da pasta de usuário ou tmp
        home = Path.home().resolve()
        is_safe = False
        try:
            target.relative_to(home)
            is_safe = True
        except ValueError:
            import tempfile
            for allowed in (Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()):
                try:
                    target.relative_to(allowed)
                    is_safe = True
                    break
                except ValueError:
                    pass

        if not is_safe:
            return {
                "success": False,
                "message": f"Acesso negado: o caminho '{target}' está fora do diretório permitido.",
            }

        # Backup prévio se o arquivo já existir
        backup_path: Path | None = None
        if target.exists() and backup:
            try:
                backup_path = target.with_suffix(target.suffix + ".bak")
                shutil.copy2(target, backup_path)
                logger.debug("Backup criado em %s antes da escrita.", backup_path)
            except Exception as exc:
                logger.warning("Não foi possível criar backup de %s: %s", target, exc)

        # Garante diretório pai
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            target.write_text(content, encoding="utf-8")
            line_count = len(content.splitlines())

            if open_in_editor:
                cls.open_file(target, line=line, focus=True)

            return {
                "success": True,
                "file_path": str(target),
                "lines": line_count,
                "backup_created": bool(backup_path),
                "backup_path": str(backup_path) if backup_path else None,
                "message": f"Arquivo '{target.name}' salvo com sucesso ({line_count} linhas).",
            }
        except Exception as exc:
            logger.error("Erro ao escrever arquivo de código '%s': %s", target, exc)
            return {"success": False, "message": f"Erro ao escrever arquivo: {exc}"}

    @classmethod
    def patch_code_file(
        cls,
        file_path: str | Path,
        target_code: str,
        replacement_code: str,
        workspace_path: Path | None = None,
        open_in_editor: bool = True,
        backup: bool = True,
    ) -> dict[str, Any]:
        """Substitui cirurgicamente um trecho de código específico em um arquivo existente."""
        raw_path = Path(file_path).expanduser()
        if not raw_path.is_absolute():
            base = workspace_path or cls.get_active_workspace() or Path.cwd()
            target = (base / raw_path).resolve()
        else:
            target = raw_path.resolve()

        if not target.exists():
            return {"success": False, "message": f"Arquivo '{target}' não existe."}

        if cls.is_secret_file(target):
            return {
                "success": False,
                "message": f"Por segurança, a modificação de arquivos de segredos ou credenciais ('{target.name}') é bloqueada.",
            }

        # Segurança: impede escrita fora da pasta de usuário ou tmp
        home = Path.home().resolve()
        is_safe = False
        try:
            target.relative_to(home)
            is_safe = True
        except ValueError:
            import tempfile
            for allowed in (Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()):
                try:
                    target.relative_to(allowed)
                    is_safe = True
                    break
                except ValueError:
                    pass

        if not is_safe:
            return {
                "success": False,
                "message": f"Acesso negado: o caminho '{target}' está fora do diretório permitido.",
            }

        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            return {"success": False, "message": f"Falha ao ler arquivo: {exc}"}

        # Localização do bloco de destino: exato ou normalizado por linhas
        patch_pos = content.find(target_code)
        target_len = len(target_code)

        if patch_pos == -1:
            t_norm = target_code.replace("\r\n", "\n").strip()
            c_norm = content.replace("\r\n", "\n")
            patch_pos = c_norm.find(t_norm)
            if patch_pos != -1:
                target_len = len(t_norm)
                content = c_norm
            else:
                # Normalização tolerante a variações de espaçamento linha a linha
                def _norm(line: str) -> str:
                    s = re.sub(r"\s+", " ", line.strip())
                    return re.sub(r"\s*([(),:={}\[\]+\-*/])\s*", r"\1", s)

                t_lines = [l for l in target_code.splitlines() if l.strip()]
                c_lines = content.splitlines(keepends=True)
                if t_lines and len(c_lines) >= len(t_lines):
                    norm_t = [_norm(l) for l in t_lines]
                    matched_idx = -1
                    for i in range(len(c_lines) - len(t_lines) + 1):
                        slice_norm = [_norm(c_lines[i + j]) for j in range(len(t_lines))]
                        if slice_norm == norm_t:
                            matched_idx = i
                            break
                    if matched_idx != -1:
                        start_char = sum(len(c_lines[k]) for k in range(matched_idx))
                        end_char = sum(len(c_lines[k]) for k in range(matched_idx + len(t_lines)))
                        matched_chunk = "".join(c_lines[matched_idx : matched_idx + len(t_lines)])
                        if not target_code.endswith(("\n", "\r\n")) and matched_chunk.endswith(("\n", "\r\n")):
                            end_char -= 1 if matched_chunk.endswith("\n") and not matched_chunk.endswith("\r\n") else 2
                        patch_pos = start_char
                        target_len = end_char - start_char

        if patch_pos == -1:
            return {
                "success": False,
                "message": f"Trecho de código a substituir não encontrado no arquivo '{target.name}'.",
            }

        # Backup prévio antes da modificação
        backup_path: Path | None = None
        if backup:
            try:
                backup_path = target.with_suffix(target.suffix + ".bak")
                shutil.copy2(target, backup_path)
            except Exception as exc:
                logger.warning("Não foi possível criar backup de %s: %s", target, exc)

        # Realiza a substituição pontual
        new_content = content[:patch_pos] + replacement_code + content[patch_pos + target_len:]
        target.write_text(new_content, encoding="utf-8")

        # Calcula linha do início da substituição
        line_num = content[:patch_pos].count("\n") + 1

        if open_in_editor:
            cls.open_file(target, line=line_num, focus=True)

        return {
            "success": True,
            "file_path": str(target),
            "line": line_num,
            "backup_created": bool(backup_path),
            "backup_path": str(backup_path) if backup_path else None,
            "message": f"Trecho de código substituído com sucesso na linha {line_num} de '{target.name}'.",
        }

    @classmethod
    def read_code_file(
        cls,
        file_path: str | Path,
        workspace_path: Path | None = None,
        max_lines: int = 400,
    ) -> dict[str, Any]:
        """Lê o conteúdo de um arquivo de código com proteção ativa contra vazamento de segredos."""
        raw_path = Path(file_path).expanduser()
        if not raw_path.is_absolute():
            base = workspace_path or cls.get_active_workspace() or Path.cwd()
            target = (base / raw_path).resolve()
        else:
            target = raw_path.resolve()

        if not target.exists():
            return {"success": False, "message": f"Arquivo '{target}' não existe."}

        # Bloqueio de arquivos sensíveis com segredos
        if cls.is_secret_file(target):
            return {
                "success": False,
                "blocked_secret": True,
                "message": (
                    f"O arquivo '{target.name}' contém credenciais ou variáveis de ambiente confidenciais "
                    "e foi protegido contra leitura pela política de segurança do Zorin Copilot."
                ),
            }

        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total_lines = len(lines)
            truncated = False
            if total_lines > max_lines:
                lines = lines[:max_lines]
                truncated = True

            return {
                "success": True,
                "file_path": str(target),
                "total_lines": total_lines,
                "truncated": truncated,
                "content": "".join(lines),
                "message": f"Arquivo '{target.name}' lido com sucesso ({len(lines)} linhas).",
            }
        except Exception as exc:
            logger.error("Erro ao ler arquivo '%s': %s", target, exc)
            return {"success": False, "message": f"Erro ao ler arquivo: {exc}"}

    @classmethod
    def read_workspace_structure(
        cls,
        workspace_path: Path | None = None,
        max_depth: int = 3,
        max_files: int = 100,
    ) -> dict[str, Any]:
        """Gera uma visão em árvore resumida do projeto aberto sem sobrecarregar o contexto do modelo."""
        base = workspace_path or cls.get_active_workspace()
        if not base or not base.exists():
            return {"success": False, "message": "Nenhum workspace ativo do VS Code localizado."}

        entries: list[str] = []
        file_count = 0

        def _walk(current: Path, depth: int, prefix: str = ""):
            nonlocal file_count
            if depth > max_depth or file_count >= max_files:
                return

            try:
                items = sorted(
                    list(current.iterdir()),
                    key=lambda x: (not x.is_dir(), x.name.lower()),
                )
            except (PermissionError, OSError):
                return

            for item in items:
                if item.name in IGNORED_PROJECT_DIRS or item.name.startswith(".git"):
                    continue
                if file_count >= max_files:
                    entries.append(f"{prefix}... (limite de arquivos atingido)")
                    return

                if item.is_dir():
                    entries.append(f"{prefix}📁 {item.name}/")
                    _walk(item, depth + 1, prefix + "  ")
                else:
                    file_count += 1
                    entries.append(f"{prefix}📄 {item.name}")

        _walk(base, depth=1)

        tree_str = "\n".join(entries) if entries else "(workspace vazio)"
        return {
            "success": True,
            "workspace_path": str(base),
            "project_name": base.name,
            "total_files": file_count,
            "tree": tree_str,
            "structure": tree_str,
            "message": f"Estrutura do projeto '{base.name}' mapeada com {file_count} arquivos.",
        }
