# Estabilidade da suíte de testes — diagnóstico e correção

> Sintoma relatado: os testes travavam ou reiniciavam o PC no meio da execução.
>
> Investigação concluída. Eram **duas falhas independentes** com o mesmo
> sintoma aparente, e as duas estão corrigidas.

---

## Resultado

| | Antes | Depois |
|---|---|---|
| Suíte completa | OOM killer reiniciava o PC; travamentos de 20+ min | **108 módulos em 89 s** |
| Pico de memória por arquivo | ~3,3 GB (30 janelas × 109,5 MB) | **488 MB** |
| Isolamento sob `unittest` | nenhum (tocava o config e o banco reais) | sandbox ativo |
| Travamentos | sim, sem sinal e sem log | **zero** |

---

## Falha 1 — estouro de memória (o PC reiniciava)

### Evidência

```
journalctl -k -b -1  ->  systemd invoked oom-killer
                         Out of memory: Killed process 2666 (electron)
free -h              ->  Swap: 0B        (15 GiB de RAM, sem swap)
```

### Causa

Cada `CopilotWindow` constrói, por padrão, três serviços de segundo plano:

- servidores **MCP** habilitados — subprocessos Node/Python via `uvx`/`npx`;
- **indexador RAG** — varredura de ~/Documentos e ~/Downloads;
- motor de **wake word** — com o microfone aberto.

Custo medido **por janela**, contando a árvore de processos (o `VmRSS` do pai
não inclui os subprocessos, então medir só o pai subestima o consumo):

| | gate de teste DESLIGADO | gate de teste LIGADO |
|---|---|---|
| MB por janela | **+109,5** | **+2,9** |
| processos por janela | **+1,80** | **+0,00** |
| threads após `destroy` | 16 | 1 |

`tests/test_ui_refactor.py` cria **30 janelas** num único teste:

```
30 × 109,5 MB ≈ 3,3 GB   e   30 × 1,8 ≈ 54 processos
```

Sem swap, o OOM killer entra em ação e o sistema reinicia.

### Por que o gate não disparava

`_is_test_environment()` em `ui/app.py` só reconhecia o **pytest**:

```python
return bool(
    os.environ.get("ZORIN_TEST_MODE")
    or os.environ.get("PYTEST_CURRENT_TEST")
    or "pytest" in sys.modules          # <- nunca verdadeiro sob unittest
)
```

A suíte do projeto roda com `python -m unittest`. Nenhum dos três sinais
aparece, então **o gate nunca disparava**. O `unittest` não expõe variável de
ambiente por teste; o que o delata é o próprio processo:

```python
main_spec = getattr(sys.modules.get("__main__"), "__spec__", None)
if getattr(main_spec, "name", "") in ("unittest.__main__", "unittest"):
    return True
```

### Correção

1. `_is_test_environment()` passa a reconhecer o `unittest`.
2. `CopilotWindow.teardown_background_services()` — idempotente — devolve MCP,
   RAG, wake word e voz ao vivo. Chamado por `do_shutdown` e pela **sobrescrita
   do método `destroy()`**.
3. `LocalDocumentRAG.cancel_indexing()` — a varredura de disco passa a ser
   cooperativamente cancelável, em vez de sobreviver à janela que a disparou.

> **Detalhe que importa:** o teardown **não** pode ser conectado ao sinal
> `destroy` do GTK4. Medido nesta versão: `Gtk.Window.destroy()` **não emite**
> esse sinal — nem em janela nunca apresentada, nem em janela apresentada (aí só
> sai `unrealize`). O sinal existe no GType (`GObject.signal_lookup` o encontra),
> então parece disponível, mas um handler conectado a ele jamais roda. O gancho
> correto é sobrescrever o método. `unrealize` também não serve: dispara quando a
> janela é apenas escondida, e no modo HUD esconder é o comportamento normal.

---

## Falha 2 — travamento silencioso (os testes "travavam")

Esta é uma causa **completamente diferente**, e não tinha nada a ver com
memória.

### Evidência

O processo de teste continuava vivo, mas:

- RSS **estático** — 319 MB em duas amostras separadas por 3 s;
- thread principal parada em `pipe_read` / `poll_schedule_timeout`;
- filho já morto, nenhuma mensagem de erro, nenhum log;
- **21 minutos** sem produzir saída.

Rastreando o inode do pipe em `/proc/*/fd`, o dono da ponta de escrita era:

```
PID 29431   PPID 1   wl-copy "Olá Mariana"
```

Um `wl-copy` **órfão**, reparentado ao systemd, segurando o pipe.

### Causa

`core/clipboard.py::ClipboardService.set_text` chamava:

```python
subprocess.run(["wl-copy", text], check=False, timeout=2.0)
```

sem redirecionar o stdio. No Wayland a seleção pertence a um **cliente vivo**:
`wl-copy` bifurca um processo que permanece enquanto for dono do conteúdo — é
assim que o protocolo funciona. Esse processo **herda stdout/stderr do pai**.

Consequência: qualquer captura de saída por pipe nunca recebe EOF. Quem captura
trava para sempre. Vale para a suíte de testes, mas também para o app rodando
sob systemd, com saída redirecionada, ou dentro de outro processo que capture
stdout. `xclip` tem exatamente o mesmo comportamento no X11.

### Reprodução mínima

```python
# filho: subprocess.run(["wl-copy", "x"], timeout=2.0)   # herda stdio
r = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True)
# -> trava indefinidamente (com timeout de 20 s: exit 124)
```

### Correção

```python
subprocess.run(
    ["wl-copy", text],
    check=False,
    timeout=2.0,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
```

O mesmo tratamento no caminho do `xclip`. Verificado: o cenário que travava
indefinidamente agora completa em **0,07 s**.

Três regressões em `tests/test_clipboard.py` cobrem isso, incluindo um teste
comportamental que reproduz a captura de ponta a ponta.

---

## Falha 3 — o isolamento não existia sob `unittest`

Descoberta durante a investigação, e é a mais silenciosa das três.

Todo o isolamento — `ZORIN_TEST_MODE`, redirecionamento de `XDG_CONFIG_HOME` e
`XDG_DATA_HOME`, bloqueio de MCP/RAG, destruição de janelas — vivia em
`tests/conftest.py`.

**`conftest.py` é um arquivo do pytest.** Ele é importado pelo pytest e por mais
ninguém. Sob o comando documentado no README
(`python3 -m unittest discover -s tests`), **nenhuma** das proteções era
aplicada — e a suíte lia e escrevia o `config.json` e o `memory.db` **reais do
usuário**.

### Correção

O isolamento foi extraído para `tests/_sandbox.py`, ativado por **dois** ganchos:

- `tests/__init__.py` — único ponto que o `unittest` oferece para rodar código
  antes de importar qualquer teste;
- `tests/conftest.py` — delega para o mesmo módulo, sem duplicação.

Isso torna o comando de descoberta dependente do `-t .`:

```bash
python3 -m unittest discover -s tests -t .
```

O `-t .` faz o `unittest` tratar `tests/` como **pacote**, o que é a única forma
de o `__init__.py` ser importado. Sem ele, a suíte roda sem isolamento nenhum —
e sem avisar.

---

## Ferramenta: runner com guarda

`~/.workbuddy-ai/skills/zorin-copilot-gui-change/scripts/run_tests_safe.py`

Roda **um arquivo de teste por processo**, mede o pico de RSS e o tempo de cada
um, e mata o arquivo que estourar o teto (900 MB / 180 s por padrão).

```bash
python3 .../scripts/run_tests_safe.py
python3 .../scripts/run_tests_safe.py tests.test_ui_refactor
python3 .../scripts/run_tests_safe.py --limit 700 --timeout 300
```

Três decisões de projeto, todas motivadas por falhas reais:

- **saída em arquivo temporário, não em pipe** — `subprocess.PIPE` somado a
  `proc.stdout.read()` trava para sempre se qualquer neto herdar a ponta de
  escrita. Foi exatamente o que o `wl-copy` fez. Arquivo não tem esse
  acoplamento;
- **`start_new_session=True`** — o filho vira líder do próprio grupo, então o
  abort mata também os netos deixados para trás;
- **teto de tempo além do teto de memória** — um teste pode pendurar sem
  consumir memória nem emitir sinal. Um runner que só olha RSS não percebe.

---

## Situação atual da suíte

```
módulos: 147   falhas: 0   tempo: 171s
pico de RSS em qualquer arquivo: 335 MB
nenhum aborto, nenhum timeout
```

A suíte tem **3.106 testes** (`pytest --collect-only -q tests/`).

### ~~As 16 falhas são pré-existentes e estruturais~~ — RESOLVIDO em 2026-09-21

> **A recomendação abaixo foi executada.** O runner de guarda passou a invocar
> `pytest -q -p no:cacheprovider` por módulo, em vez de `python -m unittest <módulo>`.
> O `pytest` coleta as duas famílias (classes `unittest.TestCase` **e** funções estilo
> pytest), então o problema sumiu de uma vez, sem converter arquivo nenhum.
> **Baseline novo: 147 módulos, 0 falhas.**
>
> **E o número real era muito pior do que este documento estimava.** Ele dizia "80+
> testes invisíveis". Medido: **435 testes que passam** e que nunca rodaram na suíte de
> guarda. O pior deles é `tests/test_study_tutor.py`, que cobre a injeção do
> `TUTOR_ADDON` no prompt — um fio de produção sem guarda efetiva.
>
> **A lição que fica, e vale além deste repo:** um número de baseline que se repete
> idêntico a cada execução **não é tranquilizador, é suspeito**. Este documento e mais
> três (`PROPOSTA_REFORMULACAO_GUI.md`, `plano-melhorias-pilula-voz.md`,
> `ANALISE_APRIMORAMENTO_RACIOCINIO.md`) registraram as mesmas 16 falhas como
> "estruturais e pré-existentes" por várias rodadas, e a leitura de "pré-existente"
> foi virando "não é problema meu". `exit 5` significa **"não rodou"**, não "verde por
> outro caminho".

**O diagnóstico original, preservado (era correto):**

Nenhuma das 16 era regressão. Todas decorriam de o projeto documentar `unittest`
enquanto boa parte da suíte é escrita para `pytest` — todas saíam com `exit 5` e
**todas passavam sob `pytest`**:

- **14 arquivos** falhavam com `ModuleNotFoundError: No module named 'pytest'`
  (import no topo do módulo): `test_academic_hub`, `test_agent_cli`, `test_agent_loop`,
  `test_agent_response_fixes`, `test_agent_tools`, `test_board_outcome`,
  `test_home_assistant`, `test_home_assistant_realtime`, `test_study_capture`,
  `test_study_cards`, `test_study_summary`, `test_subagents`, `test_system_inspect`,
  `test_vscode`;
- **2 arquivos** escritos em estilo pytest (`def test_*` no nível do módulo, fixture
  `tmp_path`) que o `unittest` não coletava — reportavam "Ran 0 tests":
  `test_agent_router` e `test_study_tutor`.

**Recomendação (agora cumprida):** escolher um runner oficial. Foi o primeiro caminho —
o `pytest` já é dependência de `[test]` no `pyproject.toml` e o CI já o usa. **Não foi
preciso converter arquivo nenhum**, e a regra antiga ("escreva teste novo em `unittest`
para não inflar a contagem de falhas") morreu junto: os dois estilos rodam.

---

## Regressão corrigida no caminho

`tests/test_fase4c_dynamic_context.py::test_key_is_deterministic` estava
quebrando porque o dublê `_FakeMemory` não tinha `fact_key` — a mudança da chave
do fato para `MemoryManager.fact_key()` (hash do conteúdo normalizado) não
atualizou o dublê. Corrigido delegando ao método real, para que o dublê não
divirja em silêncio:

```python
fact_key = staticmethod(MemoryManager.fact_key)
```

---

## Regras que ficam para o projeto

1. **Todo `subprocess` que possa sobreviver ao chamador** — donos de seleção,
   daemons, servidores MCP — precisa de `stdin/stdout/stderr=DEVNULL` e
   `start_new_session=True`. Senão ele segura os descritores do pai.
2. **`subprocess.PIPE` + `proc.stdout.read()` é armadilha.** Se um neto herdar a
   ponta de escrita, o `read()` nunca retorna. Use arquivo temporário.
3. **Ao adicionar um serviço pesado em `CopilotWindow`**, coloque-o atrás de
   `_is_test_environment()` e inclua o teardown em
   `teardown_background_services()`.
4. **Ao trocar a interface de uma dependência usada por dublês de teste**,
   procure todos os `Fake*` que a imitam.
5. **Teardown no GTK4 é por sobrescrita de método, não por sinal** — o sinal
   `destroy` não é emitido.
