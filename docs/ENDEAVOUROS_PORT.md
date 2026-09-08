# Zorin Copilot → EndeavourOS (Arch + Hyprland/Sway): Análise de Lacunas e Plano de Porte

**Repositório analisado:** `BrunoCHRBN/zorin-copilot` (público, branch `main`, HEAD `f77866d`)
**Alvo:** EndeavourOS, Arch Linux rolling release, compositor wlroots (Hyprland / Sway)
**Data da análise:** setembro de 2025

---

## 1. Resumo executivo

O Zorin Copilot é um projeto sério — 32 mil linhas, 114 arquivos Python, 50 módulos de teste, CI verde. A arquitetura em quatro camadas (percepção, execução, inteligência, interface) é limpa e o código tem decisões de design documentadas no topo de cada arquivo.

O problema não é qualidade: é que **"Zorin OS 18.1" virou uma premissa estrutural, não um parâmetro**. Ela está em três lugares distintos e cada um quebra de um jeito:

| Onde a premissa mora | Exemplo concreto | Consequência no EndeavourOS |
|---|---|---|
| **Instalação** | `setup.sh` chama `apt-get` e `dpkg -s` sob `set -euo pipefail` | Script aborta antes de criar o venv. Não instala. |
| **Declaração de dependências** | `pyproject.toml` lista `PyGObject`, `piper-tts`, `faster-whisper` como dependências obrigatórias | PEP 668 + Python 3.13 → `pip install -e .` falha. Não instala. |
| **Runtime** | Atalhos via `org.gnome.settings-daemon`, captura via `gnome-screenshot`, tema via `gsettings` | Mesmo instalado, os recursos centrais não funcionam — e boa parte falha em silêncio. |

A boa notícia: **nada disso exige reescrever o produto**. O acoplamento está concentrado em ~10 pontos, e a camada de IA (motor de intenções, provedores, RAG, memória) é ~95% independente de distribuição. O porte é cirúrgico.

**Veredito:** 5 bloqueadores, 9 problemas altos, 8 médios. Todos os bloqueadores têm correção direta. Este PR resolve os bloqueadores e os principais itens altos.

---

## 2. Bloqueadores (não instala / não abre)

### B1. `setup.sh` é Debian-only
`setup.sh:43-78` (HEAD `f77866d`) usa `dpkg -s gir1.2-ayatanaappindicator3-0.1` e `sudo apt install -y "${MISSING_DEPS[@]}"` com nomes de pacotes Debian (`poppler-utils`, `python3-venv`, `tesseract-ocr-por`). Como o script roda com `set -euo pipefail` (linha 8), o `dpkg -s` inexistente **derruba a execução antes de criar o ambiente virtual**.

> Já existia uma branch `chore/setup-multidistro` com 117 linhas de detecção apt/pacman/dnf — nunca mergeada. Este PR incorpora e estende aquele trabalho.

**Correção:** detecção de `/etc/os-release` + mapeamento capacidade → pacote por gerenciador, com AUR via `yay`/`paru`.

### B2. `PyGObject` como dependência pip
`pyproject.toml:24`. No Arch isso falha por dois motivos independentes: o Python é marcado `EXTERNALLY-MANAGED` (PEP 668), e o PyGObject do pip não traz as typelibs (`gir1.2-gtk-4.0`, `gir1.2-adw-1`) que precisam vir do `pacman`.

**Correção:** sai de `dependencies`, vira nota de instalação por distro. O `venv --system-site-packages` (que o script já usa) continua sendo o mecanismo correto.

### B3. `piper-tts` + `faster-whisper` como dependências obrigatórias
`pyproject.toml:28-29`. Puxam `onnxruntime` e `ctranslate2` — binários nativos pesados, sem wheel confiável para CPython 3.13. No Arch, `piper-tts` não tem pacote oficial. Uma falha aqui derruba a instalação inteira, mesmo para quem nunca vai usar voz offline.

**Correção:** extra opcional `[voice]`. Sem ele, a voz local fica indisponível com mensagem clara; o resto do app funciona.

### B4. Atalho global é structuralmente GNOME-only
`core/shortcuts.py:66-74` grava em `org.gnome.settings-daemon.plugins.media-keys`. Em Hyprland/Sway o schema não existe; `_media_keys_schema_exists()` devolve `False` e `register()` retorna `False` — mas `app.py:1349-1358` envolve a chamada em `except Exception: pass`, então **o usuário nunca é avisado**. O `Super+C` simplesmente não existe.

**Correção:** backends por compositor (GNOME media-keys, `hyprland.conf`, `sway config`, `kglobalshortcutsrc`).

### B5. Captura de tela depende de um portal que o wlroots não traz por padrão
`core/vision.py` falava direto com `org.freedesktop.portal.Screenshot`. Em Hyprland isso só responde com `xdg-desktop-portal-hyprland` instalado **e** configurado; sem ele, `bus.call_sync` estoura e o usuário vê "Falha no portal de screenshot" — uma mensagem que não diz o que fazer.

**Correção:** cadeia de backends — `grimblast` / `grim+slurp` em wlroots (nativos, sem serviço extra), `spectacle` no KDE, portal como rede de segurança.

---

## 3. Problemas altos (instala, mas recursos centrais não funcionam)

| # | Problema | Onde | Impacto no Hyprland/Sway |
|---|---|---|---|
| A1 | Autostart XDG é ignorado pelo compositor | `core/shortcuts.py:249-320` | Hyprland/Sway **não leem** `~/.config/autostart`. O app nunca sobe sozinho. |
| A2 | Bandeja quebrada por bug de import | `ui/tray.py:23` | `from gi.repository import importlib` — `importlib` é stdlib, não namespace GI. Lança `ImportError`, engolido por `except Exception: pass` na linha 27. **A bandeja nunca funcionou em distro nenhuma.** |
| A3 | Modo `--background` não cria bandeja | `ui/app.py:1394-1396` | Retorna `0` sem instanciar `SystemTrayIndicator`. Sem atalho + sem bandeja = app inacessível. |
| A4 | Bandeja usa XEmbed | `ui/tray.py:21` | `gi.require_version("Gtk","3.0")` + AppIndicator = XEmbed, que não existe em Wayland. Precisa de StatusNotifierItem. |
| A5 | Controles de sistema GNOME-only | `shell/system.py:20,32,87` | `gsettings org.gnome.desktop.interface`, `org.gnome.settings-daemon.plugins.color`, `gnome-screenshot -i` (removido no GNOME 43+). Tudo falha em silêncio. |
| A6 | Prompt de sistema mente para o modelo | `core/config.py:99-103`, `ai/providers.py:22`, `ai/live.py:874` | O modelo recebe "você está no Zorin OS 18 Core (GNOME 46 no Wayland)". Vai sugerir `apt`, `nautilus`, `gnome-terminal`. **Erro de conteúdo, não só estético.** |
| A7 | Pílula de voz imprestável em Wayland | `ui/voice_pill.py:965-1009` | `Window.move()` é no-op em Wayland; `set_keep_above` idem. E `_monitor_under_pointer` (linhas 992-999) tem um **laço morto** que não faz nada e retorna `monitors[0]` incondicionalmente — a detecção de monitor nunca funcionou. |
| A8 | Detecção de ambiente é cosmética | `core/memory.py:365-370`, `446-447` | Lê `PRETTY_NAME` corretamente, mas os *defaults* continuam `"Zorin OS 18"` e `"GNOME"`, e nada no código consome o valor para decidir comportamento. |
| A9 | Sem empacotamento Arch | — | Nenhum `PKGBUILD`, nenhum `.desktop` instalado, sem ícone. Um app de bandeja precisa disso. |

---

## 4. Problemas médios

| # | Problema | Onde | Nota |
|---|---|---|---|
| M1 | Aliases de app só GNOME | `core/apps.py:17-37` | `"ajustes"` → `zorin-appearance`; `"arquivos"` → `nautilus`. Faltam `dolphin`, `thunar`, `kitty`, `konsole`. |
| M2 | Red zones da cerca com medidas fixas | `core/fence.py:125-149` | Barra de tarefas de 48px + top bar de 32px assumidas para todo monitor. No Hyprland sem barra, 80px de tela ficam indevidamente bloqueados para clique. |
| M3 | Prompt de sistema sem fallback | `core/config.py:99-103` | Se o usuário customizou `system_prompt`, o override vence e o ambiente real nunca é injetado. |
| M4 | Caminho de tema hardcoded | `ui/style.py:34` | `/usr/share/zorin-copilot/` — ausente no Arch (cai no tema embutido, comportamento correto, mas merece checar `sys.prefix`). |
| M5 | Binário do venv em caminho fixo | `core/shortcuts.py:84` | `~/.local/share/zorin-copilot/venv/bin/zorin-copilot` — quebra se instalado via `PKGBUILD` no sistema. |
| M6 | `wpctl` sem fallback | `shell/system.py:49-69` | Só tenta `wpctl`. Sem `wireplumber` (apenas `pipewire-pulse`), volume para de funcionar. `@DEFAULT_AUDIO_SINK@` também é fixo. |
| M7 | Classificadores do PyPI desatualizados | `pyproject.toml:17` | `Environment :: X11 Applications :: Gnome` desencoraja empacotamento fora do GNOME. |
| M8 | `.gitignore` engolia `core/` | `.gitignore:40` | Regra `core` (para core dumps) sem âncora ignorava **qualquer diretório chamado core** — inclusive `src/zorin_copilot/core/desktop/`. Arquivos novos simplesmente não apareciam no `git status`. |

---

## 5. O que já funciona (não mexer)

Vale registrar o que **não** precisa de porte — é a maior parte do sistema:

- **AT-SPI2** (`core/a11y.py`): protocolo freedesktop, funciona em qualquer compositor com `at-spi2-core` rodando. Inclui a fusão vídeo + árvore semântica (`element_at_point`).
- **MPRIS2** (`core/media.py`): D-Bus padrão, com fallback para `dbus-send`.
- **Área de transferência** (`core/clipboard.py`): `wl-copy`/`wl-paste` → `xclip`. Correto nos dois servidores gráficos.
- **Descoberta de apps** (`core/apps.py`): via `Gio.AppInfo`, sem lista hardcoded.
- **Degradação de opcionais**: `vision.py` (Pillow), `live.py` (websockets), `local_voice.py` (numpy/piper/whisper) — todos com `try/except` honesto.
- **Cerca espacial** (`core/fence.py`): matematicamente sólida, com kill switch.

---

## 6. O que este PR implementa

### Nova camada de abstração — `core/desktop/`

| Módulo | Responsabilidade |
|---|---|
| `env.py` | Detecção pura (sem `gi`, sem D-Bus, sem disco obrigatório) de distro, gerenciador de pacotes, sessão, compositor e inventário de binários. Testável por injeção de `environ`. |
| `shortcuts.py` | Backends GNOME / Hyprland / Sway / KDE atrás de um contrato único, com conversão de acelerador (`<Super><Shift>s` → `SUPER SHIFT, s` / `Mod4+Shift+s` / `Meta+Shift+S`). |
| `screenshot.py` | Backends `grimblast`, `grim+slurp`, `spectacle`, portal XDG, ImageMagick — com preferência por wlroots quando detectado. |
| `tray.py` | StatusNotifierItem puro D-Bus. Sem GTK3, sem XEmbed. |
| `controls.py` | Tema, bloqueio de tela, volume e notificação por estratégia, com mensagem de pacote faltante. |
| `autostart.py` | XDG **e** `exec-once`/`exec` no compositor — porque wlroots não lê `~/.config/autostart`. |

### Correções

- **B1–B3**: `setup.sh` multi-distro (pacman + AUR); `pyproject.toml` com `PyGObject` fora das dependências e extra `[voice]`.
- **B4–B5**: `ShortcutManager` e `ScreenCaptureService` viram fachadas que despacham para o backend do ambiente.
- **A1–A4**: autostart duplo; bug de import do tray corrigido; `SystemTrayIndicator` reescrito (SNI primeiro, AppIndicator só em X11); `--background` agora publica a bandeja.
- **A5**: `SystemController` delegando para `controls.py`.
- **A6**: `build_system_prompt()` injeta o ambiente real ("EndeavourOS (Linux / Hyprland no Wayland)").
- **A9**: `PKGBUILD` + `.desktop` instalado.
- **M8**: `.gitignore` ancorado.

### Testes e CI

- `tests/test_desktop_portability.py` — 36 testes: detecção, conversão de aceleradores, seleção de backend, idempotência do snippet do Hyprland (não duplica bloco, preserva o config do usuário, faz backup), autostart duplo, prompt dinâmico.
- `tests/test_shortcuts.py` e `tests/test_local_voice.py` reescritos: antes fixavam o caminho interno do GNOME (`_register_binding` com path de media-keys), agora testam o **contrato** (slot, acelerador, flag).
- CI com dois jobs novos em container `archlinux:latest`: suíte completa e validação do `setup.sh` sem `dpkg`.

---

## 7. Instalando hoje no seu EndeavourOS (Hyprland)

```bash
# 1. Dependências do sistema
sudo pacman -S --needed python python-pip python-gobject gtk4 libadwaita \
    poppler grim slurp wl-clipboard libnotify wireplumber

# 2. Portal XDG (necessário para captura via portal, e para o app em geral)
sudo pacman -S --needed xdg-desktop-portal xdg-desktop-portal-hyprland   # ou -wlr no Sway

# 3. Injeção de input (opcional, para cliques físicos)
yay -S ydotool && sudo usermod -aG input "$USER"   # requer relogin

# 4. O projeto
git clone https://github.com/BrunoCHRBN/zorin-copilot.git
cd zorin-copilot
./setup.sh
```

O setup grava os atalhos em `~/.config/hypr/zorin-copilot.conf` e acrescenta o `source` ao seu `hyprland.conf` (com backup `.bak-copilot`). Recarregue com `hyprctl reload`.

Para ícone na bandeja, adicione o módulo `tray` à sua waybar.

**Voz offline** (opcional, é onde moram os binários nativos pesados):

```bash
~/.local/share/zorin-copilot/venv/bin/pip install -e ".[voice]"
```

---

## 8. Segunda leva: itens do roadmap já implementados

| Item | Onde | O que mudou |
|---|---|---|
| Versões mínimas de GTK/Adw | `ui/gi_versions.py` (novo) | Verificação em **runtime** com erro acionável. Ver a nota importante abaixo. |
| `gtk4-layer-shell` na pílula | `ui/layer_shell.py` (novo) | Ancoragem por `wlr-layer-shell` com degradação explícita quando ausente. |
| Laço morto em `_monitor_under_pointer` | `ui/voice_pill.py` | Substituído por detecção real (superfície sob o ponteiro → `get_monitor_at_surface` → casamento por modelo/descrição/geometria). |
| Menu D-Bus na bandeja | `core/desktop/menu.py` (novo) | `com.canonical.dbusmenu` completo: layout, propriedades, `clicked`, `LayoutUpdated`. |
| Red zones por ambiente | `core/fence.py` + `core/config.py` | `default_insets()` por ambiente, `red_zone_*_px` no config, `set_insets()`. |
| Backend GlobalShortcuts portal | `core/desktop/shortcuts.py` | `GlobalShortcutsPortalBackend`, último antes do nulo. |
| Aliases de app | `core/apps.py` | 26 entradas, com reordenação por ambiente (`ordered_aliases`). |
| CI em compositor real | `.github/workflows/ci.yml` | Job `sway-arch`: sobe Sway headless e valida detecção e backends. |

### A armadilha do `require_version`

A primeira tentativa de fixar versões foi `gi.require_version("Gtk", "4.10")`. **Isso está errado e nunca funcionaria**: o namespace do GI é congelado pela API major — GTK 4.6 e GTK 4.18 publicam o mesmo `Gtk-4.0.typelib`, e libadwaita 1.1 e 1.7 publicam o mesmo `Adw-1.typelib`. `require_version("Gtk","4.10")` levanta `ValueError` até na máquina mais nova.

A versão real só é conhecida depois de carregar o namespace:

```python
gi.require_version("Gtk", "4.0")          # namespace: sempre 4.0
from gi.repository import Gtk
Gtk.get_minor_version()                    # runtime: 4.14, 4.6, ...
```

É isso que `require_gtk4()` faz. Quando o sistema é antigo, ela levanta `ToolkitTooOld` com o comando de instalação da distro detectada. `ZORIN_COPILOT_ALLOW_OLD_TOOLKIT=1` troca o erro fatal por aviso, para quem está depurando num LTS antigo.

### AppIndicator saiu de vez

O caminho AppIndicator3/Ayatana foi removido, não apenas preterido. Ele exige `gi.require_version("Gtk","3.0")`, e o PyGObject recusa duas versões do mesmo namespace no mesmo processo — incompatível com o Gtk4 que o resto do app usa. Como ele nunca funcionou (o bug do `importlib`) e o SNI também cobre X11, mantê-lo só custava. `setup.sh` e o `PKGBUILD` deixaram de puxar `libayatana-appindicator`.

---

## 9. Observações sobre a validação

**O segfault era AT-SPI, não GTK.** O crash que aparecia em `tests/test_continuous_vision.py` (`Trace/breakpoint trap`) tinha uma causa identificável: sem um barramento de acessibilidade, o GTK emite

```
dbind-ERROR: AT-SPI: Couldn't connect to accessibility bus
```

e **aborta** o processo. Não era bug do projeto nem versão de GTK — era o ambiente de teste sem `org.a11y.Bus`. Confirmado reproduzindo no checkout limpo de `main`.

Para rodar a suíte completa num container/CI sem sessão gráfica:

```bash
xvfb-run -a dbus-run-session -- bash -c '
  /usr/libexec/at-spi-bus-launcher --launch-immediately &
  sleep 2
  python -m pytest -q
'
```

**A suíte também travava, por um segundo motivo.** `run_loop_until()` em `tests/test_ui_refactor.py` drenava o contexto com `while context.pending(): context.iteration(False)`. `pending()` responde "existe source registrado", não "existe algo despachável": uma conexão D-Bus ociosa mantém `pending()` verdadeiro para sempre, e `iteration(False)` não a consome — loop infinito. O `while` ganhou teto (`_drain_ready`). Com isso a suíte passou de "trava em 81%" para **completa em ~17 s**.

**A última falha era inanição de prioridade no `GMainContext` — e era bug de produção.** `test_submit_renders_assistant_response` falhava no fim da suíte e passava isolado. Não era "sensibilidade do harness": era o `GMainContext` passando fome.

O `GMainContext` só despacha uma fonte quando **não existe nenhuma de prioridade mais alta pronta**. `GLib.idle_add` roda em `G_PRIORITY_DEFAULT_IDLE` (**200**) — e é por ele que o app entrega a resposta da IA à interface (`prompt_bar` chama `GLib.idle_add(ctx._on_plan_ready, ...)` da thread de parse). Basta que sobre uma fonte periódica em `G_PRIORITY_DEFAULT` (**0**) ou o frame clock do GDK (**100**) para que **nenhum** idle rode nunca mais.

Sonda que resolveu o mistério (idles de várias prioridades, despachando o contexto à mão):

```
HIGH(-100)      -> rodou
DEFAULT(0)      -> rodou
HIGH_IDLE(100)  -> rodou
DEFAULT_IDLE(200) -> NÃO rodou   ← inclusive o _on_plan_ready do app
```

De onde vinham as fontes: a suíte constrói um `CopilotWindow` por teste e nunca os destruía, e dois timers periódicos vazavam por janela — o tick da barra de status (5 s) e a animação do indicador "digitando" (300 ms). Depois de algumas centenas de janelas acumuladas, sempre havia uma fonte pronta acima de 200 e o app parava de renderizar respostas **em produção também**, não só em teste:

- `TypingIndicator` criava o timer no `__init__` e só o removia no `unrealize` — indicador nunca exibido, fonte viva para sempre.
- `StatusBarWidget` só "se cancelava" devolvendo `SOURCE_REMOVE` na passada seguinte: a fonte sobrevivia ao fechamento por até um intervalo inteiro, e para sempre se a janela nunca fosse destruída (o `destroy` do GTK4 **não emite o sinal** para janela nunca apresentada).

Correções:

1. Os dois timers passaram a seguir a visibilidade: ligam no `map`, desligam no `unmap`, e usam `G_PRIORITY_LOW` (300) — abaixo do idle, portanto incapazes de passar fome.
2. `tests/conftest.py` ganhou uma fixture `autouse` que destrói as janelas deixadas vivas por cada teste.

Resultado atual: **828 passando, 0 falhando**. `test_setup_shortcut` (que falhava por não mockar o terceiro slot de atalho) foi corrigido junto.

Outras notas:

- **O job do Sway falhava por causa de uma file capability.** `nohup sway` devolvia `Operation not permitted` — e o detalhe é que o erro vinha do `execve`, antes de o sway rodar: o pacote do Arch entrega `/usr/bin/sway` com `cap_sys_nice=ep`, e o container não tem essa capability no bounding set. `setcap -r /usr/bin/sway` resolve. Reproduzido fora do CI, num `docker run archlinux:latest` comum.
- **`pycairo` é dependência só de teste.** Nada em `src/` importa `cairo`; um único teste de renderização do visualizador de voz usa. No Arch, `python-gobject` não o traz, então a suíte quebrava inteira lá. O CI instala `python-cairo` e o teste agora pula com aviso claro se o módulo faltar — dependência de teste não deve derrubar a suíte.
- **Não assuma `wayland-0`.** O wlroots numera o socket pelo primeiro número livre; em container limpo o sway subiu como `wayland-1`. O job agora descobre o socket por glob e exporta também `SWAYSOCK` (o marcador que o projeto usa para reconhecer o Sway).
- Os 16 módulos de UI e 13 arquivos de teste agora chamam `require_gtk4()` em vez de espalhar `require_version` — se o requisito mudar, muda em um lugar.
- `ZORIN_COPILOT_ALLOW_OLD_TOOLKIT=1` existe para desenvolvimento, não para produção.
- Recomendo rodar a suíte no Arch real antes de mergear — é lá que a história de PyGObject/GTK rola primeiro.

---

## 10. O que ainda falta

| Prioridade | Item | Por quê |
|---|---|---|
| Média | `icon-data` no dbusmenu | Ícones por nome têmático dependem do tema da barra; enviar pixels resolve. |
| Média | Configurar red zones pela UI | Hoje só por `CopilotConfig` (`red_zone_bottom_px` / `red_zone_top_px`). |
| Baixa | CI em KDE Plasma real | Plasma Wayland em container é mais frágil que Sway; segue manual. |
| Baixa | Menus dinâmicos no dbusmenu | `AboutToShow` responde `False`: a árvore é estática, o que basta hoje. |
