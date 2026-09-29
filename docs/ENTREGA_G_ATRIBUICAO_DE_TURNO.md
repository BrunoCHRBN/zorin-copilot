# Entrega G — Atribuição de turno na transcrição (lado do usuário + vocabulário único)

> Plano de ação para o **P1-7** de `docs/MELHORIAS_VOZ_TELA_AO_VIVO.md` §2.6
> (*"Diarização (P1-7)"*, risco Alto).
> Cada ponto ancora no estado real do código, **lido nesta rodada (2026-09-29)**.
>
> **STATUS: PARCIALMENTE IMPLEMENTADO (2026-09-29).** Entregas anteriores:
> `ENTREGA_A_VOZ_TELA.md` (P0-1/2/3), `ENTREGA_B_RECONEXAO.md` (P0-4),
> `ENTREGA_C_MODO_E_SINAIS.md` (P1-5), `ENTREGA_D_WATCHDOG_PENSANDO.md` (P1-8),
> `ENTREGA_E_PRIVACY_SHIELD.md` (P1-9), `ENTREGA_F_FPS_ADAPTATIVO.md` (P1-6).
>
> **Escopo declarado de antemão:** esta entrega faz **atribuição de turno** (usuário vs.
> copiloto) — o mínimo que o próprio plano autoriza (*"pelo menos marcadores de turno"*).
> Ela **não** faz diarização multi-falante (distinguir três pessoas numa reunião), que exige
> *embeddings* de voz e um modelo que o projeto não tem. Ver §7.

---

## 1. Evidência do problema (código atual, verificado)

### 1.1 Na nuvem, a transcrição **não tem o lado do usuário**

`ai/live.py:2640` é o **único** `append` em `_transcripts_log` de todo o cliente de nuvem:

```python
self._transcripts_log.append(("assistant", p["text"]))
```

E `inputTranscription` — o campo do Gemini Live que carrega a fala do **usuário** — não
aparece **em nenhum lugar** de `src/` (verificado por varredura). Ou seja: no modo nuvem o
transcrito é **só do assistente**. O "bloco sem donos" da PROPOSTA §2.3 é, na prática, um
bloco com **metade dos donos ausente**.

### 1.2 Consequência funcional: o resumo contínuo perde a metade do usuário

`ai/live.py:1691-1692`, em `_build_live_summary_text`:

```python
for role, txt in self._transcripts_log[-8:]:
    tag = "você" if role == "user" else "copilot"
```

Como no modo nuvem **nunca** existe `role == "user"`, **todas** as linhas saem como
`copilot:`. Esse resumo é injetado via `realtimeInput` justamente para **sobreviver a
reconexões** (docstring de `:1669-1679`). Resultado: depois de uma queda de sessão, o modelo
recebe de volta só as próprias falas — o que o usuário disse se perde. É o defeito real, e
ele é silencioso: nada quebra, a conversa só fica pior.

### 1.3 Os dois clientes discordam sobre o **nome** do papel

| cliente | papel do copiloto | papel do usuário |
|---|---|---|
| `ai/live.py` (nuvem) | `"assistant"` (`:2640`) | **não emite** |
| `ai/local_voice.py` | **`"agent"`** (`:1009`, `:1013`) | `"user"` (`:906`, `:910`) |

O mesmo conceito tem dois nomes. E o *fallback* esconde: `ui/live_view.py:664` faz
`role_key = "user" if role in ("user","você","voce") else "assistant"` — então `"agent"`
vira `"assistant"` **por acidente** (por não casar com nada), não por contrato.

### 1.4 Os dois clientes discordam sobre a **forma**

| cliente | `_transcripts_log` |
|---|---|
| `ai/live.py:1538` | `list[tuple[str, str]]` → `(role, text)` |
| `ai/local_voice.py:571` | `list[dict[str, str]]` → `{"role","text"}` |

E `get_session_summary()` devolve os dois sob a **mesma chave** `"transcripts"`
(`live.py:1656`, `local_voice.py:1087`) — tipos diferentes. Nenhum teste afirma a forma:
`tests/test_local_voice.py:290` só checa `len(summary["transcripts"]) == 2`.

### 1.5 O que **não** é consumido hoje (para não superdimensionar)

`ui/app.py:_consolidate_live_session` (`:3926`) renderiza `actions_executed` e **não** lê
`transcripts`. Os consumidores reais do transcrito são: o **resumo contínuo** (§1.2) e os
**callbacks** `on_transcript(role, text)` → `ui/voice_pill.py:616` e `ui/live_view.py:540`.

---

## 2. Objetivo

1. **Restaurar a metade ausente:** a nuvem passa a registrar a fala do **usuário**.
2. **Um só vocabulário de papéis**, com normalização para dados antigos.
3. **Uma só forma** de entrada, tolerante a leitura de formato legado.
4. O resumo contínuo passa a mostrar **os dois lados** — que é o ponto de sobreviver à
   reconexão.

**Fora de escopo (deliberado):** diarização multi-falante por *embeddings* (§7);
`outputTranscription` como fonte do copiloto (evita duplicar o que `modelTurn.parts[].text`
já entrega — ver §5.2); UI para nomear falantes.

---

## 3. Passos

| # | Arquivo | O que fazer | Riscos / armadilhas |
|---|---|---|---|
| W1 | `core/transcript.py` (**novo**) | `ROLE_USER`/`ROLE_ASSISTANT`; `LEGACY_ROLE_ALIASES` (`agent`→assistente, `você`/`voce`→usuário); `normalize_role()`; `make_entry()`; leitores tolerantes `entry_role()`/`entry_text()` (aceitam **dict ou tupla**). | `normalize_role` de papel desconhecido cai em **assistente** — preserva o *fallback* atual de `live_view.py:664`. Não inventar papel novo. |
| W2 | `ai/live.py` (`_server_receiver_loop`) | Ler `serverContent.inputTranscription.text` → registrar como **usuário** + `on_transcript("user", ...)`. | O campo pode vir como dict `{"text": ...}` **ou** string — extrair tolerante. **Não** adicionar `outputTranscription` (duplicaria o assistente). |
| W3 | `ai/live.py:1538/1691/1656` | `_transcripts_log` vira `list[dict[str,str]]`; o resumo contínuo lê por `entry_role`/`entry_text` e rotula com `normalize_role`. | `tests/test_live.py:788-865` monta tuplas à mão — 5 sítios a atualizar. |
| W4 | `ai/local_voice.py:906/1009` | Emitir `ROLE_ASSISTANT` (em vez de `"agent"`) e `make_entry`. | `tests/test_local_voice.py:282` e `:333` afirmam `"agent"` — atualizar. |
| W5 | `tests/test_transcript_roles.py` (novo) | Papéis/normalização/entrada legada; o handler com `inputTranscription`; o resumo contínuo com **os dois lados**. | Precisa dirigir `_server_receiver_loop` com um `ws` falso (não há teste que exercite `serverContent` hoje). |

---

## 4. Critérios de aceitação

- Mensagem de servidor com `inputTranscription` → entra em `_transcripts_log` como **usuário**.
- `_build_live_summary_text()` mostra `você:` **e** `copilot:` quando há os dois papéis.
- `normalize_role("agent") == "assistant"`; papel desconhecido → `"assistant"`.
- `entry_role`/`entry_text` leem **tuplas antigas** sem explodir (dados já persistidos).
- Os dois clientes devolvem `"transcripts"` na **mesma** forma.
- `on_transcript` do modo local emite `"assistant"` (não `"agent"`).

---

## 5. Implementado (2026-09-29) — âncoras reais

### 5.1 O vocabulário — `core/transcript.py` (novo, puro)

| Âncora | Linha | O que é |
|---|---|---|
| `ROLE_USER` / `ROLE_ASSISTANT` | `:24` / `:26` | Os dois papéis canônicos. |
| `LEGACY_ROLE_ALIASES` | `:31` | `agent`/`agente`/`copilot` → assistente; `você`/`voce`/`usuario`/`usuário` → usuário. |
| `normalize_role()` | `:44` | Papel desconhecido cai em **assistente** — preserva o *fallback* que `ui/live_view.py:664` já aplicava. |
| `make_entry()` | `:58` | A forma única `{"role","text"}`, com papel já normalizado. |
| `entry_role()` / `entry_text()` | `:63` / `:76` | Leitura **tolerante**: aceitam dict novo **e** tupla legada. |
| `extract_transcription_text()` | `:85` | Aceita `{"text": ...}` **ou** string crua. |

**Por que o default é `assistant`:** o erro perigoso é o inverso — papel desconhecido virar
`user` põe palavras do **copiloto na boca do usuário**. Um teste dedicado fixa isso, e uma
mutação o mata.

### 5.2 A metade que faltava — `ai/live.py`

| Âncora | Linha | O que é |
|---|---|---|
| `user_text = extract_transcription_text(sc.get("inputTranscription"))` | `:2649` | A leitura que **não existia**. |
| `self._transcripts_log.append(make_entry(ROLE_USER, user_text))` | `:2651` | O turno do usuário entra no log. |
| `make_entry(ROLE_ASSISTANT, p["text"])` | `:2668` | O turno do copiloto, agora na forma canônica. |
| `self._transcripts_log: list[dict[str, str]]` | `:1548` | Forma única (era tupla). |
| `tag = "você" if entry_role(entry) == ROLE_USER else "copilot"` | `:1706` | O resumo contínuo passa a mostrar **os dois lados**. |
| `t = entry_text(entry).strip()...` | `:1707` | Leitura tolerante também aqui. |

### 5.3 O papel canônico no cliente local — `ai/local_voice.py`

| Âncora | Linha | O que é |
|---|---|---|
| import | `:38` | Do vocabulário compartilhado. |
| `make_entry(ROLE_USER, user_text)` / `on_transcript(ROLE_USER, ...)` | `:907` / `:911` | Usuário. |
| `make_entry(ROLE_ASSISTANT, response_text)` / `on_transcript(ROLE_ASSISTANT, ...)` | `:1013` / `:1017` | Copiloto — era `"agent"`. |

### 5.4 O que mudou de comportamento, medido

| | antes | depois |
|---|---|---|
| transcrito da nuvem | só `assistant` | `user` **e** `assistant` |
| resumo contínuo (nuvem) | todas as linhas `copilot:` | `você:` e `copilot:` |
| papel do copiloto no local | `"agent"` | `"assistant"` |
| forma em `"transcripts"` | tupla (nuvem) / dict (local) | **dict** nos dois |
| papel desconhecido | `assistant` (por *fallback* da UI) | `assistant` (por contrato) |

---

## 6. Validação (medida nesta rodada)

### 6.1 Testes — `tests/test_transcript_roles.py` (novo, 21 testes + 14 subtestes)

Cinco classes: `RoleVocabularyTest` (papéis, aliases, *fallback*, leitura tolerante de dict
**e** de tupla, extração de envelope), `ServerContentUserTurnTest` (dirige
`_server_receiver_loop` de verdade, com um `ws` falso — **não havia nenhum teste que
exercitasse `serverContent`** antes desta entrega), `RollingSummaryTest` (os dois lados no
resumo; tupla legada ainda lê), `ClientParityTest` (os dois clientes devolvem a mesma forma
sob `"transcripts"`).

Sítios de teste atualizados pela mudança de contrato (todos por causa da unificação):
`tests/test_live.py` (4 sítios que montavam tuplas), `tests/test_local_voice.py` (2 sítios que
afirmavam `"agent"`), `tests/test_local_voice_board.py` (1 sítio idem).

### 6.2 Harness de mutação — `tests/mutacao/mutate_transcript_roles.py` (novo)

**10 KILLED / 0 sobreviventes / 1 EQUIVALENTE / 0 inválidas**, `exit=0`.

Cobre W1 (papel desconhecido virando usuário — o erro perigoso; `make_entry` sem
normalização; `entry_role` sem dict; extração ignorando o envelope), W2 (a nuvem deixando de
ler `inputTranscription`; o turno do usuário gravado como assistente; gravado como tupla) e
W3 (o resumo rotulando tudo como copilot; o resumo assumindo dict) e W4 (o local voltando a
emitir `"agent"` no callback).

**O equivalente foi medido, não presumido.** A mutação *"o alias legado `agent` some"*
**sobreviveu** — e o motivo é que `normalize_role` já cai em `ROLE_ASSISTANT` para papel
desconhecido, então remover o alias não muda nenhuma saída. É redundância real, não lacuna de
teste; ficou registrada em `EQUIVALENTES` com o motivo. O alias é **mantido de propósito**:
se o default um dia mudar, ele é o que protege um valor legado conhecido.

### 6.3 Suíte completa (runner seguro)

Executada pelo `run_tests_safe.py` (sem swap → nunca `pytest` direto). Resultado no fim desta
entrega, registrado em `ENTREGA_A_VOZ_TELA.md` §suíte:

```
módulos: 165   falhas: 0   tempo: 220s
pico de RSS em qualquer arquivo: 370 MB
```

O total subiu de 164 → **165** módulos porque esta entrega adiciona
`tests/test_transcript_roles.py`.

---

## 7. Limite declarado (o que esta entrega **não** faz)

**Não é diarização multi-falante.** Isto atribui **turno** (usuário vs. copiloto). Numa
reunião com três pessoas, as três aparecem como `user` — o transcrito diz *quando* alguém
falou, não *quem*. Uma "ata com decisões atribuídas" a pessoas nomeadas continua exigindo
*embeddings* de voz (um modelo que o projeto não tem) e segue em aberto.

**Não verificado contra a API real.** O campo `serverContent.inputTranscription` é o nome
documentado do Gemini Live e é o que os testes exercitam — mas **com mensagens sintéticas**.
Não houve sessão real contra a API nesta rodada (sem chave/sessão Live no ambiente), então o
nome do campo e o comportamento de entrega **não** foram confirmados ao vivo. Se o servidor
usar outro envelope, a leitura tolerante (§5.1) reduz o dano, mas a verificação ao vivo
continua pendente.

**`outputTranscription` não é lido**, de propósito: duplicaria o texto que
`modelTurn.parts[].text` já entrega ao log.

**Sem UI para nomear falantes** — fora de escopo.
