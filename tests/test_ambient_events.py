"""Testes do envelope de eventos do ambiente — puros, sem display.

O que se testa aqui é o **vocabulário**: que cada formato cru vire a mesma forma, e
que o envelope não invente nem descarte nada. A regra de "o que fazer com o evento"
mora em `core/attention.py` e tem suíte própria.

As guardas de deriva (`DerivaDosProdutoresTest`) leem o **código-fonte** dos
produtores. É de propósito: o defeito que elas existem para pegar é um produtor mudar
a palavra que emite e o adaptador continuar esperando a antiga — o sintoma seria
silêncio total, sem erro nenhum, indistinguível de "não houve evento".
"""

from __future__ import annotations

import inspect
import json
import unittest

from zorin_copilot.core import ambient_events
from zorin_copilot.core.ambient_events import (
    AmbientEvent,
    AmbientSource,
    DocumentKind,
    document_event,
    home_event,
)


def _estado(**overrides):
    base = {
        "kind": "state",
        "entity_id": "light.sala",
        "domain": "light",
        "state": "on",
        "old_state": "off",
        "attributes": {},
    }
    base.update(overrides)
    return base


class VocabularioTest(unittest.TestCase):
    def test_as_fontes_estao_fixadas(self):
        """Renomear uma fonte exige atualizar quem a lê — então quebre o teste."""
        self.assertEqual(
            {m.value for m in AmbientSource}, {"home", "documents"}
        )

    def test_os_tipos_de_documento_estao_fixados(self):
        self.assertEqual({m.value for m in DocumentKind}, {"index", "delete"})

    def test_o_envelope_e_imutavel(self):
        """Congelado para que um consumidor não possa reescrever o evento do outro."""
        evento = home_event(_estado())
        with self.assertRaises(Exception):
            evento.subject = "outro"  # type: ignore[misc]


class EventoDaCasaTest(unittest.TestCase):
    def test_evento_de_estado_vira_envelope(self):
        evento = home_event(_estado())
        self.assertIsNotNone(evento)
        self.assertIs(evento.source, AmbientSource.HOME)
        self.assertEqual(evento.kind, "state")
        self.assertEqual(evento.subject, "light.sala")

    def test_o_payload_e_o_evento_cru_sem_copia(self):
        """O consumidor precisa do formato original para decidir a severidade.

        Identidade, e não igualdade: uma cópia profunda a cada evento custaria caro
        no caminho quente do canal, e uma cópia rasa seria a mesma coisa com mais
        código.
        """
        cru = _estado()
        self.assertIs(home_event(cru).payload, cru)

    def test_evento_de_conexao_nao_e_percepcao(self):
        """Medido: o canal também emite `kind="connection"` e `event_matches` deixa
        passar **de propósito**. "A conexão caiu" é saúde do canal, não da casa."""
        self.assertIsNone(home_event(_estado(kind="connection")))

    def test_entrada_que_nao_e_dicionario_nao_levanta(self):
        for lixo in (None, [], "state", 7, object()):
            with self.subTest(lixo=repr(lixo)):
                self.assertIsNone(home_event(lixo))

    def test_evento_sem_entidade_ainda_vira_envelope(self):
        """O canal não garante `entity_id`; o filtro é de quem decide, não daqui."""
        evento = home_event(_estado(entity_id=None))
        self.assertEqual(evento.subject, "")

    def test_a_entidade_vem_sem_espaco(self):
        self.assertEqual(home_event(_estado(entity_id="  light.sala  ")).subject, "light.sala")


class EventoDeDocumentoTest(unittest.TestCase):
    def test_indexacao_vira_envelope(self):
        evento = document_event("index", "/home/u/Documentos/contrato.pdf")
        self.assertIs(evento.source, AmbientSource.DOCUMENTS)
        self.assertEqual(evento.kind, "index")
        self.assertEqual(evento.subject, "/home/u/Documentos/contrato.pdf")

    def test_exclusao_vira_envelope(self):
        self.assertEqual(document_event("delete", "/tmp/x.pdf").kind, "delete")

    def test_tipo_desconhecido_nao_vira_evento(self):
        """O envelope não transporta o que ninguém sabe interpretar."""
        for tipo in ("falhou", "", None, 7, "INDEX"):
            with self.subTest(tipo=repr(tipo)):
                self.assertIsNone(document_event(tipo, "/tmp/x.pdf"))

    def test_caminho_vazio_nao_vira_evento(self):
        for caminho in ("", "   ", None):
            with self.subTest(caminho=repr(caminho)):
                self.assertIsNone(document_event("index", caminho))

    def test_o_caminho_vem_sem_espaco(self):
        """Um caminho com espaço nas pontas não casaria com o arquivo no disco."""
        evento = document_event("index", "  /tmp/x.pdf  ")
        self.assertEqual(evento.subject, "/tmp/x.pdf")
        self.assertEqual(evento.payload["path"], "/tmp/x.pdf")

    def test_o_payload_traz_o_caminho(self):
        self.assertEqual(
            document_event("index", "/tmp/x.pdf").payload, {"path": "/tmp/x.pdf"}
        )


class FormaDoEnvelopeTest(unittest.TestCase):
    def test_to_dict_nao_leva_o_payload_cru(self):
        """Ele vai para log e para resposta de ferramenta — caminho de arquivo e o
        estado inteiro de uma entidade não pertencem a esse lugar."""
        d = home_event(_estado()).to_dict()
        self.assertEqual(set(d), {"source", "kind", "subject"})
        self.assertNotIn("payload", d)

    def test_to_dict_e_serializavel(self):
        """Se não serializa, o log quebra no pior momento: durante o evento."""
        for evento in (
            home_event(_estado()),
            document_event("index", "/tmp/x.pdf"),
        ):
            with self.subTest(evento=evento.kind):
                json.dumps(evento.to_dict(), ensure_ascii=False)

    def test_os_dois_produtores_produzem_a_mesma_forma(self):
        """A razão de o envelope existir: um consumidor, dois produtores."""
        casa = home_event(_estado())
        arquivo = document_event("index", "/tmp/x.pdf")
        self.assertEqual(
            set(casa.to_dict()), set(arquivo.to_dict()), "as formas divergiram"
        )


class DerivaDosProdutoresTest(unittest.TestCase):
    """O adaptador e o produtor têm de falar a mesma palavra.

    Sem estas guardas, um produtor que passasse a emitir `kind="state_changed"` (ou
    `"deleted"`) faria o adaptador devolver `None` para **todo** evento. O sintoma
    seria silêncio absoluto, sem exceção e sem log — o modo de falha mais caro de
    diagnosticar que existe neste projeto.
    """

    def _fonte(self, modulo) -> str:
        return inspect.getsource(modulo)

    def test_o_canal_do_ha_emite_o_kind_que_o_adaptador_espera(self):
        from zorin_copilot.core import home_assistant_realtime

        fonte = self._fonte(home_assistant_realtime)
        self.assertIn(
            f'"kind": "{ambient_events.HOME_STATE}"',
            fonte,
            "o canal não emite mais este `kind`: o adaptador descartaria todo evento",
        )

    def test_o_monitor_de_arquivos_emite_os_tipos_que_o_adaptador_espera(self):
        from zorin_copilot.core import file_watcher

        fonte = self._fonte(file_watcher)
        for tipo in DocumentKind:
            with self.subTest(tipo=tipo.value):
                self.assertIn(
                    f'on_event_callback("{tipo.value}"',
                    fonte,
                    f"o monitor não emite mais '{tipo.value}': este ramo viraria inalcançável",
                )

    def test_o_canal_tambem_emite_conexao_e_nao_e_erro_descartar(self):
        """Fixa o **motivo** de `home_event` ter o filtro, para ninguém "otimizá-lo".

        Se `kind="connection"` deixasse de existir, o filtro viraria código morto e
        esta guarda avisaria.
        """
        from zorin_copilot.core import home_assistant_realtime

        self.assertIn('"kind": "connection"', self._fonte(home_assistant_realtime))


class ModuloPuroTest(unittest.TestCase):
    """Se alguém importar GTK ou rede aqui, o vocabulário deixa de ser testável
    sem display — que é o motivo de o módulo existir separado dos produtores."""

    def test_sem_gtk(self):
        fonte = inspect.getsource(ambient_events)
        for proibido in ("import gi", "from gi.repository", "Adw", "Gtk.", "GLib"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, fonte)

    def test_sem_rede_audio_relogio(self):
        fonte = inspect.getsource(ambient_events)
        for proibido in ("requests", "subprocess", "socket", "sqlite3", "piper",
                         "time.time", "datetime.now", "monotonic", "threading"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, fonte)

    def test_nao_importa_os_produtores(self):
        """O adaptador conhece o **formato**, não o módulo.

        Importar `file_watcher` aqui traria junto o `rag` e o Gio — e o envelope
        deixaria de ser importável em teste headless.
        """
        fonte = inspect.getsource(ambient_events)
        for proibido in ("file_watcher", "home_assistant", "rag"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(f"import {proibido}", fonte)


if __name__ == "__main__":
    unittest.main()
