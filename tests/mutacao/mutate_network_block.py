"""Validação por mutação da contenção defensiva (`core/network_actions.py`,
classe `NetworkBlocker`): ação inválida aceita, fail-closed quando não configurado,
máscara de MAC na resposta, e resolução de IP→MAC no inventário.

Motivo de existir: o `NetworkBlocker` só *solicita* o bloqueio no roteador/HA e NUNCA
derruba tráfego localmente. Os testes afirmam **decisão** (ação inválida recusada,
backend não configurado ⇒ nada executa e orienta, MAC mascarado na saída, IP resolve
para o MAC real no comando). Um teste que passa nos dois estados é inútil — este
harness reintroduz os defeitos e exige que a suíte os mate.

**Como rodar** (da raiz do repo):

    .venv/bin/python tests/mutacao/mutate_network_block.py

Categorias: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
TESTES = ["tests/test_network_actions.py"]

#: (arquivo, [(nome, de, para)])
GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "network_actions.py",
        [
            # Ação inválida passa a ser aceita (em vez de recusada).
            (
                "blocker: ação inválida aceita",
                '            return "allow"\n        return None\n',
                '            return "allow"\n        return a\n',
            ),
            # Fail-closed: backend não configurado passa a fingir sucesso (ok=True).
            (
                "blocker: não-configurado finge sucesso",
                '                "error": "Backend de contenção não configurado.",\n',
                '                "error": "Backend de contenção não configurado.",\n'
                '                "ok": True,\n',
            ),
            # Máscara de MAC removida da resposta ao modelo.
            (
                "blocker: MAC não mascarado na resposta",
                "            return NetworkInspector.mask_mac(identifier)\n",
                "            return identifier\n",
            ),
            # Resolução de IP→MAC no inventário desligada (roteador recebe só o IP).
            (
                "blocker: IP não resolve para MAC",
                "            if inventory is not None:\n",
                "            if False:\n",
            ),
        ],
    ),
]

#: Sobreviventes que NÃO são lacuna. MEDIDO, não presumido.
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
