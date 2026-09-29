# Decisão de design: "abre o quadro e mostra a fatoração" é **navegação de UI**,
# não uma ação de desktop. O IntentEngine local (Qwen) só produz `ActionPlan` com
# `ActionType`, então não há como expressá-la lá; no caminho Gemini Live o mesmo
# comando é resolvido como *ferramenta* (`study_show_board`). Este módulo dá ao
# caminho de voz local o mesmo alcance, com um detector **puro**: sem GTK, sem
# áudio, sem rede, sem modelo. É o análogo de
# `IntentEngine._resolve_calendar_or_math` — um atalho determinístico que ganha
# do modelo quando casa, e não custa nada quando não casa.
#
# O detector é deliberadamente **conservador**: um falso positivo aqui desvia um
# pedido legítimo do IntentEngine (o usuário pede uma coisa e o app abre um
# quadro). "quadro" sozinho é ambíguo — "quadro de funcionários", "quadro
# elétrico" —, então exige verbo de exibição; "quadro negro"/"lousa" são
# inequívocos e dispensam o verbo.

"""Detecção do comando de voz do Quadro Negro (puro e determinístico)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .board_nav import fold

#: Termos inequívocos: bastam por si.
_STRONG_TERMS = ("quadro negro", "quadro-negro", "lousa", "blackboard")

#: Termos ambíguos: só valem acompanhados de verbo de exibição.
_WEAK_TERMS = ("quadro",)

#: "quadro" seguido de um destes NÃO é o nosso quadro.
_NOT_OUR_BOARD = (
    "quadro eletrico", "quadro elétrico", "quadro de funcionarios",
    "quadro de funcionários", "quadro de avisos", "quadro de horarios",
    "quadro de horários", "quadro de dados", "quadro de distribuicao",
    "quadro de distribuição", "quadro branco",
)

#: Verbos de exibição/abertura. "mostra a fatoração" também vale — é o usuário
#: pedindo para *ver* algo no quadro.
_SHOW_VERBS = (
    "abre", "abrir", "abra", "abre", "abri",
    "mostra", "mostrar", "mostre",
    "exibe", "exibir", "exiba",
    "destaca", "destacar", "destaque",
    "coloca", "colocar", "coloque",
    "poe", "poem", "manda",
    "ve", "ver", "visualiza", "visualizar",
)

#: Palavras que sobram depois de tirar o verbo e o termo do quadro. Não carregam
#: intenção — são só a cola da frase. Só valem **nas pontas** do resíduo: no meio,
#: "de"/"da" fazem parte do rótulo ("fórmula de bhaskara") e tirá-los impediria o
#: casamento com o nó. Ver `_extract_focus`.
_NOISE_WORDS = frozenset({
    "o", "a", "os", "as", "um", "uma",
    "no", "na", "nos", "nas", "em",
    "de", "do", "da", "dos", "das",
    "e", "que", "para", "pra", "por", "favor",
    "me", "ai", "la", "aqui", "agora", "entao",
    "quero", "gostaria", "poderia", "pode", "faz",
})


@dataclass(frozen=True)
class BoardCommand:
    """O que abrir no Quadro Negro, extraído de uma frase falada.

    - `problem`: a frase **crua** quando ela carrega uma equação de verdade; o
      `try_solve` extrai a equação do meio da prosa. Vazio quando o usuário só
      quer *ver* o quadro (aí vale a última solução da conversa).
    - `focus`: o resíduo depois de tirar a invocação — candidato a alvo de
      destaque. Quem decide o nó é `board_nav.find_node`; se não casar nada, o
      quadro só abre no enunciado. Vazio quando a frase traz um problema novo
      (aí o resíduo é a própria equação, não um alvo).
    """

    problem: str = ""
    focus: str = ""


def _has_equation(text: str) -> bool:
    """Há uma equação de verdade (não apenas uma palavra matemática)?

    Mais estrito que `math_solver.looks_like_math`, de propósito: aquele decide
    se vale *oferecer* o botão do quadro (e por isso aceita "fatoração" como
    sinal), mas aqui a decisão é mandar a frase ao resolvedor — e mandar
    "mostra a fatoração no quadro" ao modelo produziria um quadro inventado.
    """
    folded = fold(text)
    return "=" in folded and any(ch.isdigit() for ch in folded)


def _has_show_verb(folded: str) -> bool:
    return any(re.search(rf"\b{re.escape(v)}\b", folded) for v in _SHOW_VERBS)


def _extract_focus(folded: str) -> str:
    """Resíduo da frase depois de tirar a invocação do quadro.

    Não decide *qual* nó: devolve o resíduo e deixa `board_nav.find_node`
    resolver (ele já casa rótulo, subtítulo e "passo N"). Resíduo vazio
    significa "só abre o quadro".
    """
    text = folded
    for term in _STRONG_TERMS + _WEAK_TERMS:
        text = text.replace(term, " ")
    for verb in _SHOW_VERBS:
        text = re.sub(rf"\b{re.escape(verb)}\b", " ", text)
    words = [w for w in re.split(r"[^\w²³-]+", text) if w]
    # Tira a cola só das **pontas**: no meio, "de"/"da" fazem parte do rótulo do
    # nó ("fórmula de bhaskara") e removê-los faria `find_node` errar o alvo.
    while words and words[0] in _NOISE_WORDS:
        words.pop(0)
    while words and words[-1] in _NOISE_WORDS:
        words.pop()
    return " ".join(words).strip()


def parse_board_command(text: str) -> Optional[BoardCommand]:
    """Traduz uma frase falada em `BoardCommand`, ou `None` se não for o caso.

    Regras (todas determinísticas):
      1. precisa citar o quadro — por termo inequívoco, ou por "quadro" **com**
         verbo de exibição (senão "o quadro está lento" abriria um quadro);
      2. o termo não pode ser uma das expressões homônimas (`_NOT_OUR_BOARD`);
      3. termo inequívoco no **começo** da frase vale como vocativo
         ("quadro negro, resolve isso"), sem exigir verbo.
    """
    folded = fold(text)
    if not folded:
        return None
    if any(bad in folded for bad in _NOT_OUR_BOARD):
        return None

    has_strong = any(term in folded for term in _STRONG_TERMS)
    starts_with_strong = any(folded.startswith(term) for term in _STRONG_TERMS)
    has_weak = any(term in folded for term in _WEAK_TERMS)
    if not (has_strong or has_weak):
        return None

    if not (_has_show_verb(folded) or starts_with_strong):
        return None

    problem = text.strip() if _has_equation(text) else ""
    focus = "" if problem else _extract_focus(folded)
    return BoardCommand(problem=problem, focus=focus)
