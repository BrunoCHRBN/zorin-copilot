# Guarda estática dos harnesses de mutação (`tests/mutacao/mutate_*.py`).
#
# Os harnesses NÃO são coletados pelo pytest — de propósito: um harness coletado como teste
# reescreveria o código que está testando (ver `tests/mutacao/LEIAME.md`). O efeito colateral
# é que uma âncora que apodrece ou fica ambígua só se descobre quando alguém roda o harness —
# e, nesse momento, o relatório MENTE:
#   - âncora ausente  -> "INVÁLIDA" (a mutação nunca foi aplicada);
#   - âncora ambígua  -> `replace(..., 1)` muta a PRIMEIRA ocorrência, que pode não ser a
#     função descrita no nome da mutação. O harness então reporta KILLED/SURVIVED de um
#     defeito que não é o que ele diz estar testando.
#
# Este teste fecha a lacuna sem coletar os harnesses: lê cada um como módulo (não executa
# `main()`) e cobra que toda âncora exista EXATAMENTE UMA VEZ no arquivo-alvo.
#
# Caso real que motivou este teste (2026-09-22): a mutação "get: id vazio deixa de ser
# rejeitado" de `mutate_google_drive.py` tinha âncora com 4 ocorrências em `google_drive.py`
# (as validações de `get_file`, `download`, `trash` e `delete_permanently` são idênticas).
# Ela acertava `get_file` **por sorte**, porque é a primeira definição do arquivo. Reordenar
# as funções faria o harness mutar `download` mantendo o rótulo "get".

"""Guarda estática dos harnesses de mutação: âncora única, alvo existente, EQUIVALENTES coerente."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DIR_MUTACAO = RAIZ / "tests" / "mutacao"


def _carregar(caminho: Path):
    """Importa o harness como módulo (o `main()` fica atrás de `__main__`)."""
    spec = importlib.util.spec_from_file_location(f"_harness_{caminho.stem}", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - defensivo
        raise RuntimeError(f"não foi possível carregar {caminho}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _mutacoes(modulo) -> list[tuple[Path, str, str]]:
    """Extrai ``(arquivo_alvo, nome, âncora)`` das três variantes de estrutura em uso.

    - `GRUPOS` + listas por arquivo (`mutate_risk`, `mutate_google_{calendar,drive,gmail}`,
      `mutate_preferences_google`);
    - `MUTACOES` + `ALVO` único (`mutate_google_auth`, `mutate_file_watcher`);
    - `MUTACOES` com o caminho em cada tupla (`mutate_identity`).
    """
    if hasattr(modulo, "GRUPOS"):
        return [
            (Path(alvo), nome, de)
            for alvo, lista in modulo.GRUPOS
            for nome, de, _para in lista
        ]
    if hasattr(modulo, "MUTACOES"):
        if hasattr(modulo, "ALVO"):
            return [(Path(modulo.ALVO), nome, de) for nome, de, _para in modulo.MUTACOES]
        return [(Path(caminho), nome, de) for caminho, nome, de, _para in modulo.MUTACOES]
    return []


def _harnesses() -> list[Path]:
    return sorted(DIR_MUTACAO.glob("mutate_*.py"))


class AncorasDosHarnessesTest(unittest.TestCase):
    """Toda âncora de mutação aponta para um trecho único de um arquivo existente."""

    def test_ha_harnesses_e_cada_um_tem_mutacao(self):
        arquivos = _harnesses()
        self.assertGreater(len(arquivos), 0, "nenhum harness de mutação encontrado")
        vazios = [a.name for a in arquivos if not _mutacoes(_carregar(a))]
        # Um harness sem mutação passa com "0 sobreviventes" e mente por omissão.
        self.assertEqual(vazios, [], f"harnesses sem nenhuma mutação: {vazios}")

    def test_toda_ancora_existe_exatamente_uma_vez(self):
        problemas: list[str] = []
        total = 0
        for arquivo in _harnesses():
            for alvo, nome, de in _mutacoes(_carregar(arquivo)):
                total += 1
                if not alvo.exists():
                    problemas.append(f"{arquivo.name} / {nome}: alvo não existe ({alvo})")
                    continue
                ocorrencias = alvo.read_text(encoding="utf-8").count(de)
                if ocorrencias == 0:
                    problemas.append(
                        f"{arquivo.name} / {nome}: âncora AUSENTE em {alvo.name} "
                        "(a mutação nunca é aplicada)"
                    )
                elif ocorrencias > 1:
                    problemas.append(
                        f"{arquivo.name} / {nome}: âncora AMBÍGUA ({ocorrencias}x) em "
                        f"{alvo.name} (mutaria a primeira ocorrência, não a descrita)"
                    )
        self.assertGreater(total, 0, "nenhuma mutação encontrada nos harnesses")
        self.assertEqual(problemas, [], "\n" + "\n".join(problemas))

    def test_equivalentes_citam_mutacoes_existentes(self):
        """Um `EQUIVALENTES` com nome renomeado para de classificar em silêncio."""
        problemas: list[str] = []
        for arquivo in _harnesses():
            modulo = _carregar(arquivo)
            nomes = {nome for _alvo, nome, _de in _mutacoes(modulo)}
            for citado in getattr(modulo, "EQUIVALENTES", {}):
                if citado not in nomes:
                    problemas.append(
                        f"{arquivo.name}: EQUIVALENTES cita mutação inexistente: {citado}"
                    )
        self.assertEqual(problemas, [], "\n" + "\n".join(problemas))


if __name__ == "__main__":
    unittest.main()
