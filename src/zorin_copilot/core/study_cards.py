# Decisão de design: este módulo é a ponte entre o material capturado (Fase A) e o
# estudo ativo (Fase B). Três decisões sustentam o arquivo:
#
#   1. O PROMPT é o v2 endurecido, vindo do uso real no study-hub: proíbe card de dado
#      burocrático ("qual o código do curso?"), exige que o card valha o tempo de revisão
#      e AUTORIZA {"cards": []} — devolver vazio é melhor que inventar card trivial.
#   2. O AGENDAMENTO é SM-2 puro e determinístico (nota + estado = próxima data).
#      Nenhuma decisão de revisão passa pelo modelo: é a regra que impede o LLM de
#      "achar" que você já sabe.
#   3. O ID do card deriva da FRENTE, então regenerar o deck a partir do mesmo material
#      não zera o histórico de revisão (carry-over de estado).
#
# Nada aqui fala com a internet direto: o modelo entra por injeção (`StudyAI`), o que
# mantém os testes headless e determinísticos.

"""Flashcards e revisão espaçada (SM-2) para o material de estudo capturado."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

#: Abaixo disso o material não sustenta flashcards: o modelo começa a inventar.
MIN_SOURCE_WORDS = 80

#: Acima disso cortamos o trecho — contexto de modelo local é caro e a cauda
#: quase sempre é rodapé/navegação.
MAX_SOURCE_CHARS = 6000

#: Versão do prompt embutida no deck (permite comparar regenerações).
PROMPT_VERSION = "2"

# --- constantes SM-2 (SuperMemo 1987, variante Anki) ---
MIN_EASINESS = 1.3
INITIAL_EASINESS = 2.5
PASS_GRADE = 3
INTERVAL_FIRST = 1
INTERVAL_SECOND = 6

#: Escala mostrada ao aluno na hora de revisar.
GRADE_SCALE = [
    (0, "apagou total"),
    (1, "errou, mas reconheceu a resposta"),
    (2, "errou, resposta pareceu familiar"),
    (3, "acertou com muita dificuldade"),
    (4, "acertou depois de hesitar"),
    (5, "resposta imediata"),
]

STUDY_SYSTEM_PROMPT = """Você é um gerador de flashcards para um aluno de Gestão Comercial \
(ensino superior, EAD).

Regras obrigatórias:
1. Atomicidade: cada card testa UM único fato, conceito ou relação.
2. A frente é uma pergunta direta e autocontida — nunca "segundo o texto", nunca dependa \
de contexto fora do card.
3. O verso é a resposta completa e concisa (1 a 3 frases).
4. Não invente informação ausente no trecho. Se o trecho for raso, produza menos cards.
5. Varie o tipo de card: definição, comparação, aplicação prática, exemplo, causa-efeito.
6. Não crie cards sobre a estrutura do material ("o que a seção X apresenta?").
7. NUNCA crie cards sobre dados burocráticos ou de navegação: código, nome ou carga \
horária do curso, número da aula, nome de professor, prazos, avisos, instruções de \
acesso, menus, links, sumário.
8. Cada card precisa valer o tempo de revisão do aluno. Descarte o card se a resposta \
for só um número, um código, um nome próprio isolado ou qualquer dado que se decora \
sem entender.
9. Se o trecho não tiver NENHUM conteúdo de estudo, devolva {"cards": []}. Devolver \
vazio é melhor que inventar card trivial.
10. tags: 1 a 3 etiquetas curtas, minúsculas, sem acento, com hífen (ex: "mix-de-marketing").
11. Responda APENAS com JSON válido, sem markdown e sem texto fora do JSON:
{"cards": [{"front": "pergunta", "back": "resposta", "tags": ["etiqueta"]}]}"""

USER_TEMPLATE = """Disciplina: {discipline}
Material: {title}

Gere até {n_cards} flashcards a partir do trecho abaixo:

\"\"\"
{text}
\"\"\""""


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #


@dataclass
class Card:
    """Um flashcard com seu estado de revisão (SM-2)."""

    id: str
    front: str
    back: str
    tags: list[str] = field(default_factory=list)
    repetitions: int = 0
    easiness: float = INITIAL_EASINESS
    interval_days: int = 0
    last_reviewed: str | None = None
    next_review: str = ""

    # -- agendamento --
    def is_due(self, today: date | None = None) -> bool:
        day = (today or date.today()).isoformat()
        return not self.next_review or self.next_review <= day

    def apply_grade(self, quality: int, today: date | None = None) -> None:
        """Aplica uma nota 0–5 e atualiza o agendamento (SM-2)."""
        state = grade_card(
            {
                "repetitions": self.repetitions,
                "easiness": self.easiness,
                "interval_days": self.interval_days,
            },
            quality,
            today=today,
        )
        self.repetitions = state["repetitions"]
        self.easiness = state["easiness"]
        self.interval_days = state["interval_days"]
        self.last_reviewed = state["last_reviewed"]
        self.next_review = state["next_review"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "front": self.front,
            "back": self.back,
            "tags": list(self.tags),
            "repetitions": self.repetitions,
            "easiness": self.easiness,
            "interval_days": self.interval_days,
            "last_reviewed": self.last_reviewed,
            "next_review": self.next_review,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Card":
        return cls(
            id=str(data.get("id") or content_hash(str(data.get("front") or ""))),
            front=str(data.get("front") or ""),
            back=str(data.get("back") or ""),
            tags=list(data.get("tags") or []),
            repetitions=int(data.get("repetitions") or 0),
            easiness=float(data.get("easiness") or INITIAL_EASINESS),
            interval_days=int(data.get("interval_days") or 0),
            last_reviewed=data.get("last_reviewed"),
            next_review=str(data.get("next_review") or ""),
        )


@dataclass
class Deck:
    """Um baralho de estudo ligado a um material de origem."""

    id: str
    title: str
    source: str = ""
    discipline: str = "Gestão Comercial"
    created_at: str = ""
    prompt_version: str = PROMPT_VERSION
    provider: str = ""
    cards: list[Card] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "discipline": self.discipline,
            "created_at": self.created_at,
            "prompt_version": self.prompt_version,
            "provider": self.provider,
            "cards": [card.to_dict() for card in self.cards],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Deck":
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            source=str(data.get("source") or ""),
            discipline=str(data.get("discipline") or "Gestão Comercial"),
            created_at=str(data.get("created_at") or ""),
            prompt_version=str(data.get("prompt_version") or PROMPT_VERSION),
            provider=str(data.get("provider") or ""),
            cards=[Card.from_dict(c) for c in (data.get("cards") or [])],
        )


# --------------------------------------------------------------------------- #
# SM-2 (puro)
# --------------------------------------------------------------------------- #


def initial_state(today: date | None = None) -> dict[str, Any]:
    """Estado de um card novo (vence hoje, entra na fila imediatamente)."""
    day = today or date.today()
    return {
        "repetitions": 0,
        "easiness": INITIAL_EASINESS,
        "interval_days": 0,
        "last_reviewed": None,
        "next_review": day.isoformat(),
    }


def grade_card(
    state: dict[str, Any], quality: int, *, today: date | None = None
) -> dict[str, Any]:
    """Aplica nota 0–5 ao estado SM-2 e devolve o estado novo (não muta o antigo).

    Determinístico de propósito: a data da próxima revisão nunca depende do modelo.
    """
    if isinstance(quality, bool) or not isinstance(quality, int) or not 0 <= quality <= 5:
        raise ValueError(f"nota SM-2 deve ser inteiro de 0 a 5, recebido: {quality!r}")
    day = today or date.today()

    easiness = state["easiness"] + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    easiness = max(MIN_EASINESS, round(easiness, 4))

    if quality >= PASS_GRADE:
        repetitions = state["repetitions"] + 1
        if repetitions == 1:
            interval = INTERVAL_FIRST
        elif repetitions == 2:
            interval = INTERVAL_SECOND
        else:
            interval = max(1, round(state["interval_days"] * easiness))
    else:
        repetitions = 0
        interval = INTERVAL_FIRST

    return {
        "repetitions": repetitions,
        "easiness": easiness,
        "interval_days": interval,
        "last_reviewed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "next_review": (day + timedelta(days=interval)).isoformat(),
    }


def due_cards(deck: Deck, today: date | None = None, limit: int | None = None) -> list[Card]:
    """Cards vencidos, novos primeiro (é o que destrava o baralho no começo)."""
    pending = [card for card in deck.cards if card.is_due(today)]
    pending.sort(key=lambda c: (c.repetitions > 0, c.next_review or ""))
    return pending[:limit] if limit else pending


def deck_stats(deck: Deck, today: date | None = None) -> dict[str, int]:
    day = today or date.today()
    due = sum(1 for card in deck.cards if card.is_due(day))
    new = sum(1 for card in deck.cards if card.repetitions == 0)
    learning = sum(1 for card in deck.cards if 0 < card.repetitions < 3)
    mature = sum(1 for card in deck.cards if card.repetitions >= 3)
    return {"total": len(deck.cards), "due": due, "new": new, "learning": learning, "mature": mature}


# --------------------------------------------------------------------------- #
# Geração
# --------------------------------------------------------------------------- #


def content_hash(text: str) -> str:
    """Hash estável do conteúdo (normalizado): base do id do card e do deck."""
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def make_card(front: str, back: str, tags: Sequence[str] | None = None, today: date | None = None) -> Card:
    state = initial_state(today)
    return Card(
        id=content_hash(front),
        front=" ".join(front.split()),
        back=" ".join(back.split()),
        tags=[str(t) for t in (tags or [])][:3],
        next_review=state["next_review"],
    )


#: A regra 7 do prompt v2 proíbe card burocrático, mas modelo local insiste — e foi
#: exatamente o que apareceu em uso real ("qual o código do curso de Ambientação EAD?").
#: O prompt pede; este filtro garante.
BUREAUCRATIC_PATTERNS = (
    r"c[oó]digo d[oa] (curso|disciplina|aula|turma)",
    r"carga hor[áa]ria",
    r"nome d[oa] (professor|professora|tutor|tutora)",
    r"qual (é|e) o (n[úu]mero|prazo|link) ",
    r"(clique|acesse|navegue) (em|no|na|para)",
    r"^ementa\b",
    r"^sum[áa]rio\b",
    r"^aviso\b",
)


def _is_useful(front: str, back: str) -> bool:
    """Filtro de segurança: o prompt v2 já proíbe, mas modelo local às vezes insiste."""
    if len(front.split()) < 3 or len(back.split()) < 2:
        return False
    if not re.search(r"[^\W\d_]", back, flags=re.UNICODE):
        return False  # resposta é só número/código
    if len(back) > 600:
        return False  # virou parágrafo colado do material
    pair = f"{front} {back}".lower()
    if any(re.search(pattern, pair) for pattern in BUREAUCRATIC_PATTERNS):
        return False
    return True


def parse_cards(raw: str) -> list[dict[str, Any]]:
    """Extrai a lista de cards da resposta do modelo. Nunca levanta."""
    from ..ai.agent_router import extract_json_object

    payload = extract_json_object(raw or "")
    if not isinstance(payload, dict):
        return []
    cards = payload.get("cards")
    if not isinstance(cards, list):
        return []
    return [c for c in cards if isinstance(c, dict)]


def generate_cards(
    text: str,
    ai: Any,
    *,
    title: str = "",
    discipline: str = "Gestão Comercial",
    n_cards: int = 10,
    today: date | None = None,
) -> tuple[list[Card], list[str]]:
    """Gera cards a partir de um texto. Devolve (cards, avisos)."""
    warnings: list[str] = []
    words = len((text or "").split())
    if words < MIN_SOURCE_WORDS:
        return [], [
            f"Material curto demais para flashcards ({words} palavras, mínimo {MIN_SOURCE_WORDS}). "
            "Capture mais conteúdo (ou mais de uma seção) antes de gerar."
        ]

    excerpt = text.strip()
    if len(excerpt) > MAX_SOURCE_CHARS:
        excerpt = excerpt[:MAX_SOURCE_CHARS]
        warnings.append(f"Material cortado em {MAX_SOURCE_CHARS} caracteres para geração.")

    prompt = USER_TEMPLATE.format(
        discipline=discipline, title=title or "(sem título)", n_cards=n_cards, text=excerpt
    )
    try:
        raw = ai.complete(STUDY_SYSTEM_PROMPT, prompt)
    except Exception as exc:
        return [], [f"Falha ao consultar o modelo: {exc}"]

    parsed = parse_cards(raw)
    if not parsed:
        return [], [
            "O modelo não devolveu cards utilizáveis. "
            "Pode ser material raso (prompt v2 recusa gerar card trivial) ou resposta fora do formato."
        ]

    cards: list[Card] = []
    seen: set[str] = set()
    for item in parsed:
        front = str(item.get("front") or "").strip()
        back = str(item.get("back") or "").strip()
        if not front or not back:
            continue
        if not _is_useful(front, back):
            continue
        card = make_card(front, back, item.get("tags") or [], today=today)
        if card.id in seen:
            continue
        seen.add(card.id)
        cards.append(card)
        if len(cards) >= n_cards:
            break

    if not cards:
        return [], ["Todos os cards gerados foram descartados pelo filtro de utilidade."]
    return cards, warnings


def merge_cards(existing: Sequence[Card], new: Sequence[Card]) -> list[Card]:
    """Mescla cards novos em cima dos existentes preservando o histórico (por id)."""
    by_id = {card.id: card for card in existing}
    merged: list[Card] = []
    for card in new:
        known = by_id.get(card.id)
        merged.append(known if known is not None else card)
    # cards que saíram do material novo são mantidos: o aluno já investiu revisão neles.
    known_ids = {card.id for card in new}
    merged.extend(card for card in existing if card.id not in known_ids)
    return merged


# --------------------------------------------------------------------------- #
# Persistência
# --------------------------------------------------------------------------- #


class DeckStore:
    """Baralhos em JSON, um arquivo por deck."""

    def __init__(self, directory: str | None = None) -> None:
        self.directory = directory or os.path.expanduser("~/.local/share/zorin-copilot/estudo/decks")

    def path_for(self, deck_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_\-]", "", deck_id) or "deck"
        return os.path.join(self.directory, f"{safe}.json")

    def save(self, deck: Deck) -> str:
        os.makedirs(self.directory, exist_ok=True)
        path = self.path_for(deck.id)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(deck.to_dict(), handle, ensure_ascii=False, indent=2)
        return path

    def load(self, deck_id: str) -> Deck | None:
        path = self.path_for(deck_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                return Deck.from_dict(json.load(handle))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Deck %s corrompido: %s", deck_id, exc)
            return None

    def list(self) -> list[Deck]:
        if not os.path.isdir(self.directory):
            return []
        decks: list[Deck] = []
        for name in sorted(os.listdir(self.directory)):
            if not name.endswith(".json"):
                continue
            deck = self.load(name[: -len(".json")])
            if deck is not None:
                decks.append(deck)
        return decks


def new_deck(
    title: str,
    cards: Sequence[Card],
    *,
    source: str = "",
    discipline: str = "Gestão Comercial",
    provider: str = "",
) -> Deck:
    deck_id = content_hash(f"{title}|{source}")[:10]
    return Deck(
        id=deck_id,
        title=title,
        source=source,
        discipline=discipline,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        provider=provider,
        cards=list(cards),
    )


# --------------------------------------------------------------------------- #
# Acesso ao modelo
# --------------------------------------------------------------------------- #


class StudyAI:
    """Completações de texto para estudo: local (Ollama) ou nuvem (Gemini/OpenAI).

    Falando direto com as APIs, e não via `ai.providers`, por um motivo simples:
    a camada de provedores monta o próprio system prompt de assistente de desktop
    e tenta extrair ações. Aqui precisamos de system prompt próprio e JSON puro.
    """

    def __init__(self, config: Any | None = None, provider: Any | None = None, mode: str = "auto") -> None:
        self._config = config
        self._provider = provider
        self.mode = mode
        self.used: str = ""

    @property
    def config(self) -> Any:
        if self._config is None:
            from .config import CopilotConfig

            self._config = CopilotConfig.load()
        return self._config

    def complete(self, system: str, user: str) -> str:
        if self._provider is not None:
            self.used = getattr(self._provider, "name", "injetado")
            return str(self._provider.complete(system, user))

        if self.mode in ("auto", "local"):
            try:
                text = self._ollama(system, user)
                self.used = "local"
                return text
            except Exception as exc:
                if self.mode == "local":
                    raise
                logger.debug("Ollama falhou, tentando nuvem: %s", exc)

        text = self._cloud(system, user)
        self.used = "cloud"
        return text

    def _ollama(self, system: str, user: str) -> str:
        import requests

        url = f"{str(getattr(self.config, 'ollama_url', 'http://127.0.0.1:11434')).rstrip('/')}/api/chat"
        payload = {
            "model": getattr(self.config, "ollama_model", "qwen2.5:7b"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.4, "num_predict": 2048},
            "keep_alive": "15m",
        }
        resp = requests.post(url, json=payload, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama respondeu {resp.status_code}: {resp.text[:160]}")
        return resp.json().get("message", {}).get("content", "")

    def _cloud(self, system: str, user: str) -> str:
        import requests

        key = str(getattr(self.config, "gemini_api_key", "") or "").strip()
        if key:
            model = getattr(self.config, "gemini_model", "gemini-flash-latest")
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={key}"
            )
            payload = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
            }
            resp = requests.post(url, json=payload, timeout=90)
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini respondeu {resp.status_code}: {resp.text[:160]}")
            data = resp.json()
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            return "".join(str(part.get("text", "")) for part in parts)

        openai_key = str(getattr(self.config, "openai_api_key", "") or "").strip()
        if openai_key:
            from ..ai.providers import OpenAICompatProvider

            provider = OpenAICompatProvider(
                getattr(self.config, "openai_url", "https://api.openai.com/v1"),
                openai_key,
                getattr(self.config, "openai_model", "gpt-4o-mini"),
            )
            text, _actions = provider.chat(f"{system}\n\n{user}")
            return text

        raise RuntimeError(
            "Nenhum modelo configurado: suba o Ollama (ollama serve) ou defina a chave do Gemini."
        )


def read_source(path: str) -> tuple[str, str]:
    """Lê um .md/.txt capturado. Devolve (titulo, texto sem o cabeçalho de metadados)."""
    with open(os.path.expanduser(path), encoding="utf-8", errors="replace") as handle:
        raw = handle.read()

    title = ""
    body = raw.strip()
    if raw.startswith("# "):
        first, _, rest = raw.partition("\n")
        title = first[2:].strip()
        # Remove o bloco de metadados "- chave: valor" que a captura escreve.
        lines = rest.splitlines()
        index = 0
        while index < len(lines) and not lines[index].strip():
            index += 1
        while index < len(lines) and (
            lines[index].startswith("- ") or lines[index].strip() in ("---", "***") or not lines[index].strip()
        ):
            index += 1
        body = "\n".join(lines[index:]).strip()
    return title, body
