"""Validação por mutação do cancelamento do Modo Agente (engrenagem que não para).

Alvo: `ui/app.py` (Frentes A, B e C) e `ai/agent.py` (Defeito 1 / aborto
cooperativo). Cada mutação reintroduz um dos defeitos da
`docs/PROPOSTA_INTERRUPCAO_AGENTE.md` e exige que a suíte o mate.

**Como rodar** (da raiz do repo, sob xvfb — os testes de GUI precisam de
display, ver `README.md`):

    xvfb-run -a .venv/bin/python tests/mutacao/mutate_agent_cleanup.py
    # só o grupo do núcleo (sem GTK), útil para checagem rápida:
    .venv/bin/python tests/mutacao/mutate_agent_cleanup.py --alvo agent.py

Categorias: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.

Meta: 9 KILLED / 0 sobreviventes / 0 INVÁLIDAS.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

APP = RAIZ / "src" / "zorin_copilot" / "ui" / "app.py"
SIDEBAR = RAIZ / "src" / "zorin_copilot" / "ui" / "widgets" / "sidebar.py"
PROMPT_BAR = RAIZ / "src" / "zorin_copilot" / "ui" / "widgets" / "prompt_bar.py"
AGENT = RAIZ / "src" / "zorin_copilot" / "ai" / "agent.py"

TESTES_POR_ALVO = {
    APP: ["tests/test_agent_gui.py"],
    SIDEBAR: ["tests/test_agent_gui.py"],
    PROMPT_BAR: ["tests/test_agent_gui.py"],
    AGENT: ["tests/test_agent_loop.py"],
}

#: (arquivo, [(nome, de, para)])
GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        APP,
        [
            # Frente A.1 / Defeito 3: o finally que sempre chama _on_agent_finished
            # no start_agent_execution deixa de existir -> setup quebra prende a UI.
            (
                "start: cleanup do agent_thread removido",
                '                GLib.idle_add(self._on_agent_finished, result, objective, agent_widget, err)\n',
                '                pass  # cleanup inevitável removido (defeito 3)\n',
            ),
            # Frente A.1 / Defeito 3 (caminho de continuação).
            (
                "continue: cleanup do continue_thread removido",
                '                GLib.idle_add(self._on_agent_finished, result, objective, agent_widget, err, True)\n',
                '                pass  # cleanup inevitável removido (defeito 3)\n',
            ),
            # Frente B.2 / Defeito 2: a guarda contra resultado obsoleto em
            # _on_agent_finished desaparece -> trocar de conversa grava lixo.
            (
                "on_agent_finished: guarda de resultado obsoleto removida",
                '        active_widget = self._active_agent_widget\n        if widget is not active_widget:\n',
                '        # guarda de resultado obsoleto removida (defeito 2)\n',
            ),
            # Frente B / Defeito 2: nova conversa deixa de abortar o agente.
            (
                "new_topic: cancelamento ao trocar conversa removido",
                '        self.abort_active_agent()  # transicao: nova conversa cancela o agente\n'
                '        self._teardown_active_agent_ui()  # transicao: nova conversa libera a UI\n',
                '        # transicao: nova conversa (cancelamento removido)\n',
            ),
            # Frente B / Defeito 2: retomar conversa deixa de abortar o agente.
            (
                "resume_topic: cancelamento ao retomar conversa removido",
                '        self.abort_active_agent()  # transicao: retomar conversa cancela o agente\n'
                '        self._teardown_active_agent_ui()  # transicao: retomar conversa libera a UI\n',
                '        # transicao: retomar conversa (cancelamento removido)\n',
            ),
            # Frente A.3 / Defeito 1: o watchdog para de liberar a UI no limite.
            (
                "watchdog: liberacao da UI no limite removida",
                '        logger.warning("Watchdog do Modo Agente: execução excedeu o limite interno.")\n'
                '        if self._active_agent_loop is not None:\n'
                '            self._active_agent_loop.abort()\n'
                '        self._is_busy = False\n'
                '        self._active_agent_loop = None\n'
                '        self._active_agent_widget = None\n'
                '        self.prompt_bar.set_busy(False)\n',
                '        logger.warning("Watchdog do Modo Agente: execução excedeu o limite interno.")\n'
                '        if self._active_agent_loop is not None:\n'
                '            self._active_agent_loop.abort()\n',
            ),
        ],
    ),
    (
        SIDEBAR,
        [
            # Frente B / Defeito 2: limpar histórico deixa de abortar o agente.
            (
                "clear_history: cancelamento ao limpar historico removido",
                '        self.ctx.abort_active_agent()  # transicao: limpar historico cancela o agente\n'
                '        self.ctx._teardown_active_agent_ui()  # transicao: limpar historico libera a UI\n',
                '        # transicao: limpar historico (cancelamento removido)\n',
            ),
        ],
    ),
    (
        PROMPT_BAR,
        [
            # Frente C / Defeito 4: submit volta a engolir o Enter quando busy.
            (
                "submit: feedback ao enviar ocupado removido",
                '        if ctx._is_busy:\n'
                '            # Defeito 4 / Frente C: feedback honesto em vez de engolir o Enter.\n'
                '            self.ctx.show_toast("Aguarde a operação atual terminar (Esc interrompe).")\n'
                '            return\n',
                '        if ctx._is_busy:\n'
                '            return\n',
            ),
        ],
    ),
    (
        AGENT,
        [
            # Defeito 1 / Frente A.1: o cheque de aborto no topo do while some.
            # Planejador que dorme deixa de ser interrompido -> loop não para.
            (
                "agent_loop: cheque de aborto no topo do while removido",
                '                if self._abort.is_set():\n',
                '                if False:\n',
            ),
        ],
    ),
]

#: Sobreviventes que NÃO são lacuna. MEDIDO, não presumido.
EQUIVALENTES: dict[str, str] = {}


def rodar_testes(testes: list[str]) -> bool:
    r = subprocess.run(
        [str(RAIZ / ".venv" / "bin" / "python"), "-m", "pytest", "-q", "-p", "no:cacheprovider", *testes],
        cwd=str(RAIZ),
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def main(alvo_filtro: str | None = None) -> int:
    matadas: list[str] = []
    sobreviveram: list[str] = []
    invalidas: list[str] = []

    for alvo, mutacoes in GRUPOS:
        if alvo_filtro and alvo_filtro not in str(alvo):
            continue
        testes = TESTES_POR_ALVO[alvo]
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
                    if rodar_testes(testes):
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
    filtro = sys.argv[1] if len(sys.argv) > 1 else None
    # aceita `--alvo agent.py` ou apenas `agent.py`
    if filtro and filtro.startswith("--alvo"):
        filtro = filtro.split("=", 1)[-1] if "=" in filtro else (sys.argv[2] if len(sys.argv) > 2 else None)
    sys.exit(main(filtro))
