"""Validação por mutação da Entrega E / P1-9 (Privacy Shield por identidade + allowlist).

Motivo de existir: o defeito desta entrega é **silencioso e invertido** — o escudo não
deixava de proteger, ele protegia *errado*. Voltar a `"bb" in texto` reintroduz o falso
positivo sem quebrar nada visível; deixar a allowlist vencer o cofre de senhas não levanta
erro nenhum; inverter a precedência allowlist/denylist é invisível até alguém esquecer uma
entrada antiga. Nenhum desses defeitos aparece sem que o teste vá buscá-lo.

Cobrem W1 (fronteira de palavra), W2 (hard-block), W3 (precedência das listas) e W5 (fiação).

Como rodar (da raiz do repo):
    .venv/bin/python tests/mutacao/mutate_privacy_shield.py

Antes, rode a tranca estática (0,1 s) que confere âncora única/ausente:
    .venv/bin/python -m pytest tests/test_mutacao_anchors.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]          # tests/mutacao/ -> raiz
TESTES = [
    # Determinístico de propósito (~1 s, sem WebSocket/GLib/timers reais). Cobre a decisão
    # pura E a fiação do cliente, então serve às mutações dos dois arquivos-fonte.
    "tests/test_privacy_shield.py",
]

GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "privacy_gate.py",
        [
            (
                "W1: a fronteira de palavra some de \\binter\\b (reintroduz 'Printer')",
                're.compile(r"\\binter\\b", re.IGNORECASE),',
                're.compile(r"inter", re.IGNORECASE),',
            ),
            (
                "W1: a fronteira de palavra some de \\bbb\\b (reintroduz 'Hobby'/'Abbey')",
                're.compile(r"\\bbb\\b", re.IGNORECASE),',
                're.compile(r"bb", re.IGNORECASE),',
            ),
            (
                "W1: a união de padrões perde um banco (\\bnubank\\b)",
                '        re.compile(r"\\bnubank\\b", re.IGNORECASE),\n',
                "",
            ),
            (
                "W2: a allowlist volta a poder revelar o cofre (hard-block vira vazio)",
                "HARD_BLOCK_APP_PATTERNS: tuple[re.Pattern[str], ...] = SENSITIVE_APP_PATTERNS",
                "HARD_BLOCK_APP_PATTERNS: tuple[re.Pattern[str], ...] = ()",
            ),
            (
                "W3: a allowlist passa a vencer a denylist (ordem invertida)",
                "        # 2. Denylist do usuário.\n"
                "        if app_low and app_low in cls._normalize_entries(denylist):\n"
                "            return PrivacyCheckResult(\n"
                '                allowed=False,\n'
                '                reason=f"Aplicativo na denylist do usuário: \'{app}\'",\n'
                "                is_sensitive=True,\n"
                "            )\n"
                "\n"
                "        # 3. Allowlist do usuário: consentimento explícito.\n"
                "        if app_low and app_low in cls._normalize_entries(allowlist):\n"
                '            return PrivacyCheckResult(allowed=True, reason="", is_sensitive=False)\n',
                "        # 3. Allowlist do usuário: consentimento explícito.\n"
                "        if app_low and app_low in cls._normalize_entries(allowlist):\n"
                '            return PrivacyCheckResult(allowed=True, reason="", is_sensitive=False)\n'
                "\n"
                "        # 2. Denylist do usuário.\n"
                "        if app_low and app_low in cls._normalize_entries(denylist):\n"
                "            return PrivacyCheckResult(\n"
                '                allowed=False,\n'
                '                reason=f"Aplicativo na denylist do usuário: \'{app}\'",\n'
                "                is_sensitive=True,\n"
                "            )\n",
            ),
            (
                "W3: a allowlist casa por substring em vez de identidade exata",
                "        if app_low and app_low in cls._normalize_entries(allowlist):",
                "        if any(entry in app_low for entry in cls._normalize_entries(allowlist)):",
            ),
            (
                "W3: a normalização de caixa/espaços some da allowlist",
                "        return frozenset(str(e).strip().lower() for e in entries if str(e).strip())",
                "        return frozenset(str(e) for e in entries)",
            ),
            (
                "W3: o gate deixa de cair nos padrões (toda janela passa)",
                "        return cls.should_ingest_window(app, title)",
                '        return PrivacyCheckResult(allowed=True, reason="", is_sensitive=False)',
            ),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "ai" / "live.py",
        [
            (
                "W5: o cliente deixa de ler a allowlist do config",
                "        self._privacy_allowlist: list[str] = list(\n"
                '            getattr(self.config, "privacy_shield_allowlist", None) or []\n'
                "        )",
                "        self._privacy_allowlist: list[str] = []",
            ),
            (
                "W5: a decisão deixa de consultar as listas (allowlist/denylist ignoradas)",
                '            allowlist=getattr(self, "_privacy_allowlist", None),\n'
                '            denylist=getattr(self, "_privacy_denylist", None),\n',
                "            allowlist=None,\n"
                "            denylist=None,\n",
            ),
            (
                "W5: o laço de vídeo volta à substring crua (a CHAMADA trocada)",
                "                    is_sensitive = self._window_is_sensitive(app_name, win_title)",
                '                    is_sensitive = any(\n'
                '                        kw in f"{app_name} {win_title}".lower()\n'
                "                        for kw in self.sensitive_keywords\n"
                "                    )",
            ),
        ],
    ),
]

EQUIVALENTES: dict[str, str] = {}


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
