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
| `Ctrl+N` | Nova conversa |
| `Ctrl+H` | Mostrar/ocultar a barra de conversas |
| `Ctrl+P` | Fixar a conversa atual no topo |
| `Ctrl+M` | Conversa por voz ao vivo |
| `Ctrl+Q` | Sair |

O painel de comandos também tem um botão de lupa no canto superior esquerdo.
Com o foco num campo de texto, `Ctrl+K` continua sendo o atalho de edição do GTK
(apagar até o fim da linha) — o app não rouba a combinação.

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
