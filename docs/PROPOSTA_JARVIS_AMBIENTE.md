# Proposta — Um Jarvis (e, com freio, um Ultron) caseiro sobre o Zorin Copilot

**Data:** 2026-09-21 · **Estado:** análise, nada implementado
**Escopo:** o que o código atual já permite, o que está construído mas desligado, e o que
falta de verdade para o assistente sair da janela e passar a viver no ambiente.

---

## 0. O diagnóstico em um parágrafo

O Zorin Copilot **não está longe de um Jarvis por falta de capacidade — está longe por falta
de maestro**. O laço de presença já existe e funciona (palavra de ativação → sessão de voz →
conversa → encerramento autônomo → volta a escutar). O "corpo" existe (83 ferramentas, com
aprovação por risco, desfazer e kill switch). A "casa" existe (Home Assistant bidirecional com
canal de eventos em tempo real). A "memória" existe (fatos, RAG, histórico, contatos). O que
não existe é **quem escuta os eventos, decide se vale interromper, e fala**. E há quatro
módulos de inteligência ambiente — construídos, testados, verdes — que **nenhuma linha do app
instancia**. O trabalho para um Jarvis é, em boa parte, **ligação de fios**, não invenção.

---

## 1. Jarvis e Ultron não são o mesmo alvo

Vale separar antes de decidir, porque as duas coisas pedem engenharias diferentes e têm preços
diferentes.

| Eixo | **Jarvis** (companheiro ambiente) | **Ultron** (agente autônomo) |
|---|---|---|
| Postura | Presente e disponível; reage e sugere | Age por conta própria, persegue objetivos |
| Iniciativa | Baixa, situacional, sempre reversível | Alta, multi-passo, com efeitos reais |
| Memória | Lembra quem você é e o que aconteceu | Além disso: mantém *planos* entre sessões |
| Alcance | O desktop e a casa | Rede, serviços, dispositivos, terceiros |
| Onde mora o risco | Incomodar (interromper demais) | **Fazer algo errado sem você ver** |
| Estado no repo | **~80% pronto**, com peças soltas | **~30%** — e o que falta é o freio, não o motor |

> **Opinião, e ela é firme:** Ultron não é "Jarvis com mais recursos". É um problema de
> *confiança e auditoria* que precisa ser resolvido **antes** do motor. Um agente que persegue
> objetivos sozinho, sem trilha de auditoria confiável e sem porta de aprovação fora de banda,
> não é um Ultron caseiro — é um processo rodando como o seu usuário. O repo já tem os
> primitivos certos (`shell/risk.py`, `shell/undo.py`, `screen_fence_control`); o roteiro abaixo
> os põe **na frente**, não no fim.

---

## 2. O que já existe (inventário verificado no código)

### 2.1 O laço Jarvis de presença já está fechado

Isto é o mais surpreendente do inventário, e é bom: **o ciclo "escuta passiva → acorda →
conversa → se despede → volta a escutar" já está implementado e ligado.**

- `shell/wake_word.py` (690 linhas) traz um motor completo: `WakeWordEngine` com *backend*
  Vosk plugável, VAD, sensibilidade, cooldown entre ativações, **anti-eco** (fica surdo após a
  IA falar) e *match* em resultados parciais.
- O motor é construído em `ui/app.py:295` e **iniciado em `ui/app.py:312`**, atrás de
  `wake_word_enabled` (`core/config.py:97`, **default `False`** — é *opt-in*, o que está certo
  para microfone aberto).
- O gatilho `_on_wake_word` (`ui/app.py:347`) agenda no loop principal e
  `_handle_wake_on_main_thread` (`ui/app.py:351`) faz três coisas boas: cancela um encerramento
  pendente, roteia para a pílula ou a janela cheia, e — se a sessão de voz já está ativa — trata
  a fala como **turno de texto na sessão existente** em vez de abrir outra.
- A sessão tem **encerramento autônomo** (`end_session_enabled`, `end_session_grace_sec` em
  `core/config.py:114-115`): o assistente pode se despedir e fechar sozinho, e voltar ao estado
  de escuta. Há *grace period* para o usuário reconsiderar.

Ou seja: o Jarvis **já sabe acordar e dormir**. Falta ele saber **falar sem ser chamado**.

### 2.2 O "corpo": 83 ferramentas com freio

`ai/agent_tools.py` declara **83 `ToolSpec` distintos** (medido, não estimado), cobrindo
desktop, arquivos, janelas, rede, Docker, git, VS Code, estudo/Anki, academia e casa
inteligente. Em cima disso:

- **Aprovação por risco:** `shell/risk.py` (`RiskLevel`, `RiskPolicy.classify`) + `Step.requires_approval`
  e `on_approval` em `ai/agent.py`. Ferramentas MCP mutantes são forçadas a `CONFIRM`.
- **Reversibilidade:** `shell/undo.py` com pilha de desfazer para mutações.
- **Kill switch e cerca espacial:** `core/fence.py` + a ferramenta `screen_fence_control`.
- **Autonomia já configurável:** `auto_execute_safe_actions` (`core/config.py:152`) permite
  executar sozinho **apenas** o que for classificado seguro.
- **Recorte de catálogo por allow-list:** `ToolRegistry.restricted(names)` (`ai/subagents.py`)
  devolve um clone cujo *schema* contém só o permitido — restrição por construção, não por
  validação a posteriori.

Isto é, literalmente, a infraestrutura de "agir no mundo" — e ela já vem com os freios que a
literatura de agentes costuma acrescentar depois do primeiro acidente.

### 2.3 A "casa": bidirecional e em tempo real

`core/home_assistant.py` (REST com *retry* só para falhas transitórias) +
`core/home_assistant_realtime.py` (WebSocket RFC 6455 sem dependências externas, assinando
`state_changed`, com *backoff* 1→2→4…→30 s). O canal é subido no arranque em `ui/app.py:3283`,
atrás de `ha_enabled and ha_realtime_enabled`.

E existe a **prova de conceito do caminho de volta**: `scripts/ha_bridge.py` (296 linhas,
processo separado, `127.0.0.1:8787`) recebe webhook de uma automação do HA e **notifica e fala**.
O conceito "a casa avisa o desktop" já foi validado — só que fora do app, com outra voz.

### 2.4 A "memória": quatro camadas que ainda não conversam

| Camada | Onde | O que guarda |
|---|---|---|
| Fatos e preferências | `core/memory.py` → tabela `knowledge_facts` | O que o assistente sabe sobre você |
| Histórico de ações | `action_logs` | O que ele fez no desktop, com sucesso/falha |
| Conversas | `chat_topics` (SQLite) | Tópicos persistentes e multiturno |
| Documentos | `core/rag.py` + `core/vector_store.py` + `core/file_watcher.py` | Indexação incremental em segundo plano |
| Contatos / acadêmico | `user_contacts`, `academic_library` | Agenda e bibliografia |

Mais `core/temporal_state.py` (decadência exponencial de contexto, para não tratar "voltei do
almoço" como continuação) e `core/decision_trace.py` (trilha de raciocínio em *spans*).

---

## 3. O diagnóstico: não falta capacidade, falta maestro

### 3.1 Cinco módulos de inteligência ambiente estavam **órfãos**

Esta é a descoberta central da análise. Estes módulos têm implementação séria, **arquivo de
teste dedicado e suíte verde** — e **zero referências no `src/`** fora do próprio arquivo.
Nenhum deles era instanciado pelo aplicativo em execução.

| Módulo | O que ele faria | Consumidores em `src/` | Teste |
|---|---|---|---|
| `core/power_manager.py` — `PowerManager` | *Throttling* por bateria; teto de ingestão de eventos e `enable_proactive_predictions` | **0** | `test_power_manager.py`, `test_operational_powers.py` |
| `ai/fast_rnn_router.py` — `FastRNNRouter`/`IntentPredictor` | Rota de intenção em < 0,5 ms sem LLM (mídia, volume, janela, sistema) | **0** | `test_fast_rnn_router.py` |
| `core/temporal_state.py` — `TemporalStateTracker` | Decadência contínua do contexto ocioso, zera ao bloquear a tela | **0** | `test_temporal_state.py` |
| `core/consolidator.py` — `MemoryConsolidator` | Passa a memória a limpo (`run_consolidation(days_to_keep=14)`) | ~~**0**~~ **1** (Fase 0, item 3) | `test_consolidator.py` |
| `core/file_watcher.py` — `DocumentFileWatcher` | Reindexa o RAG quando um documento muda em disco, durante a sessão | ~~**0**~~ **1** (Fase 1, P1) | `test_file_watcher.py` |

A coluna é **consumidores** — construção de verdade —, e não o resultado cru do `grep`. A
diferença é grande e engana: hoje o `grep` de `DocumentFileWatcher` em `src/` devolve **6**
acertos, mas só **um** é construção (`ui/app.py:938`); os outros são o `import`, a anotação de
tipo e dois comentários.

> **Correção de 2026-09-21 (medida no código, não no texto).** A primeira versão desta tabela
> dizia que o `PowerManager` governaria "a taxa de amostragem **da escuta**". O código diz outra
> coisa: `PowerProfile.stream_sample_rate_hz` está comentado como *"Frequência máxima de **ingestão
> de eventos**"*, e o cabeçalho do módulo fala em "taxa de amostragem **da RNN**". Ou seja, o
> `PowerManager` é **consumidor de um fluxo de eventos rápido**, não regulador do microfone. A
> consequência prática está no item 4 da Fase 0: seu único consumidor desenhado
> (`FastRNNRouter`) também está órfão, então ligá-lo hoje seria ligar um módulo morto a outro.

> **Correção de 2026-09-21 (a tabela estava incompleta — a contagem era 5, não 4).** O
> `DocumentFileWatcher` ficou de fora das duas primeiras versões desta seção. Ele preenche
> exatamente os critérios: 141 linhas, suíte própria (`tests/test_file_watcher.py`), verde — e
> **zero consumidores**. Foi encontrado ao medir o P1, não ao reler a tabela. A lição vale mais
> do que a linha: **a lista de órfãos era ela própria um artefato não verificado.** Quem escreveu
> "4 módulos" não rodou o `grep` para o quinto.

> **Armadilha de medição (custa uma contagem errada).** O `grep` do símbolo em `src/` passou a
> devolver **1** para `PowerManager` e `TemporalStateTracker` — mas a referência é um *comentário*
> em `core/ambient_events.py`, não um consumidor. **Menção em comentário não é uso.** A contagem
> desta tabela exclui o próprio arquivo *e* confere se o acerto é código, não prosa.


Leia a tabela de novo, porque ela diz o essencial: **o repo já escreveu o Jarvis e esqueceu de
instalá-lo.** Há um roteador de intenção instantâneo, um gerente de energia consciente de
bateria, um decaimento temporal de contexto e um consolidador de memória — **todos parados na
prateleira.** Nenhum precisa ser escrito; todos precisam de um ponto de entrada.

E dois já saíram da prateleira, pela porta mais barata possível — a de *ligar*, não a de
*reescrever*: o `MemoryConsolidator` (Fase 0, item 3) e o `DocumentFileWatcher` (Fase 1, P1).
**Nenhum dos dois foi modificado para caber:** o consolidador ganhou um estado em disco ao lado
do banco, e o monitor ganhou `Any` no `import` e um `join` no `stop`. O resto do trabalho foi
fiação. É o argumento inteiro deste documento, medido duas vezes.

**O que resta órfão são os três que continuam sem consumidor** — e a P4 **não** mudou isso, ao
contrário do que o roteiro previa:

| Órfão | Por que continua parado |
|---|---|
| `PowerManager` | É consumidor de um fluxo rápido de eventos, e seu único consumidor desenhado é o `FastRNNRouter`. Ligar um ao outro seria ligar módulo morto a módulo morto. Pré-condição no item 4 da Fase 0 |
| `FastRNNRouter`/`IntentPredictor` | Mesma dependência, pelo outro lado |
| `TemporalStateTracker` | **O roteiro dizia que ele "entra na P4" (contexto decai com o ócio). Medido ao fechar a P4: ele não tem relação com identidade** — é decaimento de contexto temporal, e o consumidor desenhado é o `PowerManager`, que está adiado. Ligá-lo à P4 seria ligar módulo morto a um assunto alheio, que é o defeito que este documento existe para evitar. A pré-condição é a mesma do `PowerManager` |

> **Lição repetida três vezes:** cada vez que este documento disse "o órfão X entra no item Y",
> a medição mostrou que a relação era de nome, não de fio. `PowerManager` não regula o microfone;
> `TemporalStateTracker` não é identidade. **Antes de propor ligar um órfão, leia o que ele
> consome e o que consome ele** — e confira se o acerto do `grep` é código ou comentário.

### 3.2 O canal de eventos está aberto e **ninguém está do outro lado**

`HomeAssistantManager.on_event(callback, entity_filter, domains)` existe
(`core/home_assistant.py:743`). O canal WebSocket sobe no arranque, recebe `state_changed`,
normaliza e guarda no buffer.

**Corrigido em 2026-09-21 (Fase 0, item 1):** o consumidor passou a existir —
`ui/app.py:_on_ha_event`, assinado por `_init_ha_announcements`. Antes disso a busca por
consumidores no `src/` inteiro devolvia **apenas a própria definição e `recent_events`**: o
canal subia, recebia eventos e só servia para responder a `smart_home_events` quando o usuário
perguntava. Era a metáfora exata do estado do projeto — **o sistema tinha ouvidos e tinha boca,
e nenhum dos dois estava ligado ao cérebro.**

**E a política que faltava também já existe (Fase 1, P2 + P1):** a assinatura passou a ser
`_on_ambient_event`, que roteia por fonte, e a decisão de interromper
(`decide_attention`) governa **as duas** fontes — a casa e os documentos. O que falta neste
eixo não é mais fio nem regra: é o **consumidor do selo** (o digesto da Fase 2), porque hoje
`_attention_badges` acumula e só aparece no log.

### 3.3 Existem **três vozes** — duas delas já foram reconciliadas

| Voz | Onde | Quando é usada |
|---|---|---|
| Piper local (`pt_BR-faber-medium`) | `ai/local_voice.py:243`, `speak()` em `:252` | Voz local, offline — **a boca da casa** |
| Voz do Gemini Live (`Puck`, `core/config.py:30`) | `ai/live.py` | Durante a sessão ao vivo |
| `spd-say` (speech-dispatcher) | `scripts/ha_bridge.py` | Só como plano B quando não há app rodando |

Consequência prática original: se você pedisse por voz e a casa respondesse por automação,
**eram timbres diferentes, e um deles nem sabia que o Copilot existia**. Um assistente com três
vozes desconexas não soa como uma presença — soa como três programas.

**Reconciliado em 2026-09-21 para o caminho da ponte:** `ha_bridge.py` deixou de ter voz
própria. Ele pede a frase ao Copilot (`zorin-copilot --announce=<texto>`, instância única do
GTK) e só usa `spd-say` se não houver instância — ver `docs/CASA_INTELIGENTE.md` §6.1. A
arbitragem passou a ser uma só, no `_claim_speaker()` do app, que também conhece a sessão de
voz ao vivo. **Restam duas vozes:** a do Piper e a do Live (`Puck`), que é a peça P4
(identidade única).

Nota boa: `LocalVoiceSynthesizer` já tem `speak(text, on_done)` **e** `interrupt()` /
`is_speaking` — este último uma **propriedade**, não um método (`ai/local_voice.py:252`,
`:321`, `:343`). Isso é exatamente a interface necessária para uma fala não solicitada que
respeita barge-in. A peça está pronta; faltava o chamador — que passou a existir na Fase 0.

### 3.4 O que **não existe em absoluto**

Verificado por busca, não por suposição:

- **Nenhuma persona configurável.** Busca por `persona`, `assistant_name`, `agent_name` em
  `core/config.py`: **vazio**. O assistente não tem nome que o usuário escolha, nem tom
  ajustável. A identidade é o `SYSTEM_PROMPT` **hardcoded** em `ai/providers.py:64`, mais a
  voz fixa do Live. Para um Jarvis, "quem é você" deveria ser configuração, não constante.
- **Nenhum agendador.** Não há cron, systemd timer, fila durável nem loja de *jobs*. Os únicos
  temporizadores do repo são: `threading.Timer(0.5)` para *debounce* de config do MCP
  (`mcp/manager.py:213`), a thread de reindexação do RAG (`core/file_watcher.py`) e a thread do
  canal HA. **Nada sobrevive a um reinício do app**, exceto o histórico de conversa no SQLite.
- **Nenhuma política de atenção.** Não há lugar nenhum que decida *se* é hora de falar. Não há
  horas de silêncio, orçamento de interrupções nem "não me interrompa durante uma sessão ao vivo".
- **Nenhum conceito de confiança *do agente*.** `core/trust.py` é sobre **documentos**
  (`TrustLevel`, `PIISanitizer`, `DocumentTrustManager`) — elegibilidade para RAG e mascaramento
  de PII. Não é "quanto de autonomia eu dou a este assistente". São coisas diferentes com o
  mesmo nome, e vale não confundi-las.
- **Nenhuma trilha de auditoria *durável* de ações autônomas.** Há `action_logs` (histórico de
  ações) e `DecisionTrace` (em memória, por execução), mas nada que responda depois: *"o que
  ele fez sozinho ontem, e por quê?"*.

---

## 4. As quatro peças que faltam

*(Três já não faltam: **P1**, **P2** e **P4** foram feitas em 2026-09-21 — ver Fase 1. O texto de
cada uma fica abaixo, com a correção de premissa onde a medição contradisse o desenho.)*

### P1 — Barramento de percepção (unificar o que já existe)

> **A premissa original desta seção estava errada, e a correção vale mais do que o texto que ela
> substitui.** O que estava escrito: *"hoje cada fonte tem sua própria thread e seu próprio
> consumidor: HA realtime, file watcher, desktop_context, power_manager"*. Medido no código:
>
> - O **file watcher não tinha consumidor nenhum** — zero. Não havia thread própria porque não
>   havia uso nenhum. (§3.1, quinto órfão.)
> - `desktop_context` e `power_manager` também não têm produtor rodando.
> - O HA realtime **tem** thread própria, e um consumidor desde a Fase 0, item 1.
>
> Ou seja: **não havia "um consumidor por fonte" para unificar.** Havia *uma* fonte viva, com um
> consumidor, e três módulos parados. A peça P1, como descrita, era uma abstração sobre um
> problema que ainda não existia.

O que P1 de fato entregou foi menor e mais útil: **o envelope e a porta.** Um formato de evento
único (`core/ambient_events.py`, puro) com um adaptador por fonte, e **uma** entrada em
`ui/app.py` que roteia por fonte. As duas fontes — casa e arquivos — passam pela *mesma* decisão
de interromper (`decide_attention`), que era a tese real do P1: *a regra governa tudo o que o
assistente percebe.*

**O que foi deliberadamente NÃO construído: a classe de barramento.** Duas razões, ambas medidas:

1. **O padrão já existe duas vezes no repo.** `StudySession.subscribe`/`publish`
   (`core/study_session.py:108`) e `HomeAssistantRealtimeChannel.subscribe`
   (`core/home_assistant_realtime.py:246`) já implementam cópia sob trava, isolamento por
   assinante e cancelamento idempotente. Uma terceira cópia não seria arquitetura — seria
   duplicação com nome novo.
2. **O nome `perception` já está tomado.** `core/perception.py` é o **canal de visão** do
   operador (AT-SPI2 + captura de tela + *grounding* por VLM), consumido por `cli.py`,
   `ui/app.py` e `browser_runtime.py`. "Percepção", neste repo, quer dizer *ver a tela*. Chamar
   o barramento de `perception` seria renomear uma coisa que já tem dono.

O módulo novo documenta, no próprio cabeçalho, **a pré-condição para que uma classe de barramento
passe a valer a pena**: uma segunda fonte que **interrompa**. Enquanto o único evento que merece
fala vem da casa, um `subscribe` genérico seria estrutura sem função — e é por isso que ele não
foi escrito.

**A correção do desenho anterior** — o `on_event` de `HomeAssistantManager` continua sendo o
contrato a copiar quando a segunda fonte que interrompe aparecer (`entity_filter`/`domains` +
devolução de função de cancelamento). Só deixou de ser urgente, porque a urgência era imaginária.

### P2 — Política de atenção (a peça que decide se vale interromper)

**Módulo puro, sem GTK, sem rede** — no espírito de `core/board_outcome.py`, que já provou o
valor de separar *decisão* de *efeito*. A assinatura é uma função:

```
decide(evento, contexto, orçamento, trust_level, silêncio) → {IGNORAR, BADGE, NOTIFICAR, FALAR}
```

Ordem das condições **é** a regra (como em `board_outcome.decide_path`), e o resultado precisa
ser testável sem display. É esta peça que separa "assistente ambiente" de "assistente
insuportável": sem ela, o Jarvis vira um alarme que fala sozinho.

> **Implementada em 2026-09-21** — `core/attention.py`, ver "Fase 1 — P2, implementado". Duas
> correções ao desenho proposto acima, ambas deliberadas:
>
> 1. **A entrada é a severidade já classificada, não o evento bruto.** A assinatura real é
>    `decide_attention(*, severity, context, budget, policy)` — a classificação do evento mora em
>    `severity_for()`, separada. Motivo: a política precisa saber de *entidades do Home
>    Assistant* apenas para decidir *quanto o evento importa*; o resto dela é sobre o **usuário e
>    o momento**. Misturar as duas faria a peça deixar de servir ao *file watcher* ou ao
>    `desktop_context` sem uma linha de mudança.
> 2. **`trust_level` saiu da assinatura.** `core/trust.py` é sobre **documentos e PII**, não sobre
>    autonomia do agente — não é a grandeza certa para uma política de interrupção. Confundir as
>    duas seria pior do que não ter o parâmetro. O freio de autonomia continua onde sempre
>    esteve: `shell/risk.py` (`RiskLevel.CONFIRM`).

### P3 — Agendador durável (missões que sobrevivem ao reboot)

Tabela nova no SQLite que já existe (`memory.db`) + um laço que reconcilia na abertura. Serve
aos dois alvos: ao Jarvis ("me lembra às 7h", "resume meus e-mails às 9h") e ao Ultron
("missão: auditar as dependências do repo, com aprovação a cada escrita").

Regra que não se flexiona: **uma missão retomada após reinício não herda permissão.** Ela volta
ao estado "aguardando aprovação", porque o contexto em que foi autorizada já não existe.

### P4 — Identidade única (nome, voz, tom, silêncio)

Uma seção de configuração que possua nome do assistente, voz, tom e janelas de silêncio, e da
qual derivem **o prompt e as três saídas de voz**. Hoje há três vozes e um prompt hardcoded;
Jarvis tem uma voz e um caráter.

> **Feita em 2026-09-21, com o escopo corrigido pela medição** — ver "Fase 1 — P4, implementado".
> Duas correções de premissa, ambas medidas:
>
> 1. **"o prompt é hardcoded" era impreciso.** `config.system_prompt` **existia** desde sempre:
>    `CopilotConfig.load()` escrevia nele o texto gerado, `save()` o persistia, e
>    `build_system_prompt()` — o único consumidor possível — **nunca o leu**. O defeito não era
>    ausência de campo: era **um campo que mentia**, escrito e persistido sem leitor. Isso muda o
>    trabalho de "criar" para "ligar **e** migrar" (todo `config.json` existente tem o texto
>    gerado gravado ali; sem guarda, o prompt cairia de 9.479 para ~260 caracteres em todas as
>    instalações).
> 2. **As "janelas de silêncio" já moram na política de atenção** (`attention_silence_hours`,
>    Fase 1/P2) e **não** devem ser duplicadas na identidade: silêncio é *política de atenção*,
>    não *identidade*. Duas fontes para a mesma janela é exatamente o defeito que a P4 existe
>    para corrigir. Ficaram fora, de propósito.
> 3. **O "tom" também ficou fora**, e pelo mesmo motivo invertido: o único gancho para tom seria
>    `prompt_override` — mas um override que substitui o texto **inteiro** apagaria o contrato de
>    saída do agente (esquema JSON, regras de `explanation`, lista de ferramentas), que mora no
>    mesmo bloco. Ver a decisão de `PERSONA_MARKER` em `core/identity.py`. Um campo "tom" que
>    silenciasse o agente seria pior que nenhum campo.
> 4. **O `TemporalStateTracker` continua órfão.** O roteiro dizia que ele "entra aqui"; medido, ele
>    não tem relação com identidade — é consumidor de fluxo temporal, e o consumidor desenhado
>    (`PowerManager`) continua adiado. Ligá-lo à P4 seria ligar módulo morto a um assunto alheio.

---

## 5. Roteiro por fases

Cada fase entrega algo verificável, e nenhuma depende de reescrever o que já existe.

### Fase 0 — Ligar o que já está construído *(barato, alto impacto)*

- ✅ **Item 1 (feito em 2026-09-21):** `on_event` assinado e reação falada por
  `LocalVoiceSynthesizer`. Ver a seção "Fase 0 — item 1, implementado" logo abaixo.
- ✅ **Item 2 (feito em 2026-09-21):** a ponte deixou de ter voz própria. `ha_bridge.py`
  pede a fala ao Copilot (`--announce`, instância única do GTK) e só usa `spd-say` como
  plano B. Ver `docs/CASA_INTELIGENTE.md` §6.1.
- ✅ **Item 3 (feito em 2026-09-21):** `MemoryConsolidator.run_consolidation()` passou a rodar
  num temporizador com estado durável. Ver "Fase 0 — item 3, implementado".
- ⛔ **Item 4 — adiado de propósito:** ligar o `PowerManager`. Ver "Fase 0 — item 4, adiado".

**Critério de pronto:** um evento real da casa faz o Copilot **falar com a própria voz, sem
turno do usuário**, e o log mostra a consolidação rodando.

#### Fase 0 — item 3, implementado

A memória era consolidada **nunca**: `MemoryConsolidator` tinha suíte verde e zero chamadores. E
o custo disso é medível — `action_logs` cresce ~194 linhas / ~97 KB em 5 dias nesta máquina, o
que dá **~6,9 MB por ano** sem poda, contra **~0,27 MB** com o corte em 14 dias, num banco que
hoje tem 7,7 MB.

| Peça | Onde | Papel |
|---|---|---|
| Decisão (pura) | `core/consolidator.py` | `decide_consolidation()`, `ConsolidationState`, `ConsolidationOutcome` |
| Fio | `ui/app.py` (`_init_consolidation`, `_check_consolidation`, `_run_consolidation_async`, `_stop_consolidation`) | Pergunta de hora em hora; dispara em thread; grava o estado |
| Config | `core/config.py` | `memory_consolidation_enabled` (default **False**), `memory_consolidation_days_to_keep`, `memory_consolidation_interval_hours` |
| Preferências | `ui/preferences.py` | Grupo "Manutenção Automática" na aba Memória |
| Testes | `tests/test_consolidator.py` (32), `tests/test_consolidation_wiring.py` (27) | Regra pura + fio, sem display |

Decisões que valem registrar:

- **Opt-in explícito** (`memory_consolidation_enabled`, default `False`). O recurso **apaga
  linhas** do histórico; não se presume.
- **Um `threading.Timer` de 24 h não serviria.** O timer morre com o processo, então quem abre o
  app uma vez por dia nunca consolidaria nada. "Diário" precisa significar *no máximo uma vez por
  dia*, o que exige estado em disco — não tempo decorrido desde o arranque. Daí o arquivo de
  estado ao lado do banco e uma verificação no arranque **mais** de hora em hora.
- **O estado fica ao lado do banco, não em `system_profile`.** Aquela tabela é injetada no
  contexto do agente; uma chave de contabilidade interna vazaria para dentro do prompt.
- **Gravação atômica** (temporário + `replace`). Um desligamento no meio da escrita deixaria um
  JSON truncado — e o efeito de um estado ilegível é "nunca rodou". Há teste que simula a falha
  no meio da escrita e cobra que o estado **anterior** sobreviva.
- **Lixo no estado significa "nunca rodou", não "já rodou".** As duas leituras são assimétricas:
  consolidar uma vez a mais custa uma consulta; achar que já rodou custa não rodar nunca.
- **Relógio para trás tem condição própria.** Sem ela, um estado no futuro (correção por NTP,
  arquivo restaurado de backup) deixaria o intervalo negativo para sempre e a consolidação
  **nunca mais** rodaria — em silêncio. O desfecho `clock_skewed` existe para isso, e o log avisa.
- **Desfechos nomeados, não booleano** — mesmo raciocínio do item 1, pelo mesmo motivo.
- **Os caminhos do banco entram por atributo, não por construção implícita.** `_run_consolidation_async`
  recebe `db_path` resolvido em `_init_consolidation` em vez de construir `MemoryConsolidator()`
  sem argumento. Sem isso, um teste que exercitasse o caminho apontaria para o banco **real** do
  usuário — que `run_consolidation` apaga. A mutação que remove esse argumento é uma das que a
  suíte mata (ver "Validação por mutação").

Chaves em `config.json` (aba **Memória** → "Manutenção Automática"):

| Chave | Padrão | Descrição |
|---|---|---|
| `memory_consolidation_enabled` | `false` | Liga a consolidação automática. **Apaga** linhas de `action_logs`. |
| `memory_consolidation_days_to_keep` | `14` | Quantos dias de log bruto sobrevivem à poda (1–365). |
| `memory_consolidation_interval_hours` | `24.0` | Intervalo mínimo entre consolidações. |

**A destilação continua inerte — e isso está medido, não suposto.** `run_consolidation` também
extrai padrões (`FrequentDocument`, `FREQUENTLY_ACCESSED`, `user:default`), mas **nada lê o que
ela escreve**: esses três nomes aparecem só em `consolidator.py` e no teste dele. O único leitor
do `KnowledgeGraph` é a topologia de rede (`network_inspector.py`, `ui/widgets/network_view.py`),
que usa nós `"HostMachine"`. O que se ligou aqui é a metade que **poda**; a metade que **destila**
precisa de um consumidor antes de valer alguma coisa. Registrado como pendência, não como feito.

#### Fase 0 — item 4, adiado

O roteiro pedia: *"ligar `PowerManager` para governar a taxa de amostragem da escuta"*. **O
roteiro estava errado, e a leitura do código mostra por quê.**

`PowerProfile.stream_sample_rate_hz` está comentado como *"Frequência máxima de **ingestão de
eventos**"*, e o cabeçalho de `power_manager.py` diz *"adequar a taxa de amostragem **da RNN** e
predições proativas"*. O `PowerManager` não regula o microfone: ele é **consumidor de um fluxo de
eventos rápido**, desenhado para estrangular a inferência quando o notebook sai da tomada.

Só que esse fluxo não existe. O consumidor desenhado é o `FastRNNRouter`/`IntentPredictor` — que
está na mesma tabela de órfãos, com **zero referências**. Ligar o `PowerManager` hoje seria
**ligar um módulo morto a outro**: um `get_default()` que ninguém consulta para modular um
`stream_sample_rate_hz` que ninguém lê. Passaria no teste, apareceria como "feito" no roteiro, e
não mudaria nada em execução.

**Precondição real:** o barramento de percepção (P1) precisa existir e emitir um fluxo de eventos
com taxa. Aí sim `PowerManager.add_listener()` tem a quem falar. Até lá, o item fica **parado de
propósito** — não por falta de tempo, mas porque não há o que ligar.

*(`add_listener`/`remove_listener` existem e o `get_default()` usa `_lock` próprio, então a peça
é segura de acionar de qualquer thread no dia em que houver consumidor. O que falta é o
consumidor.)*

#### Validação por mutação (Fase 0, item 3)

Suíte verde não prova nada se os testes não morderem. Cada mutação abaixo é uma crença da
implementação quebrada de propósito; **31 de 31 foram mortas** — nenhuma sobreviveu, nenhuma
mutação falhou em aplicar.

| Crença quebrada | Desfecho |
|---|---|
| `decide_consolidation`: remove a guarda de desligado | morta |
| `decide_consolidation`: remove o desfecho "nunca rodou" | morta |
| `decide_consolidation`: remove a guarda de relógio para trás | morta |
| `decide_consolidation`: relógio para trás com guarda estreitada | morta |
| `decide_consolidation`: fronteira `>=` vira `>` | morta |
| `decide_consolidation`: intervalo negativo deixa de ser normalizado | morta |
| `decide_consolidation`: "desligado" perde a prioridade na ordem | morta |
| `should_run`: esquece `CLOCK_SKEWED` / `NEVER_RAN` | mortas |
| `should_run`: vira "tudo menos `NOT_YET`" | morta |
| `from_dict`: deixa de tolerar lixo / data inválida / contagem negativa | mortas |
| `load_consolidation_state`: deixa de tratar JSON corrompido | morta |
| `save_consolidation_state`: escrita deixa de ser atômica | morta |
| `save_consolidation_state`: não limpa o temporário após falhar | morta |
| `save_consolidation_state`: engole `OSError` vira propagação | morta |
| `app`: remove o gate do runner de teste | morta |
| `app`: remove a verificação no arranque | morta |
| `app`: remove o opt-in | morta |
| `app`: remove a trava de reentrada | morta |
| `app`: remove a guarda de caminhos ausentes | morta |
| `app`: deixa de gravar o estado | morta |
| `app`: **`MemoryConsolidator()` sem `db_path` explícito** | morta |
| `app`: `days_to_keep` deixa de vir da config | morta |
| `app`: remove o `finally` que solta a trava | morta |
| `app`: `_stop_consolidation` deixa o `source_id` preso | morta |
| `app`: `_reconfigure` arma antes de desarmar | morta |
| `app`: teardown esquece de desarmar | morta |
| `preferences`: coletor esquece o interruptor / os dias | mortas |

Duas sobreviveram na **primeira** rodada, e as duas apontavam para lacunas reais:

1. *"intervalo negativo deixa de ser normalizado"* — a decisão é **invariante** à normalização
   (o tempo decorrido nunca é menor que um intervalo negativo), então o teste que só olhava o
   desfecho não mordia. O que a normalização compra é o `next_due_at`. Corrigido: o teste passou
   a cobrar a invariante de `next_due_at`, e o docstring de `decide_consolidation` deixou de
   alegar que sem ela o recurso "nunca rodaria".
2. *"escrita deixa de ser atômica"* — a guarda existente checava que **nenhum `.tmp` sobrava**, o
   que é verdade nas **duas** implementações (a não-atômica nunca cria temporário). Corrigido com
   um teste que falha no meio da escrita e cobra que o estado **anterior** sobreviva.

O harness de consolidação **se perdeu** — ver a nota sobre `tests/mutacao/` abaixo.

> **Reversão de decisão (2026-09-21).** Até aqui os harnesses ficavam em `/tmp`, com a
> justificativa escrita: *"fora do repo: é ferramenta de sessão, não artefato"*. A
> justificativa era razoável e a consequência foi medida: dos quatro harnesses
> produzidos em 2026-09-21, **dois evaporaram** na limpeza de `/tmp` — este de
> consolidação e o da política de atenção — e os documentos continuaram apontando para
> caminhos que não existiam mais. O projeto já tinha pago essa conta uma vez, com o
> `/tmp/pytestenv`.
>
> **Um instrumento de verificação que evapora é pior que instrumento nenhum: ele deixa
> a crença de que a verificação existe.** Os harnesses passaram a morar em
> `tests/mutacao/`, com `LEIAME.md` explicando as quatro categorias. O diretório é
> invisível para a suíte (o runner filtra `test_*.py`; o `pytest` não casa `mutate_*.py`;
> e há um `conftest.py` com `collect_ignore_glob` como segunda tranca).
>
> Os dois harnesses perdidos **não foram reconstruídos**: as mutações e os resultados
> estão registrados neste documento, que é a parte durável. Reconstruí-los seria
> reescrever a partir de uma memória, não de uma medição.

#### Fase 0 — item 1, implementado

O que entrou:

| Peça | Onde | Papel |
|---|---|---|
| Decisão (pura) | `core/ha_announce.py` | `decide_announcement()` + `announcement_text()` |
| Fio | `ui/app.py` (`_init_ha_announcements`, `_on_ha_event`, `_speak_announcement`, `_stop_ha_announcements`) | Assina, decide, fala, cancela |
| Config | `core/config.py` | `ha_announce_enabled` (default **False**), `ha_announce_entities` |
| Preferências | `ui/preferences.py` | Interruptor + filtro de entidades na aba Casa Inteligente |
| Testes | `tests/test_ha_announce.py` (53), `tests/test_ha_announce_wiring.py` (41) | Decisão pura + fio, sem display |

Decisões que valem registrar:

- **Opt-in explícito** (`ha_announce_enabled`, default `False`). Um assistente que fala por
  conta própria precisa ser pedido, nunca presumido.
- **Desfechos nomeados, não booleano.** `AnnounceDecision` tem oito valores
  (`announce`/`disabled`/`not_state`/`no_state`/`filtered`/`unchanged`/`no_phrase`/`busy`) e a
  **ordem** das condições é a regra — da mais fundamental para a mais circunstancial, para que
  o motivo relatado seja o mais informativo. Um `bool` jogaria fora exatamente o motivo que se
  precisa ler quando nada é falado.
- **Sem fallback genérico de frase.** Só domínios com verbo conhecido são anunciados; um
  `sensor.*` não vira narrador de variações de leitura. Há teste fixando a **ausência** do
  catch-all, para que ninguém o acrescente depois sem perceber.
- **Verbos invariáveis em gênero** ("acendeu", "abriu", "trancou"). O nome amigável pode ser
  "Porta", "Sensor" ou "Luz" e não há como saber o gênero: uma frase que erre a concordância
  ("A Sensor abriu") soa pior do que uma que a evite.
- **Três fontes de "ocupado"**: a própria fala em andamento, uma sessão de voz ao vivo ativa
  (o usuário está conversando — não é hora de avisar que a porta abriu) e o sintetizador no
  meio de uma frase. A reivindicação do alto-falante é feita **dentro** da trava, não na
  leitura anterior: entre decidir e falar, outro evento pode ter ocupado a saída.
- **Reusa `event_matches`** do canal em vez de reimplementar o filtro. Duas cópias
  divergiriam em silêncio. O ramo "eventos de sistema sempre passam" nunca é alcançado aqui —
  a checagem de `kind` vem antes, e um anunciante **não** quer acordar para avisar que a
  conexão caiu.

**Evidência medida (Home Assistant real, 2026-09-21):** canal WebSocket conectado de verdade
(`connected: True`), assinatura ativa, `light.pera_neo_10w` acionada duas vezes e restaurada ao
estado original. Os dois eventos chegaram e foram decididos como `announce` com a frase certa:

```
on->off      => announce   'Pera NEO 10W apagou.'
off->on      => announce   'Pera NEO 10W acendeu.'
```

**Defeito encontrado ao verificar (e corrigido):** o motor de voz local **não está
instalado**. `piper-tts` é dependência **opcional** (`[project.optional-dependencies] voice` em
`pyproject.toml`) e não está presente em nenhum dos três ambientes (sistema, `.venv`, `uv`).
Como `speak()` devolve `False` em silêncio quando `load()` falha, o anúncio sairia **mudo** —
o fio certo, a frase certa, nenhum som, e nenhuma pista para o usuário. Corrigido com
`piper_available()` (`ai/local_voice.py`), que checa o módulo **sem carregar o modelo ONNX**:
agora o arranque avisa por log **e** por toast quando os anúncios estão ligados sem motor de
voz. Para ouvir de fato: `uv sync --extra voice`.

#### Fase 0 — item 2, implementado

O que entrou:

| Peça | Onde | Papel |
|---|---|---|
| Decisão de rota (pura) | `scripts/ha_bridge.py` | `decide_voice()`, `decide_after_copilot()`, `VoiceRoute` |
| Efeito | `scripts/ha_bridge.py` | `_speak_via_copilot()`, `speak()`, `make_speaker()`, `warn_if_mute()` |
| Contrato | `core/ha_announce.py` ↔ `scripts/ha_bridge.py` | Saídas `0`/`3`/`4`, duplicadas de propósito (a ponte é stdlib-only) |
| Fala relatada | `ui/app.py` `_speak_announcement` | Passa a checar o retorno de `speak()` |
| Testes | `tests/test_ha_bridge_voice.py` (48) + 12 novos em `test_ha_announce.py` | Rota, forma do pedido, CLI, paridade |

Decisões que valem registrar:

- **A ponte continua stdlib-only.** Ela não importa `zorin_copilot`; duplica as três constantes
  de código de saída e **um teste trava a paridade**. Importar o pacote destruiria a propriedade
  que a torna instalável em qualquer máquina com Python 3.
- **A ordem das condições do plano B é a regra, e a primeira é a que mais importa.** Se o
  Copilot falou, **não se cai em nada**. Sem essa condição o `spd-say` repetiria a frase logo
  depois e a casa diria tudo duas vezes, com dois timbres.
- **`--voice copilot` é estrito:** prefere o silêncio a reintroduzir a segunda boca. Quem pediu
  uma voz só não quer um plano B que a desfaça.
- **O texto vai colado à opção** (`--announce=<texto>`), não como argumento separado. Medido com
  o parser real do GTK: `--announce=--toggle` chega ao app como a string `--toggle`, e `--toggle`
  nem aparece na lista de argumentos. Sem shell, o texto é dado — nunca código.
- **`0` significa "aceito", não "falado".** A ponte não pode confundir as duas coisas: ela
  recebe `0` e para. Por isso o app ganhou a linha
  `O anúncio não saiu pelo alto-falante: <texto>`, em `WARNING` — era o único jeito de o
  silêncio deixar rastro.
- **Aviso de mudez no arranque.** `--speak` sem nenhuma boca disponível avisa uma vez, no
  arranque, em vez de aceitar webhooks mudos. Foi medido: `spd-say` **não está instalado** nesta
  máquina, então o `--speak` de antes simplesmente não falava nada.

### Fase 1 — Jarvis: presença *(P1 + P2 + P4)*

- ✅ **P2 — política de atenção (feito em 2026-09-21).** `core/attention.py` puro, com suíte
  própria e fiação no caminho de anúncio. Ver "Fase 1 — P2, implementado" abaixo.
- ✅ **P1 — envelope e porta única (feito em 2026-09-21).** `core/ambient_events.py` puro, dois
  adaptadores, uma porta em `ui/app.py`, e o **quinto órfão** (`DocumentFileWatcher`) ligado — o
  que fechou um buraco medido na indexação. **A classe de barramento foi deliberadamente não
  escrita**: o padrão já existe duas vezes no repo. Ver "Fase 1 — P1, implementado" abaixo e a
  correção de premissa em §4 P1.
- ✅ **P4 — identidade única (feito em 2026-09-21).** `core/identity.py` puro, com suíte própria
  (38 testes) e fiação em cinco arquivos. Fechou **três defeitos medidos**, um deles uma regressão
  que já estava em produção: o Piper baixava o modelo do **faber** para qualquer voz pedida. Tom e
  janelas de silêncio ficaram **fora**, de propósito — ver a correção de premissa em §4 P4.
  Ver "Fase 1 — P4, implementado" abaixo.

**Critério de pronto:** política de atenção coberta por testes puros; `docs/` com evidência
visual do assistente reagindo; e um teste afirmando que **um evento em janela de silêncio
resulta em `IGNORAR`** (o teste que impede o recurso de se tornar um incômodo).

**Estado da Fase 1: FECHADA.** P1, P2 e P4 feitos. Todas as três com suíte pura + fiação,
validação por mutação e evidência registrada. O critério de pronto acima está satisfeito:
`tests/test_attention.py` (78 casos) contém o teste da janela de silêncio, e
`docs/politica-atencao-ligada.png` / `-desligada.png` são a evidência visual.

> **Achado colateral que vale mais que a fase:** ao fechar a P4 descobriu-se que o runner de
> guarda **não rodava 435 testes** — 16 módulos em estilo pytest saíam com `exit 5` ("Ran 0
> tests") e o baseline os registrava como "estruturais, não é regressão" desde 121/137. O
> `tests/test_study_tutor.py`, que cobre a injeção do `TUTOR_ADDON` no prompt, era um deles.
> Corrigido o runner (`pytest` por módulo): **147/147 módulos, 0 falhas**. Detalhe em
> `REF-testes.md`. Um número de baseline que nunca muda não é tranquilizador — é suspeito.

#### Fase 1 — P2, implementado

O problema, medido no código: `_on_ha_event` tinha **uma** pergunta binária — *"estou ocupado?"* —
e a única resposta possível era descartar a frase. Qualquer sessão de voz ativa calava os
anúncios, e um evento às 3h da manhã era falado como um evento às 15h. É o default certo para
começar e o errado para conviver.

A peça que faltava não era capacidade: era **decisão**. E ela é pura — sem GTK, sem rede, sem
áudio, sem relógio, tudo entra por parâmetro. Mesmo padrão de `core/board_outcome.py` e
`core/ha_announce.py`, pelo mesmo motivo: a regra tem de ser exercitável sem display.

| Peça | Onde | Papel |
|---|---|---|
| Decisão (pura) | `core/attention.py` | `decide_attention()`, `severity_for()`, `AttentionLevel`/`Severity`/`AttentionReason` |
| Fio | `ui/app.py` (`_attention_policy`, `_interruption_budget`, `_note_interruption`, `_screen_is_locked`, `_attention_context`, `_on_ha_event`) | Pergunta a política a cada evento e obedece: fala, aviso, selo ou nada |
| Config | `core/config.py` | `attention_enabled` (default **True**), `attention_silence_hours`, `attention_max_per_hour`, `attention_away_minutes` |
| Preferências | `ui/preferences.py` | Grupo "Política de Atenção" na aba Conexões, ao lado dos anúncios que ela governa |
| Testes | `tests/test_attention.py` (76), `tests/test_attention_wiring.py` (50) | Regra pura + fio, sem display |
| Evidência visual | `docs/politica-atencao-ligada.png`, `docs/politica-atencao-desligada.png` | O grupo renderizado de verdade, nos dois estados |

**São duas perguntas, e elas são diferentes de propósito.** `decide_announcement` responde sobre o
**evento** (*isto tem o que dizer?*): é de estado, passa o filtro, mudou de verdade, tem frase.
`decide_attention` responde sobre o **usuário e o momento** (*vale interromper agora, e com que
força?*). A segunda não precisa saber nada sobre entidades do Home Assistant — e é por isso que
ela pode, um dia, servir ao *file watcher* e ao `desktop_context` sem uma linha de mudança.

A ordem das condições **é** a regra, e vai da mais fundamental para a mais circunstancial:

| # | Condição | Nível | Motivo |
|---|---|---|---|
| 1 | política desligada | `IGNORE` | `disabled` |
| 2 | `URGENT` (fumaça, gás, água, CO) | `SPEAK` | `urgent` — fura tudo, menos a própria fala |
| 3 | já falando | `BADGE` | `speaking` |
| 4 | janela de silêncio | `IGNORE` | `silenced` |
| 5 | tela bloqueada | `BADGE` | `screen_locked` |
| 6 | sessão de voz ao vivo | `NOTIFY` | `live_session` |
| 7 | orçamento estourado | `BADGE` | `budget_exhausted` |
| 8 | usuário ausente | `BADGE` | `away` — **pulada** quando a ociosidade é desconhecida |
| 9 | evento corriqueiro | `BADGE` | `routine` |
| 10 | caso contrário | `SPEAK` | `normal` |

Decisões que valem registrar:

- **Quatro níveis, não dois.** `NOTIFY` e `BADGE` são coisas diferentes: `NOTIFY` é alerta
  transitório ("olhe agora"), `BADGE` é contador persistente ("aconteceu, veja quando puder"). É
  essa distinção que permite trocar **gotejamento por digesto** em vez de simplesmente calar.
  Rebaixar em vez de descartar é a diferença entre guardar a informação e perdê-la.
- **`URGENT` é "fura a fila", não sinônimo de "importante".** Uma detecção de fumaça às 3h com o
  orçamento estourado **tem** de ser dita — é exatamente o evento para o qual o resto da política
  seria um erro fatal. A única coisa que a urgência não vence é a própria fala em andamento: não
  há como sobrepor áudio, então ali ela vira `NOTIFY`, para ao menos ser vista já.
- **A janela de silêncio é `IGNORE`, não `BADGE`.** Durante a noite um evento corriqueiro não é
  informação pendente — é ruído. Marcá-lo deixaria um selo esperando o usuário toda manhã por
  coisas que ele não queria saber.
- **A assimetria dos defaults sem sentido — e por que ela é correta.** Um `interval_hours`
  negativo na consolidação vira "nunca rodar"? Não: ali o **permissivo** é o seguro, porque um
  recurso invisivelmente parado não é descoberto por ninguém. Aqui o sinal se inverte: um
  `max_per_hour` sem sentido que virasse "sem limite" deixaria o assistente **visivelmente
  falante**, que é o defeito que este módulo existe para impedir. Então `max_per_hour <= 0`
  significa **nada cabe**. O princípio já estava escrito em `ha_announce.py`: *"o erro barato é
  falar de menos, não de mais"*.
- **`None` ≠ `0.0` para ociosidade desconhecida.** Quando não se sabe há quanto tempo o usuário
  saiu, a condição de ausência é **pulada**, não chutada. Um palpite errado aqui ou silencia o
  assistente sem motivo, ou o faz falar para uma cadeira vazia. Medido nesta máquina:
  `org.freedesktop.ScreenSaver.GetActive` responde `(False,)`, mas `GetSessionIdleTime` devolve
  `UnknownMethod` e `org.gnome.Mutter.IdleMonitor` / `org.gnome.ScreenSaver` / `login1` estão
  `ServiceUnknown` — logo o que se sabe de verdade é só o **bloqueio de tela**.
- **A sonda de bloqueio erra para o lado que se vê.** Falha de barramento devolve `False` (não
  bloqueado) e registra **uma vez** por execução. Presumir bloqueio calaria o assistente sem que
  nada aparecesse no log — e um silêncio sem causa é o defeito que este trabalho existe para
  evitar. O caminho oposto erra para o lado que se **vê**, e o que se vê, se conserta.
- **Nomear uma entidade no filtro é intenção declarada.** `light` é `ROUTINE`, então quem listasse
  `light.sala` em `ha_announce_entities` veria o filtro virar um botão que não faz nada. Daí
  `explicitly_selected`: nomear promove `ROUTINE` → `NORMAL`, e **nunca** rebaixa `URGENT` — um
  sensor de fumaça é urgente por si, nomeado ou não. Este foi um defeito real, pego pela suíte
  antiga antes de sair: a classificação por severidade teria emudecido entidades nomeadas.
- **Sem sentinela para "sem limite".** Um valor que significa o contrário do que aparenta é o tipo
  de coisa que se digita por engano. Quem quer mais folga aumenta o número.
- **O orçamento é uma janela deslizante, não um contador com temporizador.** Um contador
  precisaria de um temporizador para zerar, e o temporizador morreria com o processo — o mesmo
  motivo pelo qual a consolidação usa estado em disco. Aqui uma fila de instantes é mais simples e
  não guarda nada entre execuções.
- **`_ROUTINE_DOMAINS` foi apagado, e a ausência está documentada.** Ele era código morto:
  devolvia exatamente o mesmo que o caso geral. Descoberto por mutação — remover aquele ramo não
  mudava desfecho nenhum, o que é a definição de um ramo que não precisa existir.

Chaves em `config.json` (aba **Conexões** → "Política de Atenção"):

| Chave | Padrão | Descrição |
|---|---|---|
| `attention_enabled` | `true` | Liga a política. Desligada, todo evento com frase é falado na hora. |
| `attention_silence_hours` | `"23-7"` | Intervalos de hora em que não se fala nem avisa. Vazio desliga. |
| `attention_max_per_hour` | `6` | Teto de falas na última hora. `0` = nada cabe. |
| `attention_away_minutes` | `30` | Ausência a partir da qual só se marca. **Sem linha na tela** — ver abaixo. |

**Por que `attention_away_minutes` não tem linha na interface.** A condição de ausência existe e é
testada, mas hoje ela é **pulada em produção**: `_attention_context` manda a ociosidade como
`None` porque não há fonte confiável nesta máquina (medido acima). Um controle que não muda nada
é pior do que um controle ausente — mesma regra pela qual `memory_consolidation_interval_hours`
não tem linha. A chave continua na config para que os testes possam exercitá-la, e volta para a
tela quando houver fonte. Há teste fixando as duas metades disso.

**O que ainda não existe — e está registrado como pendência, não como feito:** o contador
`_attention_badges` **não tem consumidor visível**. Hoje ele aparece no log. É a entrada já
testada do digesto da Fase 2 ("uma fala às 18h com o que mudou, em vez de sete avisos ao longo do
dia"), mas quem ler o contador como recurso pronto vai se enganar. O comentário no código diz
isso explicitamente.

#### Validação por mutação (Fase 1, P2)

Suíte verde não prova nada se os testes não morderem. Cada mutação abaixo é uma crença da
implementação quebrada de propósito: **35 de 36 foram mortas**, e a que sobrou foi classificada
— não "consertada" às cegas.

| Crença quebrada | Desfecho |
|---|---|
| ordem: `enabled=False` deixa de vencer o `URGENT` | morta |
| ordem: `URGENT` deixa de furar a janela de silêncio | morta |
| ordem: janela de silêncio passa a devolver `BADGE` | morta |
| ordem: tela bloqueada sobe para `SPEAK` | morta |
| ordem: sessão ao vivo passa a `BADGE` / a `SPEAK` | mortas |
| ordem: orçamento estourado passa a `NOTIFY` | morta |
| ordem: ausência passa a `NOTIFY` | morta |
| ordem: corriqueiro sobe para `SPEAK` | morta |
| ordem: `speaking` desce para `IGNORE` | morta |
| ordem: `speaking` sobe para `SPEAK` (atropela a própria fala) | morta |
| severidade: fumaça deixa de ser urgente / `off` deixa de ser normal | mortas |
| severidade: movimento/ocupação deixam de ser corriqueiros | mortas |
| severidade: porta sem `device_class` sobe para urgente | morta |
| severidade: domínio normal (`person`, `lock`) desce para corriqueiro | morta |
| severidade: `URGENT` deixa de vencer a seleção explícita | morta |
| severidade: seleção explícita rebaixa em vez de promover | morta |
| seleção: entidade vazia passa a casar | morta |
| silêncio: fim deixa de ser exclusivo | morta |
| silêncio: janela que atravessa a meia-noite deixa de casar | morta |
| silêncio: lixo no texto derruba a leitura inteira | morta |
| silêncio: hora fora de 0–23 passa a casar | morta |
| orçamento: `max_per_hour <= 0` vira "sem limite" | morta |
| orçamento: fronteira de esgotamento fica exclusiva | morta |
| orçamento: `remaining` deixa de ser clampado | morta |
| níveis: `BADGE` passa a contar como interrupção | morta |
| níveis: `IGNORE` renomeado | morta |
| motivo: `SILENCED` renomeado | morta |
| `to_dict`: perde o motivo | morta |
| fio: `app` deixa de consultar a política | morta |
| fio: `app` passa `busy=True` para `decide_announcement` | morta |
| fio: `app` deixa de anotar a interrupção | morta |
| fio: `app` deixa de contar o selo | morta |
| fio: `app` mostra o aviso sem passar por `idle_add` | morta |
| preferências: coletor esquece o interruptor / o silêncio / o orçamento | mortas |
| **seleção: filtro vazio passa a nomear** | **equivalente** |

A sobrevivente **não é lacuna de teste**: ela é *equivalente*. Trocar `{'light.sala'}` por
`set()` no filtro do teste dá exatamente o mesmo resultado, porque a pertinência de string vazia
é idêntica nos dois casos — nenhuma asserção poderia distinguir. Isso não é um teste fraco; é a
definição de uma mutação que não muda comportamento observável. O harness passou a marcar essas
em vez de contá-las como falha, o que separa *"o teste não morde"* de *"não há o que morder"*.

E uma sobrevivente **era** lacuna real, e virou uma remoção de código morto: *"a luz deixa de ser
corriqueira"* sobreviveu porque remover o ramo `_ROUTINE_DOMAINS` dava o **mesmo** resultado que o
caso geral. Nenhum teste poderia matá-la, porque não havia diferença a observar. O ramo foi
apagado e a ausência ficou documentada no módulo.

Harness: `mutate_attention.py` — **perdido** na limpeza de `/tmp`; ver a nota sobre
`tests/mutacao/` acima. As 36 mutações e os resultados estão registrados nesta seção.

#### Evidência visual (Fase 1, P2)

A tela foi **renderizada de verdade** e olhada, não afirmada: `PreferencesDialog` real →
`Gtk.WidgetPaintable` → `Gtk.Snapshot` → `Gsk.Renderer` → PNG. O grupo é extraído do diálogo
(que vive em superfície própria e não é fotografável) e reparentado numa janela comum.

- `docs/politica-atencao-ligada.png` — política ligada: as três linhas em contraste pleno, o
  interruptor ligado, `23-7` carregado do config real e o orçamento em `6`.
- `docs/politica-atencao-desligada.png` — política desligada: as duas linhas de ajuste fino
  **apagadas**. É a regra "um ajuste sem efeito é pior do que um ajuste escondido" tornada
  visível — e o que prova que ela funciona, em vez de só existir no código.

Duas armadilhas custaram tempo e ficam registradas para a próxima vez: o PNG sai **RGBA com fundo
transparente** quando se rasteriza a **página** (o fundo é pintado pela janela, não pela página) —
o que exibe como texto claro sobre branco e se confunde com defeito de contraste do tema; e
`set_default_size` é só um **pedido**, que o compositor ladrilhador ignorou (a janela saiu
474×253 e cortou o grupo). Rasterizar a **janela** e exigir o tamanho com `set_size_request`
resolve os dois.

#### Fase 1 — P1, implementado

O P1 estava descrito como *"um fluxo de eventos com um único `subscribe`"*, e a medição
mostrou que **não havia fluxo para unificar**: uma fonte viva (a casa) com um consumidor, e
três módulos parados. O que o P1 entregou, então, foi menor e mais concreto: **o envelope, a
porta, e o fio que faltava na indexação.**

**O buraco medido que o P1 fechou — e ele era real.** `index_directories()` é chamado
**uma única vez**, no arranque (`core/rag.py:729`, dentro de `start_background_indexing`,
invocado em `ui/app.py:327`). Nada re-varre durante a sessão. Consequência: um documento
salvo com o app aberto **nunca entra no índice** até o próximo arranque — ele existe no
disco e não existe para a busca. Não é uma capacidade que faltava; é um fio.

O `DocumentFileWatcher` já estava escrito, com 141 linhas e suíte própria. Era o **quinto
órfão** (§3.1) — ninguém o instanciava. Ligá-lo foi o trabalho inteiro.

| Peça | Onde | Papel |
|---|---|---|
| Envelope (puro) | `core/ambient_events.py` | `AmbientEvent`, `AmbientSource`, `DocumentKind`, `home_event()`, `document_event()` |
| Porta | `ui/app.py:_on_ambient_event` | Roteia por **fonte** — e só por isso |
| Adaptadores | `ui/app.py:_on_home_ambient`, `_on_document_ambient` | Traduzem o formato cru de cada produtor; descartam o que não é percepção |
| Tratamento | `ui/app.py:_on_document_event` | Passa pela **mesma** `decide_attention`, com severidade `ROUTINE` |
| Fio do monitor | `ui/app.py:_init/_stop/_reconfigure_file_watcher` | Sobe, para e reconfigura o watcher; teardown passo 7 |
| Config | `core/config.py` | `file_watcher_enabled` (default **True** — ver abaixo) |
| Preferências | `ui/preferences.py` | Linha no grupo de privacidade, ao lado das pastas confiáveis que ela vigia |
| Testes | `tests/test_ambient_events.py` (24), `tests/test_ambient_wiring.py` (39), `tests/test_file_watcher.py` (27) | Envelope + fiação + o monitor, sem display |
| Mutações | 19 (envelope) + 26 (fiação) + 18 (monitor) | **63 crenças quebradas: 62 mortas**, 1 equivalente, 0 inválidas |

**O que foi deliberadamente NÃO construído: a classe de barramento.** Duas razões medidas, e
a primeira é a que importa — *o padrão já existe duas vezes no repo*:
`StudySession.subscribe`/`publish` (`core/study_session.py:108`) e
`HomeAssistantRealtimeChannel.subscribe` (`core/home_assistant_realtime.py:246`) já fazem
cópia sob trava, isolamento por assinante e cancelamento idempotente. Uma terceira cópia não
seria arquitetura, seria duplicação com nome novo. A segunda: **o nome `perception` já tem
dono** — `core/perception.py` é o canal de **visão** do operador (AT-SPI2 + captura de tela +
*grounding* por VLM), consumido por `cli.py`, `ui/app.py` e `browser_runtime.py`. O módulo
novo documenta no cabeçalho a pré-condição para uma classe de barramento passar a valer:
**uma segunda fonte que interrompa.** Enquanto só a casa merece fala, `subscribe` genérico
seria estrutura sem função.

**Duas descobertas que só apareceram porque o módulo passou a rodar de verdade.**

1. **O caminho do `delete` roda na thread da UI.** `_on_file_changed` é o sinal `changed` do
   `Gio.FileMonitor`, entregue pelo `GMainContext` — a mesma thread do GTK. O caminho do
   `index` sai da thread de debounce, mas o do `delete` **não**. A primeira versão da
   docstring em `app.py` dizia *"roda na thread do produtor, nunca na da UI"*: estava errada,
   e o erro era do tipo que causa defeito depois — o próximo autor acreditaria nela e
   chamaria `show_toast` ali dentro. Corrigido, e **travado em teste**: o cabeçalho do
   `file_watcher.py` nomeia as duas threads, e `tests/test_file_watcher.py` cobra que continue
   nomeando.
2. **O teste de debounce do monitor era um teste que mentia.** Ele **reimplementava o laço do
   worker dentro do corpo do teste** e depois assertava sobre a própria reimplementação —
   passava com o worker correto e com o worker quebrado, porque nunca chamava o worker. Um
   teste que copia a lógica que deveria verificar é pior do que um teste ausente: ocupa o
   lugar do que faltava. A suíte foi de 2 para 27 testes, e o worker passou a ser exercitado
   de verdade, numa thread de verdade.

**Três defeitos silenciosos encontrados pelo caminho, todos da mesma família** — *funciona,
mas mente para quem lê*:

| Defeito | Como apareceu | Por que importava |
|---|---|---|
| `Any` usado em `_on_file_changed` **sem estar importado** | varredura de `typing.get_type_hints` na classe | `from __future__ import annotations` transforma anotação em string: só quebra quando alguém **resolve** — mypy, IDE. O módulo funciona; a ferramenta que o lê, não. |
| Docstring afirmando "nunca na thread da UI" | leitura do `Gio.FileMonitor` contra o `GMainContext` | contrato errado escrito no código é pior do que contrato ausente: o próximo autor confia nele |
| `stop()` não esperava a thread do worker | a thread dorme 0,5 s entre ciclos | "parou" não queria dizer parou: o worker ainda indexava **depois** do teardown |

**O default de `file_watcher_enabled` é `True`, ao contrário da consolidação e dos anúncios.**
A assimetria é deliberada e segue o princípio já registrado neste roteiro: *um valor sem
sentido erra para o lado que se vê.* A consolidação **apaga** linhas e é opt-in; o monitor só
**completa** um recurso que já está ligado (a indexação do RAG), então o interruptor existe
para poder desligar, não para precisar ligar.

**O que ficou de fora, registrado como pendência:**

- **O contador de selos continua sem consumidor visível** — agora com **duas** fontes somando
  nele (`_note_badge`, sob trava). Para documento, silêncio é a resposta **certa**: o
  consumidor visível daquela fonte é o índice do RAG, que o usuário vê na busca. A entrada do
  digesto da Fase 2 está pronta e é a única coisa que falta para ela valer.
- **Mudar as pastas confiáveis ainda exige reiniciar.** `watched_dirs` é montado no arranque
  (`core/rag.py:124`); o `_reconfigure_file_watcher` sobe o monitor de novo, mas com a mesma
  lista. O limite está escrito na docstring do método para não parecer que o interruptor
  cobre isso.

#### Fase 1 — P4, implementado

**Três defeitos medidos, e nenhum deles era "falta de campo".** Os três têm a mesma assinatura:
*funciona, mas mente para quem lê*.

| # | O defeito | Como foi medido | O conserto |
|---|---|---|---|
| 1 | `config.system_prompt` era **escrito** por `load()`, **persistido** por `save()` e **lido por ninguém**. `build_system_prompt()` usava sempre a constante. Quem editasse o campo não mudava nada — e continuava vendo o campo preenchido. | `grep` de `system_prompt` em todo `src/`: 4 sítios de escrita, 0 de leitura | `build_system_prompt()` passa a ler; o campo vira **override** de verdade |
| 2 | O nome `"Zorin Copilot"` vivia em **quatro** lugares: título da janela, **duas** vezes dentro do `SYSTEM_PROMPT`, e o texto gerado por `load()`. Mudar um não mudava os outros. | `grep` do literal: 4 sítios independentes | Sentinela `__ASSISTANT_NAME__` no texto base + `_apply_identity()` no título |
| 3 | **`ensure_model_available()` baixava o modelo do `faber` para qualquer voz.** `model_path` saía de `voice_name`; `PIPER_MODEL_URL` era constante do faber. Pedir `cadu` gravava bytes do faber em `pt_BR-cadu-medium.onnx` e registrava *"carregado com sucesso: pt_BR-cadu-medium"*. | `curl` no índice oficial: `cadu`, `jeff` e `edresson` existem; a URL derivada é 200 e a do faber também — **a falha era silenciosa, não um 404** | `voice_urls()` deriva caminho **e** URL da mesma fonte |

O defeito 3 é o mais grave dos três, e não é hipotético: ele é uma **regressão já em
produção**. O log confirmava a voz errada, o que torna o erro pior que uma falha — quem
lesse o log concluiria que estava tudo certo.

**A peça central: `core/identity.py`, puro.** Zero import de efeito (verificado por varredura
AST, incluindo imports tardios). Mesmo padrão de `core/attention.py` e `core/board_outcome.py`.

| Peça | Onde | Papel |
|---|---|---|
| Decisão (pura) | `core/identity.py` | `parse_voice()`, `voice_urls()`, `AssistantIdentity`, `build_prompt()`, `voice_from_selection()`, `is_legacy_auto_prompt()` |
| Fio — prompt | `ai/providers.py` | `build_system_prompt()` vira adaptador fino: ambiente + addon do tutor são o que o provedor sabe e a identidade não |
| Fio — migração | `core/config.py` | `load()` deixa de gerar o prompt e **descarta** o texto que ele mesmo gerou |
| Fio — voz | `ai/local_voice.py` | URL e caminho derivados da mesma voz; voz malformada **recusa** em vez de baixar outra |
| Fio — janela | `ui/app.py` | `_apply_identity()` depois de `self.config` e no `_on_config_saved` |
| Fio — tela | `ui/preferences.py` | grupo "Identidade" (nome + voz), e o coletor preserva a persona |

**A decisão de escopo que mais importou: o override substitui a persona, e só ela.**
`PERSONA_MARKER = "DIRETRIZES TÉCNICAS E DE RESPOSTA:"` separa o bloco de personalidade do
contrato de saída do agente — esquema JSON, regras de `explanation`, lista de ferramentas —
que mora na **mesma string**. Um override ingênuo (substituir o texto inteiro) faria um
usuário que escrevesse *"seja breve e sarcástico"* perder o esquema e o agente parar de
responder, **sem nada na tela ligando a caixa "prompt" a isso**. Tom e janelas de silêncio
ficaram de fora por motivos simétricos (§4 P4).

**A migração é obrigatória, e é uma armadilha de duas pontas.** Todo `config.json` já usado
tem o prompt gerado gravado em `system_prompt`. No instante em que o campo passa a ser lido,
aquele texto vira um override — e o prompt cai de **9.479 para ~260 caracteres** em todas as
instalações existentes. Regressão silenciosa. `is_legacy_auto_prompt()` reconhece o texto
gerado (âncoras nas **duas** pontas: um prompt escrito por pessoa pode começar igual, mas não
termina com aquela frase exata) e o descarta — vazio volta a significar "derive".

**Prova de que o refactor não mudou comportamento:** o prompt composto com identidade default
tem **9.479 caracteres** e `sha256` `9d62ea3f74ea0f0e`, idêntico ao de antes do P4. Sem esse
número, "refatorei sem mudar nada" seria asserção.

**Validação por mutação — 37 mutações, 37 mortas, 0 sobreviventes, 1 equivalente.**
Arquivo: **`tests/mutacao/mutate_identity.py`** (`.venv/bin/python tests/mutacao/mutate_identity.py`).
A central, e a razão de o harness existir:

> *"reintroduzir a URL hardcoded do faber tem de falhar"* — **KILLED.**

Quatro sobreviventes apareceram na primeira execução. **Três eram lacunas reais** e foram
fechadas com teste (o `.json` gravado por cima do `.onnx`; o addon do `TUTOR_ADDON`
descartado; o coletor resetando a voz no salvamento). **O quarto não era lacuna**: remover
`if "_" not in locale` de `parse_voice` não muda comportamento, porque o `partition` seguinte
já rejeita um locale sem `_`. Medido, não presumido: **324 entradas hostis, 0 divergências**.
A linha fica, com comentário dizendo que é redundante de propósito — e o harness a registra
como EQUIVALENTE em vez de fingir que um teste a cobre.

**Suítes:** `tests/test_identity.py` (38) + `tests/test_identity_wiring.py` (41) = **79**.
Somadas às cinco suítes das fases anteriores: **343 testes verdes** em 10 s.

**Duas armadilhas de teste pagas nesta fase**, ambas registradas em `REF-testes.md`:

1. **Varredura de fonte não distingue uso de menção.** `assertNotIn("PIPER_MODEL_URL",
   read_text())` acusou o próprio comentário que documenta a remoção. Aconteceu **duas vezes
   no mesmo arquivo**. Conserto: perguntar ao `ast`, escopando no **corpo da função**.
2. **Comparar composto com texto-base compara grandezas diferentes.** `SYSTEM_PROMPT` carrega
   a sentinela (18) duas vezes; o composto tem o nome (13).
   `tests/test_desktop_portability.py` media `len(prompt)` contra `len(SYSTEM_PROMPT)` e
   quebrou com erro de exatamente `2 × (18 − 13) = 10`. **O teste estava errado, não o
   código** — corrigido para comparar composto com composto, que é asserção mais forte.

**Pendência registrada:** `assistant_name` não aparece na **pílula de voz** nem nas
notificações. `_apply_identity()` é o ponto de extensão declarado para isso — hoje escreve só
o título da janela. Não é esquecimento: é o mínimo que fecha os quatro lugares do defeito 2.

### Fase 2 — Jarvis: proatividade com critério

~~Horas de silêncio, orçamento de interrupções por hora, supressão durante sessão ao vivo~~ — os
três vieram **junto com a P2**, na Fase 1, porque sem eles a política não teria sobre o que
decidir. O que sobra para esta fase é o que a P2 deixou **preparado mas não entregue**:

- **Digesto em vez de gotejamento** — uma fala às 18h com o que mudou, em vez de sete avisos ao
  longo do dia. A entrada já existe, já é testada e agora tem **duas** fontes somando nela: o
  contador `_attention_badges` (casa + documentos), que hoje só aparece no log. Falta quem o
  consuma — e é a única peça que falta para o digesto valer.
- **`FastRNNRouter`** poupando LLM nos comandos diretos. *(Continua órfão — ver §3.1.)*
- **A condição de ausência de verdade** — hoje ela é pulada porque não há fonte de ociosidade
  nesta máquina. Enquanto for assim, `attention_away_minutes` fica sem linha na tela.

**Critério de pronto:** um dia de uso real sem interrupção considerada inútil — medido, não
achado.

### Fase 3 — Memória viva

Consolidador em regime, memória episódica ("o que aconteceu ontem?", "sobre o que falamos na
terça?") e recordação por tempo, apoiada em `chat_topics` + `action_logs` + `DecisionTrace`.

**Critério de pronto:** uma pergunta sobre a própria semana do usuário respondida com fatos
rastreáveis a registros reais — e a resposta **se declara incompleta** quando o registro não
cobre o período.

### Fase 4 — Ultron: missões persistentes *(P3)*

Loja de *jobs* durável, missões agendadas, `SubagentPipeline` rodando como missão de fundo, e
**trilha de auditoria durável** de tudo que foi feito sem supervisão direta.

**Critério de pronto:** uma missão sobrevive a um reinício do app, **volta pedindo aprovação**,
e o relatório do que ela fez é reconstruível a partir do banco.

### Fase 5 — Ultron: alcance

Acesso remoto conforme `docs/ACESSO_REMOTO.md` e `docs/ACESSO_REMOTO_KDE_CONNECT.md` — que já
concluíram o desenho e a ordem certa: **consultar → receber → aprovar → comandar**. O gateway
expõe só ferramentas puras, recortadas por `ToolRegistry.restricted`.

---

## 6. Segurança: o preço do Ultron

Quatro regras que não se flexibilizam, todas apoiadas em peças que **já existem**:

1. **Proatividade nunca pula a aprovação.** A `RiskPolicy` continua sendo o portão. Um evento
   percebido pode *propor*; propor não é executar. (O repo já aprendeu isto do jeito difícil —
   ver `core/board_outcome.py` e a distinção `accepted ≠ opened`.)
2. **Origem remota nunca herda `--yes`.** Aprovação remota usa `step_id` de uso único, escopo
   próprio e expiração curta.
3. **Toda ação autônoma é auditável depois.** Não basta o log de execução: é preciso
   reconstruir *por que* ela foi tomada. `DecisionTrace` é o embrião; falta torná-lo durável.
4. **A ponte HA continua sendo só de entrada.** `ha_bridge.py` declara em comentário que não
   executa comandos arbitrários vindos da rede. Isso é um acerto de projeto, não uma limitação
   a corrigir. *Receber da rede ≠ obedecer à rede.*

---

## 7. O que **não** fazer

- **Não fazer a ponte HA virar canal de comando.** O desenho atual (só notifica e fala) é
  melhor do que parece; transformá-la em canal de execução abre uma porta de entrada que o
  resto do projeto evitou com cuidado.
- **Não abrir microfone para nuvem em escuta contínua.** A escuta passiva é Vosk **local**; o
  Gemini Live é acionado *depois* da palavra de ativação. Manter essa fronteira é o que torna
  "sempre ouvindo" aceitável.
- **Não construir o Ultron antes do freio.** Auditoria durável e aprovação fora de banda são
  pré-requisitos da Fase 4, não itens da Fase 6.
- **Não confundir `core/trust.py` com autonomia.** Um é sobre documentos e PII; o outro, sobre
  quanto o agente pode fazer sozinho. Nomes iguais, problemas diferentes.
- **Nada de "AI slop" visual.** Qualquer indicador de proatividade segue as HIG: ícones
  simbólicos Adwaita, `tabular-nums`, sem gradiente decorativo.

---

## 8. Próximo passo concreto

**Fase 0, item 1 — FEITO (2026-09-21).** O canal que já estava aberto tem agora um
consumidor: `on_event` assinado em `ui/app.py`, decisão pura em `core/ha_announce.py`, fala por
`LocalVoiceSynthesizer.speak()`, com o alto-falante reivindicado dentro da trava. Verificada
contra o Home Assistant real (evidência na Fase 0). Testes: 94, sem display.

**Item 2 da Fase 0 — a segunda boca, fechada (2026-09-21):**

> A ponte emitia a fala por `spd-say` — um processo separado, com outro timbre, que nem sabia
> que o Copilot existia. Enquanto houvesse duas bocas, não haveria uma presença: haveria dois
> programas falando na mesma casa.

Agora `scripts/ha_bridge.py` **pede** a fala ao Copilot
(`zorin-copilot --announce=<texto>`, pela instância única do GTK — o mesmo IPC de
`--toggle`/`--voice`) e só usa `spd-say` quando não há instância. `--voice auto|copilot|system`
torna a escolha explícita. A decisão de rota é pura (`decide_voice`,
`decide_after_copilot`) e o pedido vai como **um** argumento colado à opção, nunca por shell.

O que essa mudança custou em código diz o que ela valeu: três funções puras e uma constante de
timeout. A peça que faltava não era capacidade — era o fio.

**Evidência (medida, não afirmada):**

| O que | Como | Resultado |
|---|---|---|
| `--announce` sem app | binário real, nenhuma instância | saída **3** + `Pedido de anúncio não atendido (no_target).` |
| `--announce` vazio | binário real | saída **4** |
| `--announce` com app | instância real no barramento (`--background`, 198 MB) | saída **0** + `Anunciando texto de ponte: Alguém tocou a campainha` |
| Forma do argumento | stand-in no PATH, `subprocess.run` real | `['--announce=Alguém tocou a campainha']`; `--toggle`, `$(id)` e crases chegam intactos |
| Cadeia inteira | webhook HTTP → ponte → app real | HTTP 200, app loga o anúncio, **ponte não cai no plano B** |
| Plano B | app desligado, sem `spd-say` | `Copilot não falou (saída 3) — rota agora: silent` + `[Fala sem voz] <texto>` |
| Mutações | 11 mutações plausíveis nas regras novas | **11/11 mortas** |

**E o silêncio que ainda sobra:** `piper` **não está instalado** nesta máquina, então o app
aceita a frase, tenta falar e não sai som. Antes isso era invisível — a ponte recebia `0`
("aceito") e não tinha como saber. Agora o app registra
`O anúncio não saiu pelo alto-falante: <texto>` em `WARNING`. Falar de verdade exige
`uv sync --extra voice`.

Depois disso faz sentido a **política de atenção** (P2), que é a peça que decide se essa
presença será útil ou insuportável. Hoje a supressão é grosseira e conservadora — qualquer
sessão de voz ativa cala os anúncios —, o que é o default certo para começar e o errado para
conviver.

**Fase 1, P2 — FEITA (2026-09-21).** A supressão binária virou decisão graduada. `core/attention.py`
é puro e tem suíte própria; `_on_ha_event` passou a fazer **duas** perguntas — *isto tem o que
dizer?* (`decide_announcement`) e *vale interromper agora, e com que força?* (`decide_attention`) —
e a segunda pode responder `SPEAK`, `NOTIFY`, `BADGE` ou `IGNORE`. Rebaixar em vez de descartar é
a diferença entre guardar a informação e perdê-la.

| O que | Como | Resultado |
|---|---|---|
| Regra pura | `tests/test_attention.py` | **78** testes, sem display |
| Fio no app | `tests/test_attention_wiring.py` | **51** testes, sem display |
| Suíte total de P2 | os dois acima | **129** verdes |
| Mutações | 36 crenças quebradas de propósito | **35 mortas**, 1 **equivalente** (não é lacuna — ver a seção) |
| Critério de pronto do roteiro | *"um evento em janela de silêncio resulta em `IGNORAR`"* | cumprido, e no **fio** (`test_janela_de_silencio_nao_faz_nada`), não só na regra |
| Evidência visual | grupo renderizado de verdade, dois estados | `docs/politica-atencao-ligada.png`, `docs/politica-atencao-desligada.png` |

**O que ficou de fora, e por quê — registrado como pendência, não como feito:**

- **O contador de selos não tem consumidor visível.** `_attention_badges` acumula e aparece no
  log; é a entrada já testada do digesto da Fase 2, não um recurso pronto.
- **A condição de ausência é pulada em produção.** Medido: não há fonte de ociosidade nesta
  máquina (`GetSessionIdleTime` → `UnknownMethod`; mutter, gnome-screensaver e logind →
  `ServiceUnknown`). A chave existe e é testada; a linha na tela **não**, porque um controle que
  não muda nada é pior do que um controle ausente.
- **P4 (identidade única) — FEITA em 2026-09-21.** Ver o bloco abaixo. A Fase 1 está **fechada**.

**Fase 1, P1 — FEITA (2026-09-21).** O envelope (`core/ambient_events.py`, puro), dois
adaptadores, uma porta (`_on_ambient_event`) e o **quinto órfão** ligado: o
`DocumentFileWatcher` passou a manter o índice do RAG em dia durante a sessão — antes,
`index_directories()` rodava **uma vez** no arranque e um documento salvo com o app aberto só
entrava no índice no próximo arranque. **A classe de barramento foi deliberadamente não
escrita**: o padrão já existe duas vezes no repo (`StudySession`, `HomeAssistantRealtimeChannel`)
e o nome `perception` já é o canal de visão. Detalhes e correção de premissa em §4 P1.

| O que | Como | Resultado |
|---|---|---|
| Envelope puro | `tests/test_ambient_events.py` | **24** testes, 34 subcasos |
| Fiação (porta + adaptadores + ciclo de vida) | `tests/test_ambient_wiring.py` | **39** testes, sem display |
| O monitor, exercitado de verdade | `tests/test_file_watcher.py` | **27** testes (**era 2** — ver abaixo) |
| Suíte total de P1 | os três acima | **90** verdes |
| Mutações | 63 crenças quebradas de propósito | **62 mortas**, 1 equivalente, **0 inválidas** |
| Buraco fechado | `index_directories()` chamado 1× no arranque (`rag.py:729`) | documento salvo durante a sessão passa a entrar no índice sem reiniciar |

**O que ficou de fora, e por quê:**

- **O contador de selos segue sem consumidor visível** — agora com **duas** fontes somando nele
  (`_note_badge`, sob trava). Para documento, silêncio é a resposta **certa**: o consumidor
  visível daquela fonte é o índice do RAG. A entrada do digesto da Fase 2 está pronta.
- **Mudar as pastas confiáveis ainda exige reiniciar** (`watched_dirs` é montado no arranque,
  `rag.py:124`). O limite está na docstring do `_reconfigure_file_watcher` para o interruptor
  não parecer cobrir isso.

**Dois achados que valem além do P1, e que ficam registrados porque vão se repetir:**

1. **A tabela de órfãos estava incompleta — a contagem era 5, não 4.** O
   `DocumentFileWatcher` preenchia os critérios desde o início e ninguém o tinha `grep`ado.
   *A lista de órfãos era ela própria um artefato não verificado.*
2. **Um teste que mentia.** O teste de debounce do monitor reimplementava o laço do worker
   dentro do corpo do teste e assertava sobre a própria reimplementação — passava com o worker
   correto **e** com o worker quebrado. Substituído por 27 testes que exercitam o worker real.

**Próximo passo concreto:** fechar a **Fase 1** com **P4 — identidade única** — a última peça
que falta. P1 e P2 estão feitos. P4 é uma seção de configuração que possua nome, voz, tom e
janelas de silêncio, e da qual derivem **o prompt e as três saídas de voz**; hoje há três vozes
(duas já reconciliadas, ver §3.3), um prompt *hardcoded* em `ai/providers.py:64` e as janelas de
silêncio presas dentro da política de atenção em vez de serem do assistente. É a peça que faz o
Jarvis ser **um** assistente com caráter, em vez de três programas que por acaso dividem a
mesma máquina. O `TemporalStateTracker` (§3.1, órfão) entra aqui: o contexto decai com o ócio.

---

**Fase 1, P4 — FEITA (2026-09-21).** A identidade deixou de estar espalhada. `core/identity.py`
é puro e tem suíte própria; `build_system_prompt()` virou adaptador fino; o título da janela vem
de `_apply_identity()`; e a URL do Piper passou a sair da mesma fonte que o caminho do arquivo.
Três defeitos medidos, todos da família *funciona, mas mente para quem lê* — incluindo uma
**regressão já em produção**: `ensure_model_available()` baixava o modelo do `faber` para
qualquer voz pedida, gravava com o nome da voz pedida e registrava *"carregado com sucesso:
pt_BR-cadu-medium"*. Detalhe completo, tabela de peças e a decisão de escopo (tom e silêncio
ficaram **fora**) em "Fase 1 — P4, implementado".

| O que | Como | Resultado |
|---|---|---|
| Regra pura | `tests/test_identity.py` | **38** testes, 0,06 s, sem display |
| Fiação (prompt, config, voz, título, preferências) | `tests/test_identity_wiring.py` | **41** testes, 0,19 s |
| Suíte total de P4 | os dois acima | **79** verdes |
| Mutações | 38 crenças quebradas de propósito | **37 mortas**, 0 sobreviventes, **1 equivalente medida** |
| A mutação central | *"reintroduzir a URL hardcoded do faber tem de falhar"* | **KILLED** |
| Prova de não-regressão | prompt default byte a byte | `9479` chars, `sha256` `9d62ea3f74ea0f0e` |
| Migração | `config.json` existente com o prompt gerado | reconhecido e descartado; sem a guarda, cairia de 9.479 → ~260 chars |
| Suíte guardada, oito módulos tocados | `run_tests_safe.py` | **343** verdes em 10 s |

**Três lacunas de teste fechadas por mutação** (sobreviventes reais, não equivalentes): o `.json`
gravado por cima do `.onnx`; o addon do `TUTOR_ADDON` descartado; o coletor de preferências
resetando a voz no salvamento.

**O que ficou de fora, e por quê:**

- **Tom** — o único gancho seria `prompt_override`, e um override que substituísse o texto
  inteiro apagaria o contrato de saída do agente. Resolvido com `PERSONA_MARKER`, não com um
  campo "tom".
- **Janelas de silêncio** — já moram na política de atenção (P2). Duplicá-las na identidade
  criaria duas fontes para a mesma janela: exatamente o defeito que a P4 existe para corrigir.
- **`TemporalStateTracker`** — o roteiro dizia que ele "entra aqui". Medido: não tem relação com
  identidade. Continua órfão, e o motivo está em §4 P4.
- **`assistant_name` na pílula de voz e nas notificações** — `_apply_identity()` é o ponto de
  extensão declarado, mas hoje escreve só o título da janela.

---

**Próximo passo concreto: Fase 2 — o digesto.** A Fase 1 está fechada (P1, P2, P4). O que a
Fase 2 resolve já está medido e esperando: `_attention_badges` acumula há duas fases e **não tem
consumidor visível** — é a única entrada do digesto que já existe, já está sob trava e já tem
teste. A P2 decidiu *rebaixar em vez de descartar*; a Fase 2 é onde o que foi rebaixado volta a
aparecer para o usuário, no momento em que ele pode prestar atenção.

O critério de pronto da Fase 1 não mudou de sentido com a P4, e vale registrar por quê: a
identidade é o que faz o assistente ser **um** assistente, e não três saídas com nomes
diferentes. Sem ela, "Jarvis" seria um apelido para um conjunto de fios.


## Anexo — Evidência das afirmações deste documento

| Afirmação | Como foi verificada |
|---|---|
| 83 ferramentas distintas | `re.findall` sobre `ToolSpec(name=...)` em `ai/agent_tools.py` → 83, 83 únicas |
| **5** módulos órfãos (era 4) | `grep -rn` do símbolo em `src/`, excluindo o próprio arquivo **e conferindo que o acerto é código, não comentário**; cada um com `tests/test_*.py` próprio. O quinto (`DocumentFileWatcher`) foi encontrado ao medir o P1, não ao reler a tabela |
| A menção em comentário engana a contagem | `PowerManager` e `TemporalStateTracker` passaram a dar **1** acerto cada no `grep` — os dois são um comentário em `core/ambient_events.py`. Menção não é uso |
| `on_event` sem consumidor | `grep -rn "on_event("` em `src/` → só a definição (`core/home_assistant.py:743`) e `recent_events` |
| Wake word ligado ao app | `ui/app.py:295` (construção), `:312` (início), `:347`/`:351` (gatilho), `:371` (abre o Live) |
| Sem persona configurável | `grep -ni "persona\|assistant_name\|agent_name"` em `core/config.py` → vazio; prompt em `ai/providers.py:64`. **Superado em 2026-09-21 (P4):** `assistant_name` existe, e o prompt deixou de ser hardcoded — ver "P4 — evidência adicional" |
| Sem agendador | `grep -rn "schedule\|cron\|systemd\|Timer("` em `src/` → só *debounce* do MCP (`mcp/manager.py:213`) e threads de RAG/HA |
| Três vozes (agora duas) | `ai/local_voice.py:243` (Piper) · `core/config.py:30` (Live `Puck`) · `scripts/ha_bridge.py` (`spd-say`, só plano B desde 2026-09-21) |
| Ponte sem voz própria | `ha_bridge.py`: `_speak_via_copilot` chama `zorin-copilot --announce=<texto>`; `decide_voice`/`decide_after_copilot` são puras |
| Ponte não importa o pacote | `grep -n "zorin_copilot" scripts/ha_bridge.py` → nenhum import; paridade dos códigos travada em `tests/test_ha_bridge_voice.py` |
| `--announce` devolve o código certo | binário real: sem app → 3; vazio → 4; com instância (`--background`, 198 MB) → 0 |
| O argumento não vira opção | `--announce=--toggle` chega ao app como a string `--toggle` e some de `args` (parser GTK real); `subprocess.run` sem shell |
| A fala que não sai agora é relatada | `ui/app.py` `_speak_announcement` checa o retorno de `speak()`; medido no e2e: `O anúncio não saiu pelo alto-falante: <texto>` |
| `trust.py` é sobre documentos | `core/trust.py:22` `TrustLevel`, `:30` `PIISanitizer`, `:68` `DocumentTrustManager` |
| HA realtime sobe no arranque | `ui/app.py:3283-3295`, atrás de `ha_enabled and ha_realtime_enabled` |
| `on_event` passou a ter consumidor | `ui/app.py` `_init_ha_announcements` / `_on_ha_event`; antes: 0 consumidores no `src/` |
| Fio funciona contra HA real | WebSocket conectado (`connected: True`) e `light.pera_neo_10w` acionada e restaurada; 2 eventos → 2 decisões `announce` |
| Motor de voz ausente | `piper_available()` → `False`; `piper` ausente no sistema, no `.venv` e no `uv`; `piper-tts` é extra opcional (`pyproject.toml`) |
| Suíte sem regressão | **147** módulos, **0 falhas**, 171 s, pico 335 MB. *(Este número já foi "145 módulos, 16 falhas, todas `exit 5`" — ver a linha abaixo, que é a correção mais importante deste anexo.)* |
| **O `exit 5` era medição quebrada, não condição benigna** | O runner usava `python -m unittest <módulo>`, que coleta só `unittest.TestCase`; os 16 módulos em estilo pytest saíam com `Ran 0 tests` / código 5. Rodados por pytest: **435 passed, 1 skipped**. Eram **435 testes que nunca rodaram** na suíte de guarda. Runner corrigido para `pytest -q -p no:cacheprovider` por módulo → **147/147, 0 falhas**. O `tests/test_study_tutor.py`, que cobre a injeção do `TUTOR_ADDON` no prompt, era um dos 16 |
| O prompt composto não mudou no P4 | `len(build_system_prompt()) == 9479` e `sha256[:16] == 9d62ea3f74ea0f0e`, idêntico ao snapshot de antes do refactor |
| `config.system_prompt` era escrito e nunca lido | `grep -rn "system_prompt" src/` **antes** do P4 → `config.py` (default, `load()`, `save()` via `asdict`) e nenhum leitor; `build_system_prompt()` usava a constante `SYSTEM_PROMPT` |
| O nome vivia em 4 lugares | `grep -n "Zorin Copilot" src/` **antes** → `ui/app.py` (título literal), `ai/providers.py` (2× dentro do `SYSTEM_PROMPT`), `core/config.py` (texto gerado por `load()`) |
| O Piper baixava a voz errada | `PIPER_MODEL_URL`/`PIPER_CONFIG_URL` eram constantes do **faber**; `model_path` vinha de `voice_name`. Medido no índice oficial (`rhasspy/piper-voices`): 177 vozes, 4 pt_BR (`cadu`, `edresson`, `faber`, `jeff`), todas com 3 partes no nome. Pedir `cadu` gravava bytes do faber em `pt_BR-cadu-medium.onnx` e logava "carregado com sucesso" |
| A derivação é regra, não sorte do pt_BR | `voice_urls("en_US-lessac-medium")` → `.../en/en_US/lessac/medium/en_US-lessac-medium.onnx` |
| A guarda de migração é obrigatória | `is_legacy_auto_prompt()` reconhece o texto gerado com âncora nas **duas** pontas; sem ela, o campo recém-lido viraria override e o prompt cairia de 9.479 → ~260 chars em toda instalação existente |
| A mutação central morreu | `tests/mutacao/mutate_identity.py`: reintroduzir a URL do faber no download → **KILLED**. 37 mutações, 37 mortas, 0 sobreviventes |
| Harness de mutação sobrevive à limpeza de `/tmp` | Os quatro harnesses de 2026-09-21 moravam em `/tmp`; **dois evaporaram**. Agora em `tests/mutacao/`, invisíveis para o runner (`--list` → 147 módulos, nenhum do diretório) e para o `pytest` (`--collect-only tests/mutacao/` → `no tests collected`). Ambos os sobreviventes rodados do novo lugar com resultado idêntico: **18/18** e **37+1 equivalente** |
| A linha redundante é **medida**, não presumida | 324 entradas hostis em `parse_voice` com e sem `if "_" not in locale` → **0 divergências**; registrada como EQUIVALENTE no harness, com comentário no código |
| O teste de portabilidade estava errado | `tests/test_desktop_portability.py` media `len(prompt)` contra `len(SYSTEM_PROMPT)`; a sentinela (18) ×2 vs nome (13) ×2 dá erro de exatamente **10**. Corrigido para compor dois prompts e provar que a única diferença é a frase do ambiente |

### P1 — evidência adicional (Fase 1)

| Afirmação | Como foi verificada |
|---|---|
| `index_directories()` roda **uma vez** | `grep -rn "index_directories"` em `src/` → definição (`rag.py:653`) + **uma** chamada (`rag.py:729`, dentro de `start_background_indexing`), invocada em `ui/app.py:327` |
| `DocumentFileWatcher` era órfão | `grep -rn "\bDocumentFileWatcher\b" src/` excluindo o próprio arquivo → 0 acertos **antes** da fiação |
| `index_file` aceita um segundo escritor | `rag.py:141` abre conexão SQLite **nova** por chamada, com `journal_mode=WAL` e `busy_timeout=5000` — não há conexão compartilhada entre threads |
| `watched_dirs` existe e é congelado no arranque | `rag.py:124` (montado no `__init__`), lido em `rag.py:671` |
| O padrão de barramento já existe 2× | `StudySession.subscribe`/`publish` (`study_session.py:108`) · `HomeAssistantRealtimeChannel.subscribe` (`home_assistant_realtime.py:246`) — os dois com cópia sob trava, isolamento por assinante e cancelamento idempotente |
| O nome `perception` já tem dono | `core/perception.py` = canal de **visão** (AT-SPI2 + captura + VLM); consumido por `cli.py`, `ui/app.py`, `browser_runtime.py` |
| O caminho do `delete` roda na thread da UI | `Gio.File.monitor_directory` + sinal `changed` é entregue pelo `GMainContext` — a thread do GTK; o `index` sai da thread de debounce (`_debounce_worker`). Travado em `test_exclusao_roda_na_thread_de_quem_dispara_o_sinal` |
| `Any` não estava importado | `typing.get_type_hints(DocumentFileWatcher._on_file_changed)` → `NameError: name 'Any' is not defined`. Corrigido; guarda em `test_toda_anotacao_da_classe_resolve` |
| O teste de debounce mentia | o antigo `test_file_watcher_debounce_index` reimplementava o laço do worker no corpo do teste e assertava sobre a cópia. Suíte reescrita: **2 → 27** testes, worker real em thread real |
