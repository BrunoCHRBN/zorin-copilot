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
| `auto` | padrão | Tenta AT-SPI; se vier ralo (menos de 120 palavras), tenta a área de transferência e fica com o que tiver mais conteúdo |

Há também um **modo grosso** automático: se a caminhada granular não achar texto
(player que só expõe o conteúdo no nó do documento), o extrator volta e coleta dali.

### Fechando o ciclo

```bash
zorin-copilot-cli rag index                       # indexa os .md capturados
zorin-copilot-cli rag ask "o que é custeio variável?"
```

## 3. Estado do plano

| Fase | Item | Estado |
|---|---|---|
| **A** | **A1 — Captura de material (AT-SPI + clipboard + gate de qualidade)** | ✅ Fase 1 entregue |
| A | A2 — Organização automática por disciplina/aula | ⏳ próximo |
| **B** | B1 — Geração de flashcards (portando o prompt v2 do study-hub) | ⏳ |
| B | B2 — Revisão espaçada SM-2 (portando `sm2.py` do study-hub) | ⏳ |
| B | B3 — Simulados a partir do material capturado | ⏳ |
| B | B4 — Resumo + glossário por aula | ⏳ |
| **C** | C1 — Entrega do PI em .docx/.pptx (`document_generators`) | ⏳ |
| C | C2 — Revisão por voz (Whisper + Piper, já no repo) | ⏳ |
| C | C3 — Rotina semanal automatizada | ⏳ |

## 4. O que foi validado — e o que não foi

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
