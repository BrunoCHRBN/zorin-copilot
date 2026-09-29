# Reformulação da GUI — Proposta centrada no uso real

> Análise das 10 capturas anexas + estado atual do código (`src/zorin_copilot/ui/`).
> Lê-se em conjunto com `UI_ANALYSIS.md` (que trata de refatoração de código já
> feita) e `SISTEMA_VISUAL_SUPERHUMAN.md` (princípios de movimento/atalho).
> Esta proposta é sobre **produto e disposição**, não sobre reescrever widgets.

---

## 0. Diagnóstico transversal — o que as telas têm em comum

Antes de falar de tela por tela, há três falhas de *design* que se repetem nas 10
capturas. São elas, e não a paleta, que tornam a interface difícil de usar:

### 0.1 — Controles acionáveis e telemetria informativa dividem a mesma hierarquia

Na barra de cabeçalho (capturas 1, 2, 10) o *badge* do modelo (ação: clicável
para trocar de cérebro) e o *badge* `AOC 27"` (telemetria: a cerca espacial do
monitor) são exatamente a mesma pílula translúcida. O usuário não distingue
"isto faz algo" de "isto só informa". O mesmo acontece na barra de status
inferior: `gemini-3.6-flash` (modelo ativo), `0 tokens` (uso), `Carga 0.87`
(métrica de SO), `RAM 9.1 GB`, `98 docs`, `Rede…` — seis itens idênticos em peso
visual, misturando configuração, contabilidade e hardware.

**Decisão proposta:** separar o que é *comando* do que é *estado*. Comandos
ganham uma forma (botão/pílula com foco e `:active`); estado ganha outra (texto
`ttnum` mudo, sem foco). É a regra "cor é sinal, neutro é estrutura" do sistema
Superhuman levada a sério na barra de topo.

### 0.2 — Ações destrutivas estão a um clique de distância do acidente

Cada linha de conversa tem o ícone de lixeira **sempre visível** (captura 1). O
botão `Limpar Histórico` no rodapé da sidebar tem a mesma cor de aviso. Na tela
de memória (captura 4) o fato "meu café favorito é espresso duplo" aparece **10
vezes** — se o usuário quiser apagar um, tem 10 alvos. Nenhuma dessas ações pede
confirmação nem se esconde atrás de um menu.

**Decisão proposta:** lixeira só em *hover* (revela em 90ms, some em 150ms —
curva única do sistema). `Limpar Histórico` vira um item de menu, não um botão
de rodapé. Memória duplicada é um **bug de dados** (ver §3.2), não só de UI — a UI
deve no mínimo agrupar e oferecer "mesclar/apagar duplicados".

### 0.3 — Listas longas não são escaneáveis

`Por que meu pc es…` corta o título em ~13 caracteres (captura 1). Cinco entradas
`Chamada de Voz e…` são idênticas (captura 1). Dez fatos idênticos na memória
(captura 4). Seis servidores MCP com a mesma silhueta (captura 5). Em nenhuma
delas há *diferenciador primário* (data, duração, modelo, categoria) antes do
texto truncado.

**Decisão proposta:** toda lista densa precisa de um metadado de escaneabilidade
à esquerda (ícone semântico) e um secundário à direita (ícone de status), e o
título deve usar `ellipsize` só quando necessário — não como padrão de 13 caracteres.
Sem isso, o `Ctrl+↑/↓` do sistema Superhuman não serve de nada: o usuário navega
por itens que não consegue distinguir.

---

## 1. Janela principal (chat) — capturas 1, 2

### 1.1 Cabeçalho

| Problema visto | Justificativa | Mudança proposta |
|---|---|---|
| Título `Zori…` truncado em 13 px | O identificador do app é a âncora de orientação; truncar no HeaderBar é raríssimo em GNOME e parece bug. | Título fixo "Zorin Copilot" (não trunca nunca); subtítulo dinâmico mostra o **tópico** da conversa atual ("Por que meu PC esquenta?"), que é o que o usuário quer ver ali. |
| 4 pílulas/botões soltos à direita (modelo, monitor, microfone, engrenagem) | Mistura comando/telemetria (§0.1) e duplica o microfone da barra de prompt. | Esquerda: menu ☰ (Nova, Fixar, Histórico, Preferências, Sair). Centro: título+tópico. Direita: **uma** pílula de comando = cérebro ativo (clicável → abre seletor de modelo, captura 9); ao lado, **um** ícone de voz (o mesmo da barra de prompt, não duplicado); ao lado, ⚙️. O monitor (`AOC 27"`) vai para a barra de status como estado, não para o topo. |
| Não há indicação de "fixada" (pin) nem de "modo HUD" | README documenta `Ctrl+P` (fixar) e modo HUD, mas a UI não reflete o estado. | Estrela discreta no título quando `session.pinned`; pequeno chip "HUD" quando a janela está em modo heads-up. Estado é texto mudo, não botão. |

### 1.2 Barra lateral de conversas

| Problema visto | Justificativa | Mudança proposta |
|---|---|---|
| Títulos cortados em 13 caracteres | Não dá para reler do que se lembra. | `ellipsize=END` com *tooltip* no hover mostrando o título completo; a coluna ganha largura mínima maior quando a sidebar está aberta. |
| 5× `Chamada de Voz e…` idênticas | Voz ao vivo gera entradas sem título; o usuário não sabe qual é qual. | Tipo de entrada vira ícone à esquerda (chat = balão, voz = onda). À direita: duração (`02:13`) + modelo (`gemini-live`). Data agrupada em seções: **Hoje / Ontem / Esta semana / Anterior**. |
| Lixeira sempre visível em cada linha | Convite ao acidente (§0.2). | Lixeira aparece só no hover da linha (90ms). O grupo "Fixadas" fica no topo, imune à lixeira acidental. |
| `Limpar Histórico` como botão de rodapé | Ação destrutiva em local de passeio. | Vira item do menu ☰ (e do clique-direito no cabeçalho "Conversas"). Antes de apagar, diálogo de confirmação com contagem ("17 conversas serão apagadas"). |
| Campo de busca sem botão limpar | Pequeno atrito em lista longa. | Ícone de limpar aparece quando há texto (padrão Adw.SearchEntry). |
| Nada indica que a sidebar recolhe | `pan-start` existe mas é invisível quando fechada. | Quando fechada, a seta ▸ no menu ☰ ganha rótulo "Mostrar conversas" e fica sempre visível. |

### 1.3 Área de boas-vindas

| Problema visto | Justificativa | Mudança proposta |
|---|---|---|
| Estrela + "Como posso ajudar hoje?" | Genérico, sem personalidade nem contexto do desktop. | Mantém a pergunta (é a função), mas **substitui a estrela** por um resumo do estado do sistema já conhecido: "Conversa nova · EndeavourOS · Chrome ativo". Transforma telemetria passiva em ponto de partida útil. |
| 4 botões 2×2 sem hierarquia (Resumir / Pesquisar / Voz / Recortar) | São 4 categorias diferentes tratadas como pares. | Dois grupos com rótulo: **"Sobre o que está na tela"** → Resumir página, Recortar área. **"Como interagir"** → Voz ao vivo, Pesquisar. O grupo "tela" só aparece se houver janela/contexto ativo (captura 1 já tem o chip "Google Chrome"). |
| Nenhuma conversa recente na tela vazia | O usuário volta e tem que ir à sidebar. | Se houver histórico, a tela vazia mostra "Retomar" com as 3 últimas conversas como linhas clicáveis (reutiliza o widget da sidebar, sem novo padrão). |

> **Correção na implementação (§1.3):** duas decisões mudaram ao encostar no código,
> e a terceira foi confirmada por ele.
>
> - **A terceira parte da linha de estado não é o aplicativo ativo.** O plano pedia
>   "Conversa nova · EndeavourOS · Chrome ativo". Esse fato já tem dono: o banner de
>   contexto logo abaixo, na mesma tela, mostra "No foco: Chrome: <título>" e se
>   atualiza sozinho. Repeti-lo seria dizer a mesma coisa em dois lugares — o
>   problema §0.1 que a reformulação existe para corrigir. No lugar vai o **ambiente
>   de desktop** (`Conversa nova · EndeavourOS · Hyprland`). Para este app isso não é
>   enfeite: é o que decide quais automações funcionam — `hyprctl` no Hyprland,
>   `gsettings` no GNOME. É a informação que ajusta a expectativa de quem vai pedir
>   uma tarefa. A sondagem roda no caminho de abertura da janela, então está sob
>   `try/except`: se falhar, a linha degrada para "Conversa nova" em vez de impedir
>   a tela de aparecer.
>
> - **O agrupamento é ditado pelos dados, não pela semântica aparente.** O plano
>   punha "Recortar área" em "Sobre o que está na tela" e "Pesquisar" em "Como
>   interagir". O motor já classifica cada sugestão com `is_contextual` — `False`
>   para os modos globais —, e "Recortar Área da Tela" é `is_contextual=False`: ele
>   não depende de haver janela ou contexto algum. Seguir o plano deixaria o grupo
>   "tela" (que só existe com contexto) carregando um chip que nunca depende de
>   contexto — e numa área de trabalho limpa o recorte **desapareceria**. A divisão
>   passou a ser `is_contextual` mesmo, sem metadado novo: "Sobre o que está na
>   tela" ← derivado da janela em foco / área de transferência; "Como interagir" ←
>   modos globais. O grupo dos modos é o piso da tela vazia: sempre há por onde
>   começar.
>
> - **A vaga reservada aos modos sai de dentro do orçamento.** Com contexto rico o
>   motor gasta o orçamento inteiro no que está na tela e "Como interagir"
>   desapareceria justamente quando o usuário tem mais sobre o que falar. A
>   primeira tentativa pedia modos *além* do limite e a tela mostrou **7 chips com
>   `limit=4`** — um bug que só apareceu ao contar os chips renderizados, não nos
>   testes de unidade. Agora uma sugestão da tela sai para o modo entrar
>   (`on_screen[: limit - len(modes)]`). O descarte é barato: voz e recorte
>   continuam na barra de prompt e no cabeçalho.
>
> - **"Retomar" não ganha lixeira.** Reutiliza `build_topic_identity` — extraído de
>   `_build_topic_row` para as duas telas não divergirem em silêncio — e a mesma
>   classe de linha da sidebar, então hover e estado ativo vêm de graça. Mas excluir
>   é ação destrutiva (§0.2) e mora onde já existe confirmação: a sidebar. Uma tela
>   de partida só inicia; não apaga. Também não há atalho de teclado para excluir
>   ali.
>
> - **Sobre o estado retratado:** a tela de boas-vindas só existe quando a sessão
>   não tem turnos (`ChatStream.rebuild`). Consequência que o mockup precisou
>   registrar: nesse estado **nenhuma** conversa está ativa, então a sidebar real
>   não destaca linha alguma e o subtítulo do cabeçalho é o neutro ("Assistente
>   Inteligente", o que `_on_new_topic` escreve). Como a conversa vazia nem chega a
>   ser persistida (`_save_current_session` sai cedo sem turnos), ela também nunca
>   aparece em "Retomar" — a lista não pode começar pela conversa em que você já
>   está.

### 1.4 Barra de prompt (entrada)

| Problema visto | Justificativa | Mudança proposta |
|---|---|---|
| 3 ícones iguais (câmera, clipboard, sparkle) | Sem hierarquia; o "sparkle" é redundante com a identidade do app. | A câmera (visão da tela) é a ação mais distintiva do app e fica **primeira**, com popover. Clipboard fica segunda. O "sparkle" some — é enfeite, não função. O chip de contexto ativo (captura 1: "Google Chrome • …") já comunica o que o modelo "vê". |
| Enviar é botão escuro isolado | OK, mas não diz o atalho. | Mantém o botão; adiciona `Enter` como envio e mostra o atalho `Ctrl+↵` no tooltip. Em voz ao vivo, o botão vira "Encerrar" (ver §4). |

> **Correção na implementação (§1.4):** o terceiro controle **não** é enfeite —
> é o alternador do **Modo Agente**, que muda o significado do Enter. Ele tem
> função e continua acessível (também em `Ctrl+K`), mas o diagnóstico da
> captura estava certo no essencial: os três dividiam a mesma silhueta, e isso
> apagava a hierarquia. A resolução foi dar **forma própria** ao modo em vez de
> apagá-lo:
>
> - o corredor de ícones volta a ter **dois** (câmera, clipboard), ambos
>   botões-fantasma circulares;
> - o modo vira um **chip rotulado** ("Agente"), com contorno no estado
>   desligado e preenchimento no ligado — o contorno existe porque sem ele o
>   rótulo flutuava entre os ícones parecendo texto solto, não um controle;
> - o campo muda de convite quando o modo liga ("peça ao Copilot" →
>   "descreva o objetivo"), que é o feedback no lugar onde o modo age.
>
> Sobre o tooltip: o campo é de **linha única**, então `Ctrl+↵` é redundante
> com `Enter`. Ele foi implementado assim mesmo — quem chega de outros
> aplicativos de conversa tenta `Ctrl+↵` por hábito, e o tooltip só pode citar
> um atalho se ele funcionar. O controlador de tecla roda em fase **CAPTURE**
> para consumir o evento antes do `Gtk.Entry`: em fase BUBBLE o mesmo Return
> viraria dois envios.
>
> O item "em voz ao vivo o botão vira Encerrar" fica com o §4 — a voz ao vivo
> abre **outra janela** (HUD/pílula), então o botão em questão é o de lá.

### 1.5 Barra de status inferior

| Problema visto | Justificativa | Mudança proposta |
|---|---|---|
| 6 itens iguais: modelo, tokens, carga, RAM, docs, rede | Sem agrupamento (§0.1); `Carga 0.87` e `Rede…` truncado são crípticos. | Três regiões separadas por leve divisor: **Esquerda** = cérebro (`gemini-3.6-flash`) + contagem de tokens da sessão. **Centro** = contexto do RAG (`98 docs`). **Direita** = saúde do SO em `ttnum`: `Carga 0.87 · RAM 9.1/15.6 GB · Rede ok`. "Rede" vira palavra inteira, não "Rede…". |
| `0 tokens` com chat vazio | Confunde (parece erro de contador). | Mostra `—` ou esconde a contagem até a primeira troca. Só exibe número após o primeiro token consumido. |

---

## 2. Diálogo de Configurações — capturas 3, 4, 5, 6, 7, 8

### 2.1 Arquitetura do diálogo (o maior problema)

A captura 3 mostra: **"Provedor Ativo" com Gemini selecionado no topo, e logo
abaixo as seções "Configuração do WorkBuddy AI" E "Configuração do Google Gemini"
empilhadas**. Isso é uma contradição de modelo mental — se só um provedor está
ativo, por que configuro os outros na mesma rolagem? E a barra de 6 abas no rodapé
(`Inteligência Arti…`, `Base de Conheci…`, `Extensões &…`, `Casa Inteligente`,
`Atalho & HUD`, `Documentos & P…`) tem **rótulos truncados** porque são 6 abas
demais para a largura.

**Diagnóstico:** o diálogo tentou ser uma "central de controle" única e virou uma
parede. Seis abas é o limite onde rótulos cortam; e provedor-ativo + todos os
provedores visíveis é um erro de exclusão mútua.

**Mudança proposta — duas colunas, não seis abas:**

```
┌─────────────────────────────────────────────────────────────┐
│ Configurações do Copilot                        [Buscar ⌘K] ✕ │
├──────────────────┬──────────────────────────────────────────┤
│ Navegação (lista)│  Conteúdo da seção (scroll vertical)      │
│  ▸ IA & Modelos  │                                           │
│  ▸ Conexões      │  ── Provedor ativo (segmentado) ──        │
│  ▸ Casa & Automação│  Gemini selecionado → SÓ Gemini mostra  │
│  ▸ Privacidade   │  ── Config do Gemini (api key, voz) ──    │
│  ▸ Atalhos       │                                           │
│  ▸ Sobre & Uso   │                                           │
└──────────────────┴──────────────────────────────────────────┘
```

- **Navegação à esquerda** (estilo painel do GNOME Conta/Compartilhamento) em vez
  de 6 abas truncadas. Cada item tem ícone + rótulo inteiro. Resolve o corte.
- **Provedor ativo é exclusivo:** o segmented control no topo da seção "IA &
  Modelos" troca o conteúdo abaixo — WorkBuddy/Ollama/OpenAI **não** aparecem
  simultaneamente. A seção "Recursos Adicionais" (captura 3) vira um subgrupo
  dentro de "Conexões", não um rodapé órfão.
- **Cinco páginas, não seis.** "Extensões & MCP" e "Casa Inteligente" viram uma
  só ("Conexões"): o usuário pensa nas duas como "o que o Copilot se conecta".
  Com cinco rótulos curtos (`IA & Modelos`, `Memória`, `Conexões`, `Atalhos`,
  `Privacidade`) a navegação deixa de truncar.

> **Correção da análise inicial:** o rodapé "O Zorin Copilot é um assistente com
> IA…" que aparece nas capturas 5–7 **não** pertence ao diálogo de preferências —
> é a legenda da barra de prompt da janela principal, visível atrás do modal.
> Não é um banner sobrepondo conteúdo; não há nada a corrigir ali.

### 2.2 Seção "IA & Modelos"

| Problema visto | Justificativa | Mudança proposta |
|---|---|---|
| API key mostra só pontos; estado "ativo" invisível | Não dá para saber se a chave funciona sem enviar mensagem. | Ao lado do campo, um chip de estado: `● Conectado` (verde) / `○ Sem chave` (neutro) / `⚠ Erro 401` (aviso) — teste leve ao colar. |
| "Modelo de Voz" e "Voz do Assistente" no fim do Gemini | São de voz, não de texto; misturados com config de API. | Vão para a subseção "Voz" (dentro de "Conexões" ou "IA & Modelos → Voz"). Agrupar por *modalidade*, não por provedor. |
| `Obter chave gratuita` é link cru | OK, mas isolado. | Mantém; fica ao lado do chip de estado, não solto no fim da seção. |

### 2.3 Seção "Conexões" (MCP + Home Assistant) — capturas 5, 6

**MCP (captura 5):**
- 6 servidores com silhueta idêntica, sem categoria. **Agrupar por tipo**:
  `Local (git, filesystem, sqlite)` · `Rede (fetch, duckduckgo)` · `Dev (github)`.
- Cada linha mostra *tools* (ex.: `12 ferramentas`) — bom, mas o chevron de expandir
  não revela nada na captura. **O expandir deve mostrar a lista de ferramentas**
  (nome + 1 linha do que faz), não ficar mudo.
- **Não há botão "Adicionar servidor"** — só edição de JSON. Para quem não é dev,
  isso é um beco. Adicionar um `Adw.ActionRow` "Adicionar servidor…" que abre um
  pequeno formulário (nome + comando + args) e escreve no JSON. O JSON continua
  sendo a fonte, mas deixa de ser a única porta.
- Aviso `requer token em GITHUB_PERSONAL_ACCESS_TOKEN` (captura 5) é bom — manter,
  mas como *sublinha vermelha* da linha github, não texto solto.

**Home Assistant (captura 6):**
- Só URL + token + "Testar" + entidade opcional. Funciona, mas o hint "Entidade de
  Luz Padrão" sugere controle de dispositivos que a UI não mostra. **Adicionar uma
  prévia de estado** após "Testar": se conectar, lista 3–5 entidades descobertas
  (luzes/dispositivos) como chips de leitura. Não é dashboard completo — é prova de
  que a integração respira.

> **Correção na implementação (§2.3)** — cinco decisões que divergem da proposta,
> todas por um motivo que só apareceu ao escrever o código:
>
> 1. **As categorias são `Nesta máquina` · `Web` · `Serviços externos` · `Outros`,
>    não `Local` · `Rede` · `Dev`.** A proposta agrupava pelo *tipo de coisa*
>    ("Dev" para o GitHub); o que o usuário precisa saber antes de ligar um
>    servidor é **o que ele alcança**, não em que prateleira ele mora. `Nesta
>    máquina` vs. `Web` vs. `Serviços externos` responde "isto mexe nos meus
>    arquivos?", "isto sai para a internet?", "isto fala com a minha conta?" — que
>    é a pergunta de confiança. `Outros` fica por último porque é o resto, não uma
>    categoria de verdade.
> 2. **A categoria é derivada, não configurada.** Não existe campo `category` no
>    `mcp.json`: exigir etiqueta à mão deixaria todo arquivo já existente sem
>    etiqueta — a mesma dívida que o `description` opcional já criou.
>    `mcp/categories.py` classifica por **token inteiro** sobre nome, comando e
>    host da URL. Token, nunca substring: `github` contém `git` e cairia em
>    "Nesta máquina". O esquema (`https://`) é **transporte, não capacidade** —
>    por isso é removido antes da tokenização; sem isso, todo servidor remoto
>    virava "Web" só por estar atrás de uma URL.
> 3. **A `description` fica fora da classificação.** É prosa escrita à mão
>    ("operações em repositórios Git"): classificar por ela seria etiquetar o
>    servidor pelo texto que o usuário escreveu *sobre* ele. Adivinhação com
>    aparência de dado.
> 4. **O erro assume o subtítulo da linha fechada.** A proposta dizia "sublinha
>    vermelha da linha github". O subtítulo do `Adw.ExpanderRow` tem **uma** linha,
>    e o aviso vinha por último — depois da descrição inteira, ou seja, era o
>    primeiro trecho cortado, justamente o mais urgente. Agora o subtítulo é
>    `Erro: requer token em …` e a descrição reaparece **dentro** do expansor
>    ("O que este servidor faria"), para não sumir. A marca vermelha é a classe
>    `.error` na linha inteira — classe do próprio Libadwaita, que **cascateia**
>    para os rótulos internos (medido em Adw 1.9.4: `rgb(255,147,140)`), em vez
>    de um seletor sob medida.
> 5. **A prévia de entidades prioriza domínios acionáveis e tem teto de 5.** O
>    Home Assistant devolve centenas de entidades; a amostra existe para provar
>    que a integração respira, não para ser um painel. Domínios que o Copilot sabe
>    *acionar* (`light`, `switch`, `climate`, `cover`, `fan`, `media_player`) vêm
>    primeiro, porque são os que respondem "o Copilot controla o quê?". Os estados
>    traduzem só o que aparece (`on` → `ligado`), em vez de fingir uma tradução
>    completa que não existe. Os chips **não têm `:hover`**: são leitura, e um
>    chip que reage ao ponteiro promete um clique que não acontece.
>
> **Nota sobre o "Adicionar servidor":** o formulário grava via `MCPManager`, que
> **relê o arquivo antes de gravar**. Sem isso, um servidor acrescentado fora do
> app seria apagado pelo `save_config()`, que escreve o dicionário inteiro — o
> formulário viraria uma máquina de perder configuração alheia.


### 2.4 Seção "Privacidade" — captura 8

| Problema visto | Justificativa | Mudança proposta |
|---|---|---|
| 2 pastas como texto puro, sem botão de editar | O usuário vê mas não toca. | Cada zona de confiança vira uma `Adw.ActionRow` com botão "Remover" (hover) + um `Adw.ActionRow` "Adicionar pasta…" com seletor de diretório. O que é listado deve ser editável no mesmo lugar. |
| "Mascarar PII" / "Forçar RAG local" técnicos demais | Jargão afasta usuário não-dev. | Mantém o nome técnico (precisão importa), mas cada toggle ganha *um* exemplo humano embaixo: "Mascara CPF, e-mail e token antes de sair do PC" / "Seu índice de documentos nunca toca a nuvem". |

### 2.5 Seção "Atalhos" — captura 7

- Três blocos (HUD, Recorte, Wake Word) já bem separados — **manter**.
- `Frases de Ativação` aceita vírgulas num campo só. Trocar por *chips* editáveis
  (cada frase = um chip removível), igual ao padrão de contexto da barra de prompt.
- `Caminho do Modelo Vosk` tem exemplo hardcoded. Adicionar botão "Selecionar
  arquivo" (usa `Gtk.FileDialog`), não só digitação.

> **Divergência deliberada na implementação (§2.5):** o seletor do modelo usa
> `select_folder`, não `open`. O modelo do Vosk é uma **pasta** (`am/`, `conf/`,
> `graph/`…), e `vosk.Model()` recusa um arquivo em runtime — o usuário escolheria
> um caminho que parece certo e falha depois, sem pista do motivo. O rótulo ficou
> "Pasta do Modelo Vosk" e o botão "Selecionar a pasta do modelo…" para o campo
> não prometer algo que não acontece.

---

## 3. Telas de estado & memória — capturas 4, 9

### 3.1 Seletor de modelo (captura 9)

| Problema visto | Justificativa | Mudança proposta |
|---|---|---|
| Só modelos locais (Ollama); cabeçalho diz "local ou em nuvem" | Promessa não cumprida na tela. | Duas subseções: **Nuvem** (Gemini/WorkBuddy ativos) e **Local (Ollama)**. A nuvem mostra o modelo padrão marcado. |
| `8.0B · Q4_K_M · 4.6 GB` sem contexto | Números crus não dizem "roda na minha GPU?". | Adicionar 1 linha de adequação: "Requer ~6 GB VRAM" ou "Roda em CPU, lento". Reutiliza o `perfil do computador` (captura 4) que já sabe a GPU/RAM. |
| Tag "Uncensored" em destaque | Pode soar como vantagem manipulada; é apenas uma característica. | Tag vira neutra (`Sem filtro`) e secundária; o destaque fica no *caso de uso* ("Privado", "Visão", "Rápido"). |
| Sem busca com 7+ modelos | Rolagem cresce. | Campo de filtro no topo do modal (já existe `Ctrl+K` global; reutilizar o padrão de busca difusa aqui também). |

> **Notas de implementação (§3.1)**
>
> **Ordem das seções — nuvem primeiro.** A proposta pedia "duas subseções", sem
> fixar a ordem. A implementação põe **Nuvem → Destaque → Ollama → LM Studio**,
> invertendo o que existia. O motivo é quem usa o app: o modelo local exige um
> Ollama instalado e modelos baixados, então para a maioria a tela abria numa
> seção vazia ou num erro de conexão. A ordem agora segue a probabilidade de a
> seção ter conteúdo — e o que está em uso vem antes do que está disponível.
>
> **A linha de adequação é do programa, não do usuário.** `8.0B · Q4_K_M · 4.6 GB`
> são números corretos e inúteis: quem vai baixar quer saber se **roda**. O cálculo
> virou um módulo próprio, `core/hardware_fit.py`, separado da UI de propósito —
> são funções quase puras, testáveis sem GPU e sem modelo. Três decisões:
>
> * o requisito é o arquivo **mais** a cache de contexto (`VRAM_OVERHEAD_FACTOR =
>   1.2`), porque o tamanho em disco sozinho subestima a carga;
> * há uma faixa de "cabe justo" (`TIGHT_HEADROOM = 0.15`) — cabe, mas sem folga
>   para o compositor e o navegador, e o offload dinâmico derruba a velocidade;
> * sem VRAM mensurável o veredito é `unknown`, nunca um palpite. GPU integrada,
>   driver exótico ou `nvidia-smi` ausente levam à mesma frase honesta.
>
> A sondagem é cacheada e **não sobe processo em modo de teste**; a VRAM por
> sysfs continua sendo lida, que é só leitura de arquivo. O nome da GPU passa por
> `_short_gpu_name`, porque o `lspci` devolve a descrição inteira
> (`Advanced Micro Devices, Inc. [AMD/ATI] Navi 33 [Radeon RX 7600/7600 XT/7600M
> XT/7600S/7700S / PRO W7600] (rev cf)`) — nada disso cabe num subtítulo. O alvo é
> `Radeon RX 7600`.
>
> **Vocabulário neutro.** O título do card Dolphin perdeu o emoji (`🐬` → ícone
> simbólico do Adwaita, que é a diretriz do projeto) e a tag `Sem filtro` deixou de
> vir com classe de destaque: é característica de treino, não argumento de venda.
> As tags passaram a priorizar **caso de uso** (`Autônomo`, `Local GPU`, `Visão`,
> `Rápido`, `Grande`), que é o que ajuda a escolher.
>
> **O filtro quase passou despercebido quebrado — e vale registrar por quê.** A
> primeira versão varria os filhos diretos do grupo. O Libadwaita **não** anexa as
> linhas como filhas diretas: empacota tudo num `Gtk.ListBox` interno dentro de uma
> `Gtk.Box` (o mesmo truque que `Adw.ActionRow.add_prefix()` faz com prefixos). A
> varredura achava um contêiner anônimo, e a guarda defensiva "linha sem título não
> é escondida" transformava esse engano em "tudo casa com tudo": o campo aceitava
> texto, nada mudava na tela e **nenhum erro aparecia**. Duas correções: a varredura
> desce a árvore ancorada em `Adw.PreferencesRow` (assim uma linha removida sai da
> varredura sozinha, sem lista paralela a manter), e a guarda silenciosa saiu —
> melhor falhar alto do que mentir em silêncio.
>
> **A busca difusa do `Ctrl+K` não servia inteira.** `score_command` termina numa
> correspondência por subsequência, ótima sobre um título curto (`nconv` →
> "Nova conversa") mas perversa sobre os subtítulos deste diálogo, que são frases
> inteiras em português: com a busca `gemini` o card do Dolphin entrava, porque
> `gemini` é subsequência de "A**g**ent**e** local se**m** censura nem recusas
> corporativas". Um filtro que mostra cinco de seis seções não filtra. A regra
> ficou em duas camadas — difusa sobre o **título** (onde o identificador do modelo
> vive), substring simples sobre o **resto** (subtítulo, especificações, tags),
> sem acentos para `visao` achar `Visão`. Isso diverge do `Ctrl+K` de propósito, e a
> divergência está documentada no código.
>
> **A visibilidade de grupo passou a ter um dono só.** O render marcava o grupo do
> LM Studio como visível *antes* de acrescentar as linhas; quando nenhum item
> trazia `id`, a marcação sobrevivia e deixava um cabeçalho de seção sozinho na
> tela. Junto com a linha "Buscando modelos…" — que continua na árvore, apenas
> oculta, depois do render — eram duas fontes de verdade sobre o que aparece. Agora
> quem decide é `_apply_filter()`, a partir das linhas que existem de fato, e o
> `_load_models_async` reaplica o filtro quando mexe na linha de carregamento.

### 3.2 Memória aprendida (captura 4) — **bug, não só UX**

O fato "meu café favorito é espresso duplo" aparece **10 vezes** com a mesma data
`2026-09-20`. Isso é duplicação no armazenamento (o `MEMORY.md`/perfil local está
gravando o mesmo fato a cada sessão). A UI só piora: 10 alvos de lixeira.

**Duas frentes:**
1. **Dados:** o gravador de memória deve *upsert* por conteúdo normalizado (hash do
   texto limpo), não append. Se já existe "café favorito = espresso duplo", atualiza
   a data, não cria linha.
2. **UI:** enquanto o bug não fecha, a lista deve **agrupar idênticos** e oferecer
   "Mesclar duplicados (9)" num cabeçalho de ação. Cada fato mostra *origem* (usuário
   / sistema) e *última vez usado*, não só "atualizado em".

---

## 4. Modo Voz ao Vivo — captura 10

| Problema visto | Justificativa | Mudança proposta |
|---|---|---|
| Botão `janela` sem ícone; os outros 4 têm | Inconsistência visual imediata. | `janela` recebe ícone (ex.: `window-popup`); todos os botões de transmissão usam a mesma família de ícone. |
| Sidebar visível consumindo ~25% da largura | Em voz, o foco é onda + transcrição, não o histórico. | Em sessão ao vivo, a sidebar **recolhe automaticamente** (como `Ctrl+H`) e a onda ganha a largura. O histórico volta ao fechar. |
| Transcrição mostra só a fala atual | O usuário perde o que foi dito/feito. | Painel de transcrição rolável à direita da onda, com timestamps e ícone por ferramenta (`launch_app`, `media_control`) — igual ao log acumulado já implementado em `live_view.py` (Sprint 2), mas visível lado a lado, não escondido. |
| Cronômetro `00:41` no cabeçalho | Bom — manter. | Manter; adicionar `● Gravando` piscando só quando o microfone está ativo (estado, não botão). |
| `Encerrar` em vermelho | Bom para destrutivo — manter. | Manter; mover para a direita extrema, separado dos botões de transmissão por um divisor. |

> **Correção na implementação (§4)** — o que a proposta dizia e o que o código mostrou:
>
> 1. **O ícone proposto não existe.** A proposta mandava dar ao botão `janela` um
>    `window-popup` — e `window-popup-symbolic` **não existe** em nenhum tema
>    instalado (conferido em `/usr/share/icons`): aplicá-lo deixaria o botão sem
>    ícone de verdade. E o botão **já tinha** ícone (`window-restore-symbolic`,
>    presente desde o `HEAD`), então o sintoma "sem ícone" não se reproduz. O
>    defeito real que a captura provavelmente mostrava é outro, e esse é
>    verificável: no estado "Tela Inteira" o botão de modo usava
>    `video-display-symbolic`, que é **o glifo exato do botão vizinho "Transmitir
>    Tela"** — dois controles diferentes com a mesma cara. Corrigido com dois nomes
>    que existem de fato (`focus-windows-symbolic` / `view-fullscreen-symbolic`), e
>    há teste afirmando que os cinco glifos do dock são distintos **e** que os
>    nomes escolhidos existem no tema.
> 2. **A piscada tem de ser Python, não CSS.** O GTK4 não tem `@keyframes` e não
>    anima `transform`, então `● Gravando` pisca por um `GLib.timeout_add` de 600 ms
>    alternando uma classe. Consequência que a proposta não previa: a preferência
>    `reduced_motion` do projeto tem de ser consultada **aqui também**, senão ela
>    valeria só para o que o CSS alcança. Para isso `_reduced_motion_enabled` virou
>    `reduced_motion_enabled` (pública) em `ui/style.py` — fonte única. Com
>    movimento reduzido o indicador fica **aceso e parado**.
> 3. **O indicador é sobre o microfone, não sobre a chamada.** Acende só em
>    `LISTENING` e com o mic aberto; apaga enquanto o modelo fala, pensa ou executa,
>    ao desconectar e quando o mic está mudo. Detalhe que quase escapou: **mutar não
>    muda o estado da sessão**, só o do microfone — sem uma chamada explícita em
>    `_on_toggle_mute`, o indicador continuaria aceso com o mic fechado.
> 4. **O histórico nasce visível e com largura fixa.** A proposta pedia "visível lado
>    a lado, não escondido"; além disso o painel aparece **vazio** no início, com uma
>    linha dizendo o que vai aparecer ali. O motivo é de layout: um painel que só
>    surge na primeira linha faz a onda ao lado **redimensionar no meio da fala**.
> 5. **O cartão é compacto, não ocupa a janela.** "A onda ganha a largura" poderia
>    sugerir que a vista de voz preenche a tela. Não preenche:
>    `chat_main_box = [live_voice_revealer, chat_stream, prompt_bar]`, então o cartão
>    fica no topo da coluna do chat e a conversa segue abaixo. O mockup documenta
>    isso.
> 6. **O encerramento autônomo também devolve a barra lateral.**
>    `_perform_end_session` **não** chama `stop_live_voice()` de propósito (ela
>    termina em `summon_hud()` e reabriria a janela na hora de encerrar). Restaurar a
>    barra só no `stop_live_voice` deixaria a barra recolhida **para sempre** depois
>    de o agente encerrar a chamada por conta própria. Os dois caminhos restauram.

---

## 5. Critérios de aceitação da reformulação

A proposta está "pronta" quando:

1. **Nenhum rótulo de navegação está truncado** (sidebar, config, modelo).
2. **Toda ação destrutiva** (lixeira, limpar histórico, apagar memória) está atrás
   de hover, menu ou confirmação — nunca como botão de rodapé sempre visível.
3. **Comando e estado têm formas diferentes** na barra de topo e na de status.
4. **Listas densas** têm diferenciador primário (ícone de tipo) + secundário
   (status/duração) e `tooltip` no título completo.
5. **Configurações** usam navegação em lista (não 6 abas) e provedor-ativo é
   exclusivo na tela.
6. **Memória duplicada** é agrupada na UI e corrigida na origem (upsert).
7. **Modo voz** recolhe a sidebar e mostra transcrição histórica ao lado da onda.

---

## 6. O que **não** estou propondo (antídoto contra "AI slop")

Para evitar o padrão genérico "tudo vira card arredondado type Linear/Notion":

- **Não** transformo a sidebar num grid de cartões — listas de texto com ícone são
  mais densas e escaneáveis para 50+ conversas; é o que o `Ctrl+↑/↓` precisa.
- **Não** introduzo "hero section" nem gradientes novos — o glassmorphism existente
  já tem identidade; a proposta estende, não substitui.
- **Não** crio um "dashboard" de Home Assistant — só uma prévia de 3–5 entidades
  após conectar, porque o app é um copilot, não um hub de IoT.
- **Não** sugiro "mais espaço em branco" como solução — o app roda como janela
  flutuante no Zorin; densidade controlada é uma feature, não um bug.
- **Não** troco a paleta `#15a6f0`/vidro por outra — `@accent_color` é a âncora de
  retematização (há teste garantindo isso em `SISTEMA_VISUAL_SUPERHUMAN.md`).

Cada mudança acima nasce de um problema visível nas capturas, não de uma tendência.

---

## 7. Status da implementação (primeira fatia)

| # | Mudança | Onde | Estado |
|---|---|---|---|
| §1.1 | Cerca espacial sai do topo e vai para a barra de status; topo fica com 3 comandos e o título deixa de truncar | `widgets/header.py`, `widgets/status_bar.py`, `ui/app.py` | ✅ |
| §1.1 | Emojis removidos dos rótulos de cerca/modelo (violavam a diretriz anti-slop) | `widgets/header.py` | ✅ |
| §1.5 | Tokens saem do badge do topo e ficam só na barra de status; barra dividida em 3 regiões; "0 tokens" some até haver uso; "Rede …" vira texto completo | `widgets/status_bar.py`, `data/zorin-copilot.css` | ✅ |
| §1.2 | Conversas agrupadas por data; lixeira só no hover (slot fixo, sem deslocar texto); ícone de tipo; tooltip com título inteiro; `Limpar histórico` vira item de menu com confirmação | `widgets/sidebar.py`, `data/zorin-copilot.css` | ✅ |
| §2.1 | 6 abas → 5 páginas curtas (`IA & Modelos`, `Memória`, `Conexões`, `Atalhos`, `Privacidade`); entidade `&amp;` literal corrigida | `ui/preferences.py` | ✅ |
| §3.2 | **Bug de dados:** chave do fato era `fato_<epoch>` → mesma frase virava N linhas. Agora a chave deriva do conteúdo (`MemoryManager.fact_key`), com `dedupe_facts()` para bases legadas e agrupamento "×N" + ação "Mesclar" na UI | `core/memory.py`, `ai/engine.py`, `ai/live.py`, `ai/agent_tools.py`, `ui/preferences.py` | ✅ |
| §2.4 | Zonas de confiança deixam de ser texto e viram linhas editáveis (remover no hover, "Adicionar pasta…" via `Gtk.FileDialog`); cada toggle técnico ganha um exemplo humano no subtítulo | `ui/preferences.py`, `data/zorin-copilot.css` | ✅ |
| §2.5 | `Frases de Ativação` vira editor de *chips* removíveis (`WakePhrasesRow`); dedupe pela normalização do próprio motor (`normalize_text`); pasta do modelo Vosk ganha seletor nativo | `ui/preferences.py`, `data/zorin-copilot.css` | ✅ |
| §1.4 | Corredor de ícones volta a ter dois (câmera, clipboard); Modo Agente vira chip rotulado com estado visível; `Ctrl+Enter` implementado e citado no tooltip do envio | `ui/widgets/prompt_bar.py`, `data/zorin-copilot.css` | ✅ |
| §3.1 | Seções reordenadas (Nuvem → Destaque → Ollama → LM Studio): a ordem segue a probabilidade de a seção ter conteúdo | `ui/widgets/model_selector.py` | ✅ |
| §3.1 | Novo `core/hardware_fit.py`: VRAM por `nvidia-smi`/sysfs, RAM por `/proc/meminfo`, veredito `gpu`/`tight`/`cpu`/`insufficient`/`unknown`; cada linha de modelo local ganha "~X GB em uso" + veredito | `core/hardware_fit.py`, `ui/widgets/model_selector.py` | ✅ |
| §3.1 | Card Dolphin perde o emoji; tag `Sem filtro` vira secundária (`dim-label`); tags passam a priorizar caso de uso (`Visão`, `Rápido`, `Grande`) | `ui/widgets/model_selector.py` | ✅ |
| §3.1 | Campo de filtro fixo no topo (`Adw.ToolbarView.add_top_bar`), grupos vazios se escondem; matcher em duas camadas (difuso no título, substring no resto, sem acentos) | `ui/widgets/model_selector.py` | ✅ |
| — | Testes novos: `tests/test_sidebar_grouping.py` (9) + 5 de memória; testes existentes que fixavam emoji/token-no-topo atualizados | `tests/` | ✅ |
| — | Testes novos: `tests/test_preferences_privacy.py` (16) e `tests/test_preferences_shortcuts.py` (30), mais `tests/_gtk_helpers.py` (introspecção da árvore de widgets compartilhada) | `tests/` | ✅ |
| — | Testes novos: `tests/test_prompt_bar_reform.py` (19) — hierarquia, ordem, estado do modo e atalhos | `tests/` | ✅ |
| — | Testes novos: `tests/test_model_selector_filter.py` (41) e `tests/test_hardware_fit.py` (38) — inclui a regressão da varredura de linhas do Libadwaita | `tests/` | ✅ |
| §1.3 | Estrela decorativa de 44px sai; entra a linha de estado (distro + ambiente de desktop), sob `try/except` para não impedir a tela de abrir | `core/desktop/env.py`, `ui/widgets/chat_stream.py` | ✅ |
| §1.3 | 4 chips 2×2 sem hierarquia → 2 grupos rotulados por `is_contextual` ("Sobre o que está na tela" / "Como interagir"), com uma vaga de modo reservada **dentro** do orçamento; grupos vazios não renderizam | `ui/widgets/chat_stream.py` | ✅ |
| §1.3 | "Retomar" na tela vazia: até 3 conversas como linhas clicáveis, reusando `build_topic_identity` e a classe `sidebar-chat-row`; sem lixeira e sem atalho de exclusão | `ui/widgets/sidebar.py`, `ui/widgets/chat_stream.py` | ✅ |
| §1.3 | `DistroInfo.display_name` como fonte única do nome curto da distro (antes o fallback estava duplicado) | `core/desktop/env.py` | ✅ |
| — | CSS órfão `.welcome-icon` removido das duas listas (claro/escuro) após a saída da estrela | `data/zorin-copilot.css` | ✅ |
| — | Testes novos: `tests/test_welcome_reform.py` (44) — linha de estado, agrupamento por contexto, orçamento, "Retomar" e identidade compartilhada entre as duas telas | `tests/` | ✅ |
| — | Mockup da janela atualizado para refletir o §1.4, com os dois estados do chip de modo lado a lado | `docs/mockup-janela-principal.html` | ✅ |
| — | Mockup atualizado para o §1.3 (linha de estado, dois grupos rotulados, "Retomar" como lista); largura fixa do chip removida e estado do quadro anotado | `docs/mockup-janela-principal.html` | ✅ |
| §2.3 | Novo `mcp/categories.py`: categoria **derivada** (sem campo `category` no JSON) por **token inteiro** sobre nome + comando + host; `github` deixa de cair em "Nesta máquina"; esquema da URL removido antes de tokenizar (transporte ≠ capacidade) | `mcp/categories.py` | ✅ |
| §2.3 | Lista plana de MCP → um `Adw.PreferencesGroup` por categoria (`Nesta máquina` · `Web` · `Serviços externos` · `Outros`), com descrição do que a categoria alcança; grupos vazios não renderizam; grupos recriados a cada recarga | `ui/preferences.py` | ✅ |
| §2.3 | "Adicionar servidor…" antes do JSON: `Adw.Dialog` com Nome/Comando/Argumentos (`shlex.split`), Enter em qualquer campo confirma; a regra ficou em `_submit_new_mcp_server` (testável sem janela) e a gravação relê o arquivo antes de escrever | `ui/preferences.py`, `mcp/manager.py` | ✅ |
| §2.3 | O erro assume o subtítulo da linha fechada (`Erro: requer token em …`) e a descrição reaparece dentro do expansor; marca `.error` na linha inteira (classe do Libadwaita, que cascateia para os rótulos internos) | `ui/preferences.py` | ✅ |
| §2.3 | Prévia de entidades do Home Assistant após "Testar": até 5 chips de leitura, domínios acionáveis primeiro, estados traduzidos só o que aparece; grupo nasce oculto e só aparece com entidades | `ui/preferences.py`, `data/zorin-copilot.css` | ✅ |
| — | Testes novos: `tests/test_mcp_connections_reform.py` (73) — classificação, agrupamento, formulário, linha de erro, prévia de entidades e guarda de CSS do chip | `tests/` | ✅ |
| — | Mockup **não** alterado no §2.3: nenhum mockup retrata o diálogo de Configurações (`mockup-janela-principal.html` é a janela principal; `landing-prototype.html` é página de marketing). Não se inventou um mockup novo — um mockup que mente é pior que nenhum | — | ✅ |
| §4 | Glifos do dock deixam de colidir: o botão de modo usava `video-display-symbolic` no estado "Tela Inteira", o mesmo ícone do vizinho "Transmitir Tela"; agora usa `focus-windows-symbolic` / `view-fullscreen-symbolic` (nomes conferidos no tema — o `window-popup-symbolic` da proposta não existe) | `ui/live_view.py` | ✅ |
| §4 | Barra lateral recolhe sozinha na chamada e volta ao estado **anterior** (se o usuário já a tinha fechado, não abre); os **dois** caminhos de encerramento restauram, inclusive o autônomo, que não passa por `stop_live_voice()` | `ui/app.py` | ✅ |
| §4 | Histórico da sessão sai de baixo das legendas e vai para o **lado** da onda, visível desde o início e com largura fixa (260px), para a onda não redimensionar quando a primeira linha chega; painel vazio traz uma linha explicando o espaço | `ui/live_view.py`, `data/zorin-copilot.css` | ✅ |
| §4 | Indicador `● Gravando` que acende só com o microfone captando (não enquanto o modelo fala, não mudo, não desconectado); piscada por timer em Python e **respeitando `reduced_motion`** (aceso e parado) — o que exigiu tornar `reduced_motion_enabled` pública em `ui/style.py` | `ui/live_view.py`, `ui/style.py`, `data/zorin-copilot.css` | ✅ |
| §4 | `Encerrar` vai para a ponta direita atrás de um divisor; dois espaçadores de peso igual mantêm o grupo de transmissão centrado | `ui/live_view.py`, `data/zorin-copilot.css` | ✅ |
| — | Testes novos: `tests/test_live_voice_reform.py` (36) — glifos distintos e existentes, regra de recolher/restaurar a barra, posição do histórico, indicador de captação (inclui o caso de movimento reduzido) e o divisor | `tests/` | ✅ |
| — | Mockup novo `docs/mockup-voz-ao-vivo.html`: a vista de voz não tinha mockup nenhum. Renderizado e conferido; a primeira versão **mentia** (cartão ocupando a janela inteira e dock no rodapé) e foi corrigida para a estrutura real | `docs/mockup-voz-ao-vivo.html` | ✅ |
| — | Correção de infraestrutura de teste: `test_find_elements_ranking` consultava o desktop real (Hyprland) e falhava com o app aberto (`0 != 2`); passou a injetar cerca vazia explícita, como o teste irmão | `tests/test_ui_grounding.py` | ✅ |
| — | Correção de infraestrutura de teste: a suíte subia `uvx mcp-server-git` de verdade (`start_server` escapa do patch de `start_all_enabled`) e travava em ~82 %; `uvx`/`npx` entraram na lista de bloqueio do `conftest.py` e as duas classes de config neutralizam `start_server` | `tests/conftest.py`, `tests/test_mcp_connections_reform.py` | ✅ |
| — | Correção de infraestrutura de teste: `test_escape_closes_popover_before_hiding_window` travava o runner de guarda (`exit 124`, 180 s) porque `Gtk.Popover.popup()` bloqueia sem laço principal; passou a exercitar a ordem com popover falso, sem compositor | `tests/test_ui_refactor.py` | ✅ |
| — | Correção de infraestrutura de teste: `test_play_registers_and_advances_the_animation` bombeava até "a seleção saiu da raiz" e depois afirmava `type == "step"` — corrida entre predicado e asserção, `'branch' != 'step'` em ~1 de cada 5 execuções; o `_pump` passou a esperar a condição que a asserção exige | `tests/test_blackboard_render.py` | ✅ |
| — | Correção de infraestrutura de teste: o flake de pixel (~10 %, pixel do enunciado saindo na cor de fundo) vinha de resize assíncrono do compositor entre a fotografia do `WidgetPaintable` e o recorte do `render_texture`; o `setUp` agora espera o tamanho da área parar de mudar — igualdade exata preservada, 0 falhas em 30 execuções | `tests/test_blackboard_render.py` | ✅ |
| — | Correção de infraestrutura de teste: a janela do teste de render é **ladrilhada pelo compositor**, e `set_default_size` é só um pedido — num espaço de trabalho cheio o Hyprland concede 961x210 (desenho 641x160), onde o `_layout` **não cabe** (métodos em y=168..232, passos a partir de y=289); três testes falhavam por **recorte**, não por defeito de desenho. O tamanho passou a ser **exigido** (`set_size_request(680, 590)` na área de desenho), que o compositor honra: a janela sai 1000x640, o mesmo tamanho que o app usa | `tests/test_blackboard_render.py` | ✅ |

**Nada pendente.** O §4 fechou nesta fatia e era o último item: §1.1 a §1.5, §2.1 a
§2.5, §3.1, §3.2 e §4 estão implementados. Os sete critérios de aceitação de §5 ficam
cobertos — inclusive o 7º ("modo voz recolhe a sidebar e mostra transcrição histórica ao
lado da onda"), que era o único em aberto. As correções de infraestrutura de teste que
saíram da verificação também estão aplicadas, e o runner de guarda ficou **sem nenhuma
falha que não seja estrutural**.

**Suíte na última verificação (após §4):** `/tmp/pytestenv/bin/python -m pytest -q`
→ **2430 passados, 5 pulados, 0 falhas** (227 s, `EXIT=0`). Pelo runner de guarda
(`run_tests_safe.py`, unittest, um arquivo por processo) → **132 módulos, 13 falhas**,
pico de RSS **512 MB**, 101 s, nenhum aborto. (O runner de guarda caiu de 278 s / 531 MB
para ~100 s por causa das correções abaixo. O pico de RSS subiu de 472 MB para 512 MB
quando o teste de render passou a **exigir** uma tela de 680x590 em vez de aceitar a
que o compositor desse: são texturas maiores, é o custo esperado da correção — e 512 MB
segue abaixo do teto de 900 MB por arquivo do runner.)

As **13 falhas do runner de guarda são todas estruturais e pré-existentes**: 11 arquivos
importam `pytest` no topo e o `unittest` não vê o módulo, e 2 são estilo pytest — os 13
saem com `exit 5`, "Ran 0 tests", e dão verde sob o pytest. **Nenhum timeout, nenhuma
falha real.** (Antes desta fatia eram 278 s e uma falha ambiental a mais.)

> **Correção de 2026-09-21 — a leitura estava certa, a conclusão era perigosa.** Os
> números acima são deste round e continuam válidos como registro. Mas "estrutural e
> pré-existente" virou, em quatro documentos, a leitura de *"não é problema meu"* — e
> **eram 435 testes que passavam e nunca rodavam**, não uma condição benigna. O runner
> de guarda passou a invocar `pytest` por módulo e o baseline hoje é **147 módulos, 0
> falhas**. Detalhe em `docs/ESTABILIDADE_TESTES.md`. **`exit 5` significa "não rodou".**

O `test_ui_refactor` era essa falha a mais, e a causa foi medida, não suposta:
`Gtk.Popover.popup()` **bloqueia indefinidamente** quando não há laço principal rodando —
4 de 4 execuções travadas em reprodução isolada, inclusive com a janela já mapeada
(`get_mapped()` já devolvia `True`) e com o `GMainContext` drenado antes. O teste que
chamava isso era `WindowCompositionTest::test_escape_closes_popover_before_hiding_window`
(`set_visible(True)` + `popup()`). Sob o **pytest** ele passava, porque o `conftest.py`
mantém contexto e limpeza entre testes; sob o `unittest`, que não carrega `conftest.py`,
travava. **Não era regressão do §4** — a reprodução não tocava em nada que o §4 tenha
alterado. O que a regressão guarda é a **ordem** (popover fecha antes de a janela
esconder), e isso se verifica sem compositor: o teste agora injeta um popover falso via
`patch.object(Gtk.MenuButton, "get_popover", ...)`, afirma que `popdown()` foi chamado e
que a janela continua visível. O módulo saiu de `exit 124` (180 s) para **2,1 s**.

As outras duas correções de infraestrutura de teste que saíram desta verificação, ambas
com medição antes/depois:

1. **`tests/test_ui_grounding.py::test_find_elements_ranking` dependia do desktop.**
   Ele chamava `find_elements()` sem cerca, e isso constrói um `ScreenFenceManager()`
   real, que pergunta ao Hyprland (`hyprctl clients -j`) por janelas com "zorin" ou
   "copilot" na classe/título — ou com o `pid` do próprio processo. Com a janela do
   app mapeada (tiling `14,42 1892x982` desta máquina), as três coordenadas de fixture
   (100..300, 100..300) caem dentro dela e o teste falhava com `0 != 2`. Medido:
   `baseline -> 2`, `com janela aberta -> 0`. Agora injeta uma cerca vazia explícita,
   como já fazia o teste irmão `test_find_elements_excludes_copilot_window`.
2. **A suíte subia servidores MCP de verdade.** O fixture de sessão de
   `tests/conftest.py` blinda **apenas** `MCPManager.start_all_enabled`, mas
   `set_server_enabled`/`add_server` chamam `start_server` **direto** e escapam dele:
   `tests/test_mcp.py::test_mcp_manager_enable_disable_server` (`enable_server("git",
   True)`) e o formulário de `tests/test_mcp_connections_reform.py`, que passa
   `"mcp-server-git"`, subiam `uvx mcp-server-git`. Como o sandbox redireciona
   `XDG_CACHE_HOME`, o cache do `uv` é **frio** e o `uvx` baixa o pacote do PyPI a
   cada execução; depois o servidor fica vivo lendo stdin. Era isso que fazia a suíte
   parar em ~82 % sem escrever mais nada. Correção: `uvx`/`npx` entraram na lista de
   binários bloqueados do `conftest.py`, e as duas classes de
   `test_mcp_connections_reform.py` neutralizam `start_server` — elas testam gravação
   de configuração, não ciclo de vida de processo. Medido: o conjunto MCP foi de
   **travamento** para **157 passados em 4,65 s**.

**Lição operacional** (já na skill): o harness pode reportar uma execução como
"concluída" enquanto o processo continua vivo reparentado ao `init`, segurando ~1,2 GB.
Três desses acumulados com **swap = 0** produzem "travamentos" que não existem — a
execução seguinte para em 55 %, depois 82 %, depois 88 %, **em ponto diferente a cada
vez**. Essa variação é a assinatura de contenção, não de um teste defeituoso. Conferir
`ps -eo pid,ppid,etimes,rss,cmd | grep -E "pytest|unittest"` antes de concluir qualquer
coisa, e ler a linha de resumo no arquivo de saída em vez de confiar no status do
harness.

**Flake de pixel — diagnosticado e corrigido.** `test_root_node_is_painted_at_its_layout_position`
(e às vezes outro teste do mesmo arquivo) falhava ~10 % das vezes com o pixel do interior do
enunciado saindo na cor de **fundo**, `(238, 240, 243)` em vez de `(238, 237, 254)`, num ponto
que a geometria lógica garante estar dentro do nó. A investigação descartou as duas hipóteses
fáceis: a geometria **não** varia — `_layout()` usa uma tela lógica fixa de `960 px`, com o
enunciado sempre em `x = 480 ± 150`, `y = 60 ± 30` — e a transformação **é** identidade
(`_fit()` deixa `tx = ty = 0`, `scale = 1`), confirmado instrumentando o estado em execução.
A causa é o **resize assíncrono do compositor**: o `_pump` do `setUp` libera assim que o tamanho
mínimo é atingido, mas um redimensionamento ainda pode estar a caminho; rasterizando nesse
intervalo, o `Gtk.WidgetPaintable` fotografa o widget num tamanho e o `render_texture` recorta
noutro, e o conteúdo sai escalonado. Duas medições fecharam o diagnóstico: em processo isolado
o teste é **estável 12/12** (a janela não é redimensionada), e sob instrumentação ele ficou
**18/18 verde** — a leitura extra de estado dá tempo ao compositor e esconde a corrida.

A correção **espera o tamanho da área parar de mudar** (três leituras iguais seguidas) antes de
rasterizar. Isso ataca a causa sem afrouxar asserção nenhuma: a geometria continua sendo
conferida com **igualdade exata**, que é o ponto do teste — ele é o único do projeto que pega
falha silenciosa de desenho (o `draw_func` do GTK4 que recebe cairo e engole o `TypeError` sem
exceção). Medido: **0 falhas em 30 execuções consecutivas**, contra ~10–20 % antes.

**Segundo modo de falha do mesmo arquivo — a tela não cabia o desenho.** Fechado o flake de
pixel, rodar o arquivo junto com outros seis ainda dava `3 failed, 203 passed`: sempre
`test_dimmed_branch_renders_through_alpha_group` ("o método apagado continuou com a cor cheia"),
`test_enunciado_metodos_e_passos_are_rasterized` ("nó de método não foi desenhado") e
`test_renders_a_freshly_solved_graph`. A repetição **5 de 5 idêntica** já dizia que não era
corrida — era dependência determinística de ambiente. E, ao rodar o arquivo **sozinho**, ele
também falhava: a gravação anterior de "0 falhas em 30 execuções" era verdadeira e mesmo assim
não provava robustez, porque a área de trabalho tinha mudado desde então.

A instrumentação deu a resposta de imediato: `janela=961x253  desenho=641x203` — e não os
1000x640 pedidos em `set_default_size`. A área de desenho tinha **160 px de altura** depois de
estabilizar. Com `_layout()` colocando o enunciado em y=30..90, os métodos em y=168..232 e os
passos a partir de y=289, o desenho **não cabia**: o enunciado era pintado e os métodos ficavam
fora da tela. Os testes falhavam por **recorte**, não por defeito de desenho — e o
`test_root_node_is_painted_at_its_layout_position`, que só olha a faixa do enunciado, continuava
passando, o que é a assinatura exata desse diagnóstico.

A causa é o **compositor ladrilhando a janela do teste**. Com sete clientes abertos no espaço de
trabalho (Spotify, Chrome, LibreOffice, kitty, …), o Hyprland dá ao novo toplevel o ladrilho que
sobra; com a área de trabalho vazia, ele ocuparia a tela e o teste passaria. Ou seja: **o tamanho
da tela do teste era função de quantas janelas o usuário tinha abertas** — o que também explica
por que a falha parecia "de conjunto de arquivos": rodar vários arquivos abre e fecha janelas,
mexendo no ladrilhamento.

Medido o que o compositor honra:

| estratégia | janela | desenho |
| --- | --- | --- |
| `set_default_size(1000, 640)` (como estava) | 961x210 | 641x160 |
| `set_default_size(1600, 900)` | 961x210 | 641x160 |
| `set_resizable(False)` + default | 1000x640 | 680x590 |
| `drawing.set_size_request(680, 590)` | **1000x640** | **680x590** |

`set_default_size` é **ignorado** por quem ladrilha; o **mínimo** é honrado. A correção exige o
tamanho em vez de pedi-lo, e a janela sai exatamente 1000x640 — o mesmo tamanho que o app usa em
produção. Como qualquer WM respeita mínimo de janela, o mecanismo não é específico do Hyprland.

A largura de 680 foi escolhida com margem dos dois lados, e de propósito: passa da borda direita
do enunciado (x=630) e fica **antes** da coluna do ponto de vista (x=715..925). Se a tela ficasse
larga o bastante para mostrar o ponto de vista, `test_viewpoint_node_is_rasterized_after_highlight`
perderia o sentido — ele existe para verificar que `highlight` traz para a vista um nó que estava
**fora** dela. Aumentar a tela "para não falhar" teria desmontado o teste em silêncio.

Medido depois da correção: **12 passados em 5 execuções seguidas** do arquivo sozinho e
**206 passados em 3 execuções seguidas** do conjunto de sete arquivos (antes: 3 falhas em 5 de 5).

**A lição que este caso deixa:** um teste verde em série não é um teste robusto. O flake de pixel
foi corrigido e ficou 30/30 verde — e ainda assim o arquivo quebrou depois, porque o verde media
uma **condição de ambiente** (espaço de trabalho vazio) que ninguém tinha declarado como
pré-requisito. Ao estabilizar um teste que depende do compositor, o que se deve exigir é a
**condição** de que as asserções precisam (aqui: uma tela que caiba o `_layout`), não apenas
esperar que ela apareça.

Os dois runners continuam sendo necessários: o `unittest` não vê 13 arquivos, e o
pytest sozinho não protege contra OOM. Ver `docs/ESTABILIDADE_TESTES.md`.
