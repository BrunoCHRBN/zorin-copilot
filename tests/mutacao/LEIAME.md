# Harnesses de mutação

[🇺🇸 English Version](README.en.md) | [🇧🇷 Versão em Português](LEIAME.md)

Cada arquivo daqui quebra **de propósito** um pedaço do código de produção, roda a suíte
correspondente e exige que ela **falhe**. Se a suíte continuar verde com o defeito
reintroduzido, o teste que deveria pegar aquilo não morde — e o harness acusa.

Um teste verde prova que o código passa. Só um harness de mutação prova que o teste
**pegaria o defeito de volta**. É a diferença entre "os testes passam" e "os testes
servem".

## Como rodar

Da raiz do repo:

```bash
.venv/bin/python tests/mutacao/mutate_identity.py
.venv/bin/python tests/mutacao/mutate_file_watcher.py
```

Saída: uma linha por mutação, e um resumo no fim. **Código de saída 0** só quando não
há sobrevivente nem inválida — dá para encadear em verificação automática.

## As quatro categorias

| Categoria | Significa | O que fazer |
|---|---|---|
| **KILLED** | A suíte falhou com o defeito de volta. É o que se quer. | nada |
| **SURVIVED** | A suíte ficou verde com o defeito. **Lacuna de teste real.** | escrever o teste que falta |
| **EQUIVALENTE** | A mutação não muda comportamento observável. Não é lacuna. | **medir** e registrar no `EQUIVALENTES` do harness |
| **INVÁLIDA** | O trecho não casou; a mutação nunca foi aplicada. | corrigir a string do harness |

**EQUIVALENTE não se presume — se mede.** Um sobrevivente parece lacuna e pode não ser.
Exemplo real: remover `if "_" not in locale` de `parse_voice` não muda nada, porque o
`partition` logo abaixo já rejeita um locale sem `_` — medido com 324 entradas hostis,
**0 divergências**. Sem essa medição, a única saída honesta seria "consertar" um teste
que já estava certo.

**INVÁLIDA é o modo de falha mais traiçoeiro:** o relatório diz "sobreviveu" e parece
lacuna, mas a mutação nunca chegou a ser aplicada. É por isso que o harness confere se
o trecho existe **antes** de escrever.

## A âncora tem de ser ÚNICA (e há duas trancas)

O harness aplica `original.replace(de, para, 1)` — substitui a **primeira** ocorrência. Se a
âncora aparece mais de uma vez, ele muta a primeira e **reporta o resultado com o nome da
mutação descrita**. Ou seja: mente, dizendo KILLED/SURVIVED de um defeito que não é o que ele
diz estar testando.

**Caso real (2026-09-22).** A mutação `get: id vazio deixa de ser rejeitado` de
`mutate_google_drive.py` tinha âncora com **4 ocorrências** em `google_drive.py` — `get_file`,
`download`, `trash` e `delete_permanently` validam `file_id` com o texto idêntico. Ela acertava
`get_file` **por sorte**, porque é a primeira definição do arquivo. Reordenar as funções faria o
harness mutar `download` mantendo o rótulo "get". Mesmo padrão em `mutate_google_auth.py`
(2 âncoras: `scopes_for` vs. os outros três que repetem o mesmo `raise`; `run_connect_flow` vs.
`get_service`). Nenhum mentia **ainda** — todos eram um reorder de distância de mentir.

Tranca 1 — **em cada harness** (runtime). Depois do teste de existência:

```python
if original.count(de) > 1:
    invalidas.append(nome)
    print(f"INVÁLIDA   {nome}  (trecho ambíguo: {original.count(de)} ocorrências)")
    continue
```

O harness passa a **recusar** a mutação em vez de mutar o lugar errado. Âncora ambígua vira
INVÁLIDA, e INVÁLIDA faz o harness sair com código 1 — não é silencioso.

Tranca 2 — **`tests/test_mutacao_anchors.py`** (estática, roda na suíte). Lê cada `mutate_*.py`
como módulo (sem executar `main()`), nas três variantes de estrutura (`GRUPOS`; `MUTACOES`+`ALVO`;
`MUTACOES` com caminho por tupla) e cobra que **toda âncora exista exatamente uma vez** no
arquivo-alvo. Também cobra que nenhum harness fique sem mutação e que `EQUIVALENTES` não cite
mutação inexistente.

Por que duas: a tranca 1 só age quando alguém roda o harness; a tranca 2 roda a cada suíte e
aponta a mutação pelo nome. E a tranca 2 é mais ampla — foi ela que achou as duas âncoras
ambíguas de `mutate_google_auth.py` que uma varredura manual (que só entendia `GRUPOS`) deixou
passar.

**Ao alargar uma âncora, alargue o `para` também** — inclua a mesma linha de contexto inalterada
nos dois lados, senão a mutação passa a fazer outra coisa.

## Por que estes arquivos moram no repo, e não em `/tmp`

Até 2026-09-21 os harnesses eram escritos em `/tmp`. Dos quatro produzidos naquele dia,
**dois já tinham se perdido** na limpeza de `/tmp` (`mutate_ambient_wiring.py` e o da
política de atenção), e os documentos os referenciavam por um caminho que não existia
mais. O projeto já tinha aprendido esta lição uma vez: o `/tmp/pytestenv` morreu e virou
sintoma confuso (`timeout: falha ao executar o comando`).

Um instrumento de verificação que evapora é pior que instrumento nenhum: ele deixa a
crença de que a verificação existe.

## Por que o diretório não é coletado

O `run_tests_safe.py` lista `tests/` com `os.listdir` e filtra `test_*.py` — um
**diretório** nunca casa, então nada aqui entra na suíte. O `pytest` também não coleta
(`mutate_*.py` não casa com `test_*.py`), e o `conftest.py` deste diretório declara
`collect_ignore_glob = ["*"]` como segunda tranca explícita.

Isso importa: um harness coletado como teste seria **um teste que reescreve o código
que está testando**.
