"""Validação por mutação da Entrega C / P1-5 (indicador de modo + reconciliação de sinais).

Motivo de existir: os defeitos desta entrega são **mentiras de interface**. Nenhum deles
quebra, levanta exceção ou falha um teste de comportamento — a UI simplesmente afirma algo
que não é verdade. Se um refactor futuro voltar a decidir o modo por `hasattr`, ou devolver
a linha do modelo ao fallback da nuvem, **nada falha** e o usuário volta a ler "Gemini" num
modo que roda Qwen local. Só a mutação prova que os testes pegariam isso de volta.

Cobrem M3 (helpers de modo/capacidade), M5 (controles de tela), M7 (linha do modelo),
M8 (linha da cerca) e M6 (resposta honesta do toggle).

Como rodar (da raiz do repo):
    .venv/bin/python tests/mutacao/mutate_mode_indicator.py

Antes, rode a tranca estática (0,1 s) que confere âncora única/ausente:
    .venv/bin/python -m pytest tests/test_mutacao_anchors.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]          # tests/mutacao/ -> raiz
TESTES = [                                          # ver "Escolha dos módulos" no SKILL
    "tests/test_voice_pill.py",
]

GRUPOS: list[tuple[Path, list[tuple[str, str, str]]]] = [
    (
        RAIZ / "src" / "zorin_copilot" / "ui" / "voice_pill.py",
        [
            (
                "M3: _client_mode sempre responde 'cloud' (o chip mente nos dois modos)",
                '    return "local" if getattr(client, "mode", None) == "local" else "cloud"',
                '    return "cloud"',
            ),
            (
                "M3: _supports_video aceita qualquer valor truthy (mock faz o local parecer capaz de vídeo)",
                "    declared = getattr(client, \"supports_video\", None)\n"
                "    if isinstance(declared, bool):\n"
                "        return declared\n"
                "    return _client_mode(client) != \"local\"\n",
                "    declared = getattr(client, \"supports_video\", None)\n"
                "    return bool(declared) if declared is not None else _client_mode(client) != \"local\"\n",
            ),
            (
                "M5: a seção de tela volta a ser gateada por hasattr (os stubs do local passam)",
                "        supports_video = _supports_video(self.live_client)",
                "        supports_video = hasattr(self.live_client, \"toggle_video_stream\")",
            ),
            (
                "M7: a linha do modelo volta ao fallback da nuvem (diz 'Gemini' no modo local)",
                "        if self._mode == \"local\":\n"
                "            model_display = getattr(self.config, \"ollama_model\", \"modelo local\")\n"
                "        else:\n"
                "            model_name = getattr(self.live_client, \"model\", \"\") or getattr(self.config, \"gemini_live_model\", \"Gemini 2.0 Flash Live\")\n"
                "            from .live_view import live_model_label\n"
                "            model_display = live_model_label(model_name)\n",
                "        model_name = getattr(self.live_client, \"model\", \"\") or getattr(self.config, \"gemini_live_model\", \"Gemini 2.0 Flash Live\")\n"
                "        from .live_view import live_model_label\n"
                "        model_display = live_model_label(model_name)\n",
            ),
            (
                "M8: a cerca espacial volta a ser afirmada ativa sempre",
                "        fence_active = bool(getattr(self.live_client, \"fence\", None))",
                "        fence_active = True",
            ),
            (
                "M6: o toggle de tela volta a responder 'pausada' em vez de 'indisponível'",
                "        if not _supports_video(self.live_client):\n"
                "            self._show_chip(\"Compartilhamento de tela indisponível no modo local\", ok=False)\n"
                "            return\n",
                "",
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
