# Zorin Copilot — Análise da UI e Plano de Melhorias

Análise dos arquivos em `src/zorin_copilot/ui/` (4 arquivos, **3.425 linhas**) — `app.py` (1.976), `preferences.py` (481), `style.py` (626) e `live_view.py` (342) — cruzada com o `ARCHITECTURE.md`, o `README.md` e os screenshots fornecidos (tela inicial + modo voz ao vivo).

> Construído em GTK4 + Libadwaita com tema glassmorphism proprietário. Stack de IA: Gemini (padrão), Ollama, OpenAI-compat. Voz ao vivo via Gemini Multimodal Live, captura via Wayland + libei/ydotool, e cerca espacial de monitores (`ScreenFenceManager`).

---

## 1. O que a UI atual faz bem

| Aspecto | Onde | Comentário |
|---|---|---|
| Linguagem visual coesa | `style.py` (CSS injetado em `STYLE_PROVIDER_PRIORITY_USER`) | Sistema de design próprio, blindado contra temas do SO (`window.light-glass` / `window.dark-glass` escopados), com 13 estilos utilitários (`glass-card`, `glass-pill`, `glass-chip`, `glass-entry`, `glass-submit-btn`, `prompt-bar-card`, `user-chat-bubble`, `assistant-message-card`, `glass-row`, `glass-icon-btn`, `glass-launch-btn`, `glass-pin-btn`, `sidebar-chat-row`, `welcome-title`). |
| Tema claro/escuro sincronizado | `setup_glass_window` (style.py L610–626) | Reage a `Adw.StyleManager.notify::dark` e alterna classes de janela, sem flash. |
| Hierarquia da barra de prompt | `app.py` L886–1017 | Floating pill com 4 zonas: visão (popover), clipboard (popover), entrada de texto expansível, voz + submit. Acolhe o anexo de imagem sem reflow. |
| Multi-turn streaming | `app.py` L1233–1285 | Pending turn widget é inserido antes da resposta da IA (efeito "Pensando…") e substituído pelo card final — sem layout shift. |
| Ações propostas inline | `_create_action_row` (app.py L1526–1639) | Cada `DesktopAction` vira um `Adw.ActionRow` com ícone semântico + botão de execução + atalho "Executar Todas" quando > 1. |
| Vidro + 96% de opacidade | `style.py` L17–585 | Resolve o trade-off clássico de glassmorphism (legibilidade vs. blur); anota `PreferencesDialog` com `preferencesdialog { background-color: @dialog_bg_color; }` para não vazar. |
| Modo HUD (`toggle_hud`) | `app.py` L213–218, `_on_close_request` L200–205 | Janela esconde sem matar processo — bate com atalhos globais de `ShortcutManager`. |

---

## 2. Problemas prioritários (e onde mexer)

### 2.1 — `app.py` é um **monolito de 1.976 linhas** com 7+ responsabilidades

A classe `CopilotWindow` (L152–1886) faz tudo: ciclo de vida, layout, threading, sessão, atalhos, vision, sidebar, voz, fence, toasts. Isso está tornando qualquer mudança arriscada e o `_create_turn_widget` sozinho tem 200 linhas.

**Sugestão de refatoração** (sem mudar comportamento):

```
src/zorin_copilot/ui/
├── app.py                 # só Adw.ApplicationWindow + wiring
├── widgets/
│   ├── sidebar.py         # _build_sidebar, _populate_sidebar_history, _on_sidebar_search_changed, _on_delete_topic, _on_clear_all_history
│   ├── chat_stream.py     # _create_turn_widget, _create_action_row, _rebuild_chat_stream, _scroll_to_bottom
│   ├── prompt_bar.py      # prompt_bar_box, vision_popover, clipboard_popover, _on_submit, _on_entry_changed, _update_app_preview
│   ├── vision.py          # vision_preview_box, _render_active_vision_thumbnail, _clear_active_vision, _on_capture_finished
│   ├── header.py          # HeaderBar, fence_menu_btn, status_badge_btn, _build_fence_popover
│   ├── live.py            # re-export LiveVoiceWidget (já está em live_view.py)
│   └── toasts.py          # show_toast + helpers
```

Risco baixo: nenhuma mudança de comportamento, só movimentação de código. Posso fazer isso em um PR com diff pequeno se você quiser.

### 2.2 — HeaderBar está **sobrecarregado e pouco escaneável** (screenshot 1)

Hoje o pack_end tem **4 itens** lado a lado, sem agrupamento semântico:

```
[modelo pill]  [AOC 27" pill]  [microfone]  [engrenagem]
```

Problemas que vejo nas linhas L516–519:
- O badge "WorkBuddy (hy4-preview)" e o fence "AOC 27\"" são texto-puros sem hierarquia — parecem o mesmo tipo de coisa.
- O botão de voz duplica o botão de voz da barra de prompt (L477 + L994).
- Não há como ver se a janela está **fixada no topo** (pin) ou em **modo HUD**.
- `Ctrl+M` (voz), `Ctrl+H` (sidebar), `Ctrl+N` (nova conversa), `Ctrl+P` (pin) estão **hardcoded** no `on_key_pressed` (L542–577), fora do `ShortcutManager`. Isso quebra o padrão do app.

**Melhorias concretas:**

1. **Agrupar header com `Adw.SplitButton` + popovers.** Em vez de 4 botões soltos, ter:
   - `☰ Menu` (esquerda, abre: Nova conversa, Fixar, Histórico, Preferências, Sair)
   - Título centralizado dinâmico ("Conversa nova" / "Conversa retomada")
   - Direita: badge do modelo + botão de voz + ⚙️
   
2. **Mover todos os atalhos para `ShortcutManager`** (`core/shortcuts.py`), com escopo "app" vs. "global", e exibir a combinação ao lado do tooltip em cada botão.

3. **Mostrar estado do pin** com uma estrela discreta no título quando `session.pinned`.

### 2.3 — Tela inicial: chips com **lógica rígida e mal localizada** (screenshot 1 vs. código)

O código define `suggestions = [...]` (L736–741) como uma lista **Python literal** com strings em PT-BR embutidas, e `_trigger_prompt` (L1064–1080) trata cada uma por comparação `if text == "voz_ao_vivo"`. Isso não escala.

**Sugestões:**

- Mover para `data/onboarding_suggestions.json` (ou `.yaml`), carregado por `core/config.py`. Suporta i18n via `gettext` (GNOME já usa isso no Zorin) e personalização por usuário.
- Cada chip deveria ter: `{icon, label, prompt, category, requires_voice, requires_image}`. O botão só aparece se a capability existir (ex.: "Voz ao Vivo" some se não houver microfone ou chave Gemini).
- Adicionar **suggestions contextuais**: depois de uma conversa, oferecer "Resumir esta conversa" / "Continuar em voz" / "Salvar como nota" — recurso clássico do Gemini/ChatGPT.

### 2.4 — Sidebar tem **problema de affordance grave** no botão "Nova conversa"

No screenshot 1 a sidebar tem "Nova conversa" como um card clicável com `+` à esquerda. Mas no código (L614–632) isso é um `Gtk.Button` com `card` + `pill` + `glass-card`. **Bate.** Só que:

- O `pan-start-symbolic` no canto superior da sidebar (L604) recolhe, mas **não há nada indicando que a sidebar é recolhível** quando ela está escondida — só o `sidebar-show-symbolic` na HeaderBar. Para quem chega pela primeira vez, é invisível.
- O ícone `dialog-information-symbolic` em conversas (L1721) é o mesmo usado para "info do sistema" — ambíguo. Trocar para `format-justification-symbolic` ou `user-available-symbolic` apenas na ativa (não em todas).
- O `_populate_sidebar_history` **recarrega tudo a cada tecla digitada** (L1760–1763 → L1671). Para listas grandes isso vai engasgar. **Cache + diff incremental**, ou ao menos debounce de 80ms.

### 2.5 — `live_view.py` desenha o orbe, mas o **estado do assistente não é audível** (screenshot 2)

O visualizador (`_draw_audio_visualizer` L267–299) tem 3 círculos concêntricos com cor variando por estado (azul/roxo/verde/âmbar). Bonito, mas:

- Não há **anel de "tempo de fala"** (cronômetro / contador de segundos). Em chamadas de 5 minutos não dá pra saber quanto já durou.
- Não há **histórico rolável da transcrição** dentro do card. O `subtitle_lbl` (L111) só guarda a *última* fala (linha 260 substitui) — falas anteriores somem.
- O `GLib.timeout_add_seconds(5, ...)` na L254 para esconder o pill de ação usa uma tuple `(..., GLib.SOURCE_REMOVE)[1]` que é confusa e silenciosa se falhar.

**Melhorias:**

1. Substituir o pill efêmero por uma **lista rolável de ações** dentro do card (`Gtk.ScrolledWindow` + `Gtk.ListBox`), sempre visível durante a sessão — o usuário pode revisar o que foi feito.
2. Adicionar cronômetro no header (substituir o "• Conectando..." por "• Conectando... 00:08").
3. Mostrar transcrição completa em uma `Adw.PreferencesGroup` colapsável com timestamps.
4. Trocar a tuple trick por `return GLib.SOURCE_REMOVE` direto (refator L254 → função nomeada).

### 2.6 — Markdown renderer é **ingênuo e perde sintaxe** (app.py L58–95)

`format_markdown_to_markup` implementa: blocos de código, código inline, links, bold, itálico, listas. Mas:

- Tabelas markdown → nada.
- Cabeçalhos `#`/`##` → nada (vira texto).
- Block quotes `>` → nada.
- Imagens `![alt](url)` → nada (vira link).
- Não escapa `<`/`>` **antes** de detectar marcação (faz `html.escape` primeiro, ok — mas se o markup falha, retorna o texto escapado, escondendo o bug).

**Recomendação:** usar `python-markdown` + `pycmarkgfm` ou `mistune`, com extensão `fenced_code` + `tables`. **OU** renderizar markdown num WebView (WebKitGTK) com `Gtk.WebView` — o ChatGPT desktop faz exatamente isso e ganha com syntax highlight (highlight.js), copiar imagem, etc. Custaria ~10MB de dependência mas dá flexibilidade absurda.

### 2.7 — Atalho `Esc` no `on_key_pressed` (L562–574) tem **fallthrough perigoso**

```python
if keyval == Gdk.KEY_Escape:
    if self.live_client and self.live_client.is_active():
        self.stop_live_voice(); return True
    if self.entry.get_text():
        self.entry.set_text(""); return True
    elif self.sidebar_search.get_text():
        self.sidebar_search.set_text(""); return True
    else:
        self.set_visible(False); return True
```

Esc dentro de um popover aberto (ex.: `vision_btn.popover`) **não fecha o popover** — fecha tudo abaixo. E se o usuário está com foco num campo do PreferencesDialog (transiente), pode fechar a janela principal por engano.

**Fix:** checar `self.get_focus()` antes da cascata; fechar popovers explicitamente; só esconder a janela se nenhum filho tem foco.

### 2.8 — **Zero feedback** para ações falhas no histórico

Em `_on_plan_ready` (L1287–1327) e em `_create_turn_widget` para o `exec_all`, quando algo falha não há rastro visível na linha do tempo do chat. O usuário vê só um toast efêmero ("✗ Erro"). Para um app que toca o sistema operacional inteiro, isso é grave.

**Sugestão:** marcar ações com `Adw.ActionRow` em vermelho (`@define-color error_bg_color #fce8e6`) quando `rep.success = False`, e adicionar botão "Tentar novamente" / "Diagnosticar".

### 2.9 — `PreferencesDialog` empilha **3 grupos do mesmo provedor** sem agrupamento forte

Em `preferences.py` (L60–129) Gemini, Ollama e OpenAI ficam em três `Adw.PreferencesGroup` separados dentro da mesma página "Inteligência Artificial". Visualmente isso vira uma parede de entradas.

**Melhoria:** usar `Adw.ViewSwitcher` + `Adw.ViewStack` no topo da página:

```
[ Gemini ] [ Ollama ] [ OpenAI ]
└───────── só o grupo ativo aparece ─────────┘
```

Economiza scroll e deixa óbvio que são mutuamente exclusivos. A flag `_update_visibility` (L385–389) já existe — só precisa virar `view_stack.set_visible_child_name(...)`.

### 2.10 — `style.py` é uma **string gigante de 568 linhas** sem validação

`GLASS_CSS` está hardcoded em um único string. Não há como o usuário customizar nada (tamanho de fonte, blur, cores) — só "light" e "dark" nativos. Para um app "Spotlight-style" no Zorin, onde a comunidade gosta de tweakar, isso é limitante.

**Sugestão:** mover para `data/zorin-copilot.css` carregado de `/usr/share/zorin-copilot/` (system) e `~/.config/zorin-copilot/user.css` (override). Suporte a `@import` no GTK4 já existe.

---

## 3. Ideias incrementais (nice-to-have)

| # | Sugestão | Esforço | Impacto |
|---|---|---|---|
| 1 | **Command palette** (Ctrl+K) estilo VSCode, listando todos os comandos da app | 1 dia | Alto — descobribilidade |
| 2 | **Indicador de tokens consumidos** no badge do modelo (estilo Raycast) | 2h | Médio — usuários Pro adoram |
| 3 | **Drag-and-drop** de arquivos no chat (imagem, PDF, txt) → anexa como contexto | 1 dia | Alto |
| 4 | **Markdown export** da conversa (`⌘+S` ou botão) → `.md` bem formatado com frontmatter | 2h | Médio |
| 5 | **Split view** para comparar 2 respostas lado a lado | 3 dias | Médio — útil pra debug |
| 6 | **Animação de "digitando..."** no user-bubble antes da resposta (estilo iMessage) | 4h | Baixo — cosmético |
| 7 | **Suporte a temas customizados** carregados de `~/.config/zorin-copilot/themes/*.css` | 2 dias | Médio |
| 8 | **Picture-in-picture** do orbe quando a janela é minimizada durante voz | 1 dia | Médio |
| 9 | **Status bar inferior** com `system_load`, `quota Gemini`, `RAM do RAG indexer` (estilo Warp) | 1 dia | Médio |
| 10 | **Histórico de undo** de ações do executor (rollback das últimas 5 ações) | 2 dias | Alto — confiança |

---

## 4. Bugs prováveis que vi de relance

- `app.py:1228–1229`: `GLib.source_remove(self._search_debounce_timer)` retorna `False` se o timer já disparou, mas o `_pending_turn_box.get_parent()` check (L1300) está certo. OK.
- `app.py:254`: tuple trick `(self.action_revealer.set_reveal_child(False), GLib.SOURCE_REMOVE)[1]` — se a primeira expressão levantar, o SOURCE_REMOVE nunca retorna. Raro, mas silencioso. Usar função nomeada.
- `live_view.py:65`: `<span foreground='#e01b24'><b>● TELA AO VIVO (1 FPS)</b></span>` — string fixa em PT, deveria ser i18n.
- `app.py:1721`: `user-available-symbolic` (ícone de presença) é usado para conversa ativa — semanticamente errado. `starred-symbolic` ou `emblem-default-symbolic` seria melhor.
- `preferences.py:69–77`: lista de modelos Gemini tem `gemini-3.8-flash` como recomendado (L80), mas a string `"gemini-3.8-flash"` não bate com nenhum modelo público conhecido. Provável erro de digitação ou versão futura.
- `style.py:519`: `rgba(21, 166, 240, 0.30)` no `user-chat-bubble` dark glass — funciona, mas a cor hardcoded `#15a6f0` aparece em 14 lugares diferentes. Deveria ser uma variável CSS.

---

## 5. Roteiro recomendado

```
Sprint 1 — CONCLUÍDO
  [x] Refatorar app.py em widgets/ (item 2.1) — 1976 -> 823 linhas (-58%)
  [x] Atalhos em registro declarativo APP_SHORTCUTS (item 2.2)
  [x] Fix do Esc handler: fecha popovers antes de esconder a janela (item 2.7)
  [x] Correções extras: record_turn devolve o turno; empacotamento inclui ui/

Sprint 2 — CONCLUÍDO
  [x] Seletor de provedor segmentado no PreferencesDialog (item 2.9)
      └─ Adw.PreferencesPage.add() só aceita PreferencesGroup, então Adw.ViewStack
         não pôde ser embutido. Solução equivalente: 3 ToggleButtons linkados
         dentro de um PreferencesGroup, com exclusão mútua e ícones validados.
  [x] Sidebar: ícones corretos no histórico (item 2.4) [debounce feito no Sprint 1]
  [x] Histórico rolável + cronômetro no live widget (item 2.5)

Sprint 3 (2 semanas)
  → Markdown renderer real (item 2.6) OU WebView
  → Feedback de falha em ações (item 2.8)
  → Temas customizáveis (item 2.10)

Backlog
  → Command palette, drag-and-drop, status bar, undo de ações
```

### Estrutura resultante dos Sprints 1 + 2

```
src/zorin_copilot/ui/
├── app.py            823 linhas  (orquestração: sessão, voz, HUD, ciclo de mensagem)
├── live_view.py      487 linhas  (log de sessão acumulativo + cronômetro)
├── preferences.py    539 linhas  (seletor de provedor segmentado)
├── style.py          626 linhas
└── widgets/
    ├── header.py      221  HeaderBar, badge de modelo, popover de cerca espacial
    ├── sidebar.py     300  histórico de conversas (busca com debounce)
    ├── chat_stream.py 602  fluxo de mensagens, ações propostas, markdown
    ├── prompt_bar.py  445  barra de prompt, prévia de app, envio
    └── vision.py      180  anexo visual e captura de tela
```

#### Detalhe do Sprint 2 — live widget

| Antes | Depois |
|---|---|
| `subtitle_lbl` sobrescrito a cada transcrição | `_append_log_row()` acumula linhas com ícone de papel |
| Pill de ação que some em 5 s (`GLib.timeout_add_seconds` + truque de tupla) | Linha permanente no log, com ícone por ferramenta |
| Sem noção de duração da chamada | `timer_lbl` com `mm:ss`, inicia no CONNECTING e para no DISCONNECTED |
| Erros só no log | Erros também entram no log como linha de aviso |

---

## 6. Resumo executivo (TL;DR)

A UI é **ambiciosa e visualmente coesa** para um app GTK puro — o tema glassmorphism é bem-feito e a separação live/chat/preferências está clara. Os **3 maiores problemas** são:

1. **Acoplamento**: `app.py` com quase 2.000 linhas trava a evolução. Dividir em widgets/.
2. **Hierarquia do header**: 4 botões no pack_end sem agrupamento semântico confunde novatos.
3. **Falta de rastro**: ações que falham somem do histórico; voz ao vivo não mostra transcrição completa nem cronômetro.

Refatorar `app.py`, melhorar o header e enriquecer o live widget com histórico rolável entregaria 70% do valor com ~1 semana de trabalho.