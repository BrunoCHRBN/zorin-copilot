# PROPOSTA — Contrato de reversibilidade (item 6 do anexo de autonomia)

Complementa `docs/PROPOSTA_AUTONOMIA.md` e `docs/ANEXO_AUTONOMIA_PREREQUISITOS.md`.
Fecha o que ficou aberto no **item 6**: o critério "só vira AUTO o que dá para desfazer" hoje
limita a autonomia a um conjunto que, medido, **está vazio em produção**.

**Tese: `AUTO` é uma promessa; o `revert` é a garantia.** Uma ação só pode ser promovida a `AUTO`
se o app souber descrever e executar o inverso dela — e essa capacidade tem de ser *verificável*,
não declarada. Sem isso, `AUTO` é uma frase bonita no config.

---

## 0. Medição que muda a ordem do plano

Antes de desenhar o contrato, medi o estado atual com `autonomy_level="high"` +
`trusted_dirs=["/tmp"]` (o cenário mais permissivo que o config permite):

| chamada | resultado medido |
|---|---|
| `classify("write_file", {...})` | **AUTO** |
| `registry.call("write_file", {...})` | **"Ferramenta desconhecida: 'write_file'"** |
| `classify("write_document", {...})` | **CONFIRM** |
| `classify("smart_home_control", {"action": "turn_off", "entity": "switch.portao"})` | **SAFE** |

Três consequências, todas verificadas:

1. **A única regra `AUTO` do código aponta para duas ferramentas que não existem.**
   `write_file`/`edit_file` não estão no `ToolRegistry` (85 ferramentas; a de escrita é
   `write_document`). São nomes **legados do caminho chat/IntentEngine**: `write_file` é um
   `ActionType` (`ai/actions.py:29`), emitido pelo planejador em `ai/providers.py`, executado por
   `ActionExecutor._write_file` (`shell/executor.py:216,637`).

2. **Os dois caminhos não se cruzam.** `shell/executor.py` tem **zero** referências a
   `RiskPolicy`/`classify` (medido por grep). O caminho do executor confirma por
   `DesktopAction.requires_confirmation` — um booleano cravado em `providers.py` (5 sítios) e lido
   só em `ui/app.py:2716`. Logo, a regra `AUTO` vive no policy que o **registry** consulta,
   apontando para um nome que só existe no caminho que **não** consulta o policy.

3. **Portanto `autonomy_level="high"` é um campo que mente.** Ele promete "escrita reversível em
   `trusted_directories` roda e avisa depois" e não entrega nada observável: a ferramenta de
   escrita real (`write_document`) é `CONFIRM` e continua `CONFIRM` em zona de confiança. É a
   mesma família do `google_enabled_services` (campo lido por ninguém, antes da Fase 4) e do
   `system_prompt` zerado pela armadilha B2.

**Consequência para o plano:** não faz sentido construir o contrato de reversibilidade sobre uma
promoção ligada ao caminho errado. **Item 6.0 = religar (ou remover) a promoção.** O contrato
vem depois, porque um contrato correto sobre uma fiação morta continua sem efeito.

---

## 1. O contrato

### 1.1 Três classes, e o `None` não é zero

```
REVERSIBLE     existe caminho de volta que o app executa sozinho, com o estado
               anterior capturado NO MOMENTO da execução
IRREVERSIBLE   não existe caminho de volta
UNKNOWN        não determinado
```

`UNKNOWN` **não** é "provavelmente reversível": é tratado como `IRREVERSIBLE`. É o princípio já
registrado no projeto — valor sem sentido erra para o lado que se VÊ.

### 1.2 A regra

```
AUTO ⊆ REVERSIBLE
```

Uma linha, e é o contrato inteiro. `SAFE` e `CONFIRM` podem ser reversíveis ou não; `AUTO` não
pode ser irreversível, porque "executa sem perguntar" só se sustenta se houver volta.

### 1.3 A declaração é por (ferramenta, ação), não por ferramenta

Reversibilidade não é propriedade da ferramenta — é do par. `google_drive` `trash` é reversível;
`google_drive` `delete` não é. `google_gmail` `mark_read` é; `google_gmail` `send` não é. A
declaração tem de ser *arg-aware*, exatamente como `RiskPolicy.classify` já é.

### 1.4 Duas camadas, e a segunda é o que impede a mentira

1. **Portão** (`RiskPolicy.classify`): se o nível é `AUTO` e o par não está declarado
   `REVERSIBLE`, rebaixa para `CONFIRM` com a descrição dizendo por quê. Nunca o contrário.
2. **Verificação em execução**: quando uma ação classificada `AUTO` termina **sem** ter empilhado
   um `UndoEntry`, o contrato foi violado → registra aviso com o nome da ação.

A camada 1 é uma declaração — e declaração pode mentir. A camada 2 é a única defesa contra isso:
ela pergunta ao fato (houve revert?), não à intenção. Um teste que dirige cada par declarado e
exige um `UndoEntry` fecha o ciclo.

**Por que não um booleano.** `register_risk(name, AUTO, ..., reversible=True)` seria um campo que
mente por construção: qualquer chamador passa `True`. A declaração só vale se estiver amarrada a
um `revert` que exista de verdade.

### 1.5 Reversibilidade é POR CHAMADA, não por declaração

Medido no código que já existe: `_push_file_snapshot` (`agent_tools.py:4726` e vizinhos) reusa
`snapshot_file`, que **devolve `None`** quando não consegue fotografar — binário (`\x00` nos
primeiros 4 KB), arquivo acima de `MAX_SNAPSHOT_BYTES`, ou `OSError`. Nesse caso o early-return
deixa a pilha **sem entrada**, e a ferramenta devolve `"undoable": False`.

Ou seja: `write_document` é reversível **condicionalmente**. Declarar `write_document` como
`REVERSIBLE` e parar aí seria falso para um binário de 200 MB — a mesma mentira, em escala menor.

Portanto a camada 2 **não é refinamento, é sustentação**: a declaração diz que o app *consegue*
reverter o par; só a execução diz se *conseguiu* nesta chamada. O gancho já existe — a ferramenta
já devolve `undoable`, e o `AgentLoop` pode ler isso no passo `AUTO` para decidir o que anunciar:

- `AUTO` + `undoable=True` → aviso posterior normal ("fiz X, dá para desfazer").
- `AUTO` + `undoable=False` → aviso **com o motivo** ("fiz X; **não deu para guardar o anterior**").

O segundo caso é o que hoje passaria em silêncio. É a diferença entre um desfazer ausente e um
desfazer ausente *que o usuário não sabe que está ausente*.

---

## 2. Classificação por superfície

Reversível **hoje** = já existe fábrica de revert. A fazer = o inverso é conhecido, falta a
fábrica (capturar estado antes + empilhar).

| superfície | ação | classe | como |
|---|---|---|---|
| arquivo | `write_document` | **REVERSIBLE (existe, condicional)** | `snapshot_file` + `make_file_revert`; sem snapshot (binário/grande) ⇒ `undoable=False` |
| arquivo | `organize_directory` | **REVERSIBLE (existe)** | `make_organize_revert` |
| Calendar | `create` | REVERSIBLE (a fazer) | apagar o evento criado (`events.delete`) |
| Calendar | `update` | REVERSIBLE (a fazer) | ler o evento antes; `events.patch` com o estado anterior |
| Calendar | `delete` | REVERSIBLE (a fazer) | ler o evento inteiro antes; `events.insert` |
| Drive | `upload`/`mkdir` | REVERSIBLE (a fazer) | `trash` do que foi criado |
| Drive | `move`/`rename` | REVERSIBLE (a fazer) | inverso com `parent`/`name` anteriores |
| Drive | `trash` | REVERSIBLE (a fazer) | `untrash` |
| Drive | **`delete`/`empty_trash`** | **IRREVERSIBLE** | apaga de vez — nunca `AUTO` |
| Gmail | `mark_read`/`mark_unread`/`modify_labels` | REVERSIBLE (a fazer) | reaplicar o conjunto de rótulos anterior |
| Gmail | `draft` | REVERSIBLE (a fazer) | apagar o rascunho criado |
| Gmail | **`send`** | **IRREVERSIBLE** | a API não tem *unsend* |
| HA | `smart_home_control` (light/switch/climate) | REVERSIBLE (a fazer) | ler o estado antes (`get_status`) e reaplicar |
| HA | **`smart_home_automate`** | **IRREVERSIBLE** | uma cena pode fazer N coisas; o inverso não é derivável |
| sistema | `run_command` | IRREVERSIBLE | efeito arbitrário |
| sistema | clique, digitação, hotkey, `open_url`, `launch_app` | IRREVERSIBLE | estado de terceiros — a razão já escrita em `shell/undo.py` |

### 2.1 O caso `smart_home_control`: SAFE é o nível errado

Medido: `turn_off` em `switch.portao` hoje é **SAFE**, ou seja, executa **em silêncio** — sem
aviso posterior. Pelo próprio modelo do projeto, `SAFE` significa "nada a ver"; a ação correta
para "a casa mudou de estado" é `AUTO`: executa sem perguntar **e anuncia depois**.

Promover `smart_home_control` de `SAFE` para `AUTO` **não adiciona pergunta nenhuma** — o `AUTO`
não pede confirmação. Só torna visível o que hoje é invisível. É ganho sem custo de UX, e é
possível assim que a fábrica de revert existir (ler o estado antes de aplicar).

### 2.2 Decisão que é do Bruno (não minha)

`smart_home_automate` (`run` de cena/script) hoje também é **SAFE**. Uma cena pode trancar/destrancar
porta, armar alarme, desligar o aquecimento — e não há inverso derivável. A classificação honesta é
**`CONFIRM`**.

Isso **muda a UX**: hoje "roda a cena noite" executa direto; passaria a perguntar. É mudança de
comportamento não pedida, então fica registrada como pergunta, não aplicada — mesma regra que
seguiu a confirmação de escrita na agenda (Fase 1), que só foi ligada depois do "sim".

---

## 3. Ordem de trabalho

| # | passo | por quê nesta ordem |
|---|---|---|
| 6.0 | Religar a promoção a `write_document` (ou removê-la e deixar `high` sem regra) | Um contrato sobre fiação morta não tem efeito. Decidir primeiro **o que** a promoção deve alcançar |
| 6.1 | `shell/reversibility.py`: enum + declaração arg-aware + `reversibility_of()` | O vocabulário, num lugar só |
| 6.2 | Portão em `classify()` + teste de que `AUTO` sem declaração ⇒ `CONFIRM` | Fecha a porta para promoção por aspiração |
| 6.3 | Verificação em execução (AUTO sem `UndoEntry` ⇒ aviso) + teste | A defesa contra a declaração que mente. **Obrigatória antes de 6.0**, se a promoção alcançar `write_document`: a reversibilidade dele é condicional (§1.5) |
| 6.4 | Fábricas de revert: **HA** (`smart_home_control`) → depois **Calendar** → depois **Drive/Gmail** | HA é o maior ganho (SAFE→AUTO visível) e o mais simples (estado booleano/numérico) |
| 6.5 | `smart_home_automate` ⇒ `CONFIRM` | **Depende do "sim" do Bruno** (§2.2) |

Cada fábrica entra **junto** com a sua linha na declaração, e o teste do passo 6.3 exige o
`UndoEntry` — senão a linha é removida.

**Nota de ordem:** se 6.0 for feito como "promover `write_document`", ele vem **depois** de 6.3.
Promover antes de existir a verificação criaria um `AUTO` que, num arquivo binário, executa sem
perguntar e sem desfazer — exatamente o buraco que o contrato existe para fechar. A alternativa
segura é fazer 6.0 como "remover a regra morta", que não depende de nada.

---

## 4. Critério de pronto

1. `classify()` rebaixa a `CONFIRM` todo `AUTO` sem declaração reversível (teste + mutação).
2. Todo par declarado produz um `UndoEntry` real quando executado (teste que dirige a execução).
3. Um `AUTO` que não produza `UndoEntry` emite aviso **com o motivo** — e um teste cobre o caso
   condicional: `write_document` sobre binário/grande (`snapshot_file → None`) tem de avisar que
   não deu para desfazer, não passar em silêncio.
4. `google_gmail send`, `google_drive delete`/`empty_trash`, `run_command` e `smart_home_automate`
   **nunca** são `AUTO` (teste que enumera os irreversíveis — o teste falha se alguém promover).
5. `autonomy_level="high"` tem efeito observável **ou** é removido do config e da UI.
