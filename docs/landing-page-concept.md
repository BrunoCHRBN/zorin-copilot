# Conceito de Landing Page — Zorin Copilot

> Documento de definição para a futura divulgação pública do **Zorin Copilot**,
> o copiloto de IA nativo do desktop Linux (Zorin OS / GNOME).
> Acompanhado de um protótipo interativo em `docs/landing-prototype.html`.

---

## 1. Objetivo Principal

**Posicionar o Zorin Copilot como o primeiro copiloto de desktop "de verdade" do
ecossistema Linux** — um assistente que não apenas conversa, mas *age* no seu
computador por meio dos barramentos nativos (AT-SPI2, D-Bus, injeção de input),
com privacidade híbrida (modelos locais + nuvem).

A landing page tem três funções hierárquicas:

1. **Educar** — mostrar, com um demo visual, a diferença entre "um chatbot" e
   "um copiloto que controla a tela".
2. **Gerar confiança** — privacidade, código aberto (MIT), e o fato de o modelo
   local rodar 100% offline são diferenciais centrais de conversão.
3. **Capturar leads** — waitlist / instalação antecipada para a comunidade Zorin
   e usuários Linux em geral.

Call-to-action primário: **"Entre na lista de espera"** (ou "Baixe o AppImage").
Call-to-action secundário: **"Ver como funciona"** (rola para o demo).

---

## 2. Público-Alvo

| Segmento | Perfil | Por que se importa |
|---|---|---|
| **Usuários Zorin OS** | Donos de desktop Zorin 18 (GNOME/Wayland), muitos ex-macOS/Windows buscando UX polida no Linux. | É o "app nativo" que falta no ecossistema deles (estilo Raycast/Spotlight). |
| **Entusiastas de produtividade Linux** | Devs, sysadmins, estudantes que vivem no terminal e em atalhos. | Automação de janelas, ditado global, barra de comandos. |
| **Usuários preocupados com privacidade** | Quem não quer mandar a tela/área de transferência para a nuvem. | Modo local (Ollama/llama.cpp) e portão de sanitização de dados. |
| **Migrantes de macOS** | Quem usa Raycast/Alfred e quer equivalente no Linux. | "Command bar" global + agente supervisionado. |

Tom de voz do público: técnico o suficiente para credibilidade, mas acolhedor.
Evitar jargão de venda vazio.

---

## 3. Seções Essenciais

### 3.1 Hero
- **Headline:** "Seu desktop Linux, finalmente com um copiloto de verdade."
- **Subhead:** "O Zorin Copilot enxerga, entende e age na sua tela — abre apps,
  organiza janelas, preenche formulários e digita por você. Local quando você
  quiser, na nuvem quando precisar."
- **Elemento visual:** mockup estilizado da barra flutuante (estilo Spotlight)
  com animação de "digitação" mostrando um comando sendo executado.
- **CTAs:** primário (waitlist) + secundário (demo).
- **Prova social rápida:** "100% open source · Privacidade híbrida · Roda offline".

### 3.2 Funcionalidades
Grid de 6 cards (ícone + título + 1–2 linhas):
1. **Inspeção semântica da tela** (AT-SPI2) — lê botões/menus em tempo real.
2. **Barra de comandos estilo Spotlight** (`Super+Espaço`).
3. **Orquestração de janelas** — layouts, áreas de trabalho, foco por voz.
4. **Ditado global por voz** (Whisper local, pílula OSD não-intrusiva).
5. **Modo Agente Supervisionado** — etapas em tempo real, aprovação, parada de emergência.
6. **Privacidade híbrida** — Ollama local + nuvem com sanitização automática.

### 3.3 Benefícios (diferencial vs. assistentes convencionais)
Bloco comparativo "Chatbot comum vs. Zorin Copilot":
- Conversa só em texto → **Age na tela de verdade**.
- Precisa de OCR pesado → **Lê a árvore de acessibilidade nativa**.
- Envia tudo para a nuvem → **Modo local opcional, zero envio**.
- Sem desfazer → **Pilha de desfazer para ações reversíveis**.

### 3.4 Como funciona (3 passos)
1. Dispare o atalho global. 2. Peça em linguagem natural. 3. Aprove ou deixe o
agente executar com visualização passo a passo.

### 3.5 Depoimentos
3–4 cards de "usuários beta" (placeholder) com foto/avatar, nome, role e citação.
Slider ou grid. Ex.: "Substituiu meu Raycast + scripts de automação."

### 3.6 FAQ (acordeão)
- Precisa de GPU? / Funciona em outras distros? / Meus dados saem do PC? /
  É gratuito? / Tem versão CLI?

### 3.7 CTA final
Seção de fundo gradiente com headline de fechamento + formulário de waitlist
(email) ou botão de download do AppImage/.deb.

### 3.8 Footer
Links: GitHub, documentação, privacidade, licença MIT, redes.

---

## 4. Tom de Comunicação

- **Personalidade:** parceiro de bancada competente, ágil e empático — espelha o
  próprio system prompt do produto ("vá direto ao ponto como um colega").
- **Idioma:** Português do Brasil, coloquial mas preciso.
- **Regras:**
  - Sem clichês robóticos ("Certamente! Como seu assistente...").
  - Verbos de ação ("Abra", "Organize", "Dite") em vez de promessas vagas.
  - Transparência sobre limites (ex.: "comandos não têm desfazer").
  - Destacar privacidade como valor, não como advertência chata.

---

## 5. Identidade Visual — Anti-Slop (obrigatória)

Sujeita às **Diretrizes de Qualidade e Design (Anti-Slop)** de `docs/PROXIMOS_PASSOS.md`
(seção 3). A identidade visual **não é inventada** — ela deriva diretamente do sistema
real do app em `src/zorin_copilot/data/zorin-copilot.css`, garantindo que a landing
não pareça um produto diferente do aplicativo.

**Restrições absolutas:**
- **Sem gradientes roxo/indigo "de IA"** — nenhum `linear-gradient`/`radial-gradient` decorativo.
- **Sem emojis** — apenas ícones simbólicos Adwaita (SVG, traço 1.5, `currentColor`).
- **Design Nativo GNOME / Libadwaita (HIG)** — sólido, contornado, plano; sem "frosted glass" decorativo.
- **Números tabulares** (`font-variant-numeric: tabular-nums`) em métricas e rótulos numéricos.

| Token | Valor | Origem / uso |
|---|---|---|
| `--gray-50` | `#f7f8f9` | Fundo claro (`zc_gray_50`) |
| `--gray-100` | `#eceef0` | Superfície secundária |
| `--gray-400` | `#9aa1aa` | Texto terciário / avatares |
| `--gray-700` | `#3d454e` | Botão primário (claro) |
| `--gray-900` | `#171a1f` | Fundo escuro (`zc_gray_900`) |
| `--accent` | `#6b7280` | Acento grafite (`accent_color` do app) |
| `--success` | `#3d8b63` | Checks, confirmações (`zc_success`) |
| `--error` | `#c0392b` | Negativas, erros de form (`zc_error`) |
| `--border` | `#dde0e4` | Divisórias e contornos (`zc_gray_200`) |

- **Estrutura:** bordas de `1px`, raio de janela `18px` / cartões `12px`; elevação
  discreta (`0 6px 20px rgba(23,26,31,.08)`), nunca halos coloridos.
- **Tipografia:** fonte do sistema (Cantarell / Inter), títulos 700–800, corpo 400–500.
- **Tema claro/escuro:** nativo, alternável e seguindo `prefers-color-scheme` — espelha
  `window.light-glass` / `window.dark-glass` do app.
- **Movimento:** reveal on scroll discreto (IntersectionObserver) e a animação de
  digitação da barra de comandos no hero. Tudo respeita `prefers-reduced-motion`.
- **Responsivo:** mobile-first; grades de 3 → 2 → 1 coluna; nav comprime no mobile.

---

## 6. Notas para Implementação

- Protótipo em `landing-prototype.html` (HTML/CSS/JS puro, sem dependências externas,
  abre offline). Serve como referência de UX e identidade.
- Para produção: migrar para gerador estático (Astro/Vite) ou framework da equipe;
  substituir textos/depoimentos placeholder por conteúdo real; conectar o form de
  waitlist a um backend (ex.: Formspree, lista no GitHub, ou endpoint próprio).
- Garantir Lighthouse > 95 (sem libs pesadas), contraste AA e foco visível em todos
  os elementos interativos.
