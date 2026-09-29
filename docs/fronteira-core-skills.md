# Fronteira `core/` ↔ Skills de Infraestrutura (S7)

> Resolve o achado **C10** de `docs/plano-verificacao-porta-rede.md`: evitar que
> as skills `infra-*` (WorkBuddy) e as funções nativas do app (`core/`) divirjam
> em regras de segurança. Define a **fonte única de verdade** por domínio.

## Princípio

`core/` é a **fonte única de verdade** para as regras de segurança e para as
ações de infraestrutura que o app executa nativamente. As skills `infra-*`
**orquestram** e **orientam** o agente — **nunca reimplementam** regras de
segurança.

> Regra de ouro: **se um controle de UI ou uma skill espelha uma regra do
> domínio, importe/referencie a regra — não reimplemente.** Uma cópia local
> divergiria em silêncio.

## Duas categorias de domínio

| Categoria | Exemplo | Fonte de verdade | Papel da skill |
|---|---|---|---|
| **Domínio nativo do app** | rede | código em `core/` | chama/encaminha para a função nativa |
| **Domínio sem código nativo** | nuvem, banco, K8s, servidores | a própria skill | segue o contrato `infra-safety-standard` |

Para domínios **nativos**, qualquer regra (lista de processos protegidos,
dry-run, confirmação, rollback) vive no `core/`. Para domínios **não nativos**,
a skill é a fonte — mas precisa obedecer ao contrato `infra-safety-standard`.

## Mapa — domínio de rede (nativo)

| Skill / camada | Funções `core/` (fonte) | Papel |
|---|---|---|
| `infra-safety-standard` | — (define o contrato que o core implementa) | padrão de segurança |
| `infra-router` | `core.network_actions`, `core.port_termination` | roteia para a função nativa |
| UI (`network_view`) | `release_local_port`, `confirm_port_freed`, `get_listening_ports` | apresenta e delega |
| Ferramenta do agente (`agent_tools`) | idem + `port_audit` | expõe ao agente |

### Funções nativas canônicas (rede)

| Função | Módulo | Responsabilidade |
|---|---|---|
| `release_local_port` | `core/network_actions.py` | encerra processo (SIGTERM→SIGKILL) com blacklist + UID check |
| `confirm_port_freed` | `core/port_termination.py` | confirma liberação (IPv4/IPv6, dry-read) |
| `get_listening_ports` | `core/network_inspector.py` | enumera portas em escuta |
| `audit_attack_surface` | `core/network_actions.py` | classifica exposição (loopback vs LAN) |
| `PortAuditWatcher` | `core/port_audit.py` | auditoria contínua (transições) |

## Regras (obrigatórias)

1. **Não duplicar** `PROTECTED_PROCESS_NAMES`, lógica de dry-run, confirmação
   em duas etapas ou rollback em skills.
2. A skill **cita a função pelo nome** e delega a execução ao `core/`.
3. Se a regra mudar, muda **no `core/`** — as skills herdam automaticamente.
4. Skill de domínio **sem** `core/` correspondente deve declarar explicitamente
   que segue `infra-safety-standard` (leitura default, dry-run, confirmação).

## Como aplicar

- **Ao criar skill de rede:** referencie `core/...`; não copie a lógica.
- **Ao criar skill de nuvem/banco/K8s/servidor:** siga o contrato
  `infra-safety-standard` e mantenha as regras na própria skill (documentadas).
- **Ao revisar uma skill:** se ela reimplementa uma regra que existe no `core/`,
  é um bug de arquitetura — substitua por delegação.
