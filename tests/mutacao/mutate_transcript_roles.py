"""Validação por mutação da Entrega G / P1-7 (atribuição de turno na transcrição).

Motivo de existir: os três defeitos desta entrega são **silenciosos por construção**.

- Parar de ler `inputTranscription` não levanta erro nenhum — o transcrito da nuvem
  simplesmente volta a ter só o lado do copiloto, e ninguém percebe até uma sessão cair e o
  resumo contínuo reancorar o modelo sem metade da conversa.
- Trocar o papel do turno do usuário por `assistant` também passa despercebido: o texto
  continua lá, só atribuído à pessoa errada.
- Fazer o papel desconhecido cair em `user` é o **erro perigoso** dos dois: põe palavras do
  copiloto na boca do usuário, e nenhum caminho de erro acusa.

Cobrem W1 (vocabulário/normalização/leitores tolerantes), W2 (a metade do usuário na nuvem),
W3 (o resumo contínuo) e W4 (o papel canônico no cliente local).

Como rodar (da raiz do repo):
    .venv/bin/python tests/mutacao/mutate_transcript_roles.py

Antes, rode a tranca estática (0,1 s) que confere âncora única/ausente:
    .venv/bin/python -m pytest tests/test_mutacao_anchors.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]          # tests/mutacao/ -> raiz
TESTES = [
    # Determinístico de propósito (~0,5 s, sem rede/GTK/threads). Cobre os três
    # arquivos-fonte de uma vez: o vocabulário, o handler de servidor e a paridade entre
    # os dois clientes.
    "tests/test_transcript_roles.py",
]

GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "transcript.py",
        [
            (
                "W1: o papel desconhecido passa a ser atribuído ao USUÁRIO (o erro perigoso)",
                "    return LEGACY_ROLE_ALIASES.get(chave, ROLE_ASSISTANT)",
                "    return LEGACY_ROLE_ALIASES.get(chave, ROLE_USER)",
            ),
            (
                "W1: o alias legado `agent` some (dados antigos viram usuário)",
                '    "agent": ROLE_ASSISTANT,\n',
                "",
            ),
            (
                "W1: `make_entry` deixa de normalizar o papel",
                '    return {"role": normalize_role(role), "text": "" if text is None else str(text)}',
                '    return {"role": role, "text": "" if text is None else str(text)}',
            ),
            (
                "W1: `entry_role` deixa de ler o dict canônico",
                '        return normalize_role(entry.get("role"))',
                "        return ROLE_ASSISTANT",
            ),
            (
                "W1: a extração ignora o envelope dict da transcrição",
                '        return str(node.get("text") or "").strip()',
                '        return ""',
            ),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "ai" / "live.py",
        [
            (
                "W2: a nuvem deixa de ler o inputTranscription (a metade do usuário some)",
                '                user_text = extract_transcription_text(sc.get("inputTranscription"))',
                '                user_text = ""',
            ),
            (
                "W2: o turno do usuário é gravado como assistente (papel trocado)",
                "                    self._transcripts_log.append(make_entry(ROLE_USER, user_text))",
                "                    self._transcripts_log.append(make_entry(ROLE_ASSISTANT, user_text))",
            ),
            (
                "W2: o turno do usuário volta a ser gravado como tupla (quebra a paridade de forma)",
                "                    self._transcripts_log.append(make_entry(ROLE_USER, user_text))",
                "                    self._transcripts_log.append((ROLE_USER, user_text))",
            ),
            (
                "W3: o resumo contínuo volta a rotular tudo como copilot",
                '                tag = "você" if entry_role(entry) == ROLE_USER else "copilot"',
                '                tag = "copilot"',
            ),
            (
                "W3: o resumo deixa de ler a tupla legada (assume dict)",
                '                t = entry_text(entry).strip().replace("\\n", " ")',
                '                t = (entry.get("text") or "").strip().replace("\\n", " ")',
            ),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "ai" / "local_voice.py",
        [
            (
                "W4: o cliente local volta a emitir o papel histórico `agent` no callback",
                "                self.on_transcript(ROLE_ASSISTANT, response_text)",
                '                self.on_transcript("agent", response_text)',
            ),
        ],
    ),
]

EQUIVALENTES: dict[str, str] = {
    # Medido, não presumido: com `"agent"` fora do dicionário, `normalize_role("agent")`
    # cai no **default** (`ROLE_ASSISTANT`) e devolve exatamente o mesmo resultado. O alias
    # é redundante *hoje* — e é mantido de propósito: se o default um dia mudar, ele é o
    # que protege um valor legado conhecido. A mutação, portanto, não muda comportamento.
    "W1: o alias legado `agent` some (dados antigos viram usuário)":
        "EQUIVALENTE: `normalize_role` já cai em ROLE_ASSISTANT para papel desconhecido; "
        "remover o alias não altera nenhuma saída.",
}


def rodar_testes() -> bool:
    r = subprocess.run(
        [str(RAIZ / ".venv" / "bin" / "python"), "-m", "pytest", "-q", *TESTES],
        cwd=str(RAIZ), capture_output=True, text=True,
    )
    return r.returncode == 0


def main() -> int:
    matadas, sobreviveram, invalidas = [], [], []
    for alvo, mutacoes in GRUPOS:
        original = alvo.read_text(encoding="utf-8")
        try:
            for nome, de, para in mutacoes:
                if de not in original:
                    invalidas.append(nome); print(f"INVÁLIDA   {nome}  (trecho não encontrado)"); continue
                if original.count(de) > 1:          # GUARDA DE AMBIGUIDADE — não remova
                    invalidas.append(nome); print(f"INVÁLIDA   {nome}  (trecho ambíguo: {original.count(de)})"); continue
                mutado = original.replace(de, para, 1)
                if mutado == original:
                    invalidas.append(nome); print(f"INVÁLIDA   {nome}  (substituição não mudou nada)"); continue
                alvo.write_text(mutado, encoding="utf-8")
                try:
                    if rodar_testes():
                        if nome in EQUIVALENTES: print(f"EQUIVALENTE {nome}")
                        else: sobreviveram.append(nome); print(f"SOBREVIVEU {nome}")
                    else:
                        matadas.append(nome); print(f"KILLED     {nome}")
                finally:
                    alvo.write_text(original, encoding="utf-8")
        finally:
            alvo.write_text(original, encoding="utf-8")   # restaura se algo explodir no meio
    print("\n" + "=" * 70)
    print(f"matadas: {len(matadas)}   sobreviveram: {len(sobreviveram)}   "
          f"equivalentes: {len(EQUIVALENTES)}   inválidas: {len(invalidas)}")
    if sobreviveram:
        print("\nSOBREVIVENTES (lacunas de teste):")
        for n in sobreviveram: print(f"  - {n}")
    if invalidas:
        print("\nINVÁLIDAS (mutação não aplicada):")
        for n in invalidas: print(f"  - {n}")
    return 1 if (sobreviveram or invalidas) else 0


if __name__ == "__main__":
    sys.exit(main())
