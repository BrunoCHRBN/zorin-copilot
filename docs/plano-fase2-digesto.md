# Plano — Fase 2: o digesto

> Próximo passo concreto da `PROPOSTA_JARVIS_AMBIENTE.md` (linha 1218). Fase 1 fechada
> (P1 + P2 + P4). Este plano é a Fase 2 — tirar do log o que a P2 rebaixou.

## Tese

Trocar **gotejamento por digesto**: em vez de sete selos discretos ao longo do dia (que
ninguém vê porque estão só no log), uma fala curta no momento em que o usuário pode
prestar atenção, com o resumo do que mudou. A política de atenção (`core/attention.py`)
já foi desenhada para isto: o nível `BADGE` é o acumulador persistente, e o retorno do
`decide_attention` separa explicitamente *interromper agora* de *acumular para depois*.

## Estado medido (não deduzido)

| Peça | Onde | Estado |
|---|---|---|
| Política (pura, 4 níveis) | `core/attention.py` `decide_attention` | ✅ pronta, 78+51 testes |
| Contador de selos | `ui/app.py:_attention_badges` (linha 278) | ⚠️ só conta — **não guarda o quê** |
| Incremento sob trava | `ui/app.py:_note_badge` (linhas 725–734) + `_attention_lock` | ✅ pronto, com `threading.Lock` |
| Duas fontes somando | casa (`_on_home_ambient`) + documentos (`_on_document_event`) | ✅ (com a ressalva: doc → selo é o fim do tubo — o ganho é o índice do RAG) |
| Voz unificada | `scripts/ha_bridge.py` pedindo fala ao Copilot (`--announce`) | ✅ pronta (Fase 0 item 2) |
| Comentário honesto no código | `ui/app.py:866–870` ("consumidor visível ainda não existe") | ✅ registrado |

**O buraco:** o contador diz "3 coisas aconteceram", mas não diz **quais**. Para um
digesto com conteúdo ("a luz da sala acendeu às 18:02, o Bruno chegou às 18:11") é
necessário um **log de selos** com `(timestamp, reason, severity, text)` — e esse log
não existe.

## Design

### 1. Dado — `BadgeItem` + log limitado

Novo dataclass puro em `core/digest.py`:

```python
@dataclass(frozen=True)
class BadgeItem:
    ts: float          # monotonic time do POSIX
    reason: AttentionReason   # reusa o enum existente — mantém a causa legível no log
    severity: Severity
    text: str          # a frase que teria sido dita (já validada por decide_announcement)
```

O estado mora em `ui/app.py` como `self._badge_log: deque[BadgeItem]` sob
`self._attention_lock`, **limitado** (proposta: 200 itens). Limite existe porque o log
não é auditoria — é insumo descartável do digesto. Acima do limite, descarta do mais
antigo; o número de selos continua totalizando o contador existente.

`_note_badge()` passa a devolver o item (para que o caller possa logar com o que
aconteceu), e o ramo `should_badge` em `_on_ambient_event` (linhas 865–878) constrói
o `BadgeItem` a partir do que já tem em mãos (`attention.reason`, `attention.severity`,
`decision.text`).

### 2. Decisor (puro) — `core/digest.py`

Mesma filosofia de `core/attention.py`: **sem GTK, sem rede, sem áudio, sem relógio**
(time vem por parâmetro). Assinatura proposta:

```python
def build_digest(
    *,
    items: Sequence[BadgeItem],
    now: float,
    min_items: int = 3,
    window_seconds: float = 12 * 3600,   # 12 h cobre um dia normal
    live_session_active: bool = False,
) -> Digest | None:
    """Devolve um digesto resumido, ou None se ainda não vale a pena falar.

    Regras (todas puras, todas testáveis):
    1. `live_session_active` ⇒ None — competir com a conversa é o defeito.
    2. `len(items) < min_items` ⇒ None — falar de duas coisas é ruído.
    3. Janela: só considera itens com `now - ts <= window_seconds`.
    4. Dedup por `(reason, text)`: N repetições viram "sensor X: N detecções".
    5. Ordena por severidade (URGENT primeiro, depois NORMAL, depois ROUTINE),
       preservando a ordem temporal dentro de cada grupo.
    """
```

`Digest` carrega `headline` (≤140 caracteres — a frase falada), `detail` (a lista
completa para o recap visual), e `count` (quantos itens originais, antes do dedup).
Nenhum texto é construído por template sofisticado — é junção com vírgulas, mesmo
padrão da fala da casa.

### 3. Gatilho

Três, do mais ao menos frequente:

1. **Fim de janela de silêncio** — quando `is_within_silence` deixa de ser verdade, o
   `AttentionContext` recebe um sinal. É o gatilho natural: o que acumulou de noite
   vira fala de manhã. Implementação: observação do relógio no fio que já existe
   (`_attention_context`), sem novo temporizador (a consolidação já ensinou que timers
   em thread morrem com o processo).
2. **Manual** — comando de voz/teclado ("o que mudou?"). Casa com o que a paleta de
   comandos (`Ctrl+K`) já faz: adiciona uma entrada `what-changed` que dispara o
   digesto agora.
3. **Threshold** — opcional, fora do corte mínimo: se `len(items) >= N` (config,
   default 20) sem que nenhum gatilho acima tenha disparado, flush. É o cinto de
   segurança contra "acumulou demais e ninguém viu".

### 4. Consumidor — voz + UI

- **Voz:** reusa o caminho unificado (`zorin-copilot --announce=<headline>` via
  `scripts/ha_bridge.py`). Sem novo canal de fala.
- **UI:** um cartão **"Recap"** no painel de notificações do app (mesma área do
  toast, com o mesmo tempo de vida), contendo o `detail` completo. Aparece só
  quando há digesto — não é uma tela nova.

### 5. Regras de convivência (deliberadas)

- **Digesto não inclui itens urgentes.** `URGENT` já fala por si (política, condição
  2). Misturar fumaça com "a luz acendeu" rebaixa a urgência real.
- **Digesto não fala se já falou há menos de `min_interval_seconds` (default 90 min).**
  Duas janelas de silêncio que terminam coladas não geram dois recaps.
- **Digesto durante sessão ao vivo é `None`.** A condição 6 da política já
  rebaixa `SPEAK→NOTIFY`; o digesto segue a mesma lógica de não competir.
- **Texto original é preservado no `detail`.** O `headline` é a versão falada;
  o `detail` é a lista que o usuário lê. Perder o original no detalhe perderia
  o valor do log.

### 6. O que **não** entra nesta fase

Para manter o corte pequeno e o critério de pronto verificável:

- **`FastRNNRouter`** — continua órfão (PROPOSTA §3.1). Não toca no digesto.
- **Condição de ausência real** — sem fonte de ociosidade nesta máquina
  (`REF-ambiente.md` linha 57). `attention_away_minutes` segue sem linha na tela.
  O digesto **não depende** de ociosidade — ele é sobre *acúmulo*, não sobre
  *ausência*.
- **Persistência do log entre execuções** — o log é descartável. Se o app
  reiniciar, começa do zero. Persistir exige decidir formato, migração e TTL em
  disco — escopo separado.

## Critério de pronto (gate)

1. `core/digest.py` puro, com **≥15 testes puros** (mín itens, dedup,
   ordenação por severidade, supressão em live session, janela, vazio).
2. `ui/app.py` com `_badge_log` limitado, sob trava, com teste que
   **reintroduz a perda de selo** (duas threads concorrentes) e confirma que
   passa a falhar — restaurado.
3. Um teste de fiação que monta um dia simulado (silêncio noturno + 4 selos
   mistos + fim de silêncio) e afirma o `headline` esperado, byte a byte.
4. Manual: o comando "o que mudou?" na paleta produz o digesto e o exibe no
   recap, com screenshot real da UI (`docs/digesto-recap.png`).
5. Critério subjetivo (da proposta, linha 1003): *um dia de uso real sem
   interrupção considerada inútil* — registrado após deploy, não automatizável.

## Riscos medidos

| Risco | Mitigação |
|---|---|
| Falar digesto durante sessão ao vivo e atropelar a conversa | `live_session_active` ⇒ `None`, mesma lógica da política P2 |
| Acúmulo infinito se nada disparar | `_badge_log` limitado a 200 + gatilho por threshold |
| Selo perdido em condição de corrida | trava já existe; o teste de reintrodução confirma |
| Digesto vazio mas mesmo assim falado | `min_items` + retorno `None` |
| `headline` ambíguo ("luz mudou: 3, porta trancou: 1") | dedup por `(reason, text)`, ordenado por severidade |
| Misturar urgente com corriqueiro | `URGENT` é filtrado **fora** antes de chamar `build_digest` |

## Sequência

1. `core/digest.py` — `BadgeItem` + `build_digest` + testes puros.
2. `ui/app.py` — `_badge_log` (deque limitado, sob trava) + `_note_badge`
   devolvendo o item.
3. `ui/app.py` — gatilho de fim de janela (observação do `AttentionContext`).
4. Paleta — entrada `what-changed` que dispara digesto manual.
5. UI recap — cartão com `detail`, `headline` no toast alto.
6. Render real (`docs/digesto-recap.png`) — olho no PNG, não no teste.

Tudo testado pela suíte completa via
`~/.workbuddy-ai/skills/zorin-copilot-gui-change/scripts/run_tests_safe.py
--python .venv/bin/python` (sem swap nesta máquina — `REF-testes.md`).