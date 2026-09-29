"""Validação por mutação da seção de Contas Google nas preferências (Fase 4) e da
regra única de habilitação (`core/google_auth.service_enabled`).

Motivo de existir: os pontos caros aqui são silenciosos — o toggle que apaga a
permissão dos outros serviços, o carregamento que reescreve a config sozinho, e a
regra de habilitação divergindo entre a UI e o agente.

**Como rodar** (da raiz do repo):

    .venv/bin/python tests/mutacao/mutate_preferences_google.py

Categorias: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
TESTES = ["tests/test_preferences_google.py", "tests/test_google_auth.py"]

#: (arquivo, [(nome, de, para)])
GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "google_auth.py",
        [
            ("regra: portão mestre deixa de desligar tudo",
             "    if not enabled:\n        return False",
             "    if False:\n        return False"),
            ("regra: allowlist deixa de bloquear serviço não listado",
             "    if allowed and service not in allowed:\n        return False",
             "    if False:\n        return False"),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "ui" / "preferences.py",
        [
            ("toggle: desligar um serviço apaga a permissão dos outros",
             "        if not allowed:\n            allowed = list(google_services())",
             "        if False:\n            allowed = list(google_services())"),
            ("estado: 'reconexão necessária' some (vira 'não conectado')",
             '            row.set_subtitle("Reconexão necessária" if wants_it else "Não conectado")',
             '            row.set_subtitle("Não conectado")'),
            ("carga: guarda de carregamento do toggle de serviço some",
             "        if self._loading_google:\n            return\n        allowed = [str(s) for s in",
             "        allowed = [str(s) for s in"),
            ("carga: guarda de carregamento do portão mestre some",
             "        if self._loading_google:\n            return\n        self.config.google_enabled = bool(row.get_active())",
             "        self.config.google_enabled = bool(row.get_active())"),
            ("coleta: portão mestre deixa de vir do controle",
             "        cfg.google_enabled = self.google_switch_row.get_active()",
             '        cfg.google_enabled = bool(getattr(self.config, "google_enabled", False))'),
            ("botão: 'Desconectar' vira 'Conectar' mesmo conectado",
             '                button.set_label("Desconectar")',
             '                button.set_label("Conectar")'),
            ("botão: clique conectado deixa de desconectar",
             "            google_auth.disconnect(service)",
             "            pass"),
            ("botão: deixa de sinalizar 'Aguardando…' durante o fluxo",
             '        button.set_label("Aguardando…")',
             "        pass"),
            # -- aviso de expiração (Fase 5) -----------------------------
            ("aviso: 'data desconhecida' some do título",
             '            titulo = "Expiração da autorização (data desconhecida)"',
             '            titulo = "Expiração da autorização"'),
            ("aviso: ícone de dúvida vira ícone de informação",
             '            icone = "dialog-question-symbolic"',
             '            icone = "dialog-information-symbolic"'),
            ("aviso: deixa de somar os outros serviços",
             '            subtitulo += f" (vale para mais {restantes} serviço(s))"',
             "            pass"),
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
