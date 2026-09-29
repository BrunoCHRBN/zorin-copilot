"""Validação por mutação da lógica de risco e autonomia.

Cobre o que foi implementado em `docs/ANEXO_AUTONOMIA_PREREQUISITOS.md` (itens 1–6):
higienização da observação no Live, selo de aprovação por chamada, política injetável,
nível `AUTO` + `notify_after`, `autonomy_level` (normalização, teto em `low`, promoção
em `high` com zonas de confiança) e a armadilha B2 do coletor de preferências.

Motivo de existir: os testes afirmam **decisão**. Um teste que afirma o comportamento
errado passa nos dois estados — este harness reintroduz defeitos plausíveis e exige que
a suíte os mate. Sem ele, um refactor futuro pode quebrar o fail-closed em silêncio.

**Como rodar** (da raiz do repo):

    .venv/bin/python tests/mutacao/mutate_risk.py

Categorias: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
TESTES = [
    "tests/test_autonomia_prereq.py",
    "tests/test_autonomia_nivel_auto.py",
    "tests/test_autonomia_nivel.py",
    "tests/test_fase3_risk.py",
    "tests/test_agent_loop.py",
]

#: (arquivo, [(nome, de, para)])
GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "ai" / "live.py",
        [
            ("higienização: Live volta a reenviar a observação crua ao modelo",
             '                    "output": self._sanitize_tool_output(name, output),',
             '                    "output": output,'),
            ("selo: o portão volta a ignorar a aprovação por chamada",
             'if not approved and getattr(self, "risk_policy", None) is not None:',
             'if getattr(self, "risk_policy", None) is not None:'),
            ("selo: confirm_action deixa de repassar a aprovação",
             '                return self._dispatch_tool(\n'
             '                    pending["name"], dict(pending["args"]), approved=True\n'
             '                )',
             '                return self._dispatch_tool(\n'
             '                    pending["name"], dict(pending["args"])\n'
             '                )'),
            ("portão do Live passa a barrar AUTO (exige confirmação p/ tudo que não é SAFE)",
             '            if level == RiskLevel.CONFIRM:',
             '            if level != RiskLevel.SAFE:'),
            ("política injetada é descartada (o cliente cria a própria)",
             'self.risk_policy = risk_policy or RiskPolicy()',
             'self.risk_policy = RiskPolicy()'),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "shell" / "risk.py",
        [
            ("normalização: valor inválido cai no default em vez de 'low'",
             '    return candidate if candidate in AUTONOMY_LEVELS else "low"',
             '    return candidate if candidate in AUTONOMY_LEVELS else DEFAULT_AUTONOMY_LEVEL'),
            ("teto: 'low' deixa de rebaixar AUTO para CONFIRM",
             '        if self.autonomy_level == "low" and level is RiskLevel.AUTO:\n'
             '            return RiskLevel.CONFIRM, desc\n'
             '        return level, desc',
             '        return level, desc'),
            ("promoção: escrita em zona de confiança ignora o nível de autonomia",
             '            if self.autonomy_level == "high" and fpath and self._in_trusted_zone(fpath):',
             '            if fpath and self._in_trusted_zone(fpath):'),
            ("zona: qualquer caminho passa a contar como dentro da zona",
             '        return any(target == base or base in target.parents for base in self._trusted_dirs)',
             '        return True'),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "ai" / "agent.py",
        [
            ("AUTO deixa de marcar o passo para aviso posterior",
             '            notify_after=level == RiskLevel.AUTO,',
             '            notify_after=False,'),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "ai" / "agent_tools.py",
        [
            ("notify_after confundido com CONFIRM (faria o cartão pedir clique)",
             '        return level == RiskLevel.AUTO',
             '        return level == RiskLevel.CONFIRM'),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "config.py",
        [
            ("load(): autonomia deixa de ser normalizada (typo passaria direto)",
             '        from ..shell.risk import normalize_autonomy_level\n\n'
             '        config.autonomy_level = normalize_autonomy_level(config.autonomy_level)',
             '        pass'),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "ui" / "preferences.py",
        [
            ("armadilha B2: o coletor deixa de reatribuir o nível de autonomia",
             '        sel_autonomy = self.autonomy_level_row.get_selected()\n'
             '        cfg.autonomy_level = (\n'
             '            self.autonomy_options[sel_autonomy][0]\n'
             '            if 0 <= sel_autonomy < len(self.autonomy_options)\n'
             '            else "standard"\n'
             '        )',
             '        pass'),
        ],
    ),
]

#: Sobreviventes que NÃO são lacuna: a mutação não muda comportamento observável.
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
