# Integrações Google — Configuração e Privacidade

Este documento explica como autorizar o Zorin Copilot a operar a **sua** conta
Google (Agenda, Drive, Gmail) e onde ficam os dados. É o passo a passo da Fase 0
do `plano-integracao-google.md`.

> **Escopo: uso pessoal.** O desenho assume uma única conta — a sua. Não há
> distribuição a terceiros, e por isso **não é preciso passar pela verificação da
> Google**. Se um dia o app for distribuído, esta decisão muda (ver o fim).

---

## Pré-requisitos

- Uma conta Google (Gmail pessoal serve).
- Acesso ao [Google Cloud Console](https://console.cloud.google.com/).
- O extra `[google]` instalado (ver passo 4).

---

## Passo 1 — Projeto no Google Cloud

1. Acesse [console.cloud.google.com](https://console.cloud.google.com/) e crie um projeto
   (ex.: `zorin-copilot-pessoal`).
2. **Ative as APIs** que vai usar, em **APIs e serviços → Biblioteca**:
   - *Google Calendar API*
   - *Google Drive API*
   - *Gmail API*

   Pular este passo é a causa nº 1 de erro `403` na primeira chamada: o OAuth autoriza o
   *acesso*, mas a *API* precisa estar ligada no projeto.
3. Não é preciso ativar faturamento: as cotas gratuitas cobrem uso pessoal com folga.

> Nota de UI (2026): o console renomeou a antiga "Tela de permissão OAuth" para
> **Google Auth platform**, com três abas — **Branding**, **Audience** e **Clients**.
> Caminho direto: `console.cloud.google.com/auth/overview`.

## Passo 2 — Consentimento: o testeiro e o preço do modo "Testing"

Em **Google Auth platform → Branding**:

1. Preencha nome do app, e-mail de suporte e e-mail do desenvolvedor.

Em **Google Auth platform → Audience**:

2. **User type: External.**
3. **Test users: adicione A SUA CONTA** (o e-mail Google que vai autorizar).

> **Isto não é opcional.** Em "Testing", a Google só permite autorizar quem está
> na lista de testadores. Sem o seu e-mail aqui, o navegador devolve
> **`Erro 403: access_denied` — "o app não concluiu o processo de verificação"**.
> É a causa nº 1 de falha neste passo.

### O que "Testing" custa, e por que não é trivial sair dele

| | "Testing" (uso pessoal) | "In production" |
|---|---|---|
| Funciona para você | Sim, se estiver na lista | Sim |
| Limite de usuários | 100 testadores | Ilimitado |
| **Refresh token** | **expira em 7 dias** | não expira |
| Verificação da Google | **não exige** | **exige** para escopos sensíveis/restritos |

A leitura ingênua é "publique e pronto". **Não é assim:** Calendar, Drive e Gmail
são escopos **sensíveis** (e `gmail.modify` chega a ser **restrito**). Publicar com
eles exige **verificação da Google** — política de privacidade pública, revisão de
2 a 6 semanas; escopos restritos ainda pedem auditoria de segurança (CASA).

**Portanto, para uso pessoal, o caminho pragmático é ficar em "Testing" com o seu
e-mail como testador e conviver com a reautorização semanal.** Publicar só compensa
se o app for distribuído a outras pessoas.

> A tela "app não verificado" aparece mesmo para testadores — clique em
> **Avançado → Ir para (inseguro)**. É a sua própria conta e o seu próprio código;
> o aviso não indica risco novo.

## Passo 3 — Client ID do tipo "Desktop app"

Em **Google Auth platform → Clients** (ou **APIs e serviços → Credenciais**) →
**Create Client / Criar credenciais → OAuth client ID**:

1. Application type: **Desktop app**.
2. Dê um nome (ex.: `zorin-copilot-desktop`) e clique em **Create**.
3. Use **Download JSON** (na listagem ou na página de detalhe do cliente).
4. Salve em:

   ```
   ~/.config/zorin-copilot/google_client_secret.json
   ```

   (ou aponte outro caminho em `google_client_secret_path` na config, ou na
   variável `ZORIN_GOOGLE_CLIENT_SECRET`).

O fluxo usado é o *installed-app* com **PKCE** e redirect de loopback
(`http://localhost:<porta>`) — o correto para app local. Em app instalado o
`client_secret` não é segredo (por isso o PKCE é obrigatório), e nada é exposto
ao abrir o navegador.

## Passo 4 — Instalar o extra

```bash
pip install 'zorin-copilot[google]'
# ou, no repositório:
uv sync --extra google
```

Sem o extra, o app sobe normalmente e só as ferramentas Google ficam
indisponíveis — o comando `google status` diz exatamente o que falta.

## Passo 5 — Conectar

```bash
zorin-copilot-cli google status                 # o que está instalado/conectado
zorin-copilot-cli google services               # serviços e escopos mínimos
zorin-copilot-cli google connect calendar       # abre o navegador uma vez
zorin-copilot-cli google connect drive
zorin-copilot-cli google connect gmail
zorin-copilot-cli google disconnect gmail       # remove o token guardado
```

---

## Escopos (least-privilege)

O app pede o **menor** escopo que ainda faz a operação funcionar. Estão em
`core/google_auth.py` (fonte única) e travados por teste.

| Serviço | Escopo | O que permite | O que **não** permite |
|---|---|---|---|
| Agenda | `calendar.events` | criar/editar/excluir eventos | mexer em agendas, ACL, compartilhamento |
| Drive | `drive.file` | arquivos criados/abertos **pelo app** | ver o resto do seu Drive |
| Gmail | `gmail.send`, `gmail.modify` | enviar, rascunhos, etiquetas | acesso total (`mail.google.com`) |

Nunca é pedido o escopo de acesso total ao Gmail (`https://mail.google.com/`),
que é **restrito** na classificação da Google.

## Onde ficam os dados e o token

- **Token:** no **Secret Service do sistema** (gnome-keyring), via `keyring`,
  quando instalado. Sem `keyring`, cai para um arquivo **0600** em
  `~/.config/zorin-copilot/google_tokens.json`. Force o backend com
  `ZORIN_GOOGLE_TOKEN_BACKEND=file|keyring` ou pelo campo `google_token_backend`.
- **O token nunca é gravado no `config.json`** — esse arquivo vai para backup e
  para o olho de terceiros com facilidade.
- **Os dados saem da máquina.** Ao usar Agenda/Drive/Gmail, o conteúdo
  correspondente é enviado aos servidores da Google. É por isso que a integração
  é **opt-in** e desligada por padrão (`google_enabled = False`): um app que se
  diz privacidade-primeiro não fala com a nuvem sem que você mande.

## Verificar a integração ao vivo (opcional)

A suíte normal **não** toca a rede: os testes das integrações usam dobras. Para
exercitar a **conta real** — criar, conferir e limpar — existe um teste de fumaça
atrás de uma variável de ambiente:

```bash
ZORIN_GOOGLE_SMOKE=1 .venv/bin/python -m pytest -q tests/test_google_smoke.py -v
```

Ele roda só nos serviços **conectados** e pula o resto. Faz *round-trip* real:
Calendar (cria → lista → apaga), Drive (pasta → upload → download com SHA-256 →
lixeira → purga) e Gmail **só em leitura** — um teste que dispara e-mail de
verdade seria intrusivo demais para rodar sem querer. A limpeza vai em
`try/finally`: falhar no meio não deixa lixo na sua conta.

Sem a variável, os três testes **pulam** (`3 skipped`) — é o comportamento
esperado na suíte normal.

> Vale a pena rodar depois de mexer nas integrações. Foi assim que apareceu um
> defeito que nenhuma dobra pegava: `list_files()` devolvia os arquivos da
> **lixeira** junto com os ativos.

## Aviso de expiração (evitar o imprevisto)

Em modo "Testing" a Google expira o refresh token em ~7 dias. O app **avisa
antes**, em vez de você descobrir quando um comando falhar. O aviso aparece em
`zorin-copilot-cli google status` e na tela **Conexões → Contas Google**.

**De onde vem a data.** A Google **não informa** quando o refresh expira — isso
não está em campo nenhum do token (medido: `expiry` é o do *access token*, ~1 h).
Então o app grava a data da autorização junto do token, no `connect`, e preserva
esse campo a cada renovação. A idade é o que ele mede.

**A premissa, em voz alta.** Os 7 dias são propriedade da **publicação do app**,
não do token. Se você publicar o app (ou o cliente for Interno), o refresh **não
expira** e o aviso é falso alarme. Por isso ele diz a premissa na própria
mensagem, e o limite é configurável:

```jsonc
"google_refresh_warning_days": 7   // 0 desliga o aviso
```

**Três estados**, e o terceiro é o que evita a falha invisível:

| Estado | Quando | O que diz |
|---|---|---|
| `warning` | passou da janela | "reconecte para não ser pego de surpresa" |
| `info` | a menos de 2 dias do fim | "reconecte quando for conveniente" |
| `unknown` | conectado, mas sem a data | **"Não sei quando esta autorização foi feita"** |

O `unknown` existe porque a alternativa seria o silêncio: um aviso que só dispara
com data conhecida **nunca** dispararia no primeiro ciclo — justamente o que você
quer cobrir. Ele se resolve sozinho na próxima reconexão, que passa a gravar a
data. Os tokens conectados antes deste recurso aparecem nesse estado.

## Como revogar

- Pelo app: `zorin-copilot-cli google disconnect <serviço>`.
- Pela Google: [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
  → remova o acesso do app. Na chamada seguinte o app **detecta a revogação**
  (`invalid_grant`), **apaga o token do cofre** e passa a mostrar **"Reconexão
  necessária"** na tela de Conexões — nunca quebra nem fica repetindo erro críptico.
- **Falha de rede é tratada como outra coisa:** o token é bom, então ele **fica
  guardado** e o erro diz que é conexão. Apagar ali custaria uma reautorização
  inteira por causa de um Wi-Fi ruim.

## Solução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| "bibliotecas não instaladas" | falta o extra `[google]` | `pip install 'zorin-copilot[google]'` |
| "credenciais não encontradas" | falta o `client_secret.json` | Passo 3 |
| **`Erro 403: access_denied`** no navegador | app em "Testing" e a conta **não está na lista de testadores** | Passo 2, item 3: adicione o seu e-mail em **Audience → Test users** |
| Reconectar toda semana | refresh token de 7 dias do modo "Testing" | Passo 2 (é o preço do uso pessoal; publicar exige verificação) |
| `Erro 403` na primeira chamada de API | API não ativada no projeto | Passo 1, item 2 (ative Calendar/Drive/Gmail) |
| "app não verificado" no navegador | esperado, uso pessoal | Avançado → Ir para (inseguro) |
| Erro de rede ao conectar | saída HTTPS bloqueada | libere `*.googleapis.com` / `accounts.google.com` no firewall |
