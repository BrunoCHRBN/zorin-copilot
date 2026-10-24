# PROPOSTA — Pill de anotação por voz dentro do Edge

**Cenário alvo.** Estudando um PDF, grifando com a caneta pela mesa digitalizadora. Ao escolher
"Adicionar texto" e clicar com a caneta, uma pill aparece, escuta, e o que foi dito entra no campo
de anotação do Edge.

**Status deste documento.** Escrito sobre medição da máquina (30/09/2026) e leitura do código do
repo. Tudo que não foi medido está marcado como **NÃO MEDIDO** — e há um protocolo de medição de
~20 min na seção 7, porque três das decisões dependem dele.

---

## 1. Diagnóstico: a parte difícil não é a que parece

O repo **já tem** quase todo o encanamento deste cenário. Medido por leitura:

| peça | onde | o que já resolve |
|---|---|---|
| `DictationService` | `core/dictation.py` | `start/stop/toggle/cancel`, `_inject_text`, fallback para clipboard, hook `custom_text_injector` |
| `VirtualInputDriver` | `shell/input_driver.py` | `type_text`, `hotkey`, `click` via wtype/ydotool; `ScreenFenceManager` para cliques |
| `VoicePillWindow` | `ui/voice_pill.py` (2139 linhas) | a pill em si: layer-shell, snap ao monitor sob o cursor, auto-hide ocioso, atalhos |
| `DictationOSDWindow` | `ui/dictation_osd.py` | o OSD de ditado |
| `DesktopInspector` | `core/a11y.py` | `get_active_window_info`, `get_focused_app`, `element_at_point`, `text_insert`, `focus_element` |
| `app.toggle-dictation` | `ui/app.py` | atalho global "Ditado por Voz Global — Digitar fala no aplicativo em foco" |

E o precedente mais importante: **`core/study_capture.py` já lê a árvore AT-SPI renderizada** —
a decisão está documentada no cabeçalho do módulo, e o motivo é o mesmo que aparece aqui: o que
o aluno vê está no AT-SPI, não no HTML baixado.

**Conclusão do diagnóstico: não falta transcrição, não falta injeção, e não falta pill.**
Faltam exatamente duas coisas:

1. **gatilho por clique de caneta** (hoje só existe atalho de teclado — e não existe nenhum
   listener global de input no código: `grep evdev|libinput` em `src/` só casa com
   `shell/input_driver.py`, que é *emissão*, não *escuta*);
2. **guarda de alvo** — saber que o texto vai cair no campo certo, e não na barra de endereços.

O resto é integração.

---

## 2. A restrição que define a arquitetura inteira

O visualizador de PDF do Edge **não é DOM de página**. Ele é um componente interno do navegador
(plugin PDFium servido por uma extensão interna). Consequências, em ordem de dureza:

- **`content script` não alcança o visualizador.** Extensões não podem ser injetadas em páginas
  internas do navegador; o DOM da camada de anotação é inacessível por projeto.
- **Não existe API pública de anotação de PDF** no Edge/Chromium. Nada de "inserir texto na
  anotação X".
- O único gancho *sancionado* para controlar a superfície de PDF é o experimental
  `mime_types_handler`, que **substitui o visualizador embutido inteiro**. Ou seja: a rota
  oficial para controlar o PDF é escrever o seu próprio visualizador — o que já é o Plano B.

**Portanto:** a integração não pode ser "falar com o Edge". Tem de ser **entrar pelo teclado do
sistema e confirmar o alvo por fora** (AT-SPI). Isso não é uma limitação da nossa implementação —
é a forma da plataforma, e ela não vai mudar.

---

## 3. Arquitetura proposta

Cinco camadas, das quais três já existem.

```
[1] GATILHO            caneta (botão) → bind do Hyprland / OpenTabletDriver
                       └─ ou: atalho global, tecla expressa da mesa
                                  │
                                  ▼
[2] PILL               VoicePillWindow  (JÁ EXISTE)
                       posiciona-se sob o cursor, mostra estado e nível
                                  │
                                  ▼
[3] CAPTURA+TRANSCRIÇÃO  PipeWire → VAD → whisper-cli  (JÁ EXISTE)
                       └─ whisper-server AUSENTE nesta máquina → custo fixo ~1,7 s por chamada
                                  │
                                  ▼
[4] GUARDA DE ALVO     DesktopInspector (AT-SPI)  ← FALTA
                       "o elemento focado é editável? é a janela certa?"
                                  │
                                  ▼
[5] INJEÇÃO            VirtualInputDriver.type_text  (JÁ EXISTE)
                       + fallback clipboard (wl-copy + Ctrl+V)   ← FALTA o modo clipboard
                                  │
                                  ▼
                       campo de anotação do Edge
```

**A regra que amarra a arquitetura: a pill nunca digita sem confirmar o alvo.**
Se a camada 4 não confirmar, a camada 5 **não digita nada** — apenas copia para o clipboard e
avisa na pill. Digitar às cegas no app em foco é o defeito que este desenho existe para impedir:
o texto iria para a barra de endereços, para a busca, ou para outro aplicativo.

### Por que não inverter (gatilho = detectar a caixa de anotação)

Porque o gatilho por detecção depende de o Edge publicar a caixa de anotação na árvore de
acessibilidade — **NÃO MEDIDO**, e a medição exige o Edge instalado. O gatilho por botão da caneta
é determinístico e não depende de nada disso. Detecção entra depois, como conveniência, se a
medição autorizar.

---

## 4. Como acionar a pill por clique

Três caminhos. Recomendo **A** como gatilho primário e **B** como refinamento opcional.

### A. Botão da caneta / tecla da mesa → bind do Hyprland  *(recomendado)*

É o mais fiel a "acionada por um clique" e o mais determinístico: não depende de adivinhação.

O ambiente já usa esse mecanismo — medido em `~/.config/hypr/hyprland.conf`:

```
bind = $mainMod, mouse:275, workspace, e-1
bind = $mainMod, mouse:276, workspace, e+1
bind = $mainMod, mouse:274, killactive
```

Um gatilho novo entra no mesmo formato, sem `$mainMod`, para ser um clique só:

```
bind = , mouse:NNN, exec, <caminho do projeto>/bin/zorin-copilot dictation toggle
```

Dois caminhos para descobrir `NNN`:

- **Botão lateral da caneta** (BTN_STYLUS = 331, BTN_STYLUS2 = 332). Se o Hyprland entregar esse
  botão como bind, é o melhor gatilho possível: a mão já está na caneta. **NÃO MEDIDO** — é a
  primeira coisa a medir (seção 7).
- **Teclas expressas da mesa**, se houver. Elas já chegam como teclado, então o bind é trivial e
  funciona hoje. Se a sua mesa tem botões, **comece por aqui** — zero incerteza.

Se o Hyprland não entregar os botões da caneta, o fallback é o **OpenTabletDriver**
(`otd` **AUSENTE** nesta máquina), que mapeia botão da caneta para tecla ou comando no nível do
dispositivo — funcionando independentemente do compositor.

### B. Detecção do campo focado por AT-SPI  *(opcional, depois da medição)*

A pill aparece sozinha quando o elemento focado é editável e pertence à janela do Edge.

O precedente existe: `study_capture.py` já caminha a árvore AT-SPI. E eu **medi** que o mecanismo
funciona para o Chromium — ver seção 6. Mas há um falso positivo conhecido e medido: a própria
barra de endereços aparece como `[entry] editable=True`. Um gatilho ingênuo de "é editável?"
dispara na barra de endereços.

### C. Não usar

Emitir o clique programaticamente (`ui_grounding.py` já sabe clicar com fence de segurança) não
serve: o clique tem de ser **seu**, para escolher onde o bloco de texto vai nascer na página.

---

## 5. Injeção: os três modos, e quando usar cada um

| modo | como | quando | risco |
|---|---|---|---|
| **teclado** | `VirtualInputDriver.type_text` (wtype/ydotool) | padrão, quando a guarda de alvo confirma | alvo errado se a guarda falhar |
| **AT-SPI** | `DesktopInspector.text_insert(elemento, texto)` | se a caixa do Edge aparecer na árvore | **NÃO MEDIDO** para o Edge |
| **clipboard** | `wl-copy` + `Ctrl+V` sintético | sempre como fallback; e é o mais robusto para acento | exige foco correto também |

**Sobre acentos.** `wtype` emite pelo layout xkb corrente; "não", "anotação", "página" dependem do
layout ativo. O caminho clipboard é imune a isso e por isso deve ser o fallback **padrão**, não
uma exceção. Custo: um `Ctrl+V` sintético a mais.

**Sobre injeção incremental.** O ditado produz segmentos ao longo do tempo. O campo de anotação do
Edge provavelmente re-layouta conforme o texto cresce — e pode commitar/fechar. Então:
**injetar por segmento e re-verificar o foco entre segmentos**, não acumular tudo para o fim.
Se o foco se perdeu no meio, parar e avisar em vez de continuar digitando no lugar errado.

---

## 6. O que eu medi nesta máquina (30/09/2026)

Ambiente:

- Sessão **Wayland / Hyprland 0.56.2**, `WAYLAND_DISPLAY=wayland-1`.
- `wtype` ✅ `/usr/bin/wtype` · `ydotool` ✅ `/usr/bin/ydotool` · `wl-copy` ✅ (wl-clipboard 2.3.0).
- `xdotool` **AUSENTE** (esperado — é X11). `whisper-cli` ✅ · `whisper-server` **AUSENTE**.
- `/dev/uinput` existe, grupo `input`, e o usuário **está** no grupo `input` → `ydotool` viável
  sem root. Relevante porque **`wtype` é somente teclado**: clique exige ydotool (o próprio
  `input_driver.py` já explica isso ao usuário).
- **Edge NÃO está instalado** — nem binário, nem flatpak. Nada sobre o comportamento do Edge foi
  medido aqui.
- Nenhum tablet/caneta visível em `/sys/class/input` no momento da medição.

**Experimento de AT-SPI (o que importa).** Abri um Chromium com perfil isolado em `/tmp` (sem
tocar a sessão do usuário) servindo um `contenteditable` com `aria-label="campo-anotacao"`, e
despejei a árvore:

- **Sem `--force-renderer-accessibility`:** a árvore vem **vazia** — 3 frames, nenhum conteúdo.
  As instâncias de Chromium que já estavam rodando também apareceram vazias.
- **Com `--force-renderer-accessibility`:** a árvore aparece inteira, e o campo editável **está lá**:

```
[document web] 'ATSPI PROBE' editable=False
  [section] 'campo-anotacao' editable=True      ← o contenteditable, aria-label virou o nome
    [static] 'clique aqui e digite' editable=True
[entry] 'Barra de endereço e de pesquisa' editable=True   ← falso positivo a tratar
```

Duas conclusões, e uma armadilha evitada:

1. **O Chromium publica campos editáveis no AT-SPI, com o nome acessível — mas só com a
   acessibilidade forçada.** Sem a flag não há árvore para ler. Isso vira requisito de implantação.
2. **O primeiro resultado do experimento foi um nulo** ("nenhum nó editável encontrado") — e o
   nulo era **artefato da montagem**, não do fenômeno. Só apareceu como artefato porque rodei o
   controle positivo com a flag. Sem esse controle, a conclusão teria saído invertida: "AT-SPI não
   serve para o Chromium", que é falso.
3. A barra de endereços é `editable=True` — a guarda de alvo **não pode** ser só "é editável?".

**O que segue NÃO MEDIDO** (e não pode ser, sem o Edge): se a caixa de anotação do PDF aparece na
árvore; se o `text_insert` do AT-SPI escreve nela; se o botão da caneta chega ao Hyprland; se o
`wtype` digita acentos corretamente ali.

---

## 7. Protocolo de medição (~20 min, antes de escrever código)

Faça nesta ordem — cada passo decide um ramo do desenho.

**7.1 — O Edge tem "Adicionar texto" no Linux?**
Instale o Edge e confirme a ferramenta na barra do PDF. Se não existir, o cenário inteiro muda e o
Plano B (seção 9) passa a ser o caminho principal. *(A página da Microsoft descreve a configuração
de leitor padrão só para Windows e macOS — indício de que o Linux é cidadão de segunda classe, mas
indício não é medição.)*

**7.2 — A caixa de anotação aparece no AT-SPI?**
```bash
microsoft-edge --force-renderer-accessibility   # abra um PDF, use "Adicionar texto", clique na página
# então despeje a árvore procurando o nó focado e editável:
python3 -c "
import gi; gi.require_version('Atspi','2.0')
from gi.repository import Atspi; Atspi.init()
e = Atspi.get_desktop(0)
f = e.get_child_at_index(0)
print(f.get_name())
"   # e caminhe a árvore como no experimento da seção 6
```
- **Aparece editável e com nome** → guarda de alvo por AT-SPI é viável; usar `text_insert`.
- **Não aparece** → guarda de alvo por AT-SPI **não** serve para o Edge. Cai para: gatilho explícito
  (caneta) + injeção por teclado/clipboard, sem detecção. **O app continua funcionando** — só perde
  a conveniência da pill automática.

**7.3 — O botão da caneta chega ao compositor?**
```bash
libinput debug-events --show-keycodes   # você está no grupo input, não precisa de sudo
# pressione o botão lateral da caneta e leia o código (BTN_STYLUS=331, BTN_STYLUS2=332)
```
- **Aparece e o Hyprland aceita `bind = , mouse:331, ...`** → gatilho ideal.
- **Não** → use teclas expressas da mesa, ou OpenTabletDriver.

**7.4 — Injeção de verdade.** Com o campo aberto no Edge:
```bash
wl-copy "anotação de teste: ção, ã, é"
wtype -M ctrl v -m ctrl          # caminho clipboard
wtype "anotação de teste: ção, ã, é"   # caminho teclado direto — confira acentos
```
Compare os dois no campo real. O que preservar acento vira o padrão.

---

## 8. Limitações e restrições da plataforma Edge

1. **Sem API de anotação.** Não há como inserir texto programaticamente na anotação. Toda
   integração é por teclado/clipboard do sistema. *(Fato documentado; não medido aqui — Edge ausente.)*
2. **O visualizador de PDF é opaco a extensões.** `content script` não o alcança; páginas internas
   do navegador não recebem injeção.
3. **Acessibilidade precisa ser forçada.** Medido: sem `--force-renderer-accessibility` a árvore do
   Chromium vem vazia. Isso significa **mudar como o Edge é lançado** — um wrapper ou um `.desktop`
   próprio. E o Edge é, em geral, lançado como leitor padrão de PDF pelo sistema; garantir a flag
   nesse caminho é trabalho de implantação, não de código.
4. **A anotação vive dentro do navegador até ser salva.** O Edge grava as anotações em uma cópia do
   PDF no momento de salvar. Fechar sem salvar perde o texto — e não há como ler a anotação de fora
   enquanto ela está sendo editada.
5. **`wtype` é somente teclado.** Clique sintético exige `ydotool` + daemon + `/dev/uinput`.
6. **Acento depende do layout.** Motivo para o clipboard ser o caminho padrão.
7. **O alvo é "quem tem o foco", não "o Edge".** Se o foco mudar (uma notificação, um clique
   perdido, a pill roubando o foco ao aparecer), o texto vai para o lugar errado. A pill **não pode
   roubar foco** — em Wayland, uma janela layer-shell sem foco é obrigatória aqui.
8. **Custo fixo de transcrição.** `whisper-server` ausente → ~1,7 s por chamada, medido na skill de
   transcrição. Para frases curtas de anotação isso é aceitável; para ditado contínuo, mostre a fila.

---

## 9. Plano B honesto, e a recomendação

**O Edge é a pior superfície de integração disponível para este cenário** — não por dificuldade
técnica nossa, mas porque é o único visualizador de PDF que se recusa a ser programado. O
`mime_types_handler` confirma isso pelo avesso: a rota *sancionada* para controlar a superfície de
PDF é substituir o visualizador.

Ainda assim, **construa o gatilho e a guarda de forma genérica**, não específica do Edge. Assim:

- funciona no Edge se a medição 7.2 autorizar;
- funciona **igual** em Zotero, Okular, no navegador X ou num visualizador PDF.js próprio, sem
  código novo;
- e se o Edge não publicar a caixa de anotação, você perde a detecção automática — **não o recurso**.
  O caminho "caneta → pill → clipboard → Ctrl+V" não depende de detecção nenhuma.

Se a medição 7.2 falhar e você quiser a experiência completa (pill ancorada ao campo, texto
entrando ao vivo), a resposta é o **Plano B**: um visualizador PDF.js dentro do próprio Zorin
Copilot, onde a camada de anotação é sua. Aí a integração deixa de ser contorno e passa a ser
direta — e o `study_capture.py` já mostra que ler o que está na tela é território conhecido.

---

## 10. Etapas de implementação

| # | etapa | arquivos | critério de pronto |
|---|---|---|---|
| 0 | medições 7.1–7.4 | — | ramos decididos, com saída registrada |
| 1 | gatilho por caneta | `~/.config/hypr/hyprland.conf` (ou OpenTabletDriver) | clique no botão da caneta alterna o ditado, sem tocar no teclado |
| 2 | guarda de alvo | `core/a11y.py` (novo `focused_editable()`), `core/dictation.py` | com o foco na barra de endereços, **nada** é digitado e a pill avisa |
| 3 | modo clipboard | `shell/input_driver.py` (`paste_text`) | acentos preservados no campo do Edge |
| 4 | injeção por segmento + re-verificação | `core/dictation.py` | perder o foco no meio interrompe a digitação |
| 5 | diário da sessão | padrão JSONL append-only da skill de transcrição | cada anotação ditada é relida depois, com PDF e página |
| 6 | verificação por mutação | `tests/mutacao/` | remover a guarda de alvo **faz a bancada falhar** |

A etapa 5 não é enfeite: como a anotação do Edge só existe de verdade quando salva, o diário é a
única cópia que sobrevive a um fechamento acidental.

A etapa 6 é obrigatória pela régua do projeto. Uma bancada que passa com a guarda de alvo removida
é pior que bancada nenhuma — ela fabrica confiança exatamente na camada que existe para impedir o
texto de ir para o lugar errado.
