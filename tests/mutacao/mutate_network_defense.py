"""Validação por mutação das capacidades defensivas de rede
(`core/network_inspector.py`): resolução OUI Apple, parser de hosts ativos
(`_parse_nmap_hosts_xml`), parser de fingerprint (`_parse_nmap_device_xml`),
o galho de "nmap ausente" / checagem de returncode em `scan_subnet`, e o
baseline persistente (`DeviceInventory`) + `monitor_lan` (semeadura no baseline
vazio, detecção de device novo, flag de não-confiável, persistência de `trusted`).

Motivo de existir: os testes afirmam **decisão** (device Apple resolvido, host
'down' descartado, OS mais provável escolhido, porta aberta extraída, nmap
ausente ⇒ lista vazia). Um teste que afirma o comportamento errado passa nos
dois estados — este harness reintroduz defeitos plausíveis e exige que a suíte
os mate.

**Como rodar** (da raiz do repo):

    .venv/bin/python tests/mutacao/mutate_network_defense.py

Categorias: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
TESTES = ["tests/test_network_inspector.py"]

#: (arquivo, [(nome, de, para)])
GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "network_inspector.py",
        [
            # OUI: prefixo do device da captura deixa de resolver Apple.
            (
                "oui: 1c:36 deixa de resolver Apple",
                '        "1c:36:bb": ("Apple Inc.", "phone"),\n',
                "",
            ),
            # Scan ativo: host 'down' deixa de ser descartado (filtro de estado invertido).
            (
                "scan: filtro de 'up' invertido inclui host down",
                '            if status is None or status.get("state") != "up":',
                '            if status is None or status.get("state") == "up":',
            ),
            # Fingerprint: porta 'open' deixa de ser extraída.
            (
                "identify: filtro de porta 'open' invertido",
                '                if state is None or state.get("state") != "open":',
                '                if state is None or state.get("state") == "open":',
            ),
            # Fingerprint: OS mais provável deixa de ser o de maior acurácia.
            (
                "identify: escolhe OS de menor acurácia",
                "                if acc > best_acc:",
                "                if acc < best_acc:",
            ),
            # scan_subnet: galho de "nmap ausente" deixa de retornar lista vazia.
            (
                "scan: 'nmap ausente' deixa de retornar []",
                '        if not shutil.which("nmap"):\n'
                '            logger.warning("nmap ausente: scan_subnet indisponível.")\n'
                "            return []\n",
                '        if not shutil.which("nmap"):\n'
                '            logger.warning("nmap ausente: scan_subnet indisponível.")\n',
            ),
            # scan_subnet: returncode 0 (sucesso) deixa de ser tratado como sucesso.
            (
                "scan: returncode 0 deixa de parsear",
                '            if proc.returncode != 0:\n'
                '                logger.warning("nmap retornou %s em scan_subnet", proc.returncode)\n'
                "                return []\n",
                '            if proc.returncode == 0:\n'
                '                logger.warning("nmap retornou %s em scan_subnet", proc.returncode)\n'
                "                return []\n",
            ),
            # monitor_lan: semeadura silenciosa no baseline vazio deixa de ocorrer
            # (trata a LAN inteira como 'novo' na primeira execução).
            (
                "monitor: semeadura no baseline vazio desligada",
                '    if not known and seed_if_empty:\n',
                '    if not known and seed_if_empty and False:\n',
            ),
            # monitor_lan: detecção de device novo invertida (device conhecido vira 'novo').
            (
                "monitor: detecção de novo device invertida",
                '        if mac not in known:\n',
                '        if mac in known:\n',
            ),
            # monitor_lan: ramo de 'não-confiável' invertido (confiável aparece como não-confiável).
            (
                "monitor: flag de não-confiável invertida",
                '        elif not known[mac]["trusted"]:\n',
                '        elif known[mac]["trusted"]:\n',
            ),
            # remember: trusted deixa de persistir no conflito (re-ver device rebaixa a confiável).
            (
                "inventory: trusted deixa de persistir no conflito",
                '                    last_seen=excluded.last_seen\n',
                '                    last_seen=excluded.last_seen,\n'
                '                    trusted=excluded.trusted\n',
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
