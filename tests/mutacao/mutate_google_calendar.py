"""Validação por mutação da Google Agenda (`core/google_calendar.py`) e da
correção do parser de datas (`core/calendar.py::parse_when`).

Motivo de existir: os testes afirmam **decisão** (janela de tempo, corpo enviado
à API, normalização, roteamento). Um teste que afirma o comportamento errado
passa nos dois estados — este harness reintroduz defeitos plausíveis e exige que
a suíte os mate.

**Como rodar** (da raiz do repo):

    .venv/bin/python tests/mutacao/mutate_google_calendar.py

Categorias: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
TESTES = ["tests/test_google_calendar.py", "tests/test_operational_modules.py"]

#: (arquivo, [(nome, de, para)])
GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "google_calendar.py",
        [
            ("janela: 'amanhã' deixa de avançar um dia",
             '    if day in ("tomorrow", "amanhã", "amanha"):',
             "    if False:"),
            ("janela: 'semana' deixa de cobrir 7 dias",
             '    elif day in ("week", "semana", "próximos 7 dias", "proximos 7 dias", "7d"):',
             "    elif False:"),
            ("fuso: datetime ingênuo deixa de ganhar offset",
             "    if moment.tzinfo is None:\n"
             "        moment = moment.replace(tzinfo=local_timezone())\n"
             "    return moment.isoformat()",
             "    return moment.isoformat()"),
            ("criação: duração curta deixa de ser elevada ao mínimo",
             "    duration = max(MIN_DURATION_MINUTES, int(duration_minutes or DEFAULT_DURATION_MINUTES))",
             "    duration = int(duration_minutes or DEFAULT_DURATION_MINUTES)"),
            ("criação: título vazio deixa de ser rejeitado",
             '    if not title:\n        raise ValueError("Título do compromisso é obrigatório.")',
             '    if False:\n        raise ValueError("Título do compromisso é obrigatório.")'),
            ("edição: patch vazio deixa de ser rejeitado",
             "    if not patch:",
             "    if False:"),
            ("edição: mover o início deixa de ajustar o fim",
             '        patch["start"] = {"dateTime": to_rfc3339(start_dt)}\n'
             '        patch["end"] = {"dateTime": to_rfc3339(start_dt + timedelta(minutes=duration))}',
             '        patch["start"] = {"dateTime": to_rfc3339(start_dt)}'),
            ("remoção: id vazio deixa de ser rejeitado",
             "    if not event_id:",
             "    if False:"),
            ("freebusy: itens da consulta deixam de ser enviados",
             '        "items": [{"id": cal_id} for cal_id in ids],',
             '        "items": [],'),
            ("normalização: título deixa de vir do summary",
             '        "title": raw.get("summary", ""),',
             '        "title": "",'),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "calendar.py",
        [
            ("parse: 'depois de amanhã' volta a ser inalcançável",
             '    if "depois de amanhã" in clean or "depois de amanha" in clean:',
             "    if False:"),
            ("parse: ISO lido em minúsculas (o 'T' quebra)",
             "                parsed = datetime.strptime(clean, fmt)",
             "                parsed = datetime.strptime(clean.upper(), fmt)"),
        ],
    ),
]

#: Sobreviventes que NÃO são lacuna: a mutação não muda comportamento observável.
#: MEDIDO, não presumido — foi assim que esta entrada nasceu: eu supus que o ISO
#: com "T" maiúsculo estava quebrado por a string ser baixada para minúsculas, e
#: o harness mostrou que a suíte não sentia a diferença. A causa é que o
#: `strptime` compila o formato com IGNORECASE, então o literal "T" casa com "t".
EQUIVALENTES: dict[str, str] = {
    "parse: ISO lido em minúsculas (o 'T' quebra)":
        "strptime é case-insensitive para literais — 'T' e 't' parseiam igual",
}


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
