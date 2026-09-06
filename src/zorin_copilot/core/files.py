# Decisão de design: gerenciamento de arquivos seguro — escrita com resolução de caminhos ~/ e organização de diretórios protegida por lixeira reversível (gio trash), sem exclusão destrutiva.

"""Gerenciador de arquivos, documentos e organização inteligente para o Zorin Copilot."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CATEGORY_EXTENSIONS: dict[str, set[str]] = {
    "Imagens": {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".tiff", ".raw"
    },
    "Documentos": {
        ".pdf", ".docx", ".doc", ".odt", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".md", ".csv", ".epub", ".rtf"
    },
    "Instaladores_e_Pacotes": {
        ".deb", ".rpm", ".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".zip", ".rar", ".7z", ".AppImage", ".iso", ".bin"
    },
    "Audio_e_Video": {
        ".mp3", ".wav", ".flac", ".aac", ".ogg", ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4a"
    },
    "Codigo_e_Scripts": {
        ".py", ".sh", ".js", ".ts", ".html", ".css", ".json", ".xml", ".yaml", ".yml", ".c", ".cpp", ".rs", ".go"
    },
}


class FileManager:
    """Serviço para criação de documentos, relatórios e organização inteligente de pastas."""

    @classmethod
    def resolve_target_path(cls, filename: str, directory: str | None = None) -> str:
        """Caminho final que `write_document` vai usar.

        Existe para que o executor possa fotografar o arquivo **antes** de
        sobrescrevê-lo — sem duplicar aqui a regra de higienização do nome.
        """
        clean_name = os.path.basename((filename or "").strip())
        if not clean_name:
            clean_name = "relatorio.md"
        if "." not in clean_name:
            clean_name = f"{clean_name}.md"

        if directory:
            target_dir = os.path.expanduser(directory.strip())
        else:
            target_dir = os.path.expanduser("~/Documentos/Relatorios")
        return os.path.join(target_dir, clean_name)

    @classmethod
    def write_document(
        cls,
        filename: str,
        content: str,
        directory: str | None = None,
        append: bool = False,
    ) -> tuple[bool, str, str]:
        """Cria ou atualiza um arquivo de texto ou Markdown no diretório especificado."""
        try:
            full_path = cls.resolve_target_path(filename, directory)
            target_dir = os.path.dirname(full_path)
            clean_name = os.path.basename(full_path)

            os.makedirs(target_dir, exist_ok=True)

            mode = "a" if append else "w"
            with open(full_path, mode, encoding="utf-8") as f:
                if append and os.path.exists(full_path) and os.path.getsize(full_path) > 0:
                    f.write("\n\n")
                f.write(content)

            size_bytes = os.path.getsize(full_path)
            verb = "anexado a" if append else "salvo em"
            msg = f"Arquivo '{clean_name}' {verb} '{target_dir}' com sucesso ({size_bytes} bytes)."
            return (True, msg, full_path)

        except Exception as exc:
            logger.error(f"Erro ao salvar arquivo '{filename}': {exc}")
            return (False, f"Erro ao salvar arquivo: {exc}", "")

    @classmethod
    def read_document(cls, file_path: str, max_chars: int = 8000) -> tuple[bool, str]:
        """Lê o conteúdo de um arquivo de texto com limite de caracteres seguro."""
        try:
            expanded = os.path.expanduser(file_path.strip())
            if not os.path.exists(expanded):
                return (False, f"Arquivo '{file_path}' não foi encontrado.")

            with open(expanded, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars)
            return (True, content)
        except Exception as exc:
            return (False, f"Erro ao ler arquivo: {exc}")

    @classmethod
    def organize_directory(
        cls,
        directory: str | None = None,
        dry_run: bool = False,
        moves: list[tuple[str, str]] | None = None,
    ) -> tuple[bool, str, dict[str, int]]:
        """Organiza arquivos avulsos de uma pasta em subpastas categorizadas por tipo.

        `moves` é opcional e funciona como saída: recebe os pares
        `(origem, destino_final)` conforme os arquivos são movidos. É o que
        permite desfazer — o resumo em `stats` só conta categorias e não diz
        quem foi para onde. Ficou como parâmetro (e não 4º retorno) para não
        quebrar quem já desempacota três valores.
        """
        target_dir = os.path.expanduser((directory or "~/Downloads").strip())
        if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
            return (False, f"Diretório '{target_dir}' não existe ou não é uma pasta.", {})

        stats: dict[str, int] = {}
        items_to_move: list[tuple[str, str, str]] = []  # (origem, destino_dir, nome_arquivo)

        known_subdirs = set(CATEGORY_EXTENSIONS.keys()) | {"Outros"}

        try:
            for entry in os.scandir(target_dir):
                if entry.is_dir() or entry.name.startswith("."):
                    continue

                ext = Path(entry.name).suffix.lower()
                # Verifica extensão dupla como .tar.gz
                if entry.name.endswith(".tar.gz"):
                    ext = ".tar.gz"
                elif entry.name.endswith(".tar.xz"):
                    ext = ".tar.xz"
                elif entry.name.endswith(".tar.bz2"):
                    ext = ".tar.bz2"

                category = "Outros"
                for cat, exts in CATEGORY_EXTENSIONS.items():
                    if ext in exts:
                        category = cat
                        break

                dest_folder = os.path.join(target_dir, category)
                items_to_move.append((entry.path, dest_folder, entry.name))
                stats[category] = stats.get(category, 0) + 1

            if not items_to_move:
                return (True, f"A pasta '{target_dir}' já está limpa e organizada.", {})

            if dry_run:
                summary_lines = [f"Simulação de organização para '{target_dir}':"]
                for cat, count in stats.items():
                    summary_lines.append(f"• {cat}: {count} arquivo(s)")
                return (True, "\n".join(summary_lines), stats)

            # Execução real com prevenção de colisões
            moved_count = 0
            for src_path, dest_folder, fname in items_to_move:
                os.makedirs(dest_folder, exist_ok=True)
                dest_file = os.path.join(dest_folder, fname)

                # Previne sobrescrita caso já exista arquivo com o mesmo nome no destino
                if os.path.exists(dest_file):
                    stem = Path(fname).stem
                    suffix = Path(fname).suffix
                    counter = 1
                    while os.path.exists(dest_file):
                        dest_file = os.path.join(dest_folder, f"{stem}_{counter}{suffix}")
                        counter += 1

                shutil.move(src_path, dest_file)
                moved_count += 1
                if moves is not None:
                    moves.append((src_path, dest_file))

            summary_lines = [f"Organização concluída em '{target_dir}' ({moved_count} arquivos movidos):"]
            for cat, count in sorted(stats.items()):
                summary_lines.append(f"• {cat}: {count} arquivo(s)")

            return (True, "\n".join(summary_lines), stats)

        except Exception as exc:
            logger.error(f"Erro ao organizar pasta '{target_dir}': {exc}")
            return (False, f"Erro ao organizar pasta: {exc}", stats)

    @classmethod
    def trash_file(cls, file_path: str) -> tuple[bool, str]:
        """Envia um arquivo para a Lixeira de forma reversível via gio trash."""
        expanded = os.path.expanduser(file_path.strip())
        if not os.path.exists(expanded):
            return (False, f"Arquivo '{file_path}' não encontrado.")

        gio_bin = shutil.which("gio")
        if gio_bin:
            try:
                res = subprocess.run([gio_bin, "trash", expanded], capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    return (True, f"Arquivo '{os.path.basename(expanded)}' movido para a Lixeira com sucesso.")
                return (False, f"Falha ao mover para a Lixeira: {res.stderr.strip()}")
            except Exception as exc:
                return (False, f"Erro ao executar gio trash: {exc}")

        return (False, "Comando 'gio' não disponível para descarte seguro.")
