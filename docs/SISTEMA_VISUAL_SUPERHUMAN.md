# Sistema Visual & de Interação — inspiração Superhuman

Documento de referência para o estilo de interação da interface do Zorin Copilot:
velocidade percebida, foco de teclado e feedback imediato.

---

## 1. Por que "Superhuman"

O Superhuman é a referência de e-mail mais rápido do mercado. O que ele ensina
não é decoração — é **percepção de velocidade**:

| Princípio do Superhuman | Como foi aplicado aqui |
|---|---|
| Paleta sóbria, cor é sinal | Cinza neutro para estrutura; cor só como conteúdo |
| Tipografia refinada | Fonte do sistema; `tnum` em métricas e atalhos |
| Atalhos fluidos | Cada ação com acelerador visível, não escondido |
| Feedback imediato | Todo clique tem estado `:active` que responde em 60ms |
| Movimento discreto | Poucas durações (60/90/150ms), uma curva só |

---

## 2. Escala de tempo (uma curva, três tempos)

Curva única: `cubic-bezier(0.2, 0.8, 0.2, 1.0)` (fast-out, slow-in).

| Tempo | Uso |
|---|---|
| **60ms** | `:active` — retorno tátil do clique |
| **90ms** | Foco de teclado, hover em listas densas, superfícies de comando |
| **150ms** | Transição de estado em cartões, status bar, rolagem |

A regra: quanto mais o usuário **age** (clica, navega por setas), mais rápido o
feedback. Quanto mais o app **reage sozinho** (status mudando), mais suave.

---

## 3. Restrições técnicas (importante)

O GTK4 CSS **não é CSS de navegador**. Regras que valem aqui:

- **`transform` e `scale` não são animáveis** — nem existe `@keyframes` no GTK4.
  Por isso *não* há microinterações de "escala no hover": elas seriam um
  relayout de widget, o oposto de fluido.
- **`prefers-reduced-motion` não existe.** Em vez de fingir suporte, o app
  oferece a preferência explícita `reduced_motion` (classe `window.reduced-motion`),
  que zera todas as durações de uma vez.
- **`box-shadow` como halo é proibido** neste projeto (regra anti-LED): o foco
  usa `background-image` (tint), nunca `border-color` nem glow.
- **Acento sempre via `@accent_color`** — nunca hex literal fora do
  `@define-color`, senão re-tematizar quebra (há teste garantindo isso).

---

## 4. Microinterações implementadas

| Superfície | Classe | Comportamento |
|---|---|---|
| Foco de teclado | `button:focus-visible`, `entry:focus-visible`… | Anel monocromático em 90ms |
| Botões de ícone | `.glass-icon-btn` | `:active` em 60ms |
| Painel de comandos | `.command-row`, `.model-row` | Seleção/hover em 60–90ms |
| Barra lateral | `.sidebar-chat-row` | Navegação por seta em 60ms |
| Tabelas densas | `.network-table-row` | Varredura por teclado em 60–90ms |
| Cartões | `.assistant-message-card`, `.agent-card` | Estado em 150ms |
| Status bar | `.status-item`, `.status-text` | Cor em 150ms |
| Atalhos | `.kbd`, `.shortcut-chord` | Teclas com `tnum` |

---

## 5. Atalhos como cidadãos de primeira classe

O pilar "atalhos fluidos" tem três camadas:

1. **Registro único** — `core.shortcuts.APP_SHORTCUTS` é a fonte da verdade.
2. **Descobribilidade** — `Ctrl+K` mostra cada ação com o atalho ao lado,
   renderizado como teclas reais (`.kbd`), não texto cinza.
3. **Referência** — comando "Atalhos de teclado" no painel abre a cheatsheet
   com todos os atalhos, separando os do app dos globais do sistema.

> **Bug corrigido:** `_accelerator_parts()` removia todos os `>` e separava por
> `<`, o que transformava `<Control>q` em `Controlq` e `<Control><Shift>m` em
> `Ctrl+Shiftm`. Agora o parser lê modificadores entre `<>` corretamente.

---

## 6. Como personalizar

O CSS vive em `src/zorin_copilot/data/zorin-copilot.css`. A cadeia de
sobrescrita (cada item vence o anterior):

1. tema embutido no pacote
2. `/usr/share/zorin-copilot/zorin-copilot.css`
3. `~/.config/zorin-copilot/themes/*.css`
4. `~/.config/zorin-copilot/user.css`

Exemplo — interface sem micro-animações, mantendo tudo o mais:

```css
window.glass-window * { transition-duration: 0ms; }
```

Ou, sem tocar em CSS, ligue `reduced_motion` na configuração (ou defina
`ZORIN_COPILOT_REDUCED_MOTION=1` na sessão).
