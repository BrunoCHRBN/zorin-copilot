# Viabilidade de Integrações Reais com o Ecossistema Google

_Status: análise de viabilidade (2026-09-21). Nenhuma implementação ainda — só o diagnóstico e as condições._

## TL;DR

Sim, é plenamente viável. Existem **duas rotas** e ambas funcionam de verdade hoje:

1. **Servidor MCP do Google Workspace** (oficial/open-source) — gruda as ferramentas
   Calendar/Drive/Gmail/Docs/Sheets/Tasks no agente já existente, sem escrever cliente HTTP.
2. **Biblioteca nativa Python** (`google-api-python-client` + OAuth2 PKCE) — alinhada ao
   princípio "privacidade em primeiro lugar" e ao controle fino que o projeto já exige.

O ponto crítico não é técnico: é o **fluxo OAuth 2.0** e a **verificação da tela de
consentimento** da Google. Para uso pessoal do Bruno funciona hoje, sem atrito. Para
distribuir publicamente, exige verificação da Google (privacy policy, domínio, etc.).

---

## Estado atual no repositório (o que JÁ existe — e o que NÃO é)

O projeto já "fala" com o Google, mas **de forma superficial** (deep links, não API):

| Módulo | O que faz hoje | É integração real? |
|---|---|---|
| `core/calendar.py` | Agenda **local** em SQLite, gera `.ics` e abre no GNOME Calendar/Evolution | ❌ Não é Google Calendar |
| `core/email.py` | Abre **link web** de composição do Gmail (`mail.google.com/mail/?...`) | ❌ Não usa a Gmail API |
| `core/app_integrations.py` | `GoogleDriveIntegration`, `GoogleCalendarIntegration`, `GmailIntegration` etc. — todas geram `OPEN_URL` (abrem páginas) | ❌ Deep link, não API |
| `mcp/categories.py` | Já classifica tokens `drive`, `gdrive`, `gmail`, `calendar`, `sheets` como **"Serviços externos"** | ✅ Espera servidores MCP desses serviços |

Conclusão honesta: **hoje não há nenhuma operação real no ecossistema Google** — só
abrir o site. Criar/ler/atualizar eventos, arquivos ou e-mails programaticamente exige
OAuth2 + as APIs REST da Google.

---

## Rota A — Servidor MCP do Google Workspace (recomendada para começar)

- Repositório oficial (lista `google/mcp`): **Google Workspace MCP** cobre Docs, Sheets,
  Slides, **Calendar, Gmail, Drive**, Tasks, Contacts, Chat — controle em linguagem natural.
- Comunidade madura: `taylorwilsdon/google_workspace_mcp` (mesma cobertura, amplamente usada).
- Como encaixa: o Zorin Copilot **já consome MCP** (`mcp_enabled`, `mcp_config_path`,
  category "Serviços externos"). Basta adicionar o servidor ao `mcp_servers.json` e as
  ferramentas viram parte das 83+ ferramentas do agente.

**O que permite fazer (via MCP):**
- **Calendar:** criar/atualizar/excluir/listar eventos, consultar livre-ocupado, gerenciar agendas.
- **Drive:** upload/download, listar/pesquisar/organizar arquivos, criar pastas, gerenciar permissões/compartilhamento, mover para lixeira.
- **Gmail:** enviar, rascunhos, listar/ler, gerenciar labels, marcar lido/não lido.
- **Docs/Sheets/Slides/Tasks:** criar e editar documentos, planilhas, apresentações e tarefas.

**Condições para funcionar de verdade:**
1. Projeto no Google Cloud + **OAuth Client ID do tipo "Desktop app"** (installed-app).
2. Tela de consentimento configurada + usuário adicionado como **testador** (modo "Testing").
3. Credenciais passadas ao servidor MCP (via `.env`/`env` no `mcp_servers.json`).
4. Primeira autorização é interativa (abre o navegador, o usuário loga na conta Google).

> Atenção: o `google_workspace_mcp` usa **Python/uvx**. Em máquina sem swap, rode a suíte
> completa de testes só via `run_tests_safe.py` (regra já conhecida do repo).

---

## Rota B — Biblioteca nativa Python (alinhada à privacidade)

Pacotes: `google-api-python-client`, `google-auth-oauthlib`, `google-auth`.

**Fluxo OAuth para app de desktop Linux (correto e atual):**
- Client ID **"Desktop app"** → fluxo installed-app com **PKCE** (`code_verifier`/`code_challenge`).
- URI de redirecionamento `http://localhost:<porta>` (loopback). Não expõe `client_secret`
  útil — em app instalado o segredo não é segredo, por isso o PKCE é obrigatório.
- Armazenar **refresh/access token** em local seguro: **Secret Service / libsecret**
  (já presente no GNOME/Zorin) ou keyring — **nunca** em plaintext no `config.json`
  (que hoje guarda `ha_token` em 0600; tokens Google merecem algo melhor).
- Renovação transparente de access token via refresh token.

**Mesmas capacidades da Rota A**, com a vantagem de controle fino de escopo e de dados
(não depende de um processo MCP terceiro) — condizente com o posicionamento privacy-first.

---

## Escopos e a barreira de verificação (a parte que decide "real" vs "demo")

| Serviço | Escopos típicos | Classificação Google |
|---|---|---|
| Calendar | `calendar.events`, `calendar.readonly` | **Sensível** |
| Drive | `drive.file` (só arquivos do app) ou `drive` (tudo) | `drive.file` = sensível; `drive` = **restrito** |
| Gmail | `gmail.send`, `gmail.compose`, `gmail.modify`, `gmail.readonly` | **Sensível**; `https://mail.google.com/` = **restrito** |

**Regras de verificação:**
- **Uso pessoal (Bruno):** tela de consentimento "External" em modo **"Testing"**, com a
  própria conta Google como testadora. Funciona hoje, sem verificação. Limitação: em
  "Testing" o **refresh token expira em 7 dias** (precisa reautorizar periodicamente).
- **Distribuição pública / produção:** escopos sensíveis e restritos exigem **verificação
  da Google** (privacy policy pública, verificação de domínio, possível auditoria). Apps
  não verificados mostram tela de aviso e só funcionam para testadores cadastrados.
- **Workspace "Internal"** não se aplica (conta é Gmail pessoal, não Google Workspace).

---

## Condições gerais (independente da rota)

1. **Conexão de saída** para `*.googleapis.com` e `accounts.google.com` (firewalld ativo
   nesta máquina — pode precisar de regra, assim como o KDE Connect já é bloqueado).
2. **Navegador disponível** para o fluxo de autorização inicial (uma vez só).
3. **Política de privacidade do app**: se o Zorin Copilot distribui isso publicamente,
   precisa de privacy policy e domínio verificado para passar na verificação.
4. **UI de consentimento explícito**: seguindo o padrão do repo (opt-in, "fala só quando
   pedido"), o usuário deve ativar cada serviço e ver o que foi autorizado.
5. **Tratamento de erro de revogação**: token revogado/recusado deve voltar ao estado
   "serviço desconectado" graciosamente, não quebrar o app.

---

## Trade-off que precisa ser dito em voz alta

O Zorin Copilot se posiciona como **privacidade em primeiro lugar, execução local**.
Integrar Google significa que **dados saem da máquina para os servidores da Google**
(eventos, arquivos, e-mails, conteúdo de docs). Não é contradição se for **opt-in e
explicado**, mas é uma decisão de produto, não só técnica — merece ser registrada.

---

## Próximos passos sugeridos (quando quiser implementar)

1. Decidir rota: MCP (rápido, recomendado para provar valor) vs nativo Python (controle/privacidade).
2. Criar projeto Google Cloud + Client ID "Desktop app" + tela de consentimento "Testing".
3. POC: conectar Calendar via MCP e criar um evento de verdade a partir de um comando de voz.
4. Validar o fluxo de token no Secret Service e o ciclo de reautorização de 7 dias.
5. Só depois estender para Drive e Gmail.

> Nada disto foi implementado. Este documento é diagnóstico + condições, não código.

---

## Decisão para uso estritamente pessoal (sem terceiros)

Restrição do Bruno: **só ele usa**, nenhuma conta de terceiro. Isso elimina o maior
atormentador (verificação da Google) e muda a recomendação.

### A armadilha dos 7 dias (resolvida)
- Em **"Testing"**, todo refresh token expira em **7 dias** → reautorizar toda semana.
- **Flipar o publishing status para "In production"** faz o refresh token parar de expirar.
  Isso **não exige verificação** para uso solo: a tela "app não verificado" aparece uma vez
  (clica em "Avançado → Ir para (inseguro)") e pronto. Verificação só é necessária se um dia
  ele distribuir o app a outras pessoas ou quiser suprimir o aviso.
- Portanto: consent screen **External**, publishing **In production**, conta dele como único
  usuário. Refresh token de longa duração, sem verificação.

### Recomendação: Python nativo (não MCP) para este caso
Motivo, na ordem de peso:

1. **Postura privacy-first.** É o app dele, na máquina dele, com as chaves dele. O token do
   Gmail/Drive fica **no código dele**, guardado no **Secret Service / keyring** do GNOME/Zorin
   — não num processo MCP de terceiro segurando a credencial.
2. **Encaixa no padrão `core/`.** O repo já tem `core/calendar.py` (local) e `core/email.py`.
   Adicionar `core/google_calendar.py` / `google_drive.py` / `google_gmail.py` é o mesmo molde
   e é testável com o harness pytest existente — substituindo os stubs de deep link de
   `app_integrations.py` por chamadas de verdade.
3. **Memória da máquina.** Sem swap: um servidor MCP (`uvx`, processo Python extra) é peso que
   o Python nativo evita. O app já roda; as ferramentas viram funções no mesmo processo.
4. **Controle de escopo mínimo.** `calendar.events` (não `calendar`), `drive.file` (não
   `drive`), `gmail.modify`/`gmail.send` (não `https://mail.google.com/`). Menos superfície.

O MCP (Rota A) segue sendo a opção **mais rápida para ver funcionando** (uma tarde), mas para
uso pessoal mantido e alinhado à identidade do projeto, nativo é a melhor alternativa.

### Receita concreta (uso pessoal)
1. Google Cloud → novo projeto → **OAuth consent screen** (External) → publishing **In production**.
2. **Credentials → OAuth client ID → tipo "Desktop app"** → baixar `client_secret.json`.
3. `pip install google-api-python-client google-auth-oauthlib keyring`.
4. `InstalledAppFlow.from_client_secrets_file(..., scopes=[...])` com
   `redirect_uri="http://localhost:<porta>"` (fluxo installed-app + PKCE nativo da lib).
5. Após o primeiro auth, persistir `credentials` no **keyring** (`keyring.set_password`).
   Fallback aceitável: arquivo 0600 em `~/.config/zorin-copilot/` (igual ao `config.json`).
6. Expor funções que as `agent_tools` já chamam; trocar os `OPEN_URL` de
   `app_integrations.py` por chamadas reais onde fizer sentido.
7. UI: toggle **opt-in por serviço** + estado "reconexão necessária" gracioso se o token for
   revogado.
8. Checar que o `firewalld` libera saída HTTPS para `*.googleapis.com` (a máquina já bloqueia
   o KDE Connect — confirmar que isto não).
