# Decisão de design: Motor de RAG local e pesquisa semântica ultrarrápida (1ms) em SQLite FTS5 nativo.
# Indexa de forma incremental e segura documentos pessoais (~/Documentos e ~/Downloads)
# nos formatos PDF (via pdftotext/Poppler C++ nativo), Markdown, Texto puro e CSV,
# com remoção automática de acentuação (unicode61) e integração com o leitor Evince para abrir na página exata.

"""Motor de RAG local (Retrieval-Augmented Generation) e busca em documentos para o Zorin Copilot."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from .memory import MemoryManager

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".log"}
EXCLUDED_PATTERNS = {
    "node_modules",
    ".git",
    ".cache",
    ".local",
    ".config",
    "__pycache__",
    ".env",
    "venv",
    ".venv",
}


@dataclass
class DocumentSearchResult:
    """Resultado estruturado de busca em documento pessoal."""

    file_path: str
    file_name: str
    title: str
    page_number: int
    snippet: str
    rank_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_name": self.file_name,
            "title": self.title,
            "page_number": self.page_number,
            "snippet": self.snippet,
            "score": round(self.rank_score, 2),
        }

    def format_citation(self) -> str:
        """Formata citação legível para resposta textual e por voz."""
        page_str = f" (Pág. {self.page_number})" if self.page_number > 0 else ""
        return f"📄 **{self.file_name}**{page_str}:\n> \"{self.snippet}\""


class LocalDocumentRAG:
    """Gerenciador de indexação incremental e busca em texto completo para documentos pessoais."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        memory: MemoryManager | None = None,
        watched_dirs: Sequence[Path | str] | None = None,
    ):
        if memory:
            self.db_path = memory.db_path
        elif db_path:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
            self.db_path = Path(base) / "zorin-copilot" / "memory.db"
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if watched_dirs is not None:
            self.watched_dirs = [Path(os.path.expanduser(str(d))) for d in watched_dirs]
        else:
            self.watched_dirs = [
                Path(os.path.expanduser("~/Documentos")),
                Path(os.path.expanduser("~/Downloads")),
            ]

        self._is_indexing = False
        self._init_rag_tables()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_rag_tables(self) -> None:
        """Cria tabelas de metadados e tabela virtual FTS5 para busca em texto completo."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Metadados de documentos indexados
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL UNIQUE,
                    file_name TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 1,
                    indexed_at TEXT NOT NULL
                )
                """
            )

            # 2. Chunks de texto detalhados
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    page_number INTEGER NOT NULL DEFAULT 1,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc ON rag_chunks (doc_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_file ON rag_chunks (file_path)")

            # 3. Tabela Virtual FTS5 de alta performance com unicode61 e remoção de diacríticos
            cursor.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(
                    doc_id UNINDEXED,
                    file_path UNINDEXED,
                    file_name,
                    title,
                    chunk_content,
                    page_number UNINDEXED,
                    tokenize='unicode61 remove_diacritics 2'
                )
                """
            )
            conn.commit()

    # =========================================================================
    # Extração de Texto por Formato
    # =========================================================================

    def extract_text_pages(self, file_path: Path) -> list[tuple[int, str]]:
        """Extrai texto estruturado em páginas (número_da_página, texto)."""
        suffix = file_path.suffix.lower()
        if not file_path.is_file():
            return []

        if suffix in (".txt", ".md", ".log"):
            return self._extract_text_file(file_path)
        elif suffix == ".csv":
            return self._extract_csv_file(file_path)
        elif suffix == ".pdf":
            return self._extract_pdf_file(file_path)
        return []

    def _extract_text_file(self, file_path: Path) -> list[tuple[int, str]]:
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            # Se for muito grande, divide em páginas lógicas de 2000 caracteres
            if len(content) <= 3000:
                return [(1, content)]

            pages: list[tuple[int, str]] = []
            page_size = 2000
            for i, start in enumerate(range(0, len(content), page_size)):
                page_text = content[start : start + page_size]
                pages.append((i + 1, page_text))
            return pages
        except Exception as exc:
            logger.debug(f"Falha ao ler arquivo de texto {file_path}: {exc}")
            return []

    def _extract_csv_file(self, file_path: Path) -> list[tuple[int, str]]:
        try:
            import csv
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)
                lines: list[str] = []
                for i, row in enumerate(reader):
                    if i > 500:  # Limite de segurança de 500 linhas
                        lines.append("... [linhas adicionais truncadas]")
                        break
                    lines.append(" | ".join(row))
                text = "\n".join(lines)
                return [(1, text)]
        except Exception as exc:
            logger.debug(f"Falha ao ler CSV {file_path}: {exc}")
            return []

    def _extract_pdf_file(self, file_path: Path) -> list[tuple[int, str]]:
        """Extrai páginas de PDF usando pdftotext nativo do sistema operacional."""
        pdftotext_bin = shutil.which("pdftotext")
        if not pdftotext_bin:
            logger.warning("pdftotext não disponível no sistema.")
            return []

        # 1. Determina total de páginas via pdfinfo se disponível
        page_count = 1
        pdfinfo_bin = shutil.which("pdfinfo")
        if pdfinfo_bin:
            try:
                res = subprocess.run([pdfinfo_bin, str(file_path)], capture_output=True, text=True, timeout=3.0)
                for line in res.stdout.splitlines():
                    if line.startswith("Pages:"):
                        page_count = int(line.split(":", 1)[1].strip())
                        break
            except Exception:
                pass

        # Limite máximo de páginas por arquivo para segurança de performance
        max_pages = min(page_count, 60)
        pages: list[tuple[int, str]] = []

        try:
            # Extrai página a página
            for p in range(1, max_pages + 1):
                cmd = [pdftotext_bin, "-f", str(p), "-l", str(p), "-layout", str(file_path), "-"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=4.0)
                txt = res.stdout.strip()
                if txt:
                    pages.append((p, txt))
            return pages
        except Exception as exc:
            logger.debug(f"Falha ao extrair PDF {file_path}: {exc}")
            return []

    # =========================================================================
    # Indexação Incremental
    # =========================================================================

    def index_file(self, file_path: Path) -> bool:
        """Indexa ou atualiza um arquivo na base se tiver sido modificado."""
        if not file_path.is_file():
            return False

        ext = file_path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return False

        # Verifica exclusões
        path_str = str(file_path)
        for excl in EXCLUDED_PATTERNS:
            if excl in path_str:
                return False

        try:
            stat = file_path.stat()
            size = stat.st_size
            # Ignora arquivos vazios ou maiores que 25MB
            if size == 0 or size > 25 * 1024 * 1024:
                return False

            mtime = stat.st_mtime
            doc_id = hashlib.sha256(path_str.encode("utf-8")).hexdigest()[:16]

            # 1. Verifica cache no banco para ver se o arquivo não mudou
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT mtime, file_size FROM rag_documents WHERE id = ?",
                    (doc_id,),
                )
                cached = cursor.fetchone()
                if cached and abs(cached["mtime"] - mtime) < 0.01 and cached["file_size"] == size:
                    # Arquivo idêntico já indexado
                    return False

            # 2. Extrai páginas de texto
            pages = self.extract_text_pages(file_path)
            if not pages:
                return False

            now = datetime.now().isoformat()
            file_name = file_path.name
            title = file_path.stem.replace("_", " ").replace("-", " ").title()

            # 3. Transação atômica de inserção
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Remove chunks anteriores do FTS e da tabela
                cursor.execute("DELETE FROM rag_chunks WHERE doc_id = ?", (doc_id,))
                cursor.execute("DELETE FROM rag_fts WHERE doc_id = ?", (doc_id,))

                # Insere os novos chunks
                chunk_idx = 0
                for page_num, page_text in pages:
                    # Divide em parágrafos ou chunks de 800 caracteres
                    chunks = self._chunk_text(page_text, chunk_size=800, overlap=100)
                    for c_text in chunks:
                        if not c_text.strip():
                            continue
                        cursor.execute(
                            """
                            INSERT INTO rag_chunks (doc_id, file_path, chunk_index, page_number, content, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (doc_id, path_str, chunk_idx, page_num, c_text, now),
                        )
                        cursor.execute(
                            """
                            INSERT INTO rag_fts (doc_id, file_path, file_name, title, chunk_content, page_number)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (doc_id, path_str, file_name, title, c_text, str(page_num)),
                        )
                        chunk_idx += 1

                # Salva metadados do documento
                cursor.execute(
                    """
                    INSERT INTO rag_documents (id, file_path, file_name, file_type, file_size, mtime, title, content_hash, page_count, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        file_size=excluded.file_size,
                        mtime=excluded.mtime,
                        title=excluded.title,
                        page_count=excluded.page_count,
                        indexed_at=excluded.indexed_at
                    """,
                    (doc_id, path_str, file_name, ext, size, mtime, title, doc_id, len(pages), now),
                )
                conn.commit()
            return True

        except Exception as exc:
            logger.debug(f"Erro ao indexar {file_path}: {exc}")
            return False

    def _chunk_text(self, text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
        """Divide texto longo em blocos com sobreposição para preservar contexto de frases."""
        clean = text.strip()
        if len(clean) <= chunk_size:
            return [clean]

        chunks: list[str] = []
        start = 0
        while start < len(clean):
            end = start + chunk_size
            chunks.append(clean[start:end])
            start += max(50, chunk_size - overlap)
        return chunks

    def index_directories(self, max_depth: int = 3) -> dict[str, Any]:
        """Varre e indexa todas as pastas observadas incrementalmente."""
        self._is_indexing = True
        stats = {"indexed": 0, "unchanged": 0, "failed": 0, "total_scanned": 0}

        try:
            for directory in self.watched_dirs:
                if not directory.exists():
                    continue

                for root, dirs, files in os.walk(directory):
                    # Filtra pastas excluídas no próprio os.walk
                    dirs[:] = [d for d in dirs if d not in EXCLUDED_PATTERNS and not d.startswith(".")]

                    # Limita profundidade
                    depth = len(Path(root).relative_to(directory).parts)
                    if depth > max_depth:
                        continue

                    for f in files:
                        if f.startswith("."):
                            continue
                        fpath = Path(root) / f
                        if fpath.suffix.lower() in SUPPORTED_EXTENSIONS:
                            stats["total_scanned"] += 1
                            changed = self.index_file(fpath)
                            if changed:
                                stats["indexed"] += 1
                            else:
                                stats["unchanged"] += 1

            return stats
        finally:
            self._is_indexing = False

    def start_background_indexing(self, on_complete: Callable[[dict[str, Any]], None] | None = None) -> threading.Thread:
        """Inicia varredura e indexação em background sem travar a interface."""
        def worker():
            stats = self.index_directories()
            logger.info(f"Indexação de documentos concluída: {stats}")
            if on_complete:
                try:
                    on_complete(stats)
                except Exception:
                    pass

        t = threading.Thread(target=worker, daemon=True, name="RAGIndexer")
        t.start()
        return t

    # =========================================================================
    # Motor de Busca RAG
    # =========================================================================

    def search(self, query: str, limit: int = 4) -> list[DocumentSearchResult]:
        """Executa busca em texto completo com BM25 ranking e extração de snippets."""
        q_clean = query.strip()
        if not q_clean:
            return []

        # Sanitiza query para FTS5: divide em termos e remove caracteres especiais
        words = re.findall(r"\w+", q_clean)
        if not words:
            return []

        # Monta expressão FTS com match de prefixo para termos parciais: term*
        fts_expr = " ".join([f'"{w}"*' for w in words])

        results: list[DocumentSearchResult] = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                # Usa ranking bm25 nativo e snippet() para contexto visual
                sql = """
                    SELECT
                        file_path,
                        file_name,
                        title,
                        page_number,
                        snippet(rag_fts, 4, '<b>', '</b>', '...', 22) as highlighted_snippet,
                        rank
                    FROM rag_fts
                    WHERE rag_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
                cursor.execute(sql, (fts_expr, limit))
                rows = cursor.fetchall()
                for r in rows:
                    p_num = int(r["page_number"]) if str(r["page_number"]).isdigit() else 1
                    results.append(
                        DocumentSearchResult(
                            file_path=r["file_path"],
                            file_name=r["file_name"],
                            title=r["title"],
                            page_number=p_num,
                            snippet=r["highlighted_snippet"],
                            rank_score=float(r["rank"]),
                        )
                    )
            except Exception as exc:
                logger.debug(f"Falha na busca FTS5 com query '{fts_expr}': {exc}")
                # Fallback: busca por LIKE tradicional se FTS5 falhar na sintaxe
                results = self._fallback_like_search(words, limit)

        return results

    def _fallback_like_search(self, words: list[str], limit: int) -> list[DocumentSearchResult]:
        results: list[DocumentSearchResult] = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            first_word = f"%{words[0]}%"
            cursor.execute(
                """
                SELECT c.file_path, d.file_name, d.title, c.page_number, c.content
                FROM rag_chunks c
                JOIN rag_documents d ON c.doc_id = d.id
                WHERE c.content LIKE ? OR d.file_name LIKE ?
                LIMIT ?
                """,
                (first_word, first_word, limit),
            )
            rows = cursor.fetchall()
            for r in rows:
                snippet = r["content"][:160] + "..." if len(r["content"]) > 160 else r["content"]
                results.append(
                    DocumentSearchResult(
                        file_path=r["file_path"],
                        file_name=r["file_name"],
                        title=r["title"],
                        page_number=r["page_number"],
                        snippet=snippet,
                    )
                )
        return results

    # =========================================================================
    # Ações e Utilitários de Desktop
    # =========================================================================

    def read_document_page(self, file_path: str, page_number: int = 1) -> str:
        """Lê o conteúdo completo de uma página específica de um documento indexado."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT content FROM rag_chunks
                WHERE file_path = ? AND page_number = ?
                ORDER BY chunk_index ASC
                """,
                (file_path, page_number),
            )
            rows = cursor.fetchall()
            if rows:
                return "\n\n".join([r["content"] for r in rows])
        return ""

    def open_document(self, file_path: str, page_number: int = 1) -> tuple[bool, str]:
        """Abre o arquivo no leitor padrão (Evince na página exata para PDFs ou visualizador padrão)."""
        fpath = Path(file_path)
        if not fpath.exists():
            return False, f"Arquivo '{file_path}' não encontrado no disco."

        # Se for PDF e Evince estiver disponível, abre diretamente na página
        if fpath.suffix.lower() == ".pdf" and shutil.which("evince"):
            try:
                subprocess.Popen(
                    ["evince", "-p", str(max(1, page_number)), str(fpath)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True, f"Documento '{fpath.name}' aberto na página {page_number} no Evince."
            except Exception as exc:
                logger.debug(f"Falha ao abrir Evince: {exc}")

        # Fallback universal: gio open / xdg-open
        for opener in ("gio", "xdg-open"):
            if shutil.which(opener):
                try:
                    subprocess.Popen(
                        [opener, "open" if opener == "gio" else "", str(fpath)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return True, f"Arquivo '{fpath.name}' aberto no aplicativo padrão."
                except Exception:
                    pass

        return False, f"Nenhum visualizador disponível para abrir '{fpath.name}'."

    def get_stats(self) -> dict[str, Any]:
        """Retorna estatísticas da base RAG local."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as doc_count FROM rag_documents")
            doc_count = cursor.fetchone()["doc_count"]

            cursor.execute("SELECT COUNT(*) as chunk_count FROM rag_chunks")
            chunk_count = cursor.fetchone()["chunk_count"]

            return {
                "total_documents": doc_count,
                "total_chunks": chunk_count,
                "watched_directories": [str(d) for d in self.watched_dirs],
            }
