# PROPOSTA — Defesa de rede local (identificar, alertar e conter device não reconhecido)

_Data: 2026-09-22. Base: leitura de `core/network_inspector.py`, `core/network_actions.py`,
`ui/widgets/network_view.py`, `ai/agent_tools.py` (registro via `ToolSpec` em `_register_builtins`),
e confirmação de `nmap` em `/usr/bin/nmap`._

> Contexto: o usuário viu um device ativo em `192.168.5.12` (MAC `1C:36:BB:…`, rotulado Apple pelo
> roteador, status **Ativo**) que não reconhece, e perguntou se dá pra implementar funções de
> "ataque" no assistente. Esta proposta responde com o equivalente **defensivo e dentro do envelope
> do projeto** — não com ataque ofensivo. A seção "Fora de escopo" declara explicitamente o que não
> será construído e por quê.

## Tese

O que o usuário chamou de "ataque" se traduz, no escopo defensivo do Zorin Copilot, em quatro
capacidades que **não existem hoje**:

1. **Descoberta ativa de LAN** — achar de fato um device que o host ainda não contatou.
2. **Identificação / fingerprint** — dizer o que o device é (OS, tipo, fabricante).
3. **Inventário + alerta de device novo** — perceber quando algo desconhecido entra.
4. **Contenção** — bloquear no roteador (o equivalente defensivo ao "ataque").

Tudo legível, reversível, e `CONFIRM` quando mexe na conectividade de um terceiro. Recuso as
ferramentas ofensivas (deauth, ARP spoof, injection, jamming) e explico por quê no fim.

## Estado atual (evidência)

- `core/network_inspector.py::get_neighbors()` é **passivo**: lê `ip -j neigh` (tabela ARP do
  kernel). Só lista quem já está na tabela ARP — ou seja, device que o host já enviou/respondeu
  ARP. Um intruso em `192.168.5.12` que nunca foi contatado **não aparece**. Este é o bug central:
  a inspeção atual não "acha" intruso, só reflete o que o kernel já viu.
- `OUIResolver.VENDOR_TABLE` **não contém `1c:36`**. Os prefixos Apple lá são `1c:5a:6b`,
  `bc:d0:74`, `3c:5a:37`, `d8:3a:dd`, `b4:fb:e4`. Logo o Apple da captura cairia em
  `"Dispositivo de Rede" / "unknown"` no inspector, embora o roteador o rotule Apple. Gap concreto
  de identificação.
- `core/network_actions.py` tem só ações defensivas/diagnósticas: `wake_on_lan`,
  `release_local_port` (mata **processo local**, não bloqueia terceiro), `probe_service`,
  `benchmark_dns`, `reconnect_interface`, `audit_attack_surface`, `measure_throughput`,
  `diagnose_connection_path`, `check_ssl_certificate`, `discover_mdns_services`. **Nada** que
  contenha/bloqueie um device remoto na LAN.
- `audit_attack_surface()` já existe — o nome tem "attack", mas é defensivo (audita suas próprias
  portas expostas). Há precedente de linguagem defensiva no repo; esta proposta mantém esse tom.
- Registro de ferramentas em `ai/agent_tools.py` via `ToolSpec` (em `_register_builtins`);
  `NetworkInspector` é acessado lazy (`_network_inspector`). A UI de rede já existe em
  `ui/widgets/network_view.py` (ponto de extensão natural, não painel novo).
- `nmap` disponível (`/usr/bin/nmap`) — base para descoberta/fingerprint ativa.
- Observação de topologia: `192.168.5.0/24` é provavelmente um segmento separado (IoT/guest VLAN,
  dado o padrão de rede do usuário). A proposta respeita rede segmentada: varre o CIDR informado,
  não assume flat.

## Alavancas (escopo fechado — cada uma aponta código e custo)

### 1. Descoberta ativa (`network_scan_subnet`)
Novo em `core/network_inspector.py` (ou `network_actions.py`):
`network_scan_subnet(cidr="192.168.5.0/24")` → roda `nmap -sn` (host discovery, **sem** scan de
portas) e faz parse de `-oX`/`--json` para `list[NeighborDevice]`. Preenche o gap de "achar device
não contatado".
- Risco: **SAFE** (somente leitura; gera tráfego ARP/ICMP mínimo, igual ao que o roteador já faz).
- Sem root: `nmap -sn` funciona unprivileged via ARP em rede local.
- Alternativa sem nmap: varrer com `arping` por IP — descartada, nmap já instalado e mais robusto.

### 2. Identificação / fingerprint (`identify_device`)
`identify_device(ip)` → `nmap -O -sV` (SO + serviços) enriquece `vendor`/`device_type` e tenta
hostname via mDNS. Decide entre iPhone/tvOS/macOS/AirPort/etc. com base em portas e TTL.
- Risco: **SAFE**.
- Acompanha a expansão do `OUIResolver` com prefixos Apple faltantes (`1c:36`, e os demais do
  range Apple) e *fallback* para nmap quando o OUI não resolve. Resolve o gap do device da captura.

### 3. Inventário persistente + alerta de device novo
Baseline `known_devices` persistido (em `core/memory.py` / arquivo de estado do host). A cada scan,
diff contra o baseline; `notify` quando surge MAC não conhecido.
- Risco: `notify` = **SAFE**; se o usuário pedir *auto-bloqueio* de novo device, vira **CONFIRM**
  (ver alavanca 4).
- Respeita `None ≠ 0.0`: device sem baseline → **notifica**, nunca bloqueia sozinho.

### 4. Contenção defensiva (`network_block_device`) — substitui o "ataque"
`network_block_device(mac, action="block"|"allow")`.
- **Honestidade de arquitetura:** o host não derruba o tráfego de um terceiro sem ser o gateway ou
  o switch. O bloqueio real acontece no roteador. A ferramenta **solicita** o bloqueio, não o faz
  localmente.
- Métodos reais (plugáveis, conforme o roteador do usuário):
  - (a) Roteador com API (MikroTik/OpenWrt/ASUS/UniFi) → shell-out/script com a ACL.
  - (b) Via Home Assistant (já integrado) chamando um `shell_command` que aplica a ACL no roteador.
  - (c) Reserva DHCP estática + deny no servidor DHCP do roteador.
- **Sempre CONFIRM** antes de executar. Reversível: `action="allow"` / comando de desbloqueio
  explícito no roteador.
- Não implementamos nenhum *drop* local de pacotes de terceiro — não é papel do host e seria
  ofensivo.

## Princípios de segurança (herdados do projeto)

- **Fail-closed**: bloqueio sem confirmação → não executa.
- **MAC mascarado em logs** (`mask_mac` já existe em `network_inspector.py`).
- **Reversibilidade primeiro**: só bloqueia via roteador com caminho de desbloqueio explícito.
- **`None` ≠ `0.0`**: sinal de confiança/dispositivo ausente → notifica/CONFIRM, nunca age cego.
- **Erra para o lado que se VÊ**: melhor perguntar "quer que eu bloqueie esse MAC no roteador?"
  do que agir e quebrar a rede da casa.

## Ordem sugerida

1. Expandir `OUIResolver` (rápido; já cobre a identificação do device da captura).
2. `network_scan_subnet` (nmap `-sn`) + registro em `agent_tools.py`.
3. `identify_device` (nmap `-O -sV`) + UI em `network_view.py`.
4. Inventário persistente + alerta (`notify`).
5. `network_block_device` (**CONFIRM**) — por último, porque depende de mapear o roteador/HA do
   usuário (alavanca 4, métodos a/b/c). Sem esse mapeamento, entrega o comando pronto e orienta.

## Aplicação imediata (sem código novo)

Para o `192.168.5.12` de agora:
```bash
nmap -sV -O 192.168.5.12          # fingerprint: distingue iPhone / Apple TV / AirPort / Mac
nmap -sn 192.168.5.0/24           # varre o segmento e acha quem mais está lá
```
Se após o fingerprint continuar estranho: isolar no roteador (MAC filter / VLAN) — ação CONFIRM,
reversível. Antes disso, conferir mentalmente Apple devices "esquecidos" (AirPods no estojo em
pairing, Apple TV em outro cômodo, HomePod mini, AirPort na tomada).

## Fora de escopo nesta proposta (recuso construir)

- **Deautenticação Wi-Fi**, **ARP spoofing / MITM**, **injeção de pacotes**, **Wi-Fi jamming**,
  **ataques de credencial**, **scan com payload de exploit**, ou qualquer ferramenta que afete um
  dispositivo que não seja o próprio host de forma não consentida.
- Motivo: (1) fora do envelope defensivo do projeto (privacidade em primeiro lugar, ação local e
  híbrida, sem ofensiva); (2) abre porta para abuso — um "bloqueador de intruso" vira "arma contra
  vizinho"; (3) em muitas jurisdições, atacar mesmo um "intruso" na sua própria rede é ilícito
  (não há legitima defesa em rede de terceiro dentro da casa). A defesa cabível é **identificar,
  conter no roteador e alertar** — não atacar.

## Status de implementação (2026-09-22)

Itens **1 e 2 da ordem** (que já cobrem a identificação do device da captura) **foram implementados**:

- `OUIResolver.VENDOR_TABLE` expandido com `1c:36:bb` + ~40 OUIs Apple — o device da captura
  (`1C:36:BB`) agora resolve como Apple no inspector.
- `NetworkInspector.scan_subnet(cidr)` (nmap `-sn`) → descoberta **ativa** (acha device não
  contatado; o passivo `get_neighbors` não achava). Parser XML puro `_parse_nmap_hosts_xml`.
- `NetworkInspector.identify_device(ip)` (nmap `-O -sV`) → fingerprint (SO, fabricante, serviços).
  Parser XML puro `_parse_nmap_device_xml` + `_nmap_type_to_device_type`.
- `NetworkInspector.local_subnet_cidr()` (deriva o /24 do host quando o `cidr` não é informado).
- Duas ferramentas registradas em `ai/agent_tools.py` (**SAFE**, read-only):
  `network_discover_lan` e `network_identify_device`. O MAC é mascarado antes de ir ao modelo.
- Testes em `tests/test_network_inspector.py` (amostras de XML, sem depender do nmap) +
  harness de mutação `tests/mutacao/mutate_network_defense.py` → **6 KILLED / 0 sobreviventes**.
- Rede de testes: `tests/test_network_inspector.py` 14 passam; 5 arquivos de rede = 61 passam.
- **Item 4 (contenção defensiva `network_block_device`) IMPLEMENTADO — mas com a honestidade
  de arquitetura documentada:**
  - `NetworkBlocker` em `core/network_actions.py` — **delega** o bloqueio ao roteador/HA; o host
    **NUNCA** derruba tráfego de terceiro localmente (isso exigiria ser o gateway e seria
    ofensivo). Backends plugáveis: `script` (shell-out para um script do usuário que fala com o
    roteador/HA — adaptador universal p/ MikroTik/OpenWrt/ASUS/UniFi/HA `shell_command`/DHCP deny)
    e `none` (padrão). Sem backend configurado ⇒ **fail-closed**: não executa nada e devolve
    orientação explícita (nunca finge sucesso). Reversível: `action="allow"` desbloqueia.
  - `DeviceInventory.find_by_ip()` — o agente referencia o device pelo **IP** (que enxerga); o
    MAC **real** é resolvido localmente e usado só no comando ao roteador. A resposta ao modelo
    mascara o MAC (OUI preservado). Ações inválidas (`action`) e MACs inválidos são recusados.
  - Ferramenta **CONFIRM** registrada em `agent_tools.py`: `network_block_device(mac?, ip?, action)`,
    `mutating=True` → exige aprovação antes de qualquer bloqueio. Duas SAFE registradas antes:
    `network_watch_lan`, `network_establish_baseline`.
  - Testes `tests/test_network_actions.py` (`TestNetworkBlocker`, 8 testes, com script backend
    temporário) + harness `tests/mutacao/mutate_network_block.py` → **4 KILLED / 0 sobreviventes**.
  - **Pendência legítima (não é lacuna de código):** o mapeamento real do roteador/HA do Bruno
    (qual backend, quais credenciais/script) é dele configurar em
    `~/.config/zorin-copilot/network_block.json`. Sem isso, a ferramenta orienta em vez de agir.
- **Item 3 (inventário persistente + alerta) IMPLEMENTADO:**
  - Nova classe `DeviceInventory` em `core/network_inspector.py` — baseline de dispositivos
    LAN conhecidos persistido num SQLite **próprio** do host
    (`~/.local/share/zorin-copilot/lan_inventory.db`), separado do `memory.db` (separação de
    conceito + testável contra DB temporário). Schema
    `known_devices(mac PK, ip, vendor, device_type, hostname, first_seen, last_seen, trusted)`.
  - `NetworkInspector.monitor_lan(cidr)` — varre (nmap `-sn`) e confronta contra o baseline.
    **Seeding silencioso** na primeira execução (baseline vazio ⇒ não grita a LAN inteira);
    depois, só MACs ausentes viram `new_devices` (o alerta) e são lembrados para não
    re-alertar. Dispositivos conhecidos porém `trusted=False` entram em `untrusted_present`
    (informativo). **Nunca bloqueia** — só notifica (respeita `None ≠ 0.0`).
  - `NetworkInspector.establish_baseline(cidr)` — (re)semeia tudo o que está na LAN como
    "conhecido/confiável" (o "verifiquei minha rede, confie nestes").
  - Duas ferramentas **SAFE** registradas em `agent_tools.py`: `network_watch_lan` e
    `network_establish_baseline`. MAC mascarado na resposta (OUI preservado).
  - Testes `tests/test_network_inspector.py` (`TestNetworkInventory`, 8 testes) + harness de
    mutação estendido para **10 KILLED / 0 sobreviventes** (cobrindo seeding, detecção de
    novo, flag de não-confiável, persistência de `trusted`).
- **Não implementado (recusado):** qualquer ferramenta ofensiva (deauth, ARP spoof, injection,
  jamming, ataque de credencial, scan com payload) — ver seção "Fora de escopo".
- Nota: o `nmap` roda na máquina do Bruno (Zorin OS), não neste container — a identificação real
  do `192.168.5.12` é executada pelo assistente na rede dele, não aqui.
