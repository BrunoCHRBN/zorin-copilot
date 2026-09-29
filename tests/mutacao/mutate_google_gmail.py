"""Validação por mutação do Gmail (`core/google_gmail.py`), da revisão de e-mail
(`core/email_review.py`) e do fluxo de envio (ferramenta + portão de risco).

Motivo de existir: o ponto mais caro aqui é a HONESTIDADE da revisão (dizer
`reviewed=False` quando não revisou) e a ETAPA OBRIGATÓRIA antes do envio. Um
teste que só cobre o caminho feliz deixaria os dois passarem.

**Como rodar** (da raiz do repo):

    .venv/bin/python tests/mutacao/mutate_google_gmail.py

Categorias: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
TESTES = ["tests/test_google_gmail.py", "tests/test_email_review.py"]

#: (arquivo, [(nome, de, para)])
GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "google_gmail.py",
        [
            ("mensagem: destinatário vazio deixa de ser rejeitado",
             '    if not recipient:\n        raise ValueError("Destinatário é obrigatório.")',
             '    if False:\n        raise ValueError("Destinatário é obrigatório.")'),
            ("mensagem: cópia (Cc) deixa de ser incluída",
             '    if cc:\n        message["Cc"] = str(cc).strip()',
             '    if False:\n        message["Cc"] = str(cc).strip()'),
            ("listagem: consulta `q` deixa de ser enviada",
             '    if query:\n        params["q"] = str(query)',
             '    if False:\n        params["q"] = str(query)'),
            ("rótulos: mudança vazia deixa de ser rejeitada",
             "    if not body:\n"
             '        raise ValueError("Nada a modificar: informe ao menos um rótulo para adicionar ou remover.")',
             "    if False:\n"
             '        raise ValueError("Nada a modificar: informe ao menos um rótulo para adicionar ou remover.")'),
            ("marcar lida: passa a ADICIONAR o rótulo de não lida",
             "    return modify_labels(message_id, remove=[LABEL_UNREAD], service=service)",
             "    return modify_labels(message_id, add=[LABEL_UNREAD], service=service)"),
            ("leitura: assunto deixa de vir dos cabeçalhos",
             '        "subject": headers.get("subject", ""),',
             '        "subject": "",'),
            ("envio: `raw` deixa de ser enviado à API",
             '        .send(userId="me", body={"raw": raw})',
             "        .send(userId=\"me\", body={})"),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "email_review.py",
        [
            ("revisão: sem provedor passa a MENTIR que revisou",
             "            reviewed=False,\n"
             '            note="Nenhum provedor de IA configurado — o texto NÃO foi revisado.",',
             "            reviewed=True,\n"
             '            note="Nenhum provedor de IA configurado — o texto NÃO foi revisado.",'),
            ("revisão: falha do provedor passa a MENTIR que revisou",
             "            reviewed=False,\n"
             '            note=f"Falha na revisão ({type(exc).__name__}) — o texto NÃO foi revisado.",',
             "            reviewed=True,\n"
             '            note=f"Falha na revisão ({type(exc).__name__}) — o texto NÃO foi revisado.",'),
            ("lint: espaços duplicados deixam de ser detectados",
             "    if _DOUBLE_SPACE_RE.search(body):",
             "    if False:"),
            ("lint: espaço antes de pontuação deixa de ser detectado",
             "    if _SPACE_BEFORE_PUNCT_RE.search(body):",
             "    if False:"),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "shell" / "risk.py",
        [
            ("risco: alterações no Gmail deixam de pedir confirmação",
             '            if action in ("send", "draft", "mark_read", "mark_unread"):',
             "            if False:"),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "ai" / "agent_tools.py",
        [
            ("envio: deixa de exigir a revisão prévia (reviewed)",
             '                if not args.get("reviewed"):',
             "                if False:"),
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
