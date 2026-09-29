# Zorin Copilot

[![CI](https://github.com/BrunoCHRBN/zorin-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/BrunoCHRBN/zorin-copilot/actions/workflows/ci.yml)

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
* **Modo Agente Supervisionado (GUI & CLI):** Execução autônoma de tarefas multi-etapa no desktop com visualização de etapas em tempo real (*stepper*), aprovação interativa para ações sensíveis e parada de emergência imediata.
* **Ditado Global por Voz (Voice Typing):** Fale em qualquer aplicativo em foco (LibreOffice, navegadores, terminal, editores de código) com o atalho `<Super><Shift>D` ou `--dictate`. Pílula flutuante OSD não-intrusiva que preserva 100% do foco, VAD com sensibilidade de silêncio, transcrição Whisper local (CPU INT8) e injeção automática de texto no cursor via drivers de entrada virtual.
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
.venv/bin/python -m pytest -q
```

> **Use `pytest`, não `unittest`.** A suíte tem as duas famílias de teste (classes
> `unittest.TestCase` **e** funções no estilo pytest). O `python -m unittest` coleta
> **só a primeira**: os outros 14 módulos saem com `Ran 0 tests` e código **5**.
> Até 2026-09-21 isso significava **435 testes que passavam e nunca rodavam** — o
> `tests/test_study_tutor.py`, que cobre a injeção do addon do modo tutor no prompt,
> era um deles. `exit 5` significa **"não rodou"**, não "verde por outro caminho".
> O `pytest` está no `.venv` (extra `[test]`); o `python3` do sistema **não** o tem.

O `tests/__init__.py` e o `tests/conftest.py` ativam o mesmo isolamento de ambiente
por caminhos diferentes — `unittest` importa o primeiro, `pytest` carrega o segundo.
É ele que redireciona `XDG_CONFIG_HOME`/`XDG_DATA_HOME`, define `ZORIN_TEST_MODE` e
bloqueia os serviços pesados (MCP, indexação RAG em disco, wake word). Sem ele a
suíte roda contra o `config.json` e o `memory.db` **reais do usuário** e sobe um
processo de servidor MCP por janela criada, o que esgota a memória da máquina.
Detalhes em `tests/_sandbox.py`.

A suíte completa (inclui os testes de interface) roda sob um servidor gráfico
virtual — é exatamente o que o CI faz:

```bash
xvfb-run -a .venv/bin/python -m pytest -q
```

> **Nesta máquina o `xvfb-run` não está instalado** (verificado em 2026-09-21), então
> esse comando é o do CI, não o local. Localmente há `DISPLAY=:0`, e é por isso que o
> caminho recomendado aqui é o runner com guarda abaixo — ele roda um módulo por
> processo e não depende de servidor gráfico virtual.

**Para a suíte inteira com guarda de memória**, use o runner que roda um módulo por
processo e aborta quem passar de 900 MB (esta máquina **não tem swap**):

```bash
.venv/bin/python ~/.workbuddy-ai/skills/zorin-copilot-gui-change/scripts/run_tests_safe.py \
    --python .venv/bin/python
```

Baseline: **147 módulos, 0 falhas**.

> **Por que o isolamento e por que os testes travavam:** o diagnóstico completo
> (duas falhas independentes — estouro de memória e um `wl-copy` órfão segurando
> o pipe de saída) está em [`docs/ESTABILIDADE_TESTES.md`](docs/ESTABILIDADE_TESTES.md).

Os testes de interface precisam das typelibs do sistema (`gir1.2-gtk-4.0`,
`gir1.2-adw-1`). Pillow é opcional em runtime, mas obrigatória para rodar a
suíte: `pip install -e ".[test]"`.

### Rodando um subconjunto

Prefira um arquivo por processo. Um arquivo de UI cria dezenas de janelas e,
mesmo com o isolamento, a memória só volta de fato quando o processo termina:

```bash
.venv/bin/python -m pytest -q tests/test_ui_refactor.py
.venv/bin/python -m pytest -q tests/test_ui_reform.py tests/test_sidebar_grouping.py
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

Redefinir `@accent_color` é suficiente para retintar a interface inteira: botão
de envio, bordas de foco, chips e o item ativo da barra lateral leem a variável,
não a cor literal. Uma linha troca tudo.

O diretório de configuração respeita `XDG_CONFIG_HOME`. CSS inválido é ignorado com um
aviso no log — o tema embutido continua sendo aplicado.

---

## 📄 Licença

MIT License.
