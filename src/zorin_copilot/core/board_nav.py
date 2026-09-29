# Decisão de design: a navegação por voz/agente no Quadro Negro precisa escolher
# um nó a partir de uma frase falada ("destaca o passo 2", "mostra a fatoração").
# Essa lógica é pura e determinística — nada de GTK —, então mora aqui e é testável
# sem display. O widget (ui/widgets/blackboard.py) só aplica o resultado.

"""Seleção de nós do Quadro Negro a partir de uma consulta textual (voz/agente)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

# Palavras que introduzem um passo numerado: "passo 2", "etapa 2", "step 2".
_STEP_WORDS = ("passo", "etapa", "step", "número", "numero")
_STEP_RE = re.compile(r"(?:passo|etapa|step|n[uú]mero)\s*(\d+)", re.IGNORECASE)

#: Comprimento mínimo para uma palavra contar no casamento por palavra (passo 4).
#: "de", "da", "no" não identificam nó nenhum.
_MIN_WORD = 4


def _significant_words(text: Any) -> list[str]:
    """Palavras com conteúdo de `text` (dobradas, sem as curtas/cola)."""
    return [w for w in re.split(r"[^\w²³-]+", fold(text)) if len(w) >= _MIN_WORD]


def fold(text: Any) -> str:
    """Dobra para comparação: minúsculas e sem acentos, preservando pontuação.

    Não reusa `normalize_text` do wake word: aquele remove pontuação, o que
    tornaria alvos como "x² - 5x + 6" inencontráveis. Aqui só se tira acento e
    caixa, para "fatoração" casar com "fatoracao".
    """
    if text is None:
        return ""
    norm = unicodedata.normalize("NFKD", str(text))
    without_accents = "".join(c for c in norm if not unicodedata.combining(c))
    return without_accents.lower().strip()


def find_node(nodes: list[dict], query: str) -> Optional[dict]:
    """Escolhe o nó do Quadro Negro mais adequado a `query`.

    Ordem de precedência:
      1. consulta vazia -> o nó raiz (enunciado);
      2. "passo N"/"etapa N"/"step N" -> o N-ésimo nó de passo (1-based);
      3. rótulo idêntico;
      4. rótulo contendo a consulta (métodos/passos vêm antes da raiz);
      5. subtítulo contendo a consulta;
      6. último recurso: casamento por **palavra** do rótulo (o nó com mais
         palavras em comum com a consulta). Cobre o caso em que o usuário diz o
         alvo com palavras a mais — "mostra o ponto de vista geométrico" contra o
         rótulo "Geométrico (数形结合)". Só roda quando 1-5 não casaram, então
         **não altera nenhum resultado que já existia**.

    Devolve ``None`` quando nada casa (o chamador decide o que fazer).
    """
    if not nodes:
        return None
    q = fold(query)
    if not q:
        return nodes[0]

    m = _STEP_RE.search(q)
    if m:
        idx = int(m.group(1))
        steps = [n for n in nodes if n.get("type") == "step"]
        if 1 <= idx <= len(steps):
            return steps[idx - 1]

    for n in nodes:
        if fold(n.get("label")) == q:
            return n
    for n in nodes:
        if q in fold(n.get("label")):
            return n
    for n in nodes:
        if q in fold(n.get("sub")):
            return n

    words = _significant_words(q)
    if words:
        best: Optional[dict] = None
        best_score = 0
        for n in nodes:
            label_words = set(_significant_words(n.get("label")))
            score = sum(1 for w in words if w in label_words)
            if score > best_score:
                best, best_score = n, score
        if best is not None:
            return best
    return None
