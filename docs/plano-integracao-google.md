# Plano de Implementação — Integrações do Ecossistema Google (Uso Pessoal)

_Status: **autorizado** pelo Bruno em 2026-09-22. Fase 0 implementada; Fases 1–6 pendentes._

## Decisões resolvidas (Bruno, 2026-09-22)

1. **Calendar local × Google** → **Google como base** (fonte de verdade única).
   O `CalendarManager` local vira cache/offline na Fase 1.
2. **E-mail** → **sempre confirmar antes de enviar**, com validação
   gramatical e ortográfica **conforme o contexto que o Bruno fornecer**
   (requisito adicional, incorporado à Fase 3).
3. **Escopo Drive** → **least-privilege em todas as etapas**: `drive.file`,
   `calendar.events`, `gmail.send`+`gmail.modify`. Nunca os escopos amplos.
4. **firewalld** → saída liberada e funcionando. Fase 0 desbloqueada.

## Status de execução

| Fase | Estado |
|---|---|
| 0 — Núcleo OAuth2 + cofre | **implementada** (`core/google_auth.py`, CLI `google`, extra `[google]`, testes, doc) |
| 1 — Calendar | **implementada** (`core/google_calendar.py`, roteamento Google↔local na ferramenta `calendar_event`, cliente Live delegando ao registro, cache local via `mirror_events`) |
| 2 — Drive | **implementada** (`core/google_drive.py`, ferramenta `google_drive`, escopo `drive.file`, portão de risco) |
| 3 — Gmail | **implementada** (`core/google_gmail.py`, `core/email_review.py`, ferramenta `google_gmail` com revisão forçada + envio confirmado) |
| 4 — UI/consentimento | **implementada** (seção "Contas Google" em Conexões: portão mestre + toggle por serviço + Conectar/Desconectar + estado "Reconexão necessária") |
| 5 — Testes/docs finais | **implementada** (revogação × rede, smoke test opcional, docs de privacidade e revogação) |

### Notas de execução da Fase 1

- **Google é a fonte de verdade; o local é cache.** `CalendarManager.mirror_events()`
  faz `INSERT OR REPLACE` por id. Toda escrita vai à API primeiro — o inverso deixaria
  um evento "criado" que não existe na agenda real.
- **`live.py` delega ao `ToolRegistry`** em vez de manter um branch próprio: uma regra
  só de "qual agenda", sem duas cópias divergindo.
- **Parser de datas é fonte única**: `core/calendar.parse_when()` (extraído do
  `CalendarManager`) é reusado pelo cliente Google. Corrigido de passagem um defeito
  latente: "depois de amanhã" era inalcançável (dava +1 dia em vez de +2).
- **Decisão aberta para o Bruno (não implementada):** escrita na agenda
  (`create`/`update`/`delete`) **não** pede confirmação hoje — comportamento
  pré-existente do `calendar_event`. Avaliei tornar CONFIRM (como `email_compose`),
  mas isso muda a UX de voz e quebra um teste existente do cliente Live; deixei como
  estava e trago a decisão em vez de impô-la. Ligar isso é uma linha em `shell/risk.py`.

### Notas de execução da Fase 2

- **Confirmação ligada (decisão do Bruno, 2026-09-22).** Escrita na agenda
  (`create`/`update`/`delete`) agora é `CONFIRM` em `shell/risk.py`. O teste do cliente
  Live foi reescrito para **asserir o fluxo** (barra → `requires_confirmation` → aprova →
  executa), não só para passar.
- **`drive.file` é o escopo, e a consequência está dita**: `list`/`search` só veem os
  arquivos que o Copilot criou ou que o usuário abriu com ele (`google_drive.scope_note()`).
- **Sem fallback local para o Drive**: é nuvem; fingir um caminho local seria mentir para
  o modelo. Sem conexão, erro explícito e acionável.
- **Portão de risco do Drive**: `list`/`search`/`get` = SAFE; `upload`/`download`/`mkdir`/
  `move`/`rename`/`trash` = CONFIRM (o `download` grava arquivo local, podendo sobrescrever).
- O cliente Live não precisou de branch novo: `_dispatch_tool` já cai no `ToolRegistry`
  para nomes que ele não conhece (linha ~3381).

### Notas de execução da Fase 3

- **Revisão e envio são etapas SEPARADAS.** `core/email_review.py` cuida da correção
  gramatical/ortográfica conforme o `context` que o Bruno fornecer; `core/google_gmail.py`
  só fala com a API. Misturar esconderia a etapa de revisão dentro do envio.
- **O envio é confirmado DUAS vezes, por caminhos diferentes e complementares:**
  (a) `shell/risk.py` marca `send` como `CONFIRM` — o portão de risco do app exige o "sim";
  (b) a ferramenta **recusa** `send` sem `reviewed=true` — etapa de revisão forçada em
  **código**, não só pedida no texto. Sem `reviewed=true`, não envia.
- **A revisão degrada de forma HONESTA.** Sem provedor de IA (ou se ele falhar), o resultado
  traz `reviewed=false` + o motivo, e o texto original é preservado. Devolver o texto como
  se tivesse sido corrigido seria a falha mais cara possível aqui — o usuário enviaria um
  e-mail achando que foi revisado. Há mutação dedicada a isso no harness.
- **Escopos `gmail.send` + `gmail.modify`**: envia e etiqueta; não apaga de vez nem mexe em
  configurações (`scope_note()`).
- **Bcc ficou de fora de propósito**: incluir no MIME cru depende de a API removê-lo antes da
  entrega, e esse comportamento não foi exercitado. Não se expõe um campo de privacidade cuja
  segurança não se verificou.

---

## Verificação AO VIVO (2026-09-22) — Calendar e Drive contra a API real

Com a credencial instalada e a conta adicionada como **testadora**:

- **Calendar:** criou/listou/apagou um evento de verdade. Fuso `-03:00` (São Paulo) correto,
  duração respeitada. A ferramenta `calendar_event` roteou **`source: google`**.
- **Drive:** criou pasta, subiu arquivo, listou, **baixou e conferiu SHA-256 (íntegro)**,
  renomeou e mandou para a lixeira.

**Defeito encontrado SÓ pela execução ao vivo** (nenhum teste com dobra pegaria):

> `list_files()` sem filtro **devolvia os arquivos da lixeira**. O usuário mandava algo para a
> lixeira e ele reaparecia em "meus arquivos". Medido: sem `q`, a API retorna `trashed=True`;
> com `q="trashed = false"`, some. **Corrigido** (o padrão agora filtra a lixeira), com 2 testes
> novos e mutação no harness. Re-verificado ao vivo: `list_files()` → 0.

**Correção de afirmação minha:** eu havia escrito que publicar em "In production" não exigia
verificação. Para escopos sensíveis/restritos (Calendar/Drive/Gmail; `gmail.modify` é restrito),
**exige** — verificação da Google, 2–6 semanas, com política de privacidade. Para uso pessoal, o
caminho é ficar em **Testing** com o e-mail como testador e aceitar a **reautorização semanal**.
`docs/google-oauth-setup.md` corrigido.

**Estado:** Calendar e Drive conectados (`google_enabled=true`); Gmail ainda não autorizado.

### Gmail — verificado ao vivo (2026-09-22)

Com os três serviços conectados:

- **Leitura real:** 17 rótulos; mensagens recentes lidas com assunto e remetente reais.
- **Revisão gramatical real** (provedor Gemini): corrigiu acentuação e pontuação respeitando o
  contexto informado, e as checagens determinísticas do `lint_draft` apontaram os mesmos
  problemas — as duas camadas concordaram.
- **Envio real** (para o próprio Bruno), `mark_read`/`mark_unread` confirmados, e limpeza pela
  lixeira (recuperável).

**Capacidade adicionada no caminho:** `google_drive.delete_permanently()` + ação `delete` na
ferramenta (CONFIRM). Sem ela, a lixeira do app só cresceria — `trash` não tinha contraparte.
O nome é explícito de propósito: quem lê sabe que não há volta.

**Nota de escopo:** o `gmail.modify` até permite apagar mensagem de vez, mas o módulo **não**
expõe isso — o e-mail de teste ficou na lixeira. É superfície mínima deliberada, não limitação
de permissão.

---

## Premissas (travas do plano)

1. **Uso exclusivamente pessoal** — só a conta Google do Bruno, zero terceiros.
2. **Rota nativa Python** (decisão da seção "Decisão para uso estritamente pessoal" em
   `PROPOSTA_INTEGRACAO_GOOGLE.md`): `google-api-python-client` + `google-auth-oauthlib`
   + `keyring`.
3. **OAuth Desktop app**, consent screen **External** em publishing **In production** →
   refresh token de longa duração, **sem verificação da Google** (tela "não verificado"
   aparece uma vez no primeiro acesso).
4. **Escopos mínimos**: `calendar.events` (não `calendar`), `drive.file` (não `drive`),
   `gmail.send` + `gmail.modify` (não `https://mail.google.com/`).
5. **Token no Secret Service / keyring** do GNOME/Zorin (fallback: arquivo 0600 em
   `~/.config/zorin-copilot/`).
6. **Privacidade**: dados saem da máquina para os servidores Google — toda integração é
   **opt-in** e explicada na UI.

## Princípios de execução (rigor do projeto)

- **Testar contra a API real**, não só compilar. Cada fase tem critério de aceite que exige
  uma operação de verdade na conta do Bruno (ex.: criar e apagar um evento de teste).
- **Non-regression**: suíte completa **só** via `run_tests_safe.py --python .venv/bin/python`
  (máquina sem swap). Baseline hoje: 147 módulos, 0 falhas.
- **Anti-slop / HIG do GNOME**: sem gradiente roxo, sem emoji em botão, ícones simbólicos.
- **Sem quebra do app**: token revogado/recusado → estado "reconexão necessária" gracioso,
  não crash.

## Resumo das fases

| Fase | Foco | Entrega-síntese | Esforço |
|---|---|---|---|
| 0 | Auth OAuth2 + cofre de tokens | `core/google_auth.py` | Médio |
| 1 | Google Calendar (backend real) | `core/google_calendar.py` + bridge | Médio |
| 2 | Google Drive (arquivos) | `core/google_drive.py` | Médio |
| 3 | Gmail (enviar/gerenciar) | `core/google_gmail.py` | Médio |
| 4 | Consentimento, UI, estados | toggles + banner reconexão | Médio |
| 5 | Testes, robustez, docs | suíte pytest + README/privacidade | Médio |
| 6 *(opcional)* | Docs/Sheets/Tasks/Contacts | módulos adicionais | Baixo–Médio |

---

## Fase 0 — Núcleo de Autenticação OAuth2 + Cofre de Tokens

**Objetivos**
- Estabelecer um fluxo OAuth2 "Desktop app" com PKCE, uma vez só por serviço.
- Persistir e renovar o token com segurança (keyring), sem plaintext.
- Expor `get_service(api_name)` que devolve um cliente autenticado pronto pra uso.

**Escopo**
- Criar `src/zorin_copilot/core/google_auth.py`.
- `InstalledAppFlow.from_client_secrets_file(..., redirect_uri="http://localhost:<porta>")`
  com escopos por serviço.
- Armazenamento via `keyring.set_password`/`get_password` (service=`zorin-copilot-google`,
  username=`<serviço>`); fallback para `~/.config/zorin-copilot/google_tokens.json` (0600).
- `load_credentials()`, `save_credentials()`, `refresh_if_expired()`, `is_connected()`.
- Entradas em `CopilotConfig` (`core/config.py`): `google_enabled`, `google_client_secret_path`,
  `google_scopes` (dict por serviço).

**Entregas principais**
- `core/google_auth.py` com as 4 funções acima + CLI/`--connect-google <serviço>` que abre o
  navegador uma vez e salva o token.
- Teste unitário com token mockado (sem rede) para `refresh_if_expired` e `is_connected`.
- Documento curto `docs/google-oauth-setup.md` (passo a passo do Google Cloud: criar projeto,
  consent screen In production, baixar `client_secret.json`).

**Dependências**
- Definição final dos escopos (itens 4 das premissas).
- Confirmação de que o `firewalld` libera saída HTTPS para `*.googleapis.com` e
  `accounts.google.com` (a máquina já bloqueia KDE Connect — verificar que isto não).

**Estimativa de esforço**: Médio (2–3 dias focados). É a fundação; tudo depende dela.

**Critérios de aceite**
- `python -m zorin_copilot.core.google_auth --connect-google calendar` abre o browser, autoriza,
  e o token sobrevive a reinício do app (lido do keyring).
- `is_connected("calendar")` retorna `True` sem reautorizar.
- Suíte `run_tests_safe.py` segue 0 falhas.

---

## Fase 1 — Google Calendar (backend real de agenda)

**Objetivos**
- Operações CRUD reais de eventos + livre/ocupado na conta Google.
- Substituir o stub de deep link `GoogleCalendarIntegration` (`core/app_integrations.py`,
  linhas ~200–223) por chamada de API de verdade (manter deep link só para "abrir no browser").

**Escopo**
- `src/zorin_copilot/core/google_calendar.py`: `create_event`, `list_events`,
  `update_event`, `delete_event`, `freebusy`.
- **Decisão pendente (ver abaixo)**: relação com o `CalendarManager` local
  (`core/calendar.py`, SQLite+ICS). Opção A: Google Calendar vira fonte de verdade e o local
  vira cache offline. Opção B: manter os dois, sem sync. Recomendo A para uso pessoal.
- Ligar funções às `agent_tools` existentes (criar/agendar por voz).

**Entregas principais**
- `core/google_calendar.py`.
- Bridge ou substituição do `GoogleCalendarIntegration` em `app_integrations.py`.
- Comando de voz "agende X na minha agenda do Google" roteado para a API.

**Dependências**: Fase 0.

**Estimativa de esforço**: Médio (2–3 dias).

**Critérios de aceite**
- Criar um evento de teste via comando → aparece de verdade no calendar.google.com.
- `delete_event` remove o mesmo evento (verificado no navegador/API).
- `freebusy` retorna ocupação correta num intervalo conhecido.

---

## Fase 2 — Google Drive (arquivos)

**Objetivos**
- Upload/download, listar/pesquisar, organizar (mover/renomear), lixeira. Escopo `drive.file`.

**Escopo**
- `src/zorin_copilot/core/google_drive.py`: `upload`, `download`, `list_files`, `search`,
  `move`, `rename`, `trash`.
- Substituir stubs `GoogleDriveIntegration` (`app_integrations.py`, linhas ~111–182) por
  chamadas reais onde fizer sentido (manter deep link para "abrir pasta no browser").

**Entregas principais**
- `core/google_drive.py`.
- Comandos: "salve este arquivo no meu Drive", "procure X no Drive", "mova Y para pasta Z".

**Dependências**: Fase 0 (pode rodar em paralelo com Fase 1 após Fase 0).

**Estimativa de esforço**: Médio (2–3 dias).

**Critérios de aceite**
- `upload` de um arquivo de teste → arquivo presente no Drive (verificado).
- `download` recupera o mesmo conteúdo (hash confere).
- `search` retorna o arquivo recém-upado.

---

## Fase 3 — Gmail (enviar / gerenciar)

**Objetivos**
- Enviar mensagens, rascunhos, listar/ler, modificar labels. Escopos `gmail.send`+`gmail.modify`.

**Escopo**
- `src/zorin_copilot/core/google_gmail.py`: `send_message`, `create_draft`, `list_messages`,
  `get_message`, `modify_labels`.
- Integrar com `EmailManager` (`core/email.py`): quando o serviço Google estiver conectado,
  `compose` envia de verdade via API em vez de só abrir link. Manter o deep link como fallback.

**Entregas principais**
- `core/google_gmail.py`.
- "Envie um e-mail para X" dispara `send_message` real (com confirmação antes de enviar —
  alinhado ao padrão opt-in do repo).

**Dependências**: Fase 0 (paralelo a 1 e 2).

**Estimativa de esforço**: Médio (2–3 dias).

**Critérios de aceite**
- `send_message` entrega e-mail de teste que chega à caixa de destino (verificado).
- `modify_labels` marca como lido/não lido corretamente.
- Envio exige confirmação explícita (não dispara sozinho).

---

## Fase 4 — Consentimento, UI e Estados de Reconexão

**Objetivos**
- Toggles **opt-in por serviço** nas preferências.
- Estado "reconexão necessária" quando o token é revogado/expira.

**Escopo**
- `ui/preferences.py`: seção "Contas Google" com toggle por serviço + botão "Conectar".
- Widget de status (reaproveitar `ui/widgets/status_bar.py`) que mostra conexão por serviço.
- Tratamento de `RefreshError`/`TokenRevokedError` → estado degradado gracioso.

**Entregas principais**
- Toggles em `preferences.py`.
- Banner/selo "Google Calendar desconectado — reconectar" sem quebrar o app.

**Dependências**: Fases 1–3 (precisa dos serviços prontos para ligar os toggles).

**Estimativa de esforço**: Médio (2 dias).

**Critérios de aceite**
- Desligar o toggle de um serviço → chamadas àquele serviço são bloqueadas e o deep link
  volta a ser o caminho.
- Revogar o token no painel da Google → app mostra "reconexão necessária", não crash.

---

## Fase 5 — Testes, Robustez e Documentação

**Objetivos**
- Cobertura pytest das três integrações (com token mockado para não bater na rede nos
  unitários; um teste de fumaça opcional contra a conta real, isolado e exploratório).
- Tratamento de erros de rede/revogação.
- Documentação de privacidade e setup.

**Escopo**
- `tests/test_google_auth.py`, `tests/test_google_calendar.py`, `tests/test_google_drive.py`,
  `tests/test_google_gmail.py` (unitários com fixtures mockadas).
- Seção de privacidade em `README.md` ou `docs/google-oauth-setup.md`: o que cada escopo
  acessa, onde o token fica, como revogar.

**Entregas principais**
- Suíte de testes verde via `run_tests_safe.py`.
- Doc de privacidade/setup.

**Dependências**: Fases 0–4.

**Estimativa de esforço**: Médio (3 dias).

**Critérios de aceite**
- `run_tests_safe.py --python .venv/bin/python` → 0 falhas, baseline mantido.
- Teste que injeta token inválido → exercita o caminho de "reconexão necessária" (validado
  contra o defeito, não só verde).

---

## Fase 6 — (Opcional / futuro) Docs, Sheets, Tasks, Contacts

- `drive.file` já habilita criar Docs/Sheets via Drive; API específica se houver edição de
  conteúdo. Tasks/People seguem o mesmo molde da Fase 0 + módulo.
- Baixo esforço incremental porque o núcleo (Fase 0) já existe.

---

## Decisões pendentes

Nenhuma. As quatro decisões foram resolvidas em 2026-09-22 (ver topo do documento).

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Token revogado em uso | Fase 4: estado degradado + reauth sob demanda |
| `firewalld` bloqueia egress | Checar na Fase 0; abrir regra se necessário |
| Escopo indefinido gera retrabalho | Decisões pendentes resolvidas antes de cada fase |
| Teste verde sem validar API real | Critérios de aceite de cada fase exigem operação real |
| Peso de memória (sem swap) | Nativo no mesmo processo; evita processo MCP extra |

## Sequenciamento

```
Fase 0 ──┬──> Fase 1 (Calendar) ──┐
         ├──> Fase 2 (Drive)    ──┤──> Fase 4 (UI) ──> Fase 5 (Testes/Docs)
         └──> Fase 3 (Gmail)    ──┘
```
Fases 1–3 podem ser paralelas após a Fase 0; Fase 4 e 5 são finais e sequenciais.
