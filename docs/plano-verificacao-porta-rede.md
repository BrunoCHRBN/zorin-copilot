# Análise de Lacunas e Plano de Implementação Detalhado — Verificação de Liberação de Porta e Integração de Skills de Infra

> Complementa `docs/plano-implementacao-infra.md` (plano macro). Este documento
> aprofunda o subsistema de rede recém-criado (`core/port_termination.py`) e
> fecha as lacunas de integração com a UI (`ui/widgets/network_view.py`) e com a
> ferramenta do agente (`ai/agent_tools.py`). Descobertas baseadas na leitura do
> código real do projeto (set/2026).

> **Status (21/09):** Etapas **S1–S9, S11 e S12 concluídas** (S11 = achado de
> produção; S12 = opção "Forçar" na UI).
> S11: encerramento ciente de systemd — `release_local_port` detecta o unit via
> cgroup e usa `systemctl stop` (ver §1.7). Validado ao vivo liberando a porta
> 8765 do `aigis0-ui.service`.
> S12: opção **"Forçar"** no diálogo de liberação da UI — passa `force=True`
> (SIGKILL) ao `release_local_port`, para o caso de processo comum que ignora
> SIGTERM **e não** é serviço systemd (que a UI nunca alcançava). Ver §2.2.
> S1–S4: precisão semântica corrigida (zumbi/TIME_WAIT + caractere corrompido
> removido), verificação IPv6 e de `ip_binding` específico, `EACCES`/porta
> privilegiada via `ss`, e teste destrutivo seguro (subprocess-filho).
> S5: botão "Verificar" por porta + confirmação pós-encerramento na UI
> (`network_view`), com toast estruturado. S6: ferramenta `network_confirm_port`
> (somente leitura) + `network_release_port` retornando `freed`/`confirmation`.
> S7: fronteira core↔skills documentada (`docs/fronteira-core-skills.md`) e
> skills `infra-safety-standard`/`infra-router` atualizadas para delegar ao core.
> S8: `core/port_audit.py` — watcher de transições (`PortAuditWatcher`) + export
> Prometheus (`to_prometheus`).
> S9: i18n — o app é **pt-BR-only** (sem gettext/`locale`); em vez de introduzir
> um pipeline de tradução isolado num único módulo, as mensagens de
> `port_termination` foram **centralizadas no catálogo `Messages`**
> (consistência + extração trivial para gettext no futuro).
> **Testes: 19/19 (port_termination) · 7/7 (port_audit) · 13/13 (network_view,
> incl. `test_release_port_force_action_invoked`) · 17/17 (network_actions) ·
> 5/5 (network_tools) · 61/61 no conjunto dos 5 módulos de rede (runner
> unittest), 0 falhas.** Próxima: S10 (validação E2E em hardware real — requer o app aberto).
> *Pendência de verificação visual:* o painel de rede não tem mockup próprio, a
> mudança de forma (botão "Verificar") reusa as mesmas classes do botão de
> encerramento já existente — verificação visual em execução fica como follow-up.

---

## Parte 1 — Análise: pontos, requisitos e casos de borda ainda não observados

### 1.1 Precisão semântica (corrigir no módulo atual)

| # | Achado | Impacto | Onde |
|---|--------|---------|------|
| C1 | **Processo zumbi NÃO ocupa a porta.** Quando um processo sai, o kernel libera todos os descritores/sockets *antes* de virar zumbi. Portanto, `pid_state=ZOMBIE` com `port_state=FREE` é o estado normal e seguro — não um risco. A sugestão de "next_step" atual para zumbi está imprecisa. | Mensagem enganosa; pode induzir ação desnecessária. | `port_termination.py` (`next_steps`) |
| C2 | **TIME_WAIT não se aplica a LISTEN.** `TIME_WAIT` ocorre no lado *ativo* de uma conexão encerrada, nunca em um socket de escuta ao fechar. A preocupação com TIME_WAIT para "porta liberada" está superestimada; o caso real é socket em fechamento pelo kernel ou processo em estado `D` (uninterruptible). | Comentário/direção de extensão impreciso. | `port_termination.py` (docstring) |
| C3 | **`comm` truncado em /proc.** `/proc/<pid>/stat` corta o nome em 15 chars (`TASK_COMM_LEN`). Comparação `expected_process` falha para nomes longos (ex.: nomes de containers). | Falso positivo de `DIFFERENT_PROCESS`. | `classify_pid_state` |

### 1.2 Cobertura de protocolo / interface (gap de funcionalidade)

| # | Achado | Impacto | Onde |
|---|--------|---------|------|
| C4 | **IPv6 não verificado.** `get_listening_ports()` já captura `ip_binding` IPv6 (`::1`, `::`), mas `confirm_port_freed` faz bind **só** em `127.0.0.1` (AF_INET). Porta em `::1`/`::` é declarada "livre" injustamente. | Falso "livre" para serviços IPv6. | `confirm_port_freed` / `_try_bind` |
| C5 | **Interface específica (multi-homed) não considerada.** Porta amarrada em IP concreto (ex.: `192.168.15.14:8080`) aparece "livre" no bind de loopback, mas segue ocupada na LAN. O `ip_binding` existe mas não é usado no teste. | Falso "livre" em cenário multi-homed real (o do print da tela: `enp4s0`/`192.168.15.14`). | `confirm_port_freed` |
| C6 | **UDP menos confiável.** Com `SO_REUSEADDR`, dois sockets UDP podem coexistir em alguns kernels; o bind-test para UDP é menos determinístico que para TCP. | Falso "livre" ocasional em UDP. | `_try_bind` (proto udp) |
| C7 | **Portas privilegiadas (<1024) e EACCES.** Non-root tentando bind em porta livre <1024 recebe `EACCES`, não `EADDRINUSE`. Se um processo *root* ocupa a porta 80, o teste reporta "sem privilégio" em vez de "ocupada por root" — não distingue. | Diagnóstico ambíguo para serviços bem-known. | `_try_bind` (EACCES) |

### 1.3 Integração (gap de implementação)

| # | Achado | Impacto | Onde |
|---|--------|---------|------|
| C8 | **Nenhuma confirmação pós-encerramento.** `release_local_port` é chamado em `network_view.py:754` e `agent_tools.py:3925`, mas nenhum dos dois chama `confirm_port_freed`. O `refresh()` da UI re-lista portas (porta some se livre), mas não há *mensagem estruturada* "livre vs ainda ativa". | UX e agente não sabem se o encerramento funcionou de fato. | `network_view._do_release_port`, `agent_tools._tool_network_release_port` |
| C9 | **`port_termination` órfão.** Módulo criado mas não importado em lugar algum (só self-referências em docstring/teste). | Código morto até ser plugado. | todo o repo |

### 1.4 Arquitetura de camadas (risco estrutural)

| # | Achado | Impacto | Onde |
|---|--------|---------|------|
| C10 | **Dupla camada de critérios.** O app tem funções nativas (`network_actions.py`); as skills `infra-*` (WorkBuddy) são instruções externas para o agente. Se ambas evoluírem separadas, divergem em regras de segurança (ex.: blacklist de processos protegidos, dry-run). | Risco de comportamento inconsistente entre "usar a UI" e "pedir ao agente". | `core/network_actions.py` vs `skills/infra-*` |

### 1.5 Testes / validação (gap)

| # | Achado | Impacto | Onde |
|---|--------|---------|------|
| C11 | **Sem teste destrutivo seguro.** Os 8 testes atuais só validam bind livre/ocupado e classificação de PID, mas nunca matam um processo real e confirmam a liberação ponta a ponta. | A lógica de "processo morreu → porta livre" não está coberta. | `tests/test_port_termination.py` |
| C12 | **`ss` ausente degrada parcialmente.** `get_listening_ports` retorna `[]` se `ss` não existir; `confirm_port_freed` perde o PID ocupante, mas o bind ainda detecta ocupação. Aceitável, porém não documentado como requisito de ambiente. | Comportamento em sistema mínimo não especificado. | `network_inspector.py`, `port_termination.py` |

### 1.6 Requisitos implícitos não contemplados

- **R1 — Não bloquear a UI:** `wait_until_port_freed` com timeout de 2s travaria a main thread se chamado fora de thread; precisa de invariantes de execução.
- **R2 — i18n:** strings do módulo estão em pt-BR inline; o app pode usar `gettext`. Precisam ser extraíveis.
- **R3 — Single source of truth:** a lógica de "é seguro encerrar / confirmar" deve viver em `core/`, e as skills `infra-*` apenas orquestrar, não reimplementar.
- **R4 — Cobertura IPv6 obrigatória:** dado que o inspetor já é IPv6-aware, o verificador também deve sê-lo (C4/C5).

### 1.7 Processos gerenciados por systemd (achado em produção — 21/09)

| # | Achado | Impacto | Onde |
|---|--------|---------|------|
| **C13** | **Matar por PID é ineficaz para serviços systemd.** O `aigis0` (porta 8765) é um serviço `systemd --user` (`aigis0-ui.service`, `Restart=on-failure`). `release_local_port` enviava SIGTERM ao PID — que o systemd ignora/recupera. A porta nunca era liberada. | Encerramento falha para **qualquer** serviço gerenciado por systemd. | `core/network_actions.release_local_port` |

**Correção aplicada (S11):** `release_local_port` agora detecta o unit via
`/proc/<pid>/cgroup` (`systemd_unit_for_pid`). Para **units de usuário** usa
`systemctl --user stop <unit>` (sem root, reversível); para **units de sistema**
recusa e orienta `sudo systemctl stop <unit>`; descarta o gerenciador de sessão
`user@<uid>.service` (pará-lo derrubaria a sessão — nunca é alvo). Validado ao
vivo: liberou a porta 8765 (`aigis0-ui.service`).

---

## Parte 2 — Plano de Implementação Detalhado

### 2.1 Estrutura de módulos (alvo)

```
src/zorin_copilot/
├── core/
│   ├── network_inspector.py      (existente) get_listening_ports (IPv4/IPv6)
│   ├── network_actions.py        (existente) release_local_port (kill + segurança)
│   ├── port_termination.py       (ESTENDER)  confirm_port_freed com IPv6 + ip_binding
│   └── port_audit.py             (NOVO)      watcher contínuo multi-porta (extensão)
├── ui/widgets/network_view.py    (EXTENDER)  botão "Verificar" + toast estruturado
└── ai/agent_tools.py             (EXTENDER)  release chama confirm + _tool_network_confirm_port
```

### 2.2 Etapas ordenadas (com dependências e riscos)

| Etapa | Ação | Depende de | Risco |
|-------|------|-----------|-------|
| **S1** | Corrigir precisão semântica: remover sugestão errada de zumbi (C1/C2); `next_steps` reflete que zumbi+porta livre = OK; ajustar docstring sobre TIME_WAIT. | — | Baixo |
| **S2** | Estender `_try_bind`/`confirm_port_freed` para (a) IPv6 (`AF_INET6`, alvos `::1`/`::`) e (b) usar `ip_binding` real como alvo de bind (wildcard `0.0.0.0`/`::` → bind no wildcard; IP concreto → bind no IP). | S1 | Médio |
| **S3** | Tratar `EACCES` distintamente: se porta <1024 e sem privilégio, rotular `PortState.PRIVILEGE_CHECK_NEEDED` e usar `ss -o` como fonte de verdade para "ocupada por root" (C7). | S2 | Baixo |
| **S4** | Testes destrutivos seguros: subprocess-filho escuta numa porta efêmera, é morto via `release_local_port`/`os.kill`, e `confirm_port_freed` deve retornar `FREED`. Nunca usar PID de sistema (reaproveitar `PROTECTED_PROCESS_NAMES`). (C11) | S2 | Baixo (uso de PID próprio) |
| **S5** | Plugar confirmação na UI: `network_view._do_release_port` chama `confirm_port_freed(port, proto, pid, name)` após o release e mostra toast estruturado ("Porta X confirmada livre" vs "ainda ativa — PID Y"). Botão extra "Verificar" por linha de porta. (C8/C9) | S2, S4 | Médio (GTK/Libadwaita) |
| **S6** | Plugar na ferramenta do agente: `_tool_network_release_port` retorna `freed` + `report`; novo `_tool_network_confirm_port(port, proto)` expõe a verificação isolada. (C8/C9) | S2 | Baixo |
| **S7** | Reconciliação de camadas: documentar fronteira (C10) — `core/` é a fonte; skills `infra-*` orquestram e citam as funções nativas em vez de reimplementar regras de segurança. | S5, S6 | Baixo |
| **S8** | Extensão de auditoria contínua: `port_audit.py` com `watch_ports(ports, interval)` (QTimer/thread) que dispara alerta quando algo muda FREE→OCCUPIED; exporta métrica para Prometheus (C12 ambiente). | S2 | Médio |
| **S9** | i18n: extrair strings de `port_termination` para o domínio de tradução do app (R2). | S2 | Baixo |
| **S10** | Validação end-to-end em hardware real (Zorin) + atualizar `docs/` e registrar no `CHANGELOG`/README. | S5–S9 | Baixo |
| **S11** | (achado de produção) Encerramento ciente de systemd: `release_local_port` lê `/proc/<pid>/cgroup`, para o unit de usuário via `systemctl --user stop` e recusa/instrui para unit de sistema (ver §1.7). Corrige UI e agente. | S5 | Médio |
| **S12** | Opção **"Forçar"** no diálogo da UI: resposta `DESTRUCTIVE` adicional que passa `force=True` (SIGKILL) ao motor — cobre processo comum que ignora SIGTERM e não é serviço systemd (a UI nunca passava `force=True`). | S5, S11 | Baixo |

### 2.3 Riscos (detalhados)

- **R-A (bind em interface específica / IPv6):** binding em `::` wildcard pode conflitar com app que escuta só IPv4-mapped em alguns kernels. *Mitigação:* escolher alvo conforme `ip_binding` e tentar ambas as famílias quando o bind for wildcard; tratar erro de família ausente graciosamente.
- **R-B (teste destrutivo):** matar PID errado. *Mitigação:* só usar `subprocess.Popen` do próprio teste como alvo; nunca PID < 1000 ou em `PROTECTED_PROCESS_NAMES`; rodar sob o runner seguro (`run_tests_safe.py`).
- **R-C (UI travamento):** `wait_until_port_freed` na main thread. *Mitigação:* sempre chamar de `threading.Thread(daemon=True)`; timeout default baixo (≤ 2s) e sem polling agressivo.
- **R-D (divergência skills vs core):** regras duplicadas. *Mitigação:* S7 — single source of truth em `core/`; skills referenciam, não reimplementam.
- **R-E (ambiente mínimo sem `ss`):** degradação. *Mitigação:* bind ainda funciona; documentar que `ss` é requisito recomendado para PID ocupante.

### 2.4 Critérios de validação

**Unitários (automáticos):**
- Mantêm-se os 8 atuais (livre/ocupado/bind/PID) e adicionam:
  - IPv6: `_try_bind(port, "tcp", "::1")` detecta ocupação de listener IPv6.
  - `ip_binding` concreto: porta em `192.168.x:port` é detectada ocupada quando se liga ao IP certo.
  - `EACCES`: bind em porta <1024 sem root → `PortState.PRIVILEGE_CHECK_NEEDED` (não `OCCUPIED`).
  - Zumbi: `pid_state=ZOMBIE` + `port_state=FREE` → `freed=True` e `summary` não alarmista.

**Integração (subprocess-fantasma, S4):**
- Cenário A: filho escuta → `release_local_port` → `confirm_port_freed.freed == True`.
- Cenário B: filho escuta e ignora SIGTERM (handler) → após `force=True` (SIGKILL) → `freed == True`; sem force → `pid_state==STILL_ALIVE` e `freed == False`.

**UI (S5):**
- Liberar com sucesso → toast "Porta X confirmada livre".
- Falhar/ainda ativa → toast "ainda ativa (PID Y)" e porta permanece listada.
- Diálogo com três respostas: Cancelar (default/close), Encerrar (`force=False`,
  SIGTERM) e **Forçar** (`force=True`, SIGKILL) — ambos destrutivos. `_do_release_port`
  repassa `force` ao `release_local_port`. (S12)

**Agente (S6):**
- `_tool_network_release_port` retorna `details.freed` bool + `report`.
- `_tool_network_confirm_port` retorna `freed`/`pid_state`/`port_state`.

**Não-regressão:**
- Suíte completa via `python -m unittest discover -s tests -t .` (ou `run_tests_safe.py`) permanece verde; `py_compile` dos módulos alterados.

**Desempenho:**
- `confirm_port_freed` em porta livre: < 300 ms (bind direto).
- Com retries/timeout: < 2,5 s no pior caso.

---

## Resumo executivo

O subsistema já tem o núcleo (`port_termination.py` + 8 testes verdes), mas três lacunas o impedem de ser correto em produção:
1. **Não verifica IPv6 nem interface específica** (C4/C5) — apesar do inspetor já enumerar `ip_binding` IPv6.
2. **Não está plugado** na UI nem na ferramenta do agente (C8/C9) — não há confirmação real do encerramento.
3. **Imprecisões semânticas** sobre zumbi/TIME_WAIT (C1/C2) que podem induzir ações erradas.

O plano (S1–S10) corrige a precisão, estende a cobertura (IPv6 + `ip_binding`), adiciona testes destrutivos seguros, pluga a confirmação onde o encerramento já acontece, e reconcilia a dupla camada skills↔core. Estima-se entrega incremental: S1–S4 (núcleo+testes) em um ciclo; S5–S6 (integração) no seguinte; S7–S10 (arquitetura, auditoria, i18n, validação final) em sequência.
