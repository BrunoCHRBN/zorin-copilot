"""Validação por mutação da Entrega D / P1-8 (watchdog de THINKING).

Motivo de existir: o watchdog antigo era um `asyncio.sleep(12.0)` fixo que **liberava o
microfone** no meio da geração. Todo defeito desta entrega é silencioso por construção —
voltar ao timer fixo, ignorar o `interaction_status` do servidor, ou trocar a mensagem
honesta por "Ouvindo você..." **não quebra nada visível**: o app segue funcionando, apenas
volta a cortar turnos longos e a mentir sobre o que aconteceu. Só a mutação prova que os
testes pegariam isso de volta.

Cobrem W1 (timeout adaptativo) e W4-W6 (IN_PROGRESS, nudge, liberação honesta).

Como rodar (da raiz do repo):
    .venv/bin/python tests/mutacao/mutate_thinking_watchdog.py

Antes, rode a tranca estática (0,1 s) que confere âncora única/ausente:
    .venv/bin/python -m pytest tests/test_mutacao_anchors.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]          # tests/mutacao/ -> raiz
TESTES = [                                          # ver "Escolha dos módulos" no SKILL
    # Determinístico de propósito (0,5 s, sem WebSocket/GLib/timers reais). Fica FORA
    # `tests/test_live.py`: tem laço de WebSocket e sleeps, e o harness roda a suíte uma
    # vez por mutação.
    "tests/test_thinking_watchdog.py",
]

GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "ai" / "live.py",
        [
            (
                "W1: o timeout ignora o volume devolvido (volta a ser fixo)",
                "    extra = (chars / _CHARS_PER_EXTRA_SECOND) + (chamadas - 1)\n"
                "    return min(topo, base + extra)\n",
                "    return base\n",
            ),
            (
                "W1: o teto deixa de existir (espera ilimitada)",
                "    return min(topo, base + extra)",
                "    return base + extra",
            ),
            (
                "W4: o watchdog ignora o interaction_status do servidor (desmente o IN_PROGRESS)",
                "            if (\n"
                "                getattr(self, \"_interaction_status\", \"IDLE\") == \"IN_PROGRESS\"\n"
                "                and in_progress_waits < _MAX_IN_PROGRESS_WAITS\n"
                "            ):\n"
                "                in_progress_waits += 1\n"
                "                logger.debug(\n"
                "                    \"Watchdog: servidor em IN_PROGRESS (%d/%d); mantendo THINKING.\",\n"
                "                    in_progress_waits, _MAX_IN_PROGRESS_WAITS,\n"
                "                )\n"
                "                continue\n",
                "",
            ),
            (
                "W6: a liberação volta a mentir ('Ouvindo você...' em vez de dizer que não houve resposta)",
                "                \"Sem resposta do modelo — pode falar de novo.\",",
                "                \"Ouvindo você...\",",
            ),
            (
                "W5: a continuação deixa de ser pedida (libera no primeiro estouro)",
                "        max_nudges = max(0, int(getattr(cfg, \"live_thinking_max_nudges\", 2)))",
                "        max_nudges = 0",
            ),
            (
                "W6: falha ao pedir continuação prende o estado em THINKING (mic fechado)",
                "                except Exception as exc:\n"
                "                    # Não interrompe o laço: o nudge já foi descontado, então as próximas\n"
                "                    # voltas esgotam e caem na liberação honesta. Sair daqui deixaria o\n"
                "                    # estado preso em THINKING — com o microfone fechado.\n"
                "                    logger.warning(\"Watchdog: falha ao pedir continuação: %s\", exc)\n"
                "                continue\n",
                "                except Exception as exc:\n"
                "                    logger.warning(\"Watchdog: falha ao pedir continuação: %s\", exc)\n"
                "                    break\n"
                "                continue\n",
            ),
            (
                "W5: o watchdog deixa de notar que a resposta já está na fila de áudio",
                "            if hasattr(self, \"_audio_play_queue\") and not self._audio_play_queue.empty():\n"
                "                return  # já há áudio na fila: a resposta chegou\n",
                "",
            ),
            (
                "W3: o watchdog nunca é armado (a CHAMADA apagada — lacuna de fiação clássica)",
                "        try:\n"
                "            asyncio.create_task(self._thinking_watchdog(ws, timeout))\n"
                "        except Exception:\n"
                "            pass\n",
                "",
            ),
            (
                "W3: o watchdog é armado com a base fixa, ignorando o timeout calculado",
                "            asyncio.create_task(self._thinking_watchdog(ws, timeout))",
                "            asyncio.create_task(self._thinking_watchdog(ws, 12.0))",
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
