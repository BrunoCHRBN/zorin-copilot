# ANEXO — Pré-requisitos e riscos para uma implementação completa e estável

_Data: 2026-09-22. Complementa `docs/PROPOSTA_AUTONOMIA.md`. Toda linha citada foi lida no
código desta árvore, não inferida._

Este anexo responde a uma pergunta diferente da proposta: não *o que* fazer, mas *o que precisa
estar resolvido antes* e *o que pode quebrar* o baseline de 147 módulos / 0 falhas.

---

## A. Bloqueadores de arquitetura (resolver ANTES de escrever a feature)

### A1. Não existe um ponto único de decisão de risco — existem pelo menos três
`ToolRegistry.__init__` cria uma `RiskPolicy()` nova quando nenhuma é passada
(`ai/agent_tools.py:244,270`). Na GUI viva há instâncias independentes:
- Live: `ai/live.py:1308` (compartilhada com o registry do Live, `live.py:1321-1328`).
- `start_agent_execution`: `ui/app.py:2803-2810` — **sem `policy=`** → policy própria.
- `continue_agent_execution`: `ui/app.py:3017-3024` — idem.
- Ainda: `cli.py:1061`, `ai/browser_runtime.py:647`, e as study tools efêmeras
  (`live.py:3338,3353,3368`).

**Consequência:** um `TrustManager` compartilhado **não** pode ser injetado uma vez pelo caminho
atual. Duas saídas, e a escolha é uma decisão de arquitetura, não detalhe:
(a) passar `policy=`/`trust=` em **todos** os sítios de construção (mais explícito, mais
fácil de esquecer um); ou (b) `RiskPolicy` aceitar um `TrustManager` injetado com default
module-level/DI (independente do número de instâncias de policy, todas consultam a mesma fonte).

> **DECISÃO (2026-09-22): opção (a).** Injeção explícita no *composition root*. A opção (b)
> exigiria um default de módulo **mutável e compartilhado** — exatamente o tipo de estado
> escondido que este projeto evita, e que arrisca o isolamento entre testes (uma regra
> `register_risk` de um teste vazando para outro). Implementado: `GeminiLiveClient` aceita
> `risk_policy=`; `ui/app.py` cria **uma** `self.risk_policy` e injeta no cliente de voz e nos
> dois registros do loop de agente. Sítios deliberadamente separados: `cli.py` e
> `browser_runtime.py` são pontos de entrada próprios (processos/rotas distintos), não a GUI.


Nota: subagentes herdam a policy do pai — `restrict_registry` usa `copy.copy`
(`agent_tools.py:316-333`, `subagents.py:471`), então o clone compartilha o atributo `policy`.
Isso é bom, mas precisa ser reconfirmado **depois** do refactor (uma cópia rasa deixa de
compartilhar se o `trust` virar estado mutável próprio).

### A2. Existe um SEGUNDO sistema de confirmação, disjunto do `RiskPolicy`
`DesktopAction.requires_confirmation` (`ai/actions.py:43`, serializado em `51`/`61`), forçado a
`True` em cinco pontos de `ai/providers.py` (425, 453, 482, 581, 618 — todos `FIX_COMMAND`).
Único consumidor em produção: `ui/app.py:2691` (o portão do `auto_execute_safe_actions`).
`ActionPlan.has_high_risk_actions` (`actions.py:129-130`) é **código morto** (só
`tests/test_core.py:78,81`).

**Consequência:** a autonomia vai ficar inconsistente se só mexer no `RiskPolicy`. Ou o anexo
declara este caminho **explicitamente fora de escopo** (com nota), ou a unificação entra no
escopo — e aí o alvo é o allowlist `safe_types` em `app.py:2690`.

### A3. `_bypass_risk_gate` é estado compartilhado sem trava
`live.py:1310` (bool) → setado `True`, redespacha recursivamente, resetado em `finally`
(`live.py:3024-3028`). **Hoje é seguro apenas porque o loop WebSocket serializa** as chamadas:
`_execute_tool_call` aguarda cada `functionCall` em sequência (`live.py:2286-2293`). Não há
`threading.Lock` em `live.py`.

Vetores latentes de fail-open: (1) qualquer produtor concorrente que chame `_dispatch_tool`
nessa janela pula o portão (`live.py:2510`); (2) `confirm_action` aninhado reseta o flag cedo
demais; (3) `_pending_actions` sem trava em iterate-and-pop (`3409-3420`, `3422-3433`, `3012`).

**Consequência:** como `AUTO` aumenta o volume de execuções automáticas, isto deixa de ser
teórico. **Pré-requisito:** trocar o bool por um **token/capacidade por chamada** e guardar o
estado com o padrão `_attention_lock` (`ui/app.py:286`, uso em `758-767`) — que já existe no
repo exatamente para contadores compartilhados entre threads produtoras.

---

## B. Armadilhas de config (a mais cara de todas)

### B1. `load()` não valida e ignora chave desconhecida
`core/config.py:323-325`: `if hasattr(config, k) and v is not None: setattr(...)`. Sem validação
de tipo/enum, sem versão, sem registro de migração (migrações são ad-hoc inline, `326-329`,
`364-365`).

**Exigência para `autonomy_level`:** (1) default explícito no dataclass; (2) normalização no
`load()` no modelo de `config.py:326-329`; (3) **fail-closed no sítio de leitura** — valor
desconhecido ⇒ `standard`/CONFIRM, nunca AUTO.

### B2. `_collect_current_config` parte de um `CopilotConfig()` NOVO
`ui/preferences.py:2566`: `cfg = CopilotConfig()`. Todo campo **não reatribuído** volta ao
default e é gravado por cima no `save()`. O próprio arquivo documenta que esse defeito já
**apagou** o `system_prompt` de instalações existentes (`preferences.py:2570-2574`, e o mesmo
aviso em `2752-2754`).

**Consequência:** é **obrigatório** adicionar `cfg.autonomy_level = ...` em
`_collect_current_config`, ou salvar qualquer preferência zera a autonomia — silenciosamente.
**Teste obrigatório:** salvar uma preferência não relacionada e verificar que `autonomy_level`
sobrevive (a suíte ainda não cobre isso para este campo).

### B3. Não repetir a ambiguidade do `google_enabled_services`
`core/google_auth.py:548`: **lista vazia = qualquer serviço passa**. É exatamente o tipo de
"vazio = permissivo" que produz fail-open silencioso. `autonomy_level` deve ser **enum fechado**
(`low|standard|high`) com regra explícita e testada para o desconhecido.

---

## C. Segurança que a autonomia amplifica (não é polimento, é pré-requisito)

### C1. O Live NÃO sanitiza observações de ferramenta
`live.py:2313-2327` devolve o `output` **cru** ao modelo. O loop de agente sanitiza
(`ai/agent_router.py:348` via `DocumentTrustManager.sanitize_tool_observation`,
`core/trust.py:245-279`). Ou seja: a defesa contra injeção indireta de prompt existe num caminho
e **não** no outro.

**Consequência:** `AUTO` no Live = ação automática dirigida por conteúdo não confiável (arquivo,
página, saída de comando). **Sanitizar no Live entra antes da feature**, senão a autonomia
amplifica o vetor em vez de só acelerar o trabalho.

### C2. Reversibilidade só existe para arquivo e organização
`shell/undo.py` (`UndoEntry`/`UndoStack`, limite 5), `shell/executor.py`
(`snapshot_file`/`make_file_revert`/`make_organize_revert`), ferramenta `undo_last`
(`agent_tools.py:2741`). **Não existe undo para Gmail, Calendar nem Home Assistant.**
Drive: `trash` é reversível, `delete`/`empty_trash` não.

**Consequência:** o critério "só vira AUTO o que dá para desfazer" hoje limita a autonomia a um
conjunto estreito (escrita de arquivo, organização). Se quisermos `AUTO` em Calendar/HA, o
"contrato de desfazer" vem **antes** do `AUTO`.

### C3. Hard-block deve vencer a autonomia
`ignored_patterns` (`config.py:273-284`) e `is_blocked_app` (`risk.py:175-180`) são bloqueios
duros. Definir e testar a ordem: **hard-block > zona de confiança > AUTO/CONFIRM**. `AUTO`
nunca sobrepõe hard-block.

---

## D. Testes e estabilidade (baseline: 159 módulos, 0 falhas)

### D1. Blast radius do enum
Adicionar um membro a `RiskLevel` toca ~20 arquivos de teste. Principais:
`test_fase3_risk.py` (63-83, 86-225 — o núcleo), `test_agent_tools.py` (90-104, 564-566,
583-585, 734-737, 919-922), `test_agent_loop.py`, `test_live.py`, `test_end_session.py`,
`test_google_calendar.py`/`drive`/`gmail`, `test_mcp.py`, `test_network_tools.py`,
`test_infra_tools.py`, `test_vscode.py`, `test_fase4_live_robustness.py`,
`test_fase4c_dynamic_context.py`, `test_agent_gui.py`, `test_turn_plan.py`,
`test_preferences_privacy.py`, `test_preferences_google.py`.

**Ponto fino:** `test_agent_tools.py` compara por **identidade** —
`requires_approval(...) is (expected is RiskLevel.CONFIRM)`. Introduzir `AUTO` quebra essas
asserções. E `requires_approval` é hoje `level == CONFIRM` (`agent_tools.py:515-517`):
`AUTO` deve continuar **não** exigindo aprovação, mas precisa de um sinal novo e distinto
(ex.: `Step.notify_after`) — não reaproveitar `requires_approval`, ou o cartão volta a pedir
clique.

### D2. Como rodar
Suíte completa **só** por `run_tests_safe.py --python .venv/bin/python` (sem swap nesta máquina
— o OOM killer já reiniciou a máquina no meio de uma suíte). Durante o desenvolvimento, rodar
por arquivo; completo no fim.

### D3. Harness de mutação
Criar `tests/mutacao/mutate_risk.py` no padrão existente (`tests/mutacao/conftest.py` com
`collect_ignore_glob = ["*"]`; modelo em `mutate_google_calendar.py`: `GRUPOS` +
`EQUIVALENTES`, aplica `replace`, roda pytest, restaura em `finally`, classifica
KILLED/SOBREVIVEU/EQUIVALENTE/INVÁLIDA). Mutações sugeridas: forçar `AUTO→CONFIRM`; remover o
fail-closed do desconhecido; quebrar a normalização de `autonomy_level`; remover a trava do
trust.

**Feito (2026-09-22):** `tests/mutacao/mutate_risk.py`, **13 mutações** em 6 arquivos de produção
(`live.py`, `risk.py`, `agent.py`, `agent_tools.py`, `config.py`, `preferences.py`).
Guarda de ambiguidade incluída (`trecho com >1 ocorrência` ⇒ INVÁLIDA, em vez de mutar o lugar
errado). Resultado: **13 KILLED, 0 sobreviventes, 0 equivalentes, 0 inválidas.**

**A primeira execução teve 1 sobrevivente — e ele pagou o harness inteiro.**
`higienização: Live volta a reenviar a observação crua ao modelo` passou com a suíte verde. Causa:
os testes de `ObservationSanitizationTest` chamavam `_sanitize_tool_output` **direto**; nada
exercitava `_execute_tool_call`, o único ponto que de fato monta o payload do modelo. Ou seja, o
teste provava que o higienizador funciona, não que o Live o **usa** — exatamente a família
"funciona, mas mente para quem lê" (REF-testes). Conserto: novo teste
`test_execute_tool_call_sends_the_sanitized_observation`, que dirige `_execute_tool_call` com um
WebSocket falso e afirma sobre o JSON enviado (conteúdo envolvido em `untrusted_tool_observation`)
**e** sobre o log de auditoria (que deve ficar cru). Validado contra o defeito: com a mutação
aplicada, **só o teste novo falha** (1 failed, 4 passed) — os quatro antigos seguem verdes, que é
a demonstração de que a lacuna era real.

**Lição generalizável:** teste de unidade do ajudante não cobre a fiação. Sempre que a decisão de
segurança mora em "chamar X antes de enviar", o teste tem de olhar o **payload**, não o X.

---

## E. UI (HIG, zero slop)

- `autonomy_level` de 3 estados → `Adw.ComboRow`, seguindo o `shortcut_combo_row`
  (`preferences.py:2590-2594`), **não** switch. Template de ciclo completo do switch
  `auto_execute_safe_actions`: construir `1208-1212` → carregar `2408` → persistir `2691`.
- **Sempre** com a linha em `_collect_current_config` (ver B2).
- Teste de subtitle no padrão `tests/test_preferences_privacy.py:163-178`.
- Precedência a definir e testar: autonomia do config **vs** "aprovar tudo" por execução no
  cartão do agente (`auto_approve_rest`, `ui/widgets/agent_card.py:220`).

---

## F. Observabilidade e critério de "pronto"

- Ações `AUTO` gravam em `action_logs` (`core/memory.py`) com detalhe suficiente para revisão
  pós-fato; ligar ao digesto da Fase 2 (`docs/plano-fase2-digesto.md`).
- Definição de pronto verificável: (1) teste de contrato por nível; (2) teste de que uma ação
  `AUTO` é **reversível** de fato (o `undo` funciona); (3) teste de que valor desconhecido ⇒
  CONFIRM; (4) teste de que hard-block vence `AUTO`; (5) mutação sem sobreviventes.

---

## G. Ordem recomendada (revisada pelos bloqueadores)

A ordem da proposta muda: os pré-requisitos de segurança vêm **antes** do enum, porque `AUTO`
sem eles amplifica o que já está frágil.

1. ✅ **Sanitizar observações no Live** (C1) — feito. `_sanitize_tool_output` percorre o
   resultado da ferramenta e envolve conteúdo externo como não confiável antes de devolvê-lo ao
   modelo; log/UI seguem com o cru.
2. ✅ **Trocar `_bypass_risk_gate` por token por chamada** + trava (A3) — feito. O selo é um
   parâmetro de `_dispatch_tool(..., approved=True)`, nunca estado do cliente; `_pending_actions`
   passou a ser guardado por trava (`_pending_guard`).
3. ✅ **Fonte única de trust/policy** (A1) — decidido e feito: opção (a), injeção explícita no
   composition root (ver A1).
4. ✅ `RiskLevel.AUTO` + `Step.notify_after` (A/D1) — feito. O nível executa sem pergunta e o
   passo é marcado para aviso posterior (ícone informativo no cartão; `notify_after` no
   `to_dict`/`from_dict` do `Step`). **Nenhuma regra embutida produz AUTO ainda** (fail-safe,
   testado): quem o usa é `register_risk`. As regras de zona de confiança/reversibilidade são o
   item 6.
5. ✅ `autonomy_level` no config com normalização + linha no coletor (B1/B2) + UI ComboRow (E) —
   feito. `low` rebaixa AUTO a CONFIRM; `standard` mantém o comportamento histórico; `high`
   promove escritas reversíveis em `trusted_directories` a AUTO. Valor inválido ⇒ `low`
   (fail-closed, lado que se VÊ — refina o "⇒ standard" sugerido antes, pelo próprio princípio
   do projeto).
6. 🔶 **Núcleo feito junto do item 5** (porque `high` sem regra alguma seria um campo que mente):
   zonas de confiança ligadas ao portão via `RiskPolicy(trusted_dirs=...)` e promoção de escrita
   em zona confiável sob `high`. **Falta** o contrato de reversibilidade para Calendar/Gmail/HA
   (sem undo hoje) — só depois disso faz sentido promover mais algo a AUTO.

   **DEFEITO MEDIDO (2026-09-22) — a promoção do item 5 está ligada ao caminho errado.**
   Com `autonomy_level="high"` + `trusted_dirs=["/tmp"]`:
   `classify("write_file", …)` → **AUTO**, mas `registry.call("write_file", …)` →
   **"Ferramenta desconhecida"**. `write_file`/`edit_file` **não estão no `ToolRegistry`** (85
   ferramentas; a de escrita é `write_document`, que fica **CONFIRM** mesmo em zona de confiança).
   São nomes legados do caminho chat/IntentEngine (`ActionType.WRITE_FILE`, `ai/actions.py:29`;
   executados por `ActionExecutor._write_file`). E `shell/executor.py` tem **zero** referências a
   `RiskPolicy`/`classify` — o executor confirma por `DesktopAction.requires_confirmation`, um
   booleano cravado em `providers.py` e lido só em `ui/app.py:2716`.
   **Conclusão: `autonomy_level="high"` não tem efeito observável.** É um campo que mente, como o
   `google_enabled_services` antes da Fase 4. Também medido: `smart_home_control`
   (`turn_off switch.portao`) é **SAFE** — a casa muda de estado **em silêncio**.
   Proposta e ordem de conserto: **`docs/PROPOSTA_REVERSIBILIDADE.md`**.
7. `TrustManager` aprendido, sob trava, com decay e kill-switch.
8. ✅ **`tests/mutacao/mutate_risk.py`** (D3) — feito: 13 mutações em 6 arquivos, **13 KILLED /
   0 sobreviventes**. A primeira execução teve 1 sobrevivente que revelou lacuna real de teste
   (higienização testada no ajudante, não na fiação) — consertada com
   `test_execute_tool_call_sends_the_sanitized_observation`. **Falta** a auditoria via digesto
   (F), que depende da Fase 2.

**Verificação dos itens 1–6 (2026-09-22):** suíte completa pelo runner seguro — **159 módulos,
0 falhas** (205 s, pico 489 MB); novos testes em `tests/test_autonomia_prereq.py`,
`tests/test_autonomia_nivel_auto.py` e `tests/test_autonomia_nivel.py`; cada contrato validado
**contra o defeito** (mutação → falha → restauração): desligar a higienização (2), ignorar o selo
(2), tirar o selo do `confirm_action` (1), descartar a política injetada (1), não marcar
`notify_after` (1), confundir `notify_after` com CONFIRM (1), o portão do Live barrar AUTO (1),
`requires_approval` exigir AUTO (1), o coletor não reatribuir `autonomy_level` (1 — a armadilha
B2), `load()` não normalizar (1), inválido cair no default (1), sumir o teto de `low` (2), a
promoção ignorar o nível (1).

A partir de 2026-09-22 essas validações manuais são **evidência durável**: o item 8
(`tests/mutacao/mutate_risk.py`) as reproduz em uma execução, com as três categorias separadas
(KILLED / SOBREVIVEU / EQUIVALENTE / INVÁLIDA). O harness não substitui a validação contra o
defeito — ele **é** a validação contra o defeito, repetível.

**Guarda das âncoras (2026-09-22):** os 8 harnesses do repo somam **140 KILLED / 0 sobreviventes /
2 EQUIVALENTES / 0 INVÁLIDAS**. A guarda de ambiguidade foi propagada a todos, e
`tests/test_mutacao_anchors.py` (novo, roda na suíte) cobra que toda âncora seja única — ela achou
**3 âncoras ambíguas** que acertavam a função pretendida apenas por ordem de definição. Detalhe em
`tests/mutacao/LEIAME.md`.

**Nota (item 4):** `ToolRegistry.requires_approval`/`notify_after` **não têm chamador em `src/`** —
o `AgentLoop` deriva o nível de `classify()` direto (uma chamada só). São superfície de API
(teste/CLI/UI), como `requires_approval` já era. Fica registrado como possível consolidação futura.
