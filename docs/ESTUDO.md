# Uso do Zorin Copilot como ferramenta de estudo (Gestão Comercial — SENAC EAD)

Este documento registra o plano de adaptação do Zorin Copilot para a rotina de estudos
e o estado de cada etapa. Ele complementa o [AGENT_MODE.md](./AGENT_MODE.md), que descreve
o modo agente (a base técnica usada aqui).

## 1. O problema real

O AVA da SENAC entrega a aula como um **shell de SPA dentro de iframe/SCORM**: o HTML
que o navegador baixa contém apenas o esqueleto da página (neste teste: 24 palavras);
o conteúdo é montado por JavaScript depois do load e, em boa parte, mora em outro
documento (o iframe).

Consequência: extrair por HTTP — que é o que `WebPageReader.fetch_and_clean` faz —
devolve o shell. Foi exatamente o sintoma observado: material ingerido com 7 palavras.

```
HTML baixado  →  24 palavras  (shell)
O que o aluno vê na tela  →  centenas de palavras (conteúdo real)
```

## 2. A solução: capturar da tela, não da rede

`core/study_capture.py` lê o conteúdo **renderizado** pela árvore de acessibilidade
(AT-SPI2) — a mesma árvore que um leitor de tela usaria. Isso:

- atravessa iframe e player SCORM, porque o que importa é o que foi desenhado;
- não depende de login/sessão (o navegador já está autenticado);
- é leitura passiva — não envia nada, não clica em nada, não automatiza o AVA.

### Uso

```bash
# 1. Abra a aula no navegador, deixe a janela visível e em foco.
# 2. Capture:
zorin-copilot-cli study capture

# Variações úteis
zorin-copilot-cli study capture --app Firefox          # força o aplicativo
zorin-copilot-cli study capture --strategy clipboard   # usa Ctrl+A/Ctrl+C feito por você
zorin-copilot-cli study capture --min-words 200        # exige mais conteúdo antes de avisar
zorin-copilot-cli study capture --out ~/Documentos/Estudos/aula03.md
zorin-copilot-cli study capture --print --no-save      # só espiar
```

Saída: `~/Documentos/Estudos/<titulo>.md` com metadados no topo (origem, estratégia,
data, contagem de palavras, URL quando detectada). Arquivos existentes nunca são
sobrescritos — recebem sufixo `-2`, `-3`.

### Estratégias

| Estratégia | Quando | Como funciona |
|---|---|---|
| `atspi` (padrão) | Player expõe acessibilidade | Caminha a árvore (18 níveis, 80 filhos/nó), coleta texto de `paragraph`, `heading`, `list_item`, `link`, `table_cell`… e **ignora** menu, barra de ferramentas, botões, campo de endereço e barra de status |
| `clipboard` | Player não expõe AT-SPI | Lê o que você copiou com Ctrl+A, Ctrl+C |
| `ocr` | Nem AT-SPI nem clipboard resolvem | Varre a tela por OCR espacial (Tesseract) e reconstrói as linhas em ordem de leitura. Exige `tesseract` com o pacote `por`; sem eles avisa, não quebra |
| `auto` | padrão | Tenta AT-SPI; se vier ralo (menos de 120 palavras), tenta a área de transferência; se ainda estiver ralo, tenta OCR. Fica com o que tiver mais conteúdo |

O `auto` é intencionalmente preguiçoso com o OCR: ele lê pixels, então é mais lento e
mais sujo que a árvore de acessibilidade. Só entra quando as duas primeiras falham.

Há também um **modo grosso** automático: se a caminhada granular não achar texto
(player que só expõe o conteúdo no nó do documento), o extrator volta e coleta dali.

### Organização por disciplina

```bash
zorin-copilot-cli study capture --discipline "Contabilidade Gerencial"
```

O material vai para `~/Documentos/Estudos/contabilidade-gerencial/` e o cabeçalho do
Markdown ganha `- Disciplina: ...`. Na hora de gerar o baralho, a disciplina é herdada
do arquivo — o prompt já fala da matéria certa sem você redigitar. Dá para trocar com
`study deck --discipline Marketing` e filtrar com `study decks --discipline Marketing`.

### O agente também sabe capturar

O modo agente tem a ferramenta `capture_lesson`, que faz exatamente isso: lê a tela em
vez de baixar o HTML. É a resposta para o AVA — `read_web_page` (e o
`WebPageReader`) baixam a URL de novo e recebem o shell de 24 palavras da SPA, enquanto
a tela tem o conteúdo renderizado.

### Fechando o ciclo

```bash
zorin-copilot-cli rag index                       # indexa os .md capturados
zorin-copilot-cli rag ask "o que é custeio variável?"
```

## 3. Estudo ativo: flashcards e revisão espaçada

`core/study_cards.py` fecha o ciclo material → pergunta → revisão.

```bash
# Gerar o baralho a partir do material capturado
zorin-copilot-cli study deck --from ~/Documentos/Estudos/aula03.md --max-cards 10

# Ver os baralhos e o que está vencido
zorin-copilot-cli study decks

# Revisar (sessão interativa no terminal, escala 0–5)
zorin-copilot-cli study review --deck c99fe2c7e7
```

### Resumo e glossário (B4)

Mesmo material dos flashcards, outra saída — serve para reler rápido antes da prova:

```bash
zorin-copilot-cli study summarize --from ~/Documentos/Estudos/contabilidade-gerencial/aula03.md
zorin-copilot-cli study summarize --from aula03.md --max-topics 5 --max-terms 8 --json
```

Gera `resumo-aula03.md` ao lado do material (ou em `--out`). O filtro é o que faz o
resultado valer: **rótulo de seção** ("Introdução", "Conceitos gerais") e
**metacomentário** ("o texto aborda...") são descartados, porque não dizem o fato; e
definição circular do tipo "custeio é custeio" também cai.

### Por que o prompt é o que é

O gerador usa o **prompt v2**, endurecido a partir de uso real (no study-hub ele gerou
o card "qual é o código do curso de Ambientação EAD?"). Regras que importam:

- proíbe card de dado burocrático/navegação (código do curso, carga horária, nome de
  professor, prazos, menus, sumário);
- exige que o card **valha o tempo de revisão** — resposta que é só número, código ou
  nome próprio isolado é descartada;
- autoriza `{"cards": []}` quando o material não tem conteúdo de estudo: devolver vazio
  é melhor que inventar card trivial;
- gate de entrada: material com menos de 80 palavras não gera nada (avisa em vez de alucinar).

O prompt pede; o código garante: `BUREAUCRATIC_PATTERNS` + filtro de utilidade
descartam o que o modelo local insistir em produzir.

### SM-2 (agendamento)

| Regra | Comportamento |
|---|---|
| Nota ≥ 3 | 1º acerto → +1 dia; 2º → +6 dias; depois `intervalo × facilidade` |
| Nota < 3 | zera repetições e reagenda para amanhã |
| Facilidade | começa em 2.5, piso 1.3 |

Determinístico: **a data da próxima revisão nunca passa pelo modelo** — é a regra que
impede o LLM de "achar" que você já sabe. O **id do card deriva da frente**, então
regenerar o deck com o mesmo material preserva o histórico de revisão. O progresso é
salvo a cada card (Ctrl+C não perde a sessão).

### Entrega em ABNT e busca acadêmica

Os dois já existiam no repo, mas só eram alcançáveis por voz. Agora:

```bash
zorin-copilot-cli study abnt --from trabalho.md --out trabalho.docx   # ABNT NBR 14724/10520/6023
zorin-copilot-cli search "mercado varejo" --academic --source sebrae  # SciELO/IBGE/Sebrae/IPEA/Scholar
```

### Modo tutor: perguntar sem copiar (`core/study_tutor.py`)

A resposta direta é o pior produto para quem quer aprender — ela pula o esforço
que gera aprendizado. O modo tutor troca o contrato do modelo em **duas
superfícies** com a mesma filosofia socrática (valida o raciocínio, faz
perguntas-guia, dá dica/primeiro passo, termina com pergunta ou exercício,
**nunca** entrega resposta final, redação pronta ou cálculo completo):

**1. Área separada de estudos** — `study ask`, com contexto do seu material:

```bash
zorin-copilot-cli study ask "como calculo o ponto de equilíbrio?" --discipline "Contabilidade Gerencial"
zorin-copilot-cli study ask "o que é markup?" --no-context --local-only
```

O contexto **não usa RAG**: pontuação determinística por sobreposição de
palavras-chave escolhe os parágrafos do material capturado que conversam com a
pergunta (a pasta da disciplina ganha o desempate). Se nada no material cobrir
o tema, o tutor avisa — e o prompt o proíbe de fingir que leu.

**2. Injeção no chat/HUD inteiro** — um parágrafo no prompt de sistema:

```bash
zorin-copilot-cli config --tutor on    # liga
zorin-copilot-cli config --tutor off   # desliga
zorin-copilot-cli config --show        # confere o estado
```

Com o tutor ligado, qualquer pergunta acadêmica no chat vira conversa de
professor; pedidos operacionais (abrir app, organizar arquivos, gerar o .docx
do trabalho que **você** escreveu) continuam sendo executados normalmente — a
regra vale para conteúdo, não para operações de desktop. A injeção acontece em
`build_system_prompt()`, então pega todos os caminhos de chat de uma vez.

## 4. Estado do plano

| Fase | Item | Estado |
|---|---|---|
| **A** | A1 — Captura de material (AT-SPI + clipboard + OCR + gate de qualidade) | ✅ |
| A | A2 — Organização automática por disciplina/aula | ✅ |
| **B** | B1 — Geração de flashcards (prompt v2 + filtro antiburocrático) | ✅ |
| B | B2 — Revisão espaçada SM-2 no terminal | ✅ |
| B | B3 — Simulados a partir do material capturado | ⏳ |
| B | B4 — Resumo + glossário por aula | ✅ |
| B | B5 — Modo tutor socrático (`study ask` + injeção no HUD) | ✅ |
| **C** | C1 — Entrega do PI em .docx ABNT | ✅ (gerador existia; agora na CLI) |
| C | C2 — Revisão por voz (Whisper + Piper, já no repo) | ⏳ |
| C | C3 — Rotina semanal automatizada | ⏳ |

## 5. O que foi validado — e o que não foi

**Validado (26 testes em `tests/test_study_capture.py`, headless):**

- extração de parágrafos/heading/lista com descarte do chrome do navegador
  (menu, barra de ferramentas, campo de endereço, barra de status);
- ordem do conteúdo preservada;
- nó que já entregou texto não é percorrido de novo (sem parágrafo duplicado);
- modo grosso quando só o nó do documento expõe texto;
- limpeza: vazios, ruído, números soltos, CSS vazado e repetições;
- queda automática para a área de transferência quando o AT-SPI vem ralo;
- aviso de captura rala com a dica acionável (Ctrl+A/Ctrl+C);
- gravação em Markdown sem sobrescrever;
- falha graciosa (inspetor indisponível, AT-SPI fora do ar).

**Não validado neste ambiente:** o sandbox não expõe aplicativos ao AT-SPI2
(nem janelas GTK nem o Chromium aparecem no barramento), então a captura
*ponta a ponta contra um navegador real* só pode ser confirmada na máquina do
usuário. A lógica de caminhada e limpeza está coberta por testes com dublês que
imitam a API do pyatspi.

**Teste de 30 segundos na sua máquina:**

```bash
# com a aula aberta e visível
zorin-copilot-cli study capture --print --no-save
```

- Se aparecer o texto da aula → funcionou, rode sem `--no-save` para gravar.
- Se aparecer "captura rala" → selecione o texto com Ctrl+A, Ctrl+C e repita com
  `--strategy clipboard`.
- Se aparecer "Não foi possível inspecionar" → rode `zorin-copilot-cli doctor`:
  o problema é o barramento de acessibilidade, não a captura.
