"""Validação por mutação da Entrega B / WS2 (buffer de áudio na nuvem, P0-4).

Motivo de existir: o WS2 troca o **acoplamento** captura↔WebSocket por uma bomba de
captura persistente + ring buffer. Se um refactor futuro devolver a bomba para dentro
do laço de tentativas, ou deixar de fechar o gravador no fim da sessão, **nada quebra
de forma visível** — o app segue funcionando, apenas volta a perder o áudio do
intervalo de backoff e a vazar um `pw-record` por reconexão. Os quatro defeitos abaixo
são silenciosos por natureza; só a mutação prova que os testes os pegariam de volta.

Cobrem N1 (ring buffer), N2 (bomba 1× por sessão lógica) e N4 (sem órfão de gravador).

Como rodar (da raiz do repo):
    .venv/bin/python tests/mutacao/mutate_ws2_buffer.py

Antes, rode a tranca estática (0,1 s) que confere âncora única/ausente:
    .venv/bin/python -m pytest tests/test_mutacao_anchors.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]          # tests/mutacao/ -> raiz
TESTES = [                                          # ver "Escolha dos módulos" no SKILL
    # Determinístico (~1,6 s) e cobre os quatro contratos sozinho. Fica FORA
    # `tests/test_fase4_live_robustness.py`: ele guarda o espelho de `_bare_client()`
    # (atributos que `_live_session` consome), mas não mata nenhuma das mutações abaixo
    # — incluí-lo só somaria tempo a cada execução do harness.
    "tests/test_ws2_buffer.py",
]

GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "core" / "audio" / "buffer.py",
        [
            (
                "N1: buffer cresce sem limite (volta o vetor de OOM na máquina sem swap)",
                "            self._chunks.append(chunk)\n"
                "            self._total += len(chunk)\n"
                "            while self._total > self._cap_bytes and len(self._chunks) > 1:\n"
                "                self._total -= len(self._chunks.popleft())\n",
                "            self._chunks.append(chunk)\n"
                "            self._total += len(chunk)\n",
            ),
        ],
    ),
    (
        RAIZ / "src" / "zorin_copilot" / "ai" / "live.py",
        [
            (
                "N2: a bomba de captura nunca sobe (captura e envio voltam a não existir)",
                "        self._capture_task = asyncio.create_task(self._capture_pump())\n",
                "",
            ),
            (
                "N2: a bomba sobe a cada tentativa (o acoplamento antigo; deixa task órfã)",
                "        self._pcm_buffer.clear()\n"
                "        self._capture_task = asyncio.create_task(self._capture_pump())\n"
                "\n"
                "        attempt = 0\n"
                "        try:\n"
                "            while self._is_running:\n"
                "                attempt += 1\n",
                "        self._pcm_buffer.clear()\n"
                "\n"
                "        attempt = 0\n"
                "        try:\n"
                "            while self._is_running:\n"
                "                attempt += 1\n"
                "                self._capture_task = asyncio.create_task(self._capture_pump())\n",
            ),
            (
                "N4: o gravador não é fechado no fim da sessão (vaza um pw-record por reconexão)",
                "                except Exception:\n"
                "                    pass\n"
                "            self._close_record_proc()\n"
                "\n"
                "    def _a11y_unavailable_msg",
                "                except Exception:\n"
                "                    pass\n"
                "\n"
                "    def _a11y_unavailable_msg",
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
