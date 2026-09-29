"""Validação por mutação da Entrega F / P1-6 (ritmo adaptativo do stream de vídeo).

Motivo de existir: o defeito desta entrega é uma **regressão de ritmo**. Voltar
`time.sleep(frame_delay)` para `time.sleep(base_delay)` deixa a função pura intacta e verde —
o produto apenas volta a amostrar 1 quadro/s numa tela parada e a picotar uma tela em
movimento. Nada quebra, nenhum erro aparece, e nenhum teste da função pura pega. Só o harness
prova que a **fiação** está coberta.

Cobrem W1 (função pura: limiar, fatores, piso/teto, ausência de diff) e W3 (fiação do laço +
captura sob demanda em resolução nativa).

Como rodar (da raiz do repo):
    .venv/bin/python tests/mutacao/mutate_video_pacing.py

Antes, rode a tranca estática (0,1 s) que confere âncora única/ausente:
    .venv/bin/python -m pytest tests/test_mutacao_anchors.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]          # tests/mutacao/ -> raiz
TESTES = [
    # Determinístico de propósito (~0,5 s). Exercita a função pura E a fiação do laço
    # (uma volta, com inspetor/captura/sleep dublados) — então cobre os dois arquivos-alvo.
    "tests/test_video_pacing.py",
]

GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "ai" / "live.py",
        [
            (
                "W1: o `diff` deixa de dosar o ritmo (fator fixo em 1.0)",
                "    factor = moving_factor if d >= MOVING_FRAME_THRESHOLD else static_factor",
                "    factor = 1.0",
            ),
            (
                "W1: o limiar de movimento inverte (parado acelera, movimento desacelera)",
                "    factor = moving_factor if d >= MOVING_FRAME_THRESHOLD else static_factor",
                "    factor = static_factor if d >= MOVING_FRAME_THRESHOLD else moving_factor",
            ),
            (
                "W1: o fator de movimento some (movimento deixa de acelerar)",
                "VIDEO_MOVING_DELAY_FACTOR = 0.5",
                "VIDEO_MOVING_DELAY_FACTOR = 1.0",
            ),
            (
                "W1: sem diff passa a acelerar em vez de manter a taxa pedida",
                "    if diff is None:\n"
                "        return base\n",
                "    if diff is None:\n"
                "        return min(max_delay, max(min_delay, base * moving_factor))\n",
            ),
            (
                "W1: o teto do intervalo some (espera ilimitada)",
                "    return min(max_delay, max(min_delay, base * factor))",
                "    return max(min_delay, base * factor)",
            ),
            (
                "W1: o piso do intervalo some (ritmo ilimitado)",
                "    return min(max_delay, max(min_delay, base * factor))",
                "    return min(max_delay, base * factor)",
            ),
            (
                "W3: a CHAMADA adaptativa é trocada pelo base fixo no laço",
                "                        frame_delay = adaptive_video_delay(diff, base_delay)",
                "                        frame_delay = base_delay",
            ),
            (
                "W3: o laço dorme o base e ignora o intervalo calculado",
                "            time.sleep(frame_delay)",
                "            time.sleep(base_delay)",
            ),
            (
                "W3: o stream contínuo passa a capturar em resolução nativa (compete com o áudio)",
                "                        max_size=STREAM_CAPTURE_MAX_SIZE,\n"
                "                        quality=STREAM_CAPTURE_QUALITY,\n",
                "                        max_size=FULL_CAPTURE_MAX_SIZE,\n"
                "                        quality=FULL_CAPTURE_QUALITY,\n",
            ),
            (
                "W3: capture_screen deixa de pedir resolução nativa",
                "                        max_size=FULL_CAPTURE_MAX_SIZE,\n"
                "                        quality=FULL_CAPTURE_QUALITY,\n",
                "                        max_size=1280,\n"
                "                        quality=85,\n",
            ),
            (
                "W3: o caminho default do capture_screen passa a usar full-res (quebra o de sempre)",
                "                    ok, img_bytes, mode = ScreenCaptureService.capture(interactive=interactive)",
                "                    ok, img_bytes, mode = ScreenCaptureService.capture(\n"
                "                        interactive=interactive,\n"
                "                        max_size=FULL_CAPTURE_MAX_SIZE,\n"
                "                        quality=FULL_CAPTURE_QUALITY,\n"
                "                    )",
            ),
            (
                "W3: a declaração deixa de anunciar o parâmetro full_resolution",
                '                        "full_resolution": {',
                '                        "_full_resolution_disabled": {',
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
