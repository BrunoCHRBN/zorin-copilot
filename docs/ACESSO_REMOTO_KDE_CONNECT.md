# Acesso Remoto via KDE Connect — celular como cliente

> **Status (21/09/2026): análise e desenho. Nada implementado.**
> Continua `docs/ACESSO_REMOTO.md`. Aquele documento conclui que o relógio é um
> periférico BLE do celular e que o transporte é o que falta. Este documento
> responde: **o transporte já está instalado** — é o KDE Connect.
>
> **Método:** em vez de ler documentação (os wikis da KDE bloqueiam acesso
> automatizado), o levantamento foi feito **na máquina**: `kdeconnectd` rodando no
> bus de sessão, enumeração dos plugins instalados em
> `/usr/lib/qt6/plugins/kdeconnect/`, `strings` sobre cada `.so` para extrair os
> nomes reais das interfaces D-Bus, e leitura do **código-fonte** dos dois plugins
> que sustentam o desenho (`runcommand`, `sendnotifications`). Cada afirmação
> abaixo diz de onde veio.

---

## 0. Sumário executivo

**Sim, e sem escrever um servidor.** O KDE Connect dá transporte cifrado,
emparelhamento por certificado e um canal de **ida e volta** que já existe — não é
preciso criar porta, token, TLS nem fila.

O achado que decide o desenho: o plugin **`runcommand` devolve `stdout`/`stderr`
ao celular** (`kdeconnect.runcommand.output`). Isso transforma uma lista de
"comandos remotos" num **request/response de verdade**: o celular toca um verbo, o
desktop executa o CLI headless do Copilot, e a resposta aparece no celular.

O segundo achado, este **negativo**, também decide: no plugin
`sendnotifications`, **as ações da notificação e o hint `inline-reply` não são
repassados** ao celular — o pacote enviado carrega só `id`, `appName`, `ticker`,
`isClearable`, `title`, `text`, `silent`. Logo **não existe "botão Aprovar" na
notificação**. A notificação serve para **ler**; a resposta volta pelo canal de
comando. Melhor descobrir isso agora do que depois de implementar.

**E o relógio entra de graça:** as notificações que chegam ao celular são
espelhadas pelo Mi Fitness para o Redmi Watch 5. Leitura no pulso sai por
acréscimo; interação continua fora de alcance.

**Ressalva de plataforma:** a abordagem pressupõe **Android**. No iOS o app
oficial existe, mas o próprio repositório do projeto declara que ele é
*"unresponsive in the background... more or less intended"* e que a sincronização
de notificações não funciona, porque apps iOS não acessam notificações de outros
apps. Sem app em segundo plano, não há canal de fundo.

---

## 1. O que está instalado e rodando (verificado)

| Item | Estado | Evidência |
|---|---|---|
| Daemon | **Rodando** | `kdeconnectd` (PID 6891) dono de `org.kde.kdeconnect` e `org.kde.kdeconnect.daemon` no bus de sessão |
| Versão | **26.08.1** | `kdeconnect-cli --version` |
| Nome anunciado | `PC-do-BRUNO` | `daemon.announcedName` |
| Dispositivos emparelhados | **nenhum** | `daemon.devices` → `[]`; `~/.config/kdeconnect/` tem só `certificate.pem`, `privateKey.pem` e `config` |
| Link providers | `LAN` **habilitado**; `Bluetooth (beta)` **desabilitado** | `daemon.linkProviders` |
| Adaptador Bluetooth | **ausente** | `/sys/class/bluetooth/` vazio, `bluetoothctl list` vazio (módulo `bluetooth` carregado, `bluetoothd` ativo) |
| Plugins instalados | **31** | `ls /usr/lib/qt6/plugins/kdeconnect/` |

**Consequência imediata:** o caminho Bluetooth do KDE Connect (que dispensaria a
mesma rede) **não é utilizável nesta máquina** — o software suporta, o hardware
não está lá. Isso vale registrar: se um dia houver adaptador, é uma alternativa
real ao "mesma rede", mas hoje não conta.

---

## 2. O que cada plugin oferece (nome real da interface D-Bus)

Extraído com `strings` sobre os `.so` instalados — são os nomes que o código
precisa usar, não aproximações.

| Plugin | Direção | O que dá | Interface / evidência |
|---|---|---|---|
| **`runcommand`** | celular → desktop | Executa um comando **definido no desktop**; **devolve stdout/stderr** | `kdeconnect.runcommand` (entrada), `kdeconnect.runcommand.output` (saída: `commandStarted`, `commandOutput`, `commandFinished` com `success`/`exitCode`) |
| **`remotecommands`** | desktop → celular | Dispara comandos definidos **no celular** | `org.kde.kdeconnect.device.remotecommands`; `triggerCommand(key)`, `commands`, `canAddCommand`, `commandsChanged` |
| **`sendnotifications`** | desktop → celular | Transmite as notificações **deste computador** | Monitor D-Bus de `org.freedesktop.Notifications.Notify`; pacote `kdeconnect.notification` |
| **`notifications`** | celular → desktop | Notificações do celular no desktop, com resposta | `org.kde.kdeconnect.device.notifications`; `sendReply`, `sendAction`, `dismiss`, `notificationPosted/Removed/Updated` |
| **`clipboard`** | ambas | `sendClipboard`, `clipboardChanged` | `org.kde.kdeconnect.device.clipboard` |
| **`share`** | ambas | `shareText`, `shareUrl`, `shareUrls` / `shareReceived` | `org.kde.kdeconnect.device.share` |
| **`sftp`** | desktop → celular | Navegar arquivos do desktop pelo celular | `org.kde.kdeconnect.device.sftp` |
| Secundários | — | `ping`, `findmyphone`, `lockdevice`, `presenter`, `mousepad`, `mpriscontrol`, `systemvolume`, `battery`, `sms`, `telephony`, `virtualmonitor`, `connectivity_report` | libs instaladas |

### 2.1 Como o `runcommand` funciona (lido no código-fonte)

Fonte: `plugins/runcommand/runcommandplugin.cpp`.

- **Os comandos moram no desktop.** O celular pede a lista
  (`requestCommandList`), o desktop responde com `commandList` + `canAddCommand: true`.
- **A execução é `/bin/sh -c <comando>`** via `QProcess`, com
  `UnixProcessFlag::CloseFileDescriptors`, rodando **como o usuário** e **sem
  nenhuma confirmação no desktop**.
- **A saída volta em blocos de 5 linhas** (`onProcessReadyReadState` →
  `sendOutput`), separando `stdout` e `stderr`, precedida de `commandStarted` e
  concluída por `commandFinished(success, exitCode)`.
- **O celular pode parar** um comando em execução (pacote `stop` →
  `process->terminate()`).
- **O celular não envia argumentos** — só a `key`. Isso é uma restrição de
  desenho, não um detalhe (ver §4.3).
- A configuração é editável pelo próprio app: o pacote `setup` abre o diálogo
  `kdeconnect_runcommand` no desktop (`OpenConfig`). O arquivo por trás é
  `~/.config/kdeconnect/<deviceId>/runcommand`, JSON no formato
  `{"<chave>": {"name": "...", "command": "..."}}` — mas **prefira o diálogo**,
  que é o caminho suportado.

### 2.2 O que o `sendnotifications` faz — e o que ele **não** faz

Fonte: `plugins/sendnotifications/dbusnotificationslistener.cpp`.

- Vira **monitor D-Bus** de `interface='org.freedesktop.Notifications', member='Notify'` —
  ou seja, captura **toda** notificação de desktop criada por qualquer app, pelo
  caminho padrão. O Zorin Copilot já usa esse caminho (toasts e
  `notify-send`, este último inclusive na ponte HA).
- **Descarta** notificações que carregam o hint `x-kdeconnect-source-device`
  (evita eco), e aplica filtros: allow-list/blacklist de app (`checkApplicationName`,
  `checkIsInBlacklist`), limiar de urgência (`generalUrgency`), notificações
  persistentes (`generalPersistent`), e exige `summary` não vazio.
- **Monta o pacote só com** `id`, `appName`, `ticker`, `isClearable`, `title`,
  `text`, `silent`. **`actions` é lido (`notification.setActions(actions)`) e não
  é enviado. O hint `inline-reply` existe no modelo (`NotificationManager`) mas
  não atravessa.** → **sem botões e sem resposta na notificação do celular.**

---

## 3. A abordagem viável

**Princípio:** o KDE Connect é o **transporte e a âncora de confiança**; do lado
do Copilot entra apenas um **adaptador de verbos finos**. Nada de novo listener,
nenhuma porta, nenhum token novo — o emparelhamento por certificado já é a
autenticação.

```
        CELULAR (KDE Connect Android)
             │                          ▲
   runcommand│ (verbo, e a RESPOSTA      │ share / notifications / sftp
   + share   │  volta como stdout)       │ (resultado, arquivo, aviso)
             ▼                          │
   ┌──────────────────────────────────────────────────┐
   │  Adaptador de verbos  (CLI headless já existente) │
   │  copilot-status · copilot-ultimo · copilot-perguntar
   │  copilot-aprovar · copilot-recusar · copilot-parar │
   └──────────────────────────────────────────────────┘
             │
             ▼  core/kdeconnect_bridge.py (puro)
     pedido pendente único + TTL curto + single-flight
             │
             ▼
     ai/agent.py  on_approval  ·  shell/risk.py  RiskPolicy(CONFIRM)
             │
             ▼
        notificações do celular ──BLE──▶ Redmi Watch 5 (leitura)
```

### Etapa 1 — Emparelhar, e resolver o "fora da rede"

- Emparelhamento é manual nos dois lados (troca de certificado). **É a fronteira
  de confiança de todo o desenho.**
- Para funcionar **sem mesma rede**, o KDE Connect não depende de broadcast se
  você disser a ele onde o outro está: a propriedade `customDevices` do daemon
  (`readwrite as`, verificada no bus) e a linha correspondente em
  `~/.config/kdeconnect/config` apontam para IP/host específico.
- Combinação prática: **malha privada (Tailscale/WireGuard)** ou DDNS → IP
  estável → `customDevices`. O celular precisa manter a VPN ativa (custo de
  bateria).
- **Não conte com Bluetooth**: provider beta, desabilitado, e sem adaptador na
  máquina.

### Etapa 2 — Registrar os verbos finos

4 a 6 comandos no plugin `runcommand`, cada um um **wrapper fino** que chama o CLI
headless que já existe (`cli.py`: `agent`, `ask`, `study`, `search`, `rag`):

| Verbo | O que faz | Volta ao celular |
|---|---|---|
| `copilot-status` | estado do agente/provedor, o que está rodando | stdout |
| `copilot-ultimo` | desfecho da última tarefa (ok/pending/falhou/interrompido) | stdout |
| `copilot-perguntar` | consome o payload da caixa de entrada e roda o agente | stdout |
| `copilot-aprovar` | resolve o pedido pendente como **sim** | stdout |
| `copilot-recusar` | resolve o pedido pendente como **não** | stdout |
| `copilot-parar` | aborta o agente ativo | stdout |

**Regra dura:** cada verbo é **fixo**. Nenhum comando registrado aceita "executar
comando arbitrário", nenhum interpreta texto como shell, e nenhum lê segredo.
Isso é o que separa este desenho de um backdoor (§6).

### Etapa 3 — Canal de payload (porque o comando não tem argumentos)

O `runcommand` só manda a `key`. Então a *pergunta* tem de chegar por outro canal:

- **Preferido — `share`:** o celular compartilha um `.txt`/`.md`; `shareReceived`
  entrega o arquivo; o desktop grava em
  `~/.cache/zorin-copilot/remote-inbox/` e `copilot-perguntar` consome o mais
  recente. Tem nome, tipo e tamanho — dá para validar.
- **Alternativa — `clipboard`:** mais rápido (copiar e tocar), mas mexe na área de
  transferência do usuário e o conteúdo é **entrada não confiável**: qualquer
  coisa copiada vira prompt. Se for por aqui, tratar como **material de leitura**,
  com delimitadores e aviso explícito — o mesmo critério que o README já aplica
  aos anexos ("o conteúdo do arquivo não vira instrução").

### Etapa 4 — Canal de saída

1. **stdout do comando** — já volta; é o canal principal e confiável.
2. **`shareText`** (`kdeconnect-cli --share-text`, ou a interface
   `org.kde.kdeconnect.device.share`) — respostas longas ou formatadas.
3. **`share` de arquivo** — conversa exportada em `.md`, PNG do quadro negro.
4. **Notificações de desktop** — transmitidas automaticamente pelo
   `sendnotifications`, **sujeitas aos filtros do plugin** (§2.2). Zero código;
   é o que faz o relógio acender.

### Etapa 5 — Ponte pura no `core`

`core/kdeconnect_bridge.py`, sem GTK e sem rede:

- **Pedido pendente único com TTL curto + single-flight** — consequência direta
  de o comando remoto não carregar payload: dois pedidos simultâneos seriam
  indistinguíveis, então o desenho **proíbe** dois.
- Resolve o `on_approval` que **já existe** em `ai/agent.py`
  (`Step.requires_approval`) — nenhum ponto de extensão novo precisa ser criado.
- Mesma disciplina de desfecho do `core/board_outcome.py`: **o veredito é nosso,
  não a frase do gancho**; `unknown` não é promovido a sucesso.

### Etapa 6 — Auditoria e segurança

- Registrar **origem** (`kdeconnect`) e **qual `key`** disparou.
- Verbos de efeito passam por `RiskPolicy` com **`CONFIRM` obrigatório** — o
  celular é só *mais uma* superfície de resposta, com o mesmo vínculo a um
  `step_id` e a mesma expiração curta.
- Aprovação de origem remota **nunca** pula a confirmação (`--yes` é privilégio
  da interação local).

---

## 4. Limitações e trade-offs

### 4.1 Estruturais (não contornáveis)

| # | Limitação | Por quê |
|---|---|---|
| L1 | **Sem fila e sem entrega offline** | O protocolo é peer-to-peer: os dois precisam estar online ao mesmo tempo. Não é um bot assíncrono. |
| L2 | **Sem botão de resposta na notificação** | Ações e `inline-reply` não atravessam o `sendnotifications` (§2.2, verificado no código). Aprovação volta pelo canal de comando. |
| L3 | **Um pedido pendente por vez** | `runcommand` não carrega payload — "aprovar" não pode dizer *o quê*. Resolve-se com single-flight + TTL curto. |
| L4 | **iOS não serve como canal de fundo** | O próprio projeto documenta: o app fica inativo em segundo plano e não sincroniza notificações. |
| L5 | **Descobrimento é broadcast na LAN** | Fora da rede, exige `customDevices` + VPN/DDNS com IP estável. |
| L6 | **Bluetooth indisponível aqui** | Provider beta e desabilitado; sem adaptador HCI na máquina. |

### 4.2 De configuração

| # | Limitação | Mitigação |
|---|---|---|
| L7 | Filtros do `sendnotifications` podem engolir notificações (urgência, allow-list de app, persistentes, blacklist) | Ajustar a config do plugin por dispositivo, ou usar `share` como canal garantido |
| L8 | Emparelhamento e revogação são manuais | Desemparelhar é o "revogar"; documentar no runbook |
| L9 | Sem UI conversacional — a UX é lista de comandos + notificações | Para chat de verdade, ver §5 |

### 4.3 Risco de segurança (o mais importante)

**O `runcommand` executa `/bin/sh -c` por requisição de um dispositivo
emparelhado, sem confirmação no desktop.** Se o celular for comprometido, tudo que
estiver registrado roda — com os privilégios do usuário. Isso não é um defeito do
KDE Connect; é a natureza do plugin.

Mitigações, todas obrigatórias neste desenho:

1. **Lista mínima e finita** de verbos — nunca "executar comando arbitrário".
2. **Nunca aceitar payload como código** (nada de `sh -c "$(cat clipboard)"`).
3. **Payload é entrada não confiável** — delimitado e anunciado como material de
   leitura, como já se faz com anexos.
4. **Efeito passa por `RiskPolicy`/`CONFIRM`**, sempre.
5. **Auditoria de origem** em cada execução.
6. Opcional, se o risco justificar: um segundo fator para verbos de efeito.

---

## 5. Alternativas, e quando cada uma ganha

| Critério | **KDE Connect** | Gateway HTTP + Tailscale | Bot de chat / ntfy | HA como hub |
|---|---|---|---|---|
| Esforço | **Baixo** | Alto | Baixo | Baixo |
| Fora da mesma rede | Sim, com VPN/DDNS | Sim | **Sim, nativo** | Sim |
| Entrega offline | **Não** | Não | **Sim** | Sim |
| UI no celular | Lista de comandos | **Chat / PWA** | **Chat** | App do HA |
| Relógio (leitura) | **Sim, espelhado** | Só se notificar | **Sim, espelhado** | Sim |
| Superfície de ataque | Emparelhamento + verbos shell | Serviço exposto | **Nenhuma porta de entrada** | A que o HA já tem |
| iOS | **Inviável em background** | Sim | Sim | Sim |

**Leitura:** o KDE Connect ganha em **esforço e em integração com o relógio**, e
perde em **entrega offline** e **UI**. Ele é a melhor escolha para *"ler o que
aconteceu e aprovar com um toque, sem construir infraestrutura"*. Para conversar
de verdade, o bot de chat continua melhor — e as duas coisas não competem: o
`share`/notificações do KDE Connect e um bot podem coexistir.

**Não é alternativa:** GSConnect (implementação GNOME). A sessão desta máquina é
**Hyprland**, com o `kdeconnectd` oficial rodando — GSConnect nem se aplica, e
usaria um barramento D-Bus diferente (`org.gnome.Shell.Extensions.GSConnect`),
o que invalidaria todo o mapeamento de interfaces da §2.

---

## 6. Diagnóstico de conexão (medido nesta máquina, 21/09)

Sintoma: *"não consigo conectar o celular com o PC pelo KDE Connect"*.

### 6.1 Causa confirmada: `firewalld` bloqueando

O `firewalld` está **ativo**, e a zona `public` — que é a da interface de rede
`enp4s0` — libera **apenas `dhcpv6-client ssh`**. As portas que o KDE Connect usa
(1714–1764, TCP e UDP) estão **fechadas**:

```
$ firewall-cmd --get-active-zones
public (default)  interfaces: enp4s0
$ firewall-cmd --list-all
  services: dhcpv6-client ssh        ← nada de kdeconnect
$ firewall-cmd --query-port=1716/tcp   → no
$ firewall-cmd --query-port=1716/udp   → no
```

A definição do serviço **existe** na máquina, só não está habilitada:

```xml
<!-- /usr/lib/firewalld/services/kdeconnect.xml -->
<port port="1714-1764" protocol="tcp"/>
<port port="1714-1764" protocol="udp"/>
```

**Correção:**

```bash
sudo firewall-cmd --permanent --add-service=kdeconnect
sudo firewall-cmd --reload
```

Verificação: `firewall-cmd --query-port=1716/udp` deve passar a responder `yes`.

**Nota de escopo:** o daemon escuta **só na 1716** (confirmado por `ss`: `tcp` e
`udp` em `*:1716`, PID 6891). Se preferir uma regra mais estreita que a oficial:

```bash
sudo firewall-cmd --permanent --add-port=1716/tcp --add-port=1716/udp
```

O emparelhamento continua sendo exigido por TLS de qualquer forma — uma porta
aberta na LAN permite que um dispositivo apareça na lista, não que ele faça algo
sem o certificado trocado.

### 6.2 O que já estava certo (e não é a causa)

| Verificação | Resultado |
|---|---|
| Daemon rodando | ✅ `kdeconnectd` PID 6891, dono de `org.kde.kdeconnect` |
| Escutando | ✅ TCP e UDP em `*:1716` |
| Rede local viva | ✅ gateway `192.168.15.1` responde; vizinhos `.2` e `.5` na tabela ARP |
| Bandeja para o diálogo de pareamento | ✅ `nwg-panel` fornece o `StatusNotifierHost`; `kdeconnect-app` (PID 75016) rodando |
| Bluetooth | ❌ `No local bluetooth adapter found` — sem adaptador, caminho indisponível |
| Log do daemon | Só ruído de AT-SPI; **nenhum erro de descoberta** |

Ou seja: o caminho de rede está saudável e o daemon está pronto — o pacote é que
não passa.

### 6.3 Se ainda não aparecer depois de liberar o firewall

Em ordem de probabilidade:

1. **Celular na mesma sub-rede.** O PC está **no cabo** (`enp4s0`,
   `192.168.15.3/24`). O celular precisa estar em `192.168.15.x` — não em dados
   móveis, nem numa rede de convidados.
2. **Isolamento de clientes (AP isolation)** no roteador — comum em rede de
   convidados; bloqueia a descoberta mesmo com o firewall aberto.
3. **Broadcast não atravessa cabo↔Wi-Fi.** Se o roteador isolar as bandas, a
   descoberta falha mesmo com tudo certo. Contorno: **"Adicionar dispositivo por
   IP"** no app do Android, apontando para `192.168.15.3`.
4. **VPN ativa** em qualquer um dos lados desvia o tráfego.
5. **No Android:** conceder acesso às notificações e **desativar a otimização de
   bateria** para o KDE Connect — sem isso o app morre em segundo plano.

Comandos úteis:

```bash
kdeconnect-cli --refresh      # procura na rede e restabelece conexões
kdeconnect-cli -a             # lista os alcançáveis
kdeconnect-cli -l             # lista os emparelhados
journalctl --user -f | grep -i kdeconnect   # acompanhar ao vivo
```

---

## 7. Próximos passos

1. **Emparelhar um Android** e confirmar, na prática, os dois pontos que só um
   dispositivo real fecha: (a) que a saída do `runcommand` chega ao app como
   esperado, (b) quais notificações o `sendnotifications` deixa passar com os
   filtros padrão.
2. **Registrar `copilot-status` e `copilot-ultimo`** — dois verbos sem efeito
   colateral. É o incremento que valida o canal inteiro sem risco nenhum.
3. Só então **`core/kdeconnect_bridge.py`** com o pedido pendente único, e os
   verbos de aprovação.
4. Se fora da LAN for requisito, decidir **VPN/mesh + `customDevices`** antes de
   escrever o adaptador — é a decisão que muda o runbook.
5. **Não** construir aprovação sobre resposta de notificação: foi verificado que
   esse caminho não existe.
