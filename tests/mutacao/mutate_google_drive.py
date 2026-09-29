"""Validação por mutação do cliente do Google Drive (`core/google_drive.py`) e do
portão de risco do Drive/agenda (`shell/risk.py`).

Motivo de existir: os testes afirmam **decisão** (o que vai na consulta `q`, o
que é gravado, quem pede confirmação). Um teste que afirma o comportamento errado
passa nos dois estados — este harness reintroduz defeitos plausíveis e exige que
a suíte os mate.

**Como rodar** (da raiz do repo):

    .venv/bin/python tests/mutacao/mutate_google_drive.py

Categorias: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
TESTES = ["tests/test_google_drive.py", "tests/test_google_calendar.py"]

#: (arquivo, [(nome, de, para)])
GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "google_drive.py",
        [
            ("list: sem filtro volta a devolver a lixeira",
             '    params["q"] = str(query) if query else "trashed = false"',
             '    if query:\n        params["q"] = str(query)'),
            ("busca: aspas simples deixam de ser escapadas",
             '    clean = str(term or "").strip().replace("\'", "\\\\\'")',
             '    clean = str(term or "").strip()'),
            ("busca: termo vazio deixa de ser barrado",
             "    if not clean:\n        return []",
             "    if False:\n        return []"),
            ("mover: deixa de remover o pai antigo (arquivo em duas pastas)",
             "            removeParents=previous or None,",
             "            removeParents=None,"),
            ("lixeira: marca como NÃO lixeira",
             '        fileId=file_id, body={"trashed": True}, fields="id,trashed"',
             '        fileId=file_id, body={"trashed": False}, fields="id,trashed"'),
            ("upload: pasta de destino deixa de virar pai",
             '        metadata["parents"] = [folder_id]',
             "        pass"),
            ("pasta: mimeType deixa de ser o de pasta",
             '    metadata: dict[str, Any] = {"name": clean, "mimeType": MIME_FOLDER}',
             '    metadata: dict[str, Any] = {"name": clean, "mimeType": "text/plain"}'),
            ("normalização: nome deixa de vir da API",
             '        "name": raw.get("name", ""),',
             '        "name": "",'),
            ("get: id vazio deixa de ser rejeitado",
             '    """Metadados de um arquivo."""\n'
             '    file_id = str(file_id or "").strip()\n'
             '    if not file_id:\n'
             '        raise ValueError("ID do arquivo é obrigatório.")',
             '    """Metadados de um arquivo."""\n'
             '    file_id = str(file_id or "").strip()\n'
             '    if False:\n'
             '        raise ValueError("ID do arquivo é obrigatório.")'),
            ("download: conteúdo deixa de ser gravado",
             "    dest.write_bytes(content)",
             "    pass"),
            ("download: diretório de destino deixa de ser criado",
             "    dest.parent.mkdir(parents=True, exist_ok=True)",
             "    pass"),
            ("apagar: passa a mandar para a lixeira em vez de apagar de vez",
             "    _service(service).files().delete(fileId=file_id).execute()",
             '    _service(service).files().update(fileId=file_id, body={"trashed": True}).execute()'),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "shell" / "risk.py",
        [
            ("risco: escritas do Drive deixam de pedir confirmação",
             '            if action in ("upload", "download", "mkdir", "move", "rename", "trash", "delete"):',
             "            if False:"),
            ("risco: escritas da agenda deixam de pedir confirmação",
             '            if action in ("create", "update", "delete"):',
             "            if False:"),
        ],
    ),
]

EQUIVALENTES: dict[str, str] = {}


def rodar_testes() -> bool:
    r = subprocess.run(
        [str(RAIZ / ".venv" / "bin" / "python"), "-m", "pytest", "-q", *TESTES],
        cwd=str(RAIZ),
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def main() -> int:
    matadas: list[str] = []
    sobreviveram: list[str] = []
    invalidas: list[str] = []

    for alvo, mutacoes in GRUPOS:
        original = alvo.read_text(encoding="utf-8")
        try:
            for nome, de, para in mutacoes:
                if de not in original:
                    invalidas.append(nome)
                    print(f"INVÁLIDA   {nome}  (trecho não encontrado)")
                    continue
                if original.count(de) > 1:
                    invalidas.append(nome)
                    print(f"INVÁLIDA   {nome}  (trecho ambíguo: {original.count(de)} ocorrências)")
                    continue
                mutado = original.replace(de, para, 1)
                if mutado == original:
                    invalidas.append(nome)
                    print(f"INVÁLIDA   {nome}  (substituição não mudou nada)")
                    continue
                alvo.write_text(mutado, encoding="utf-8")
                try:
                    if rodar_testes():
                        if nome in EQUIVALENTES:
                            print(f"EQUIVALENTE {nome}")
                        else:
                            sobreviveram.append(nome)
                            print(f"SOBREVIVEU {nome}")
                    else:
                        matadas.append(nome)
                        print(f"KILLED     {nome}")
                finally:
                    alvo.write_text(original, encoding="utf-8")
        finally:
            # Restaura mesmo se algo explodir no meio da lista de mutações.
            alvo.write_text(original, encoding="utf-8")

    print("\n" + "=" * 70)
    print(
        f"matadas: {len(matadas)}   sobreviveram: {len(sobreviveram)}   "
        f"equivalentes: {len(EQUIVALENTES)}   inválidas: {len(invalidas)}"
    )
    if sobreviveram:
        print("\nSOBREVIVENTES (lacunas de teste):")
        for nome in sobreviveram:
            print(f"  - {nome}")
    if invalidas:
        print("\nINVÁLIDAS (mutação não aplicada):")
        for nome in invalidas:
            print(f"  - {nome}")
    return 1 if (sobreviveram or invalidas) else 0


if __name__ == "__main__":
    sys.exit(main())
