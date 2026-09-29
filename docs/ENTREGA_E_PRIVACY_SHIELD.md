# Entrega E — Privacy Shield por identidade da janela + allowlist do usuário

> Plano de ação para o **P1-9** de `docs/MELHORIAS_VOZ_TELA_AO_VIVO.md` §2.3
> (*"Privacy Shield por classe/role (P1-9)"*).
> Cada ponto ancora no estado real do código, **lido nesta rodada (2026-09-29)**.
>
> **STATUS: IMPLEMENTADO (2026-09-29).** Entregas anteriores: `ENTREGA_A_VOZ_TELA.md` (P0-1/2/3),
> `ENTREGA_B_RECONEXAO.md` (P0-4), `ENTREGA_C_MODO_E_SINAIS.md` (P1-5),
> `ENTREGA_D_WATCHDOG_PENSANDO.md` (P1-8).

---

## 1. Evidência do problema (código atual, verificado)

### 1.1 A decisão é substring crua, sem fronteira de palavra

`ai/live.py:4233-4237`, dentro do laço de vídeo (`_video_stream_loop`, `:4221`):

```python
app_name, win_title, rect = self.inspector.get_active_window_info()
title_low = f"{app_name} {win_title}".lower()

# 2. Avaliação de Privacidade (Privacy Shield)
is_sensitive = any(kw in title_low for kw in self.sensitive_keywords)
```

`self.sensitive_keywords` vem de `DEFAULT_SENSITIVE_KEYWORDS` (`:49-55`), um conjunto de
**substrings soltas** — inclusive `"bb"`, `"inter"`, `"c6"`, `"cvv"`. Como o teste é
`kw in texto`, qualquer ocorrência **dentro** de outra palavra dispara o escudo.

### 1.2 Medido: 6 de 7 janelas comuns são ocultadas por engano

Executado contra o conjunto real (`DEFAULT_SENSITIVE_KEYWORDS`), não por inspeção:

| app / título | substring que dispara | resultado |
|---|---|---|
| `libreoffice-writer` / `Hobby project notes.docx` | `bb` (em "Ho**bb**y") | **oculta** ❌ |
| `firefox` / `Printer settings` | `inter` (em "Pr**inter**") | **oculta** ❌ |
| `code` / `interface.py — zorin-copilot` | `inter` (em "**inter**face") | **oculta** ❌ |
| `nautilus` / `Winter photos 2026` | `inter` (em "W**inter**") | **oculta** ❌ |
| `gnome-calculator` / `0xc6f3 + 12` | `c6` (em "0x**c6**f3") | **oculta** ❌ |
| `firefox` / `Abbey Road - Wikipedia` | `bb` (em "A**bb**ey") | **oculta** ❌ |
| `evince` / `Counter-Strike manual.pdf` | — | visível ✅ |

E os verdadeiros positivos **funcionam** (por isso o defeito passa despercebido):

| app / título | dispara | resultado |
|---|---|---|
| `bitwarden` / `Bitwarden Password Vault` | `bitwarden` | oculta ✅ |
| `firefox` / `Banco Bradesco - Internet Banking` | `banco`, `bradesco` | oculta ✅ |
| `chromium` / `Navegação Anônima` | `anônima` | oculta ✅ |
| `keepassxc` / `Minhas Senhas.kdbx` | `keepass` | oculta ✅ |

O dano não é o escudo disparar demais — é ele disparar **errado**, e o usuário não ter
como dizer "esta janela é segura". O efeito prático: quem escreve um documento sobre um
*hobby*, abre as preferências de *impressora* ou edita um `interface.py` perde a visão da
IA silenciosamente, sem entender por quê.

### 1.3 Já existe um matcher melhor — e ele não é usado aqui

`core/privacy_gate.py` já implementa `PrivacyGatekeeper.should_ingest_window()`
(`:77-101`) com **padrões ancorados por fronteira de palavra** (`\bbanco\b`, `\bitau\b`,
`\binter\b`) e **classes de aplicativo** (`bitwarden`, `pinentry`, `polkit`, `gcr-prompter`).
Esse é o matcher que protege a ingestão da RNN — e **não** protege o vídeo da tela.

Ou seja: existem **duas** decisões de privacidade divergentes no produto, e a que vale para
o **vazamento mais grave** (pixels literais da tela para a nuvem) é a mais fraca das duas.

### 1.4 Não há allowlist

Não existe nenhum caminho para o usuário marcar uma janela como segura. `PreferencesDialog`
tem uma página de privacidade de documentos (`ui/preferences.py:1147`) com `ignored_patterns`,
mas nada para o escudo de tela.

---

## 2. Objetivo

1. **Uma só decisão de privacidade.** O escudo de vídeo passa a usar o `PrivacyGatekeeper`.
2. **Casamento por identidade + fronteira de palavra.** Fim dos falsos positivos de substring
   (`bb`/`inter`/`c6`), preservando os verdadeiros positivos.
3. **Allowlist do usuário.** O usuário pode declarar janelas seguras (consentimento explícito);
   e uma denylist para acrescentar as que ele considera sensíveis.
4. **Default-deny para senha/autenticação.** Janelas de gerenciador de senhas e prompts de
   autenticação são **incondicionalmente** ocultadas — a allowlist **não** as revela.

**Fora de escopo:** UI de preferências para editar as listas (fica para follow-up; a config já
as suporta), correspondência por `app_id` real do Wayland via `hyprctl` (o `inspector` já devolve
a classe no caminho Hyprland — ver §3 W5), e o sanitizador de texto (`sanitize_text`, já correto).

---

## 3. Passos

| # | Arquivo:linha | O que fazer | Riscos / armadilhas |
|---|---|---|---|
| W1 | `core/privacy_gate.py` | União dos padrões de título com **fronteira de palavra**: acrescentar `\bbank\b`, `\bita[úu]\b`, `\bbb\b`, `\bc6\b`, `\bextrato\b`, `\bcart[ãa]o\b`, `\bcvv\b`, `\ban[ôo]nima\b`, `\birpf\b`, `\bdeclara[çc][ãa]o\b`, `\bsenhas?\b`, `\bpassword\b`, `\bcredenciais?\b`, `\bcredentials?\b`. | `\bsenha\b` **não** casa "senhas" → usar `\bsenhas?\b`. Não perder nenhum termo do set legado. |
| W2 | `core/privacy_gate.py` | `HARD_BLOCK_APP_PATTERNS = SENSITIVE_APP_PATTERNS` (identidade) — **não** sobrescrevível pela allowlist. | É a interpretação de "default-deny para senha". Documentar a rigidez como decisão. |
| W3 | `core/privacy_gate.py` | `evaluate_window(app, title, allowlist, denylist)` — ordem: **hard-block → denylist → allowlist → título → permite**. `should_ingest_window` fica **intacto** e é o último degrau: é o matcher de padrões, e o contrato da RNN. | Denylist ganha da allowlist (negação explícita vence entrada velha). |
| W4 | `core/config.py` | `privacy_shield_allowlist: list[str]`, `privacy_shield_denylist: list[str]` (`default_factory=list`, no estilo de `:320`). | Config fresco = listas vazias = comportamento de hoje (menos os falsos positivos). |
| W5 | `ai/live.py:1412` + `:4237` | Guardar as listas no `__init__`; criar `_window_is_sensitive(app, title)` que chama o gate; trocar a linha 4237 por essa chamada. | `getattr(..., None)` nas listas (fixtures `__new__` podem não passar pelo `__init__`). Manter `sensitive_keywords` como legado, mas **fora da decisão**. |
| W6 | `tests/test_privacy_shield.py` (novo) + `tests/test_live_video.py:175` | Testes da decisão pura + **regressão dos falsos positivos medidos** + allowlist/denylist + hard-block. O teste-espelho de `test_live_video.py` (que **repete a lógica de produção**) passa a chamar `_window_is_sensitive`. | O espelho é o defeito de teste: ele afirma a implementação, não o contrato. |

---

## 4. Critérios de aceitação

- `Hobby`, `Printer settings`, `interface.py`, `Winter photos`, `0xc6f3`, `Abbey Road` →
  **visíveis** (não ocultadas).
- `Bitwarden`, `Banco Bradesco - Internet Banking`, `Navegação Anônima`, `Nubank`,
  `Private Browsing`, `Minhas Senhas.kdbx` → **ocultadas**.
- App na allowlist do usuário → **visível**, mesmo com título que casaria um padrão.
- App na denylist → **ocultado**, mesmo que também esteja na allowlist.
- Gerenciador de senhas / prompt de autenticação → **ocultado mesmo se estiver na allowlist**.
- `should_ingest_window` (RNN) mantém o comportamento atual (os testes existentes passam).

---

## 5. Implementado (2026-09-29) — âncoras reais

### 5.1 O matcher — `core/privacy_gate.py`

| Âncora | Linha | O que é |
|---|---|---|
| `seahorse` em `SENSITIVE_APP_PATTERNS` | `:40` | Gerenciador de chaves do GNOME — estava no set legado do escudo, faltava aqui. |
| `HARD_BLOCK_APP_PATTERNS` | `:47` | `= SENSITIVE_APP_PATTERNS`. Identidade de cofre/prompt: a allowlist **não** revela. |
| `SENSITIVE_TITLE_PATTERNS` | `:56-83` | União do set legado com o que já existia, **tudo ancorado por `\b`**: navegação privada, credenciais (`\bsenhas?\b`, `\bpassword\b`, `\bcredenciais?\b`), bancos (`\bbanco\b`, `\bbank\b`, `\bita[úu]\b`, `\bbb\b`, `\bc6\b`, `\bextrato\b`, `\bcart[ãa]o\b`, `\bcvv\b`, `\birpf\b`, `\bdeclara[çc][ãa]o\b`). |
| `should_ingest_window()` | `:107` | Inalterado — é o matcher de padrões puro, e o contrato da RNN. |
| `_normalize_entries()` | `:133` | Identidade exata, sem caixa nem espaços (`frozenset`). |
| `evaluate_window()` | `:140-192` | A política: hard-block → denylist → allowlist → padrões. |

**Detalhe que o teste fixa:** `\bsenha\b` **não** casa "senhas" (o `s` colado não tem fronteira
depois do `a`) — por isso o padrão é `\bsenhas?\b`. Um teste dedicado a isso teria salvado a
primeira versão.

### 5.2 Config — `core/config.py:342-343`

```python
privacy_shield_allowlist: list[str] = field(default_factory=list)
privacy_shield_denylist: list[str] = field(default_factory=list)
```

Listas vazias = comportamento de hoje (menos os falsos positivos). No estilo de
`trusted_directories` (`:320`).

### 5.3 A fiação — `ai/live.py`

| Âncora | Linha | O que é |
|---|---|---|
| `from ..core.privacy_gate import PrivacyGatekeeper` | `:37` | Uma política só. |
| `self._privacy_allowlist` / `_privacy_denylist` | `:1418` / `:1421` | Lidas do config no `__init__`. |
| `_window_is_sensitive()` | `:4232` | A **porta única**. O laço de vídeo e os testes chamam a mesma função. |
| Laço de vídeo | `:4270` | `is_sensitive = self._window_is_sensitive(app_name, win_title)` — substitui o `any(kw in ...)`. |

`self.sensitive_keywords` (`:1412`) permanece como legado — **fora da decisão**. A remoção
dele é limpeza de follow-up, não desta entrega.

### 5.4 Duas decisões viraram uma

Antes havia duas políticas divergentes: `PrivacyGatekeeper` (forte, com fronteira de palavra)
protegia a ingestão da RNN, e o substring fraco protegia o **vídeo da tela** — o vazamento
mais grave, porque são pixels literais para a nuvem. Agora ambos passam pelo mesmo gate.

---

## 6. Validação (medida nesta rodada)

### 6.1 A decisão, antes e depois

Medido contra o conjunto real, não por inspeção:

| janela | antes (substring) | depois (gate) |
|---|---|---|
| `libreoffice-writer` / `Hobby project notes.docx` | oculta ❌ (`bb`) | visível ✅ |
| `firefox` / `Printer settings` | oculta ❌ (`inter`) | visível ✅ |
| `code` / `interface.py` | oculta ❌ (`inter`) | visível ✅ |
| `nautilus` / `Winter photos 2026` | oculta ❌ (`inter`) | visível ✅ |
| `gnome-calculator` / `0xc6f3 + 12` | oculta ❌ (`c6`) | visível ✅ |
| `firefox` / `Abbey Road - Wikipedia` | oculta ❌ (`bb`) | visível ✅ |
| `bitwarden` / `Bitwarden Password Vault` | oculta ✅ | oculta ✅ |
| `firefox` / `Banco Bradesco - Internet Banking` | oculta ✅ | oculta ✅ |
| `chromium` / `Navegação Anônima` | oculta ✅ | oculta ✅ |
| `keepassxc` / `Minhas Senhas.kdbx` | oculta ✅ | oculta ✅ |

### 6.2 Testes — `tests/test_privacy_shield.py` (novo, 18 testes + 32 subtestes)

Cinco classes: `WindowDecisionTest` (a regressão dos falsos positivos + o mecanismo da
fronteira de palavra), `UserListTest` (allowlist/denylist/normalização/precedência),
`HardBlockTest` (o cofre ignora a allowlist), `ShouldIngestUnchangedTest` (o contrato da RNN),
`ClientWiringTest` (a fiação do cliente) e `VideoLoopUsesTheGateTest` (o laço de vídeo).

**O teste-espelho foi corrigido.** `tests/test_live_video.py::test_sensitive_window_detection`
**repetia a lógica de produção** (`any(kw in combined ...)`) — ou seja, afirmava a
implementação, não o contrato. Ele continuou **verde** depois de a decisão migrar para o gate,
testando código morto. Agora chama `_window_is_sensitive`, e um segundo teste
(`..._ignores_substrings`) fixa a regressão dos falsos positivos.

### 6.3 Harness de mutação — `tests/mutacao/mutate_privacy_shield.py` (novo)

**11 KILLED / 0 sobreviventes / 0 equivalentes / 0 inválidas**, `exit=0`.

A primeira rodada **pegou 3 sobreviventes** — e dois deles eram **defeitos nos meus próprios
testes**, não no produto:

| sobrevivente | diagnóstico |
|---|---|
| "a normalização some da allowlist" | o teste usava o título **neutro** "Hobby": a asserção `allowed` era verdadeira *mesmo sem* a allowlist. Peso zero. |
| "o cliente deixa de ler a allowlist do config" | mesmo defeito: título neutro. |
| "o laço de vídeo volta à substring crua" | **lacuna de fiação real**: nenhum teste exercia o laço. |

Os dois primeiros foram corrigidos usando um título **sensível** (`Itaú - Extrato`), que é o
que torna a allowlist carregadora de peso. O terceiro foi fechado com
`VideoLoopUsesTheGateTest`, que roda **uma volta** de `_video_stream_worker` com inspetor e
captura dublados e afirma que a porta do gate foi chamada com a janela ativa.

Integridade após a rodada: `grep` por resíduo de mutação nas duas fontes → **vazio**;
âncoras presentes; `tests/test_mutacao_anchors.py` → **3 passed** (a tranca descobre o novo
harness sozinha, por `glob`).

### 6.4 Suíte completa (runner seguro)

Executada pelo `run_tests_safe.py` (sem swap → nunca `pytest` direto). Resultado no fim desta
entrega, registrado em `ENTREGA_A_VOZ_TELA.md` §suíte:

```
módulos: 163   falhas: 0   tempo: 219s
pico de RSS em qualquer arquivo: 370 MB
```

O total subiu de 162 → **163** módulos porque esta entrega adiciona
`tests/test_privacy_shield.py`.
