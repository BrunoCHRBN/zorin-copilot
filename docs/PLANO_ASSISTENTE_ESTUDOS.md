# Plano — Assistente de Estudos 2.0 (Voz · Vídeo ao vivo · Chat · Quadro Negro)

> Documento de planejamento. Baseia-se no que já existe no repositório:
> - Modo estudo: `docs/ESTUDO.md` (captura AT-SPI, flashcards SM-2, tutor socrático B5, ABNT, busca acadêmica).
> - Voz/vídeo ao vivo: `ai/live.py` (`GeminiLiveClient`), `ui/live_view.py`, fusão vídeo+AT-SPI (Fase 2: `core/a11y.py` `bbox` + `ai/live.py` `locate_element(x,y)` + `DesktopInspector.element_at_point`).
> - UI: GTK4/Libadwaita glassmorphism (`ui/app.py`, `ui/widgets/*`, `ui/markdown.py`).
> - Rigor de testes determinísticos: suíte ~520+ verde, dublês headless para AT-SPI e Gemini Live.

---

## 1. Visão e três princípios

O objetivo é transformar o Copilot de "respondedor" em **espaço de trabalho de estudo** onde as três modalidades (chat, voz ao vivo, vídeo ao vivo) alimentam o **mesmo estado** e onde a matemática deixa de ser texto solto para virar **dado estruturado** que o "quadro negro" exibe e expande.

Princípios:

1. **Estado compartilhado entre modalidades.** Voz, vídeo e chat operam sobre uma única `StudySession` (problema atual + quadro negro + disciplina + modo de rigor). O aluno pode começar no chat, continuar na voz e apontar a câmera pro caderno — sem perder contexto.
2. **Raciocínio é dado estruturado, não prosa.** A matemática sai do modelo como `SolutionGraph` (grafo de passos, métodos, pontos de vista, alternativas, verificação) e tem *renderizadores* distintos por modalidade.
3. **Rigor configurável.** Um `rigor_mode` (`"BR"` padrão / `"CN"` rigoroso) liga/desliga as exigências do padrão de educação chinês (formato 已知/求/解/答, 一题多解, 数形结合, 举一反三, 订正).

---

## 2. Camada de estado compartilhado — `StudySession`

Novo módulo `core/study_session.py` (puro, testável sem display — mesma filosofia de `core/export.py`/`core/attachments.py`):

```python
class StudySession:
    problem: Problem          # enunciado parseado (dados/incógnitas)
    blackboard: SolutionGraph # grafo produzido pelo math_solver
    modality: str             # "chat" | "voice" | "video"
    discipline: str
    rigor_mode: str           # "BR" | "CN"
    # pub/sub simples (callbacks) para UI e live se inscreverem
    def subscribe(self, cb): ...
    def publish(self, event): ...
```

Eventos publicados: `problem_set`, `step_highlighted`, `method_selected`, `blackboard_expanded`, `verification_done`. Chat, `live_view`, blackboard e o loop de voz se inscrevem e reagem (ex.: ao narrar um passo, a voz publica `step_highlighted` e o quadro destaca o nó).

**Por que:** hoje voz e chat são fluxos separados (`app.py` × `ai/live.py`). A `StudySession` é o "cérebro" único que fecha a torneira de duplicação que o `UI_ANALYSIS.md` já havia alertado.

---

## 3. Modalidades

### 3.1 Chat (`ui/widgets/chat_stream.py` + `ui/markdown.py`)
- Novo `ui/widgets/math_card.py`: card de resposta matemática com **passos recolhíveis**, **abas de método** (Método A / Método B), **toggle de ponto de vista** (algébrico ⇄ geométrico) e botão **"expandir no quadro negro"**.
- Reaproveita o renderer markdown existente; adiciona *slots* estruturados (não mais só prosa).
- Integra o tutor socrático (B5) à matemática: em vez de entregar a resposta, o card revela o passo *n* apenas após o aluno tentar (modo opcional "tutor").

### 3.2 Voz ao vivo (`ai/live.py`)
- `GeminiLiveClient` já unificado com `ToolRegistry` (ver `docs/LIVE_VOICE_TOOL_CALLING.md`). Adiciona a ferramenta `math_step` / comandos de quadro:
  - `"me mostre o passo da fatoração"` → publica `step_highlighted` no quadro.
  - `"explique o Método B"` → troca `method_selected`.
- Serialização TTS amigável (`to_voice_script()`): narra passo a passo com pausas e "ou, se preferir, pelo Método B…".
- Fecha o item **C2** do `ESTUDO.md` (revisão por voz com Whisper+Piper, já no repo).

### 3.3 Vídeo ao vivo (`ai/live.py` + `core/a11y.py`)
- A fusão Fase 2 hoje olha a **tela do desktop** (`locate_element(x,y)`). Para estudos, adicionar `core/vision_camera.py`: captura de **webcam** via PipeWire/portal e envia frames ao Gemini Live (modo vídeo) — habilita **"mostre seu raciocínio"**: o aluno aponta o caderno/tablet, o assistente lê a escrita à mão e reage.
- O assistente pode devolver o quadro negro ao aluno como PiP de vídeo (compartilhar superfície do `blackboard`).
- `locate_element` permite que a voz diga "olhe este termo" e o quadro destaque a região correspondente na imagem do aluno.

---

## 4. Motor de matemática — `ai/math_solver.py`

Entrada: problema em linguagem natural (ou LaTeX/foto). Saída: `SolutionGraph` **estruturado** (tool-call JSON-Schema estrito, igual à abordagem `Action Schema` do `ARCHITECTURE.md` — determinístico, sem texto solto).

```python
@dataclass
class SolutionGraph:
    problem: Problem                 # givens / unknowns (已知/求)
    methods: list[Method]            # >=2 em modo CN
    viewpoints: list[Viewpoint]      # algébrico, geométrico, numérico, intuitivo
    alternatives: list[Alternative]  # atalhos, estimativa, análogos (举一反三)
    verification: Verification       # substituição, unidades, sanity checks
    scoring: Optional[StepScoring]   # pontuação por passo (estilo 高考)

@dataclass
class Method:
    id: str; name: str               # "Fatoração", "Fórmula de Bhaskara"
    steps: list[Step]                # cada passo com justificativa
    pros: str; suitable_for: str
```

- **Backend de geração:** modelo capaz (Gemini/Claude/DeepSeek-Math) via tool-call estruturado.
- **Verificação simbólica:** passada opcional com **SymPy** para checar passos algebraicamente (evita alucinação de conta).
- **Adaptadores de render:** `to_chat_md()`, `to_voice_script()`, `to_blackboard_graph()` — um só dado, três saídas.

---

## 5. Quadro Negro Digital — `ui/widgets/blackboard.py`

Superfície GTK4 dedicada (nova aba/painel no `app.py`, não mexe no chat atual):

- **Canvas:** `Gtk.ScrolledWindow` + `Gtk.DrawingArea` (Cairo/Snapshot) ou `Gtk.Picture` com SVG. Renderiza o `SolutionGraph` como **grafo node-link**:
  - Raiz = enunciado.
  - Uma **coluna/ramo por método**.
  - Nós = passos; arestas = derivação.
  - Marcadores de ponto de vista (ícones 代数/几何).
- **Interações (a parte de "expandir para melhor visualização"):**
  - Pan/zoom (arrastar + scroll), "ajustar à tela".
  - Clicar num nó → painel lateral com o raciocínio completo e a fórmula tipografada.
  - Recolher/expandir ramos de método pela toolbar.
  - **Highlight reativo:** quando voz/vídeo publica `step_highlighted`, o nó acende.
- **Tipografia matemática — `core/math_render.py`** (abstração com 2 backends):
  - *Backend A (padrão, sem WebView):* LaTeX → **SVG** via `matplotlib.mathtext` (ou `sympy.preview`/`latex2svg`), cache em `~/.cache/zorin-copilot/math/`. Mantém o app nativo (o `UI_ANALYSIS.md` rejeitou WebView no chat por quebrar o glassmorphism).
  - *Backend B (opt-in):* WebKitGTK + KaTeX para interatividade total — documentado como opcional, fora do `.deb` base.
- **Anotação à mão:** aluno desenha no quadro com ponteiro (`Gtk.GestureDrag` → traços Cairo) ou escreve num input anexo; o assistente lê via visão e responde.
- **Exportar:** PNG/SVG/PDF do quadro e "salvar como nota de estudo" (liga com a organização por disciplina do `ESTUDO.md`).

---

## 6. Modo rigoroso — critérios do sistema de educação chinês

> **Definição de "rigor chinês" (critério do usuário).** Sempre que este plano
> menciona "rigor chinês", refere-se ao **critério de qualidade com que os chineses
> avaliam soluções voltadas à educação**, abrangendo: (1) **qualidade da
> implementação**, (2) **utilidade prática**, e (3) **a forma como o recurso é
> desenvolvido para fins de estudo**. Este é o padrão de referência tanto para
> *julgar* quanto para *produzir* o plano de implementação — não apenas um rótulo
> de estilo.

Os itens abaixo são a tradução operacional desse critério para o nosso domínio:
cada exigência do padrão educacional chinês (新课标 / 高考) é lida como uma
**diretriz de qualidade** que protege implementação, utilidade e propósito de estudo.

| Critério CN | O que o modo `rigor_mode="CN"` impõe | Onde no plano |
|---|---|---|
| **规范格式** (已知/求/解/答 — Dados/Incógnita/Resolução/Resposta) | Cabeçalho estruturado obrigatório no `SolutionGraph.problem` e na resposta | `math_solver` + `math_card` |
| **一题多解** (várias soluções p/ 1 problema) | Exige **≥2 métodos**; UI mostra comparação lado a lado | `SolutionGraph.methods` + abas |
| **数形结合** (número + forma) | Exige **ponto de vista geométrico** quando o problema admitir | `viewpoints` |
| **举一反三** (analogia/variação) | Gera 2–3 variantes análogas (muda números/contexto) | `alternatives` |
| **过程性评价** (avaliação de processo) | Cada passo carrega justificativa; **pontuação por passo** (步骤分) | `scoring` |
| **订正 / 错题本** (correção / caderno de erros) | Diagnóstico de passo errado integrado à revisão espaçada **SM-2** (estende `study_cards`) | `verification` + SM-2 |
| **严谨** (rigor) | Unidades, restrição de domínio e **verificação obrigatórias**; sem "aprox." sem bound | `verification` |

**Como ficaria na prática (modo CN ligado):** o aluno pergunta "resolva x²−5x+6=0". O assistente devolve:
1. 已知/求/解/答 — enunciado estruturado (dados, incógnita, resolução, resposta).
2. **Dois métodos** (Fatoração e Fórmula de Bhaskara), cada um com passos justificados.
3. **Ponto de vista geométrico** (raízes = interceptos da parábola no eixo x).
4. **Verificação** por substituição + checagem de domínio.
5. **Pontuação por passo** estilo 高考 ("2 pt fatoração, 1 pt raízes…").
6. Se o aluno errar um passo, vai para o **错题本** e reaparece na revisão espaçada.

O `rigor_mode` é por disciplina e configurável (`zorin-copilot-cli config --rigor cn`).

---

## 7. Roteiro (fases)

| Fase | Entrega | Depende de | Estado |
|---|---|---|---|
| **A** | `core/study_session.py` + `ai/math_solver.py` (SolutionGraph, tool-call, SymPy check) | — | ⏳ |
| **B** | `ui/widgets/math_card.py` (chat) + `to_voice_script()` | A | ⏳ |
| **C** | `ui/widgets/blackboard.py` + `core/math_render.py` (SVG) | A | ⏳ |
| **D** | Voz ao vivo narrando o quadro + highlight (`step_highlighted`) | A, B, C | ⏳ |
| **E** | Vídeo ao vivo webcam "mostre seu trabalho" + PiP do quadro | A, C | ⏳ |
| **F** | Modo `CN`: formato, ≥2 métodos, ponto de vista, 举一反三, 错题本+SM-2 | A, B | ⏳ |
| **G** | Integração com estudo existente (tutor B5, simulados B3, voz-revisão C2) | A–F | ⏳ |

---

## 8. Riscos e decisões de engenharia

- **Tipografia sem WebView:** backend SVG via `matplotlib.mathtext` (~10 MB, comum em distros) mantém o app nativo. WebView+KaTeX fica opt-in.
- **Latência voz/vídeo:** comandos de quadro são pequenos (ids de nó), não reenviam o grafo.
- **Correção matemática:** SymPy como segunda opinião simbólica reduz alucinação de conta.
- **Empacotamento (.deb):** manter deps mínimas; Simpy/matplotlib já amplamente presentes.
- **Testes determinísticos (herança do repo):** validar schema do `SolutionGraph`, saída SVG do `math_render`, expansão de nós do blackboard e pub/sub da `StudySession` com clientes dublê (sem display/sem rede).

---

## 9. Critérios de aceitação (sucesso)

1. Um problema resolvido produz `SolutionGraph` válido (schema) com ≥1 método e verificação.
2. O mesmo `SolutionGraph` renderiza em chat (math_card), voz (script) e quadro (grafo) sem reexecutar o modelo.
3. No modo `CN`, a resposta **falha graceful** se faltar formato/segundo método/verificação.
4. Trocar de modalidade no meio da explicação preserva problema + quadro (pub/sub).
5. Blackboard expande/recorta nós e destaca passo pedido por voz.
6. Suíte determinística verde para os novos módulos (mesmo padrão de ~520 testes atuais).
