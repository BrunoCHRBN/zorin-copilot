"""Testes do monitor contínuo de arquivos (`DocumentFileWatcher`).

Este arquivo passou por uma reescrita em 2026-09-21, quando o módulo deixou de ser
órfão e virou peça de produção (Fase 1, P1 — ver `docs/PROPOSTA_JARVIS_AMBIENTE.md`).

**O defeito que motivou a reescrita, e que vale mais do que os testes novos:** o
teste de debounce antigo **reimplementava o laço do worker dentro do corpo do teste**
e depois assertava sobre a própria reimplementação. Ele passava nos dois estados —
com o worker correto e com o worker quebrado — porque nunca chamava o worker. Um
teste que copia a lógica que deveria verificar não é um teste fraco: é um teste que
**mente**, e é pior do que não existir, porque ocupa o lugar do que faltava.

Aqui o worker de verdade é exercitado, numa thread de verdade, com `debounce` curto e
espera por evento (nunca `sleep` fixo no lugar de uma asserção). O contrato de
**thread** do callback também está travado: `"delete"` chega pela thread de quem
dispara o sinal do Gio (o laço principal), `"index"` chega pela thread de debounce.
Quem confundir os dois e mexer em GTK no caminho do `delete` derruba a árvore de
widgets — e é isso que o teste impede que aconteça por engano.
"""

from __future__ import annotations

import tempfile
import threading
import time
import typing
import unittest
from pathlib import Path
from unittest import mock

from zorin_copilot.core import file_watcher as fw_module
from zorin_copilot.core.file_watcher import DocumentFileWatcher

#: Curto de propósito: o `_debounce_worker` dorme 0,5 s entre ciclos, então cada
#: teste que exercita o worker custa ~0,6 s. Com `debounce` de 1,5 s (o default) a
#: espera seria a mesma, mas o teste dependeria de o ciclo pegar o prazo já vencido.
DEBOUNCE = 0.05


class _FakeMonitor:
    """Só o que o watcher usa do `Gio.FileMonitor`: `cancel()`."""

    def __init__(self):
        self.cancelado = False

    def cancel(self):
        self.cancelado = True


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.rag = mock.MagicMock()
        self.rag.index_file.return_value = True
        self.eventos: list[tuple[str, str]] = []
        self.watcher = DocumentFileWatcher(
            rag=self.rag,
            directories=[self.dir],
            debounce_seconds=DEBOUNCE,
            on_event_callback=lambda ev, p: self.eventos.append((ev, p)),
        )
        self.addCleanup(self.watcher.stop)

    # -- utilidades -----------------------------------------------------

    def _arquivo(self, nome: str = "contrato.pdf") -> Path:
        """Cria o arquivo **de verdade**: o worker checa `exists() and is_file()`.

        Sem isto o worker pula o caminho e o teste de indexação passaria a medir o
        caminho de *pulo*, não o de indexação — verde pelo motivo errado.
        """
        caminho = self.dir / nome
        caminho.write_text("conteúdo do contrato", encoding="utf-8")
        return caminho

    def _evento(self, caminho: Path, nick: str):
        """Um `Gio.FileMonitorEvent` falso com o `value_nick` que o módulo lê."""
        arquivo = mock.MagicMock()
        arquivo.get_path.return_value = str(caminho)
        tipo = mock.MagicMock()
        tipo.value_nick = nick
        return arquivo, tipo

    def _disparar(self, caminho: Path, nick: str = "changed"):
        arquivo, tipo = self._evento(caminho, nick)
        self.watcher._on_file_changed(None, arquivo, None, tipo)
        return arquivo, tipo

    def _subir_worker(self) -> threading.Thread:
        """Sobe o **worker real** numa thread, registrada para o `stop` juntar."""
        self.watcher._is_running = True
        t = threading.Thread(target=self.watcher._debounce_worker, daemon=True)
        self.watcher._worker_thread = t
        t.start()
        return t

    def _esperar(self, quantos: int = 1, teto: float = 4.0) -> bool:
        limite = time.monotonic() + teto
        while time.monotonic() < limite:
            if len(self.eventos) >= quantos:
                return True
            time.sleep(0.02)
        return False


# ---------------------------------------------------------------------------
# O filtro de entrada
# ---------------------------------------------------------------------------
class FiltroDeEntradaTest(_Base):
    def test_caminho_vazio_e_ignorado(self):
        arquivo = mock.MagicMock()
        arquivo.get_path.return_value = None
        tipo = mock.MagicMock()
        tipo.value_nick = "changed"
        self.watcher._on_file_changed(None, arquivo, None, tipo)
        self.assertEqual(self.eventos, [])
        self.assertEqual(self.watcher._pending_updates, {})

    def test_extensao_nao_suportada_e_ignorada(self):
        """Sem este filtro, cada `.o` de compilação entraria na fila de indexação."""
        self._disparar(self.dir / "objeto.o")
        self.assertEqual(self.watcher._pending_updates, {})
        self.assertEqual(self.eventos, [])

    def test_extensao_suportada_entra_na_fila(self):
        caminho = self._arquivo()
        self._disparar(caminho)
        self.assertEqual(list(self.watcher._pending_updates), [str(caminho)])

    def test_a_maiuscula_nao_escapa_do_filtro(self):
        """`Relatorio.PDF` é o mesmo arquivo que `relatorio.pdf`."""
        caminho = self._arquivo("Relatorio.PDF")
        self._disparar(caminho)
        self.assertEqual(list(self.watcher._pending_updates), [str(caminho)])


# ---------------------------------------------------------------------------
# O caminho de exclusão — síncrono, e por isso o mais perigoso
# ---------------------------------------------------------------------------
class ExclusaoTest(_Base):
    def test_exclusao_chama_o_rag_e_o_consumidor(self):
        caminho = self.dir / "velho.pdf"
        self._disparar(caminho, "deleted")
        self.rag.delete_document.assert_called_once_with(caminho)
        self.assertEqual(self.eventos, [("delete", str(caminho))])

    def test_exclusao_roda_na_thread_de_quem_dispara_o_sinal(self):
        """**O contrato de thread.** O sinal `changed` do Gio é entregue pelo
        `GMainContext`, ou seja: na thread do laço principal — a mesma do GTK.

        Este teste é o que impede alguém de concluir, por analogia com o caminho de
        `index`, que o callback do watcher sempre chega fora da thread da UI. Para
        `delete` ele **está** na thread da UI; um `show_toast` direto aqui
        corromperia a árvore de widgets. Quem precisar de GTK use `GLib.idle_add`.
        """
        thread_do_callback: list[str] = []
        self.watcher.on_event_callback = lambda ev, p: thread_do_callback.append(
            threading.current_thread().name
        )
        self._disparar(self.dir / "velho.pdf", "deleted")
        self.assertEqual(thread_do_callback, [threading.current_thread().name])
        self.assertNotIn("debounce", thread_do_callback[0].lower())

    def test_exclusao_nao_e_debounced(self):
        """Apagar é imediato: segurar a remoção por 1,5 s deixaria o índice mentindo."""
        self._disparar(self.dir / "velho.pdf", "deleted")
        self.assertEqual(self.watcher._pending_updates, {})

    def test_exclusao_cancela_um_agendamento_pendente(self):
        """Salvar e apagar em menos de `debounce` não pode indexar um arquivo morto."""
        caminho = self._arquivo("efemero.pdf")
        self._disparar(caminho, "changed")
        self.assertIn(str(caminho), self.watcher._pending_updates)
        self._disparar(caminho, "deleted")
        self.assertNotIn(str(caminho), self.watcher._pending_updates)

    def test_variante_do_nick_tambem_casa(self):
        """`value_nick` varia (`deleted`, `DELETE`, `moved_out`…): a checagem é por
        substring, em minúsculas, de propósito."""
        caminho = self.dir / "x.pdf"
        self._disparar(caminho, "DELETED")
        self.assertEqual(self.eventos, [("delete", str(caminho))])


# ---------------------------------------------------------------------------
# O worker de verdade
# ---------------------------------------------------------------------------
class WorkerTest(_Base):
    def test_o_agendamento_nao_indexa_na_hora(self):
        """O `debounce` existe para esperar o *save* terminar, não para enfeitar."""
        self._disparar(self._arquivo())
        self.rag.index_file.assert_not_called()

    def test_o_prazo_agendado_fica_no_futuro(self):
        """Agendar para **agora** é o defeito silencioso do debounce.

        Com `time.time()` no lugar de `time.time() + debounce`, tudo o que os outros
        testes cobram continuaria passando: o arquivo seria indexado de qualquer
        jeito — só que sem esperar o *save* terminar, que é exatamente o caso que o
        `debounce` existe para cobrir (download grande, editor que salva em pedaços).
        Este é o único teste que separa "esperou" de "não esperou".
        """
        caminho = self._arquivo()
        antes = time.time()
        self._disparar(caminho)
        prazo = self.watcher._pending_updates[str(caminho)]
        self.assertGreaterEqual(
            prazo - antes,
            DEBOUNCE * 0.8,
            "o evento foi agendado para agora: o debounce não empurrou o prazo",
        )

    def test_um_debounce_longo_segura_mesmo_com_o_worker_rodando(self):
        """Cobrança comportamental do mesmo contrato, com margem folgada.

        `debounce` de 1,5 s (o default de produção) contra uma janela de observação
        de 0,8 s: se o worker indexar dentro dela, o prazo não estava sendo honrado.
        """
        watcher = DocumentFileWatcher(
            rag=self.rag,
            directories=[self.dir],
            debounce_seconds=1.5,
            on_event_callback=lambda ev, p: self.eventos.append((ev, p)),
        )
        self.addCleanup(watcher.stop)
        caminho = self._arquivo()
        arquivo, tipo = self._evento(caminho, "changed")
        watcher._on_file_changed(None, arquivo, None, tipo)

        watcher._is_running = True
        t = threading.Thread(target=watcher._debounce_worker, daemon=True)
        watcher._worker_thread = t
        t.start()

        time.sleep(0.8)
        self.rag.index_file.assert_not_called()
        self.assertEqual(self.eventos, [])

        self.assertTrue(self._esperar(teto=4.0), "não indexou nem depois do prazo")
        self.rag.index_file.assert_called_once_with(caminho)

    def test_o_worker_indexa_depois_do_debounce(self):
        arquivo = self._arquivo()
        self._disparar(arquivo)
        self._subir_worker()
        self.assertTrue(self._esperar(), "o worker não indexou dentro do teto")
        self.rag.index_file.assert_called_once_with(arquivo)
        self.assertEqual(self.eventos, [("index", str(arquivo))])

    def test_uma_rajada_de_escritas_vira_uma_indexacao(self):
        """É o ponto do debounce: o editor salva 5 vezes, o índice indexa 1."""
        arquivo = self._arquivo()
        self._subir_worker()
        for _ in range(5):
            self._disparar(arquivo)
            time.sleep(0.01)
        self.assertTrue(self._esperar())
        time.sleep(0.7)  # deixa um ciclo inteiro passar
        self.rag.index_file.assert_called_once_with(arquivo)
        self.assertEqual(len(self.eventos), 1)

    def test_arquivo_que_sumiu_antes_do_prazo_e_pulado(self):
        """O evento chega, o arquivo é apagado no meio do debounce: não há o que indexar."""
        arquivo = self._arquivo("fantasma.pdf")
        self._disparar(arquivo)
        arquivo.unlink()  # some antes do prazo vencer
        self._subir_worker()
        time.sleep(0.8)
        self.rag.index_file.assert_not_called()
        self.assertEqual(self.eventos, [])

    def test_indexacao_que_devolve_false_nao_avisa_o_consumidor(self):
        """`index_file` devolve False quando o arquivo não mudou desde a última vez.

        O consumidor recebe "index" só quando houve indexação de verdade — senão o
        contador de selos da política de atenção subiria a cada `touch` repetido.
        """
        self.rag.index_file.return_value = False
        self._disparar(self._arquivo())
        self._subir_worker()
        time.sleep(0.8)
        self.rag.index_file.assert_called_once()
        self.assertEqual(self.eventos, [])

    def test_excecao_na_indexacao_nao_mata_o_worker(self):
        """Um arquivo corrompido não pode parar a indexação contínua da sessão toda."""
        quebrado, bom = self._arquivo("quebrado.pdf"), self._arquivo("bom.pdf")
        self.rag.index_file.side_effect = [RuntimeError("PDF quebrado"), True]
        self._disparar(quebrado)
        self._disparar(bom)
        self._subir_worker()
        self.assertTrue(self._esperar(teto=4.0), "o worker morreu na exceção")
        self.assertEqual(self.rag.index_file.call_count, 2)
        self.assertEqual(self.eventos, [("index", str(bom))])

    def test_o_worker_para_quando_manda_parar(self):
        t = self._subir_worker()
        self.watcher.stop()
        self.assertFalse(t.is_alive(), "o worker sobreviveu ao stop")
        self.assertFalse(self.watcher._is_running)


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------
class CicloDeVidaTest(_Base):
    def test_sem_gio_o_start_devolve_false(self):
        """`False`, e não uma exceção: quem chama trata como "recurso indisponível"."""
        with mock.patch.object(fw_module, "HAS_GIO", False):
            self.assertFalse(self.watcher.start())
        self.assertFalse(self.watcher._is_running)

    def test_start_e_idempotente(self):
        self.watcher._is_running = True
        self.assertTrue(self.watcher.start())
        self.assertIsNone(self.watcher._worker_thread)

    def test_diretorio_inexistente_nao_derruba_o_start(self):
        """Uma pasta confiável que não existe (pendrive fora) não pode ser fatal."""
        watcher = DocumentFileWatcher(
            rag=self.rag, directories=[Path("/tmp/nao-existe-mesmo-xyz")]
        )
        self.addCleanup(watcher.stop)
        if not fw_module.HAS_GIO:  # pragma: no cover - ambiente sem PyGObject
            self.skipTest("sem PyGObject/Gio neste ambiente")
        self.assertTrue(watcher.start())
        self.assertEqual(watcher._monitors, [])

    def test_stop_e_idempotente_e_cancela_os_monitores(self):
        m1, m2 = _FakeMonitor(), _FakeMonitor()
        self.watcher._monitors = [m1, m2]
        self.watcher.stop()
        self.watcher.stop()
        self.assertTrue(m1.cancelado and m2.cancelado)
        self.assertEqual(self.watcher._monitors, [])

    def test_stop_descarta_o_trabalho_pendente(self):
        """Parar e continuar indexando o que ficou na fila seria indexar depois do
        teardown — com o RAG possivelmente já fechado."""
        self._disparar(self._arquivo())
        self.watcher.stop()
        self.assertEqual(self.watcher._pending_updates, {})

    def test_stop_nao_levanta_com_monitor_que_falha_ao_cancelar(self):
        quebrado = mock.MagicMock()
        quebrado.cancel.side_effect = RuntimeError("barramento morto")
        self.watcher._monitors = [quebrado]
        self.watcher.stop()  # não pode levantar

    def test_stop_sem_nunca_ter_subido_nao_levanta(self):
        DocumentFileWatcher(rag=self.rag, directories=[self.dir]).stop()


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------
class AnotacoesResolvemTest(unittest.TestCase):
    """`Any` era usado em `_on_file_changed` **sem estar importado**.

    Não quebrava em execução porque `from __future__ import annotations` transforma
    toda anotação em string — o `NameError` só aparece quando alguém **resolve** as
    anotações: mypy, IDE, `typing.get_type_hints`. Defeito silencioso clássico: o
    módulo funciona, a ferramenta que o lê é que não.

    A varredura é sobre a classe inteira, não sobre um método, para que a próxima
    anotação com nome inexistente caia aqui em vez de cair num relatório de tipo que
    ninguém lê.
    """

    def test_toda_anotacao_da_classe_resolve(self):
        falhas: list[str] = []
        for nome in dir(DocumentFileWatcher):
            if nome.startswith("__"):
                continue
            alvo = getattr(DocumentFileWatcher, nome)
            if not callable(alvo):
                continue
            try:
                typing.get_type_hints(alvo)
            except Exception as exc:  # noqa: BLE001 - é o ponto do teste
                falhas.append(f"{nome}: {type(exc).__name__}: {exc}")
        self.assertEqual(falhas, [], "anotação sem nome definido:\n  " + "\n  ".join(falhas))


class ContratoDeThreadDocumentadoTest(unittest.TestCase):
    """O contrato de thread está escrito no módulo, não só nos testes.

    Um contrato que só existe no teste é descoberto pelo próximo autor depois de
    escrever o código errado. Aqui se cobra que o cabeçalho do módulo continue
    nomeando as **duas** threads — se alguém reescrever a docstring e apagar isso,
    o teste falha antes de o defeito chegar ao app.
    """

    def test_o_cabecalho_nomeia_as_duas_threads(self):
        fonte = fw_module.__doc__ or ""
        self.assertIn("GMainContext", fonte)
        self.assertIn("_debounce_worker", fonte)
        self.assertIn("GLib.idle_add", fonte)


if __name__ == "__main__":
    unittest.main()
