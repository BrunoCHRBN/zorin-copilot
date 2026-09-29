# Plano de implementação — Melhorias da pílula de live voice

Documento de trabalho. Consolida as melhorias levantadas para a pílula de voz ao
vivo (`ui/voice_pill.py`) em **fases sequenciais**, cada uma com objetivo,
atividades e entregáveis. A ordem vai do maior impacto / menor risco para o
maior escopo.

Base: análise do código atual (`ui/voice_pill.py`, `ui/app.py`,
`core/shortcuts_portal.py`, `core/desktop/shortcuts.py`, `core/config.py`).

Legenda de status: `[ ]` pendente · `[~]` em andamento · `[x]` concluída.

---

## Contexto: o que já foi corrigido

- **[x] Bug do atalho abrindo a GUI inteira.** Em `ui/app.py`, o atalho `voice`
  usava `elif _overlay_is_pill(win.config) and not win.get_visible():`. Quando a
  janela principal já estava visível, o fluxo caía em
  `else: summon_hud() + toggle_live_voice(as_pill=False)` e carregava a GUI
  completa. Extraído `ZorinCopilotApp._handle_voice_shortcut(win)` (estático),
  usado por `_on_portal_shortcut` e `do_command_line`. No modo `"pill"` o atalho
  agora abre **só a pílula**, sem `summon_hud()`. Testes em
  `tests/test_voice_shortcut.py`.

---

## Fase 1 — Robustez do ciclo de vida da pílula (abertura/fechamento)

**Objetivo.** A pílula nunca fica presa em estado morto (erro/desconexão) e o
cliente de voz é reaproveitado sem sessões fantasmas.

**Atividades.**
1. Novo campo de configuração `pill_error_autohide_sec` (default 12; `0` desliga)
   em `core/config.py`, e preservação do campo (e dos demais `pill_*`) em
   `PreferencesDialog._collect_current_config`, que parte de um `CopilotConfig()`
   novo e zeraria os valores a cada save.
2. Auto-ocultação em `VoicePillWindow`: ao entrar em `ERROR`/`DISCONNECTED`,
   agendar `GLib.timeout_add_seconds`; ao voltar a um estado vivo, cancelar; no
   disparo, fechar a pílula pelo mesmo caminho do `on_close` (consolida a sessão).
3. Incluir o source de auto-hide no `_release_sources()` (sem vazar timers).
4. Reuso limpo do cliente: em `start_live_voice`, encerrar o cliente anterior
   quando a sessão troca de objeto; ao reutilizar a pílula, parar o cliente antigo
   antes de reconectar os callbacks.
5. Testes de regressão (auto-hide agendado/cancelado, no-op em estado vivo,
   limpeza no release) e do roteamento do atalho.

**Entregáveis.**
- `core/config.py`, `ui/voice_pill.py`, `ui/app.py`, `ui/preferences.py` ajustados.
- `tests/test_voice_pill.py` (auto-hide) e `tests/test_voice_shortcut.py` (roteamento).
- Suíte focada verde.

**Status:** `[x]` concluída nesta rodada.

---

## Fase 2 — Integração com o atalho (pílula dedicada)

**Objetivo.** Permitir abrir a pílula mesmo no modo `"full"`, separar a intenção
"pílula" da intenção "HUD" e tratar sessões mortas no toggle.

**Atividades.**
1. Novo slot/flag dedicado `voice-pill` (`--voice-pill`), distinto do `voice`
   atual: um sempre abre a pílula, o outro respeita `voice_overlay_mode`.
2. Registrar o slot nos backends (`core/desktop/shortcuts.py`,
   `core/shortcuts_portal.py`) e expor o atalho em `ui/preferences.py`.
   *Feito com opt-in:* o atalho é **desligado por padrão** e, no portal, só é
   oferecido quando habilitado — o portal pergunta ao usuário o que vincular,
   então oferecer um atalho desligado seria ruído.
3. Tratar `ERROR`/`DISCONNECTED` como "não ativo" no critério de alternância do
   atalho, para que um segundo acionamento reinicie limpo em vez de só esconder.
4. Modificador (ex.: `Shift`) para forçar pílula vs. HUD sem trocar de atalho.
   *(Não implementado: coberto pelo atalho dedicado do item 1 — ver desvio.)*

**Entregáveis.**
- `core/config.py`, `core/desktop/shortcuts.py`, `core/shortcuts_portal.py`,
  `ui/app.py`, `ui/preferences.py` ajustados. *(`cli.py` não foi necessário: as
  flags do app são registradas via `add_main_option` em `ui/app.py`.)*
- Testes de roteamento por slot e de registro de atalho.

**Status:** `[x]` concluída nesta rodada.

> **Desvio do plano (item 4):** em vez de um modificador para "forçar pílula",
> implementou-se o atalho dedicado `voice-pill` (item 1), que é mais descobrível
> e configurável nas Preferências. O modificador ficaria invisível e dependente
> de o compositor repassar o modificador — o slot dedicado é mais robusto.

---

## Fase 3 — Feedback visual

**Objetivo.** Tornar o estado da sessão legível à distância, sem depender de ler
o texto.

**Atividades.**
1. Anel/indicador pulsante em `CONNECTING` (hoje só há o texto "Conectando...").
2. Indicador explícito de captação de áudio (ouvindo/enviando) além do
   visualizador.
3. Contorno persistente de microfone mudo (reforçar a classe `pill-muted` já
   existente no container).
4. Fila/contador de chips de ferramenta para evitar o "pisca-pisca" quando várias
   tools rodam em sequência (hoje o chip dura 3s e é substituído).
5. Clareza do fade ocioso: reduzir também o `keep_above` ao esmaecer e restaurar
   com transição suave.
6. Ajustes em `data/zorin-copilot.css` (paleta neutra, sem gradientes proibidos;
   movimento na curva única do projeto).

**Entregáveis.**
- `ui/voice_pill.py`, `data/zorin-copilot.css`, `tests/test_voice_pill.py`,
  `docs/mockup-*.html` atualizado e fiel ao app.

**Status:** `[x]` concluída nesta rodada.

**O que foi feito (por item).**
1. **Conexão pulsante.** Classe `pill-connecting` no container, ligada em
   `_ui_on_state_change` e em `show_preparing`; o avatar "respira" por opacidade
   dirigida pelo frame clock (`0.55 + 0.45·|sin|`) — GTK4 não anima CSS.
2. **Indicador de captação.** Novo `mic_dot` (label `●`) na linha de status, com
   `_tick_capture_indicator()` por frame: visível e em repouso (0.30) quando
   `LISTENING` sem mudo, opacidade subindo até 1.0 com o nível de áudio; some em
   qualquer outro estado ou com o mic mudo.
3. **Mudo reforçado.** Além do tint de fundo já existente, o texto de status
   assume `@voice_pill_error_color`. O seletor foi ancorado em `.pill-muted`
   (não em `.voice-pill-container.pill-muted`) porque os guardas exigem que todo
   bloco daquele prefixo seja um tint de fundo.
4. **Rajada de chips.** `_show_chip` conta chips consecutivos e prefixa a partir
   do segundo (`2× Executando…`); `_restore_status` zera o contador. O primeiro
   chip mantém o texto cru (preserva o contrato do teste existente).
5. **Fade ocioso.** O halo agora apaga junto: `_set_glow_color` é ciente do
   ocioso (`_GLOW_ALPHA_ACTIVE` 0.55 → `_GLOW_ALPHA_IDLE` 0.12) e o cache é
   invalidado na transição. `_apply_keep_above()` faz `keep_above = pill_pinned
   AND não-ocioso`. A transição de opacidade (420 ms) entra na curva do container.
6. **CSS.** Regras novas para `pill-connecting`, `pill-mic-dot`, `pill-idle` e o
   status mudo. `pill-connecting` é declarado **antes** de `pill-muted` de
   propósito: mesma especificidade, então mute/privacidade vencem no empate.
   Verificado com `Gtk.CssProvider` — 0 erros de parsing.

**Correção de teste necessária.** `test_pill_state_uses_tint_not_border` e
`test_pill_has_no_glow` varriam o CSS **cru** (com comentários). Documentar o
seletor num comentário criava um match espúrio que atravessava até a chave da
regra seguinte. Passaram a usar `GLASS_CSS_NC` (folha sem comentários, com a
numeração preservada) — exatamente a armadilha já registrada na skill de GUI.

**Testes.** 10 casos novos em `TestPhase3Feedback` (`tests/test_voice_pill.py`) +
3 guardas de CSS em `tests/test_style.py`. Suíte focada e varredura de regressão
(10 módulos de UI) verdes.

---

## Fase 4 — Usabilidade e descoberta

**Objetivo.** Tornar os controles descobríveis e separar "ocultar" de "encerrar".

**Atividades.**
1. Overlay transiente de dicas de atalho (`Esc`, `Espaço`, `Ctrl+M`, `i`, `p`,
   `?`) na primeira abertura de cada sessão; preferência para desligar.
2. Rótulo/ícone claro no botão de expansão (hoje `view-fullscreen-symbolic` com
   tooltip ambíguo) — separar a semântica de "abrir na janela do Copilot".
3. Ação "ocultar sem encerrar" (ex.: duplo-clique no avatar), mantendo a sessão
   viva em segundo plano.
4. Deixar o reposicionamento por menu (`_on_set_corner`) mais visível, como
   alternativa ao arraste (que falha em alguns compositores Wayland).

**Entregáveis.**
- `ui/voice_pill.py`, `ui/preferences.py` (opção "não mostrar dicas"), testes.

**Status:** `[x]` concluída nesta rodada.

**O que foi feito (por item).**
1. **Dicas de atalho.** Novo `hint_lbl` na linha de status (ocupa o lugar do
   texto, não cria linha nova — a janela é não-redimensionável). Uma dica por vez
   em ciclo de 2 s (`Espaço`, `Esc`, `Ctrl+M`, `i`, `p`, `?`), porque a área de
   texto cabe ~17 caracteres. Ciclo **único por execução do app**, não por sessão
   de voz: repetir a cada invocação vira ruído para quem já sabe. Preferência
   `pill_hint_tips` (switch em Preferências) + `pill_hint_sec`.
   *Desvios documentados:* (a) o plano dizia "primeira abertura de cada sessão" —
   implementado por execução do app, que é o que faz sentido; (b) `h` (ocultar)
   não entrou no ciclo de dicas (ele ficaria em 7 itens / 14 s); está no menu.
2. **Botão de expansão.** Ícone `view-fullscreen-symbolic` → `window-new-symbolic`
   e tooltip "Abrir a conversa na janela do Copilot (?)" — a semântica real. Os
   quatro botões só-ícone ganharam rótulo acessível (`update_property`), que antes
   não tinham nenhum.
3. **Ocultar sem encerrar.** Novo `on_hide` + `_on_hide_only()`, acionável por
   `h`, duplo-clique no avatar e entrada no menu. Distinto de `on_close`:
   o cliente Live continua conectado e ouvindo. Em `ui/app.py`, novo
   `show_live_voice_pill()` e o atalho global passou a **reexibir** a pílula
   quando a sessão está viva mas oculta — antes ele encerrava, o que tornava a
   ocultação uma porta de mão única. Guarda: só reapresenta se a pílula estiver
   ligada ao cliente atual.
4. **Posicionamento.** O submenu "Posição" listava códigos crus (`top-center`);
   agora usa rótulos humanos (`Topo · Centro`) via `_CORNER_LABELS`, e virou uma
   **ação com estado** (`pill.set-corner`), então o PopoverMenu marca a posição
   atual. Importa porque arrastar falha em vários compositores Wayland — o menu é
   o caminho confiável.

**Correção de bug encontrada durante a Fase 4.** `pill_hint_sec` com
`total // intervalo` truncava o ciclo: 6 dicas × 2 s num teto de 7 s mostravam só
3. Trocado por arredondamento para cima e o default subiu para 12 s (cobre o
ciclo inteiro).

**Correção de documentação (skill de GUI).** A regra "`Adw.ActionRow` recebe texto
puro" estava **invertida**. Medido em Adw 1.9.4 varrendo os `Gtk.Label` internos:
`Adw.PreferencesPage.title` é texto puro, mas `Adw.ActionRow.title/subtitle` e
`Adw.PreferencesGroup.title/description` são **Pango markup** — um `&` cru faz o
parse falhar e o rótulo fica **vazio**, sem exceção. Guarda nova em
`tests/test_preferences_live.py::AdwMarkupEscapingGuardTest` (varredura por `ast`).

**Testes.** 10 casos de dicas + 4 do botão de expansão + 9 de ocultar-sem-encerrar
+ 5 do menu de posição (`tests/test_voice_pill.py`), 6 de reexibição
(`tests/test_voice_shortcut.py`), 3 de preferência + 4 de markup
(`tests/test_preferences_live.py`). Varredura de regressão: 11 módulos de UI
verdes.

---

## Fase 5 — Verificação final e documentação

**Objetivo.** Fechar o ciclo com a suíte completa verde, verificação visual e
documentação atualizada.

**Atividades.**
1. Suíte completa por `run_tests_safe.py` **e** pelo venv pytest
   (`/tmp/pytestenv/bin/python -m pytest -q`) — há 13 arquivos que só o pytest vê.
2. Verificação de forma: renderizar o mockup em headless e conferir legibilidade
   (o teste verde não prova que ficou legível).
3. Atualizar `docs/mockup-*.html` se a forma mudar; manter fiel ao app.
4. Atualizar este plano (status) e a memória do projeto.

**Entregáveis.**
- Suíte verde nos dois runners; mockup fiel; plano marcado como concluído.

**Status:** `[x]` concluída nesta rodada.

**O que foi feito (por item).**
1. **Suíte completa nos dois runners.** `run_tests_safe.py` (guarda de memória)
   rodado com o interpretador do venv: **127 módulos, 114 verdes**, pico de RSS
   **549 MB** (teto 900 MB), **0 abortos por OOM**. Os 13 restantes são os que o
   `unittest` estruturalmente não coleta (`exit 5`, "Ran 0 tests") — arquivos
   pytest-only; rodados por `pytest` dão **307 passed, 1 skipped, 0 failed**.
   *Correção de método:* a primeira passada usou o `python3` do sistema, que não
   tem `pytest`, e os 13 arquivos falhavam com `ModuleNotFoundError` — ruído que
   **parecia regressão e não era**. `run_tests_safe.py --python
   /tmp/pytestenv/bin/python` resolve: guarda de memória e coleta completa no
   mesmo runner.

   > **Correção de 2026-09-21.** Os números desta rodada continuam válidos como
   > registro, mas duas conclusões caducaram: (1) o `/tmp/pytestenv` **morreu** na
   > limpeza de `/tmp` — usar `--python .venv/bin/python`; (2) não é mais preciso
   > rodar dois runners nem escolher `unittest` para teste novo. O
   > `run_tests_safe.py` agora invoca `pytest` por módulo, então **um comando cobre
   > a suíte inteira**: **147 módulos, 0 falhas**. Os "13 restantes" eram, medidos,
   > parte de **435 testes que nunca rodavam**. Ver `docs/ESTABILIDADE_TESTES.md`.
   **Duas falhas da suíte completa eram artefato de concorrência, não regressão:**
   `test_preferences_live.py::LiveSessionLogTest::test_log_starts_hidden` e
   `test_style_themes.py::BundledStylesheetTest::test_source_of_truth_is_a_data_file`.
   Ambas passam isoladas (**16 verdes**), e o motivo está nos `mtime`: a suíte
   começou ~10:48, e `data/zorin-copilot.css` foi escrito às **10:49:38** e
   `tests/test_preferences_live.py` às **10:50:40** — outra sessão editando os
   mesmos arquivos **durante** a varredura. O sintoma típico aparece no relatório:
   o nome no resumo (`test_log_starts_hidden`) não bate com a função do traceback
   (`test_log_panel_is_present_from_the_start`), porque o arquivo mudou entre a
   coleta e o resumo. Conjunto relevante revalidado depois, com os arquivos
   parados: **6 módulos, 0 falhas**. Com os arquivos parados, a suíte completa foi
   rodada de novo e fechou **limpa: 2329 passed, 5 skipped, 0 failed** (255 s).
2. **Verificação de forma — o ponto que o teste verde não cobre.** O mockup foi
   renderizado em headless (chromium) e **inspecionado**, não só gerado. A
   primeira versão tinha defeitos reais: o título "Copilot Live" **quebrava em
   duas linhas** e os status saíam truncados ("Ouv…", "Fala…", "Pen…").
   Evidência do resultado final em `docs/pilula-voz-estados.png`.
3. **Geometria medida, não estimada.** `Gtk.Widget.measure()` em headless revelou
   que a pílula é **dimensionada pelo conteúdo: 479×70 com timer / 449×70 sem**.
   A janela é não-redimensionável, então adota o tamanho *natural* — e o
   `set_default_size(390, 58)`, que o mockup usava como se fosse o tamanho real,
   é um pedido inicial **menor** que o conteúdo. O mockup passou a usar a largura
   de cada estado, não uma largura única.
4. **Cobertura conferida contra o código, não presumida.** Enumerei as classes
   `pill-*` realmente aplicadas em `voice_pill.py` e comparei com o mockup. Duas
   ficavam de fora: `pill-probe-*` (correto — são `Gtk.Label` com
   `set_opacity(0.0)` que só existem para resolver a cor do estado; nunca
   renderizam) e **`pill-popover`** (o menu do clique-direito). O menu é uma
   **superfície separada** com zero referência visual — e é justamente onde a
   Fase 4 investiu (reposicionar, que é o caminho confiável quando arrastar falha
   no Wayland; "ocultar sem encerrar"; "abrir na janela do Copilot"). Ganhou seção
   própria no mockup: 4 quadros, com o submenu "Posição" marcando a posição atual
   (prova da ação com estado). Os rótulos são literais de `_build_popover_menu` —
   inclusive o atalho, que faz parte da string e **não** é coluna de acelerador.
   Total: **25 quadros** (15 estados escuros + 6 claros + 4 do menu).
5. **Plano e memória** atualizados; correção de código abaixo.

**Correção de código encontrada na verificação.** A regra
`.voice-pill-avatar.pill-privacy-on` existe na folha desde o commit `897d61d`
("Dynamic Island v2") e **nada a aplicava** — nem em `voice_pill.py`, nem em
nenhum ponto do `src/`. O `_set_flag` age no *container*, e a classe do avatar
nunca teve quem a aplicasse. Consequência **silenciosa**: "privacidade ativa" e
"ouvindo" ficavam a um tint de aviso sutil de distância, e o avatar — que deveria
ficar âmbar — nunca mudava; nada quebrava, só se perdia o sinal. Ligado em
`_ui_on_privacy_state_change`, com teste
(`test_privacy_tints_the_avatar_too`) e guarda nova
(`test_privacy_avatar_rule_is_wired`, que falha se a regra voltar a ficar morta —
regra de CSS que existe precisa ter quem a aplique). O mockup também desenhava um
"escudo" no avatar de privacidade: era **invenção** — o app nunca renderizou um.
Removido, para o mockup não mentir.

---

## Riscos e princípios

- **GTK4 não anima `transform`/`scale`** e não tem `@keyframes` nem
  `prefers-reduced-motion`: usar `opacity`, `color`, `background`.
- **Sem emojis** em botões/labels; usar ícones simbólicos do Adwaita.
- **Testes sempre pelo `run_tests_safe.py`** (sem swap nesta máquina) e com o
  venv pytest para os arquivos invisíveis ao `unittest`.
- **Fonte única de regra:** quando a UI espelha uma regra de domínio, importar a
  função — não reimplementar (ex.: normalização de frases de ativação).
- **Regra de CSS que existe precisa ter quem a aplique.** Uma classe escrita na
  folha e nunca adicionada a um widget falha em silêncio: nada quebra, só se perde
  o sinal visual. `pill-privacy-on` ficou morta por várias versões assim. Quando
  uma regra é um *sinal de estado*, acompanhe-a de um guarda que verifique que o
  código aplica a classe (ver `test_privacy_avatar_rule_is_wired`).
- **Teste verde não prova legibilidade.** Renderize o mockup em headless e *olhe*:
  a primeira versão tinha título quebrando em duas linhas e status truncados com
  a suíte inteira verde. Meça a geometria (`Gtk.Widget.measure()`) em vez de
  confiar no `set_default_size` — a pílula é dimensionada pelo conteúdo.
