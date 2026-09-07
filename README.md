# Zorin Copilot

Assistente de Inteligência Artificial integrado nativamente ao desktop **Zorin OS 18** (GNOME 46 / Wayland).

Diferente de assistentes convencionais que apenas respondem perguntas em chat ou tiram prints lentos da tela, o **Zorin Copilot** interage de verdade com os elementos do seu desktop por meio dos barramentos nativos do Linux (**AT-SPI2**, **D-Bus** e extensões do compositor).

```
GUI : zorin-copilot
CLI : zorin-copilot-cli
```

---

## 🎯 Capacidades Principais

* **Inspeção Semântica da Tela (AT-SPI2):** Lê botões, menus, caixas de texto e janelas em tempo real sem depender de OCR pesado.
* **Orquestração de Janelas & Multitarefa:** Organiza layouts, move janelas para áreas de trabalho e alterna o foco por comando de voz ou texto.
* **Automação de Ações no Desktop:** Preenchimento de formulários, cliques guiados em botões da interface e atalhos de produtividade.
* **Barra de Comandos Estilo Spotlight:** Interface minimalista em GTK4 / Libadwaita acessível globalmente via atalho de teclado (`Super + Espaço`).
* **Privacidade Híbrida:** Suporte a modelos locais (via Ollama / llama.cpp) e APIs em nuvem com sanitização automática de dados confidenciais.

---

## 🏗️ Arquitetura

Veja detalhes completos em [ARCHITECTURE.md](ARCHITECTURE.md).

```
┌─────────────────────────────────────────────────────────────┐
│                    Zorin Copilot UI                         │
│           (GTK4 / Libadwaita - Barra Flutuante)             │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
               ▼                               ▼
    [Núcleo de Percepção]             [Motor de Execução]
    • AT-SPI2 (Árvore de A11y)        • D-Bus Bridge (Mutter/Shell)
    • D-Bus Active Window Monitor     • Extensão GNOME Shell
    • PipeWire Audio Monitor          • Synthesizer (libei / ydotool)
               │                               │
               └───────────────┬───────────────┘
                               │
                               ▼
                    [Camada de Inteligência]
                    • Provedor Local (Ollama)
                    • Provedor Nuvem (Gemini / Claude)
                    • Intent Parser & Action Schema
```

---

## 🚀 Instalação e Desenvolvimento

O Zorin Copilot utiliza o runtime nativo do Zorin OS (Python 3.12, PyGObject, AT-SPI2 e GTK4):

```bash
# Clone ou acesse a pasta do projeto
cd ~/zorin-copilot

# Testes automatizados do núcleo
python3 -m unittest discover -s tests
```

---

## ⌨️ Atalhos

| Atalho | Ação |
|---|---|
| `Ctrl+K` | Painel de comandos (busca difusa sobre tudo que o app faz) |
| `Ctrl+S` | Exportar a conversa como Markdown |
| `Ctrl+Z` | Desfazer a última ação reversível |
| `Ctrl+N` | Nova conversa |
| `Ctrl+H` | Mostrar/ocultar a barra de conversas |
| `Ctrl+P` | Fixar a conversa atual no topo |
| `Ctrl+M` | Conversa por voz ao vivo |
| `Ctrl+Q` | Sair |

O painel de comandos também tem um botão de lupa no canto superior esquerdo.
Com o foco num campo de texto, `Ctrl+K` e `Ctrl+Z` continuam sendo os atalhos de
edição do GTK — o app não rouba a combinação.

---

## ↩️ Desfazendo ações

O Copilot executa ações no desktop, e parte delas mexe nos seus arquivos. Essas
têm volta: depois de executar, aparece um toast com **Desfazer**, e o `Ctrl+Z`
(ou o painel de comandos) desfaz a última ação reversível a qualquer momento —
as últimas 5 ficam na pilha.

| Ação | Desfazer |
|---|---|
| Salvar/escrever arquivo | Restaura o conteúdo anterior; se o arquivo foi criado pela ação, é removido. Em modo *append*, volta ao tamanho anterior. |
| Organizar pasta | Devolve cada arquivo à pasta de origem e remove as pastas de categoria que ficaram vazias. |

**O que não entra na pilha, e por que:** clique, digitação, abrir aplicativo,
abrir URL, controle de mídia e execução de comando mexem em estado de terceiros.
Prometer desfazer isso seria uma promessa falsa — é melhor não oferecer do que
oferecer e falhar. A linha da ação mostra **Desfeito ↩** quando você desfaz,
para não ficar marcada como “Executado”.

Casos em que o desfazer avisa em vez de prometer:

- o arquivo foi movido ou apagado por você depois da ação (a mensagem diz quais
  não foram encontrados e quantos voltaram);
- o arquivo original era binário, ou maior que 1 MB — restaurar por texto
  corromperia o conteúdo, então a ação acontece sem snapshot.

---

## 📎 Anexando arquivos ao chat

Arraste um arquivo do gerenciador de arquivos para dentro da janela e solte.

| Tipo | O que acontece |
|---|---|
| Imagem (PNG, JPEG, WebP, GIF…) | Vai para o canal multimodal, no mesmo slot da captura de tela — com miniatura acima da barra de prompt. Uma imagem por vez. |
| PDF | Texto extraído via `pdftotext` (poppler-utils) e injetado como contexto. |
| Texto e código (`.txt`, `.md`, `.csv`, `.json`, `.py`, `.html`…) | Conteúdo lido e injetado como contexto. |

Cada anexo de texto/PDF ganha um chip removível acima da barra de prompt. Enquanto
o chip estiver lá, o conteúdo acompanha as próximas mensagens — é o mesmo critério
da visão contínua. Para tirar o contexto, use o **x** do chip ou “Remover anexos do
chat” no `Ctrl+K`.

Detalhes que importam:

- **Limites:** 12.000 caracteres por arquivo (o excedente é truncado e marcado no chip) e 8 MB por imagem.
- **O conteúdo do arquivo não vira instrução.** Ele entra no prompt entre delimitadores (`----- início de notas.txt -----`) e cercado por um aviso explícito de que é material de leitura.
- **A bolha do usuário não incha.** O que vai para o histórico é só o que você digitou; o contexto dos anexos é montado na hora de falar com o modelo.
- **Soltar sem digitar nada** envia “Resuma e explique o conteúdo anexado.”.
- Pastas, binários e formatos sem leitura possível são recusados com o motivo no toast — e o mesmo arquivo não é anexado duas vezes.

---

## 📤 Exportando uma conversa

`Ctrl+S` (ou "Exportar conversa (.md)" no painel de comandos) salva a conversa atual
em Markdown, com frontmatter YAML pronto para Obsidian, Docusaurus ou Jekyll:

```markdown
---
title: "Como listar arquivos grandes?"
source: "zorin-copilot"
topic_id: "2a9650a7681e"
provider: "gemini"
model: "gemini-flash-latest"
turn_count: 2
created_at: "2025-03-12T09:05:00"
updated_at: "2025-03-12T14:22:10"
exported_at: "2025-03-12T14:30:00"
---

# Como listar arquivos grandes?

> Exportado do Zorin Copilot em 12/03/2025 14:30 · gemini · gemini-flash-latest

## Você · 12/03/2025 09:05

como listar arquivos grandes?

## Copilot

Use `find`: …

---
```

As respostas são gravadas literalmente — blocos de código, tabelas e links chegam
intactos ao arquivo. O nome sugerido é `<titulo>-<AAAA-MM-DD>.md`, com acentos
removidos para evitar problemas ao compartilhar.

---

## 🎨 Personalizando a aparência

O tema glassmorphism fica em `src/zorin_copilot/data/zorin-copilot.css`. Não é preciso
mexer nele: dá para sobrescrever qualquer regra sem tocar no código.

Cada item abaixo vence o anterior:

| Ordem | Arquivo | Para que serve |
|---|---|---|
| 1 | `data/zorin-copilot.css` | tema embutido no pacote |
| 2 | `/usr/share/zorin-copilot/zorin-copilot.css` | ajuste feito pela distribuição |
| 3 | `~/.config/zorin-copilot/themes/*.css` | temas instalados, aplicados em ordem alfabética |
| 4 | `~/.config/zorin-copilot/user.css` | override final do usuário |

Exemplo de `~/.config/zorin-copilot/user.css`:

```css
/* Deixa o vidro mais opaco e troca a cor de destaque */
window.glass-window > contents {
    background-color: rgba(20, 24, 32, 0.99);
}

@define-color accent_color #7d5fff;
```

O diretório de configuração respeita `XDG_CONFIG_HOME`. CSS inválido é ignorado com um
aviso no log — o tema embutido continua sendo aplicado.

---

## 📄 Licença

MIT License.
