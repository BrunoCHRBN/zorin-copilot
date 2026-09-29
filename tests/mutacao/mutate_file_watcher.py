"""Validação por mutação do monitor de arquivos (`core/file_watcher.py`).

Motivo de existir: a suíte deste módulo tinha 2 testes, e o de debounce assertava
sobre uma **cópia** do laço do worker escrita no próprio teste — passava com o worker
certo e com o worker quebrado. Depois da reescrita (2026-09-21), este harness é o que
prova que os testes novos mordem, em vez de só existirem.

**Como rodar** (da raiz do repo):

    .venv/bin/python tests/mutacao/mutate_file_watcher.py

Mesmas quatro categorias de sempre: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: Raiz do repo derivada do próprio arquivo, não cravada: o harness mora em
#: `tests/mutacao/`, então a raiz são dois níveis acima.
RAIZ = Path(__file__).resolve().parents[2]
ALVO = RAIZ / "src" / "zorin_copilot" / "core" / "file_watcher.py"
TESTES = ["tests/test_file_watcher.py"]

#: (nome, de, para)
MUTACOES: list[tuple[str, str, str]] = [
    # -- o filtro de entrada ---------------------------------------------
    ("filtro: extensão deixa de ser checada",
     "        if ext not in SUPPORTED_EXTENSIONS:\n            return", "        if False:\n            return"),
    ("filtro: caminho vazio deixa de ser checado",
     "        if not path_str:\n            return", "        if False:\n            return"),
    # -- o caminho de exclusão -------------------------------------------
    ("exclusão: deixa de remover do índice",
     "            self.rag.delete_document(fpath)", "            pass"),
    ("exclusão: deixa de avisar o consumidor",
     '                self.on_event_callback("delete", path_str)', "                pass"),
    ("exclusão: não cancela o agendamento pendente",
     "                self._pending_updates.pop(path_str, None)", "                None"),
    ("exclusão: comparação exata em vez de substring",
     'if "deleted" in ev_name.lower():', 'if ev_name == "deleted":'),
    # -- o agendamento ---------------------------------------------------
    ("agendamento: agenda para agora, sem debounce",
     "time.time() + self.debounce_seconds", "time.time()"),
    # -- o worker --------------------------------------------------------
    ("worker: deixa de exigir que o arquivo exista",
     "                if p.exists() and p.is_file():", "                if True:"),
    ("worker: avisa o consumidor mesmo quando não indexou",
     "                        if ok:", "                        if True:"),
    ("worker: exceção na indexação propaga e mata a thread",
     "                    except Exception as exc:", "                    except (ValueError,) as exc:"),
    ("worker: laço sem condição de parada",
     "        while self._is_running:", "        while True:"),
    # -- parada ----------------------------------------------------------
    ("parada: não baixa a bandeira",
     "        self._is_running = False\n        for mon in self._monitors:", "        for mon in self._monitors:"),
    ("parada: não espera a thread do worker",
     "        if thread is not None and thread.is_alive():\n            thread.join(timeout=2.0)",
     "        if False:\n            thread.join(timeout=2.0)"),
    ("parada: não limpa a lista de monitores",
     "        self._monitors.clear()", "        pass"),
    ("parada: não descarta o trabalho pendente",
     "        with self._lock:\n            self._pending_updates.clear()", "        pass"),
    ("parada: exceção ao cancelar monitor propaga",
     "            except Exception:\n                pass", "            except (ValueError,):\n                pass"),
    # -- arranque --------------------------------------------------------
    ("arranque: sobe mesmo sem Gio",
     "        if not HAS_GIO:\n            logger.warning(", "        if False:\n            logger.warning("),
    ("arranque: deixa de ser idempotente",
     "        if self._is_running:\n            return True", "        if False:\n            return True"),
]

#: Sobreviventes que **não** são lacuna: a mutação não muda comportamento observável.
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
    original = ALVO.read_text(encoding="utf-8")
    matadas: list[str] = []
    sobreviveram: list[str] = []
    invalidas: list[str] = []

    try:
        for nome, de, para in MUTACOES:
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
            ALVO.write_text(mutado, encoding="utf-8")
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
                ALVO.write_text(original, encoding="utf-8")
    finally:
        ALVO.write_text(original, encoding="utf-8")

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
