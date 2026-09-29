"""Teste de fumaça OPCIONAL contra a conta Google REAL.

**Não roda na suíte normal** — fica atrás de `ZORIN_GOOGLE_SMOKE=1`. A suíte não
pode depender de rede nem da conta do usuário: um teste que só passa com internet
é pior que nenhum, porque ensina a ignorar vermelho.

Como rodar (do raiz do repo):

    ZORIN_GOOGLE_SMOKE=1 .venv/bin/python -m pytest -q tests/test_google_smoke.py -v

O que ele exercita: cria, confere e **limpa** o que criou, em `try/finally`. É a
versão reproduzível da verificação manual que encontrou o defeito da lixeira
vazando na listagem do Drive — dobra nenhuma pegaria aquilo.

O Gmail entra só em LEITURA: um teste de fumaça que dispara e-mail de verdade é
intrusivo demais para rodar sem querer.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from zorin_copilot.core import google_auth

ENABLED = os.environ.get("ZORIN_GOOGLE_SMOKE") == "1"

_SKIP_REASON = (
    "teste de fumaça contra a conta real — defina ZORIN_GOOGLE_SMOKE=1 para rodar"
)


@unittest.skipUnless(ENABLED, _SKIP_REASON)
class GoogleSmokeTest(unittest.TestCase):
    def _require(self, service: str) -> None:
        if not google_auth.google_libs_available():
            self.skipTest("extra [google] não instalado")
        if not google_auth.is_connected(service):
            self.skipTest(f"{service} não está conectado")

    def test_calendar_roundtrip(self):
        """Cria um evento, encontra na listagem e apaga. Fuso e duração conferidos."""
        self._require("calendar")
        from zorin_copilot.core import google_calendar as gcal

        start = datetime.now() + timedelta(days=1)
        event_id = None
        try:
            event = gcal.create_event(
                "Smoke Zorin Copilot (apagar)",
                start,
                duration_minutes=20,
                description="Teste de fumaça automático; é apagado em seguida.",
            )
            event_id = event["id"]
            self.assertEqual(event["title"], "Smoke Zorin Copilot (apagar)")

            inicio = datetime.fromisoformat(event["start"])
            fim = datetime.fromisoformat(event["end"])
            self.assertIsNotNone(inicio.tzinfo, "o evento precisa sair com fuso")
            self.assertEqual((fim - inicio).total_seconds(), 20 * 60)

            encontrados = gcal.list_events("amanhã")
            self.assertIn(event_id, [item["id"] for item in encontrados])
        finally:
            if event_id:
                gcal.delete_event(event_id)

    def test_drive_roundtrip(self):
        """Pasta, upload, listagem, download íntegro, lixeira e purga."""
        self._require("drive")
        from zorin_copilot.core import google_drive as gdrive

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)

        conteudo = b"Smoke Zorin Copilot - integracao Drive\n" * 3
        origem = base / "smoke_zorin.txt"
        origem.write_bytes(conteudo)
        sha_origem = hashlib.sha256(conteudo).hexdigest()

        folder_id = None
        file_id = None
        try:
            pasta = gdrive.create_folder("Smoke Zorin Copilot (apagar)")
            folder_id = pasta["id"]

            enviado = gdrive.upload(str(origem), folder_id=folder_id)
            file_id = enviado["id"]
            self.assertEqual(enviado["parents"], [folder_id])

            destino = base / "baixado.txt"
            gdrive.download(file_id, str(destino))
            self.assertEqual(
                hashlib.sha256(destino.read_bytes()).hexdigest(),
                sha_origem,
                "o arquivo baixado precisa ser idêntico ao enviado",
            )

            # A lixeira NÃO pode aparecer na listagem padrão (defeito já corrigido).
            visiveis = [item["id"] for item in gdrive.list_files()]
            self.assertIn(file_id, visiveis)

            gdrive.trash(file_id)
            self.assertNotIn(
                file_id,
                [item["id"] for item in gdrive.list_files()],
                "arquivo na lixeira não pode reaparecer em 'meus arquivos'",
            )
        finally:
            for identificador in (file_id, folder_id):
                if identificador:
                    try:
                        gdrive.delete_permanently(identificador)
                    except Exception:  # já pode ter sido apagado; a limpeza é best-effort
                        pass

    def test_gmail_read_only(self):
        """Só LEITURA: rótulos e as mensagens mais recentes."""
        self._require("gmail")
        from zorin_copilot.core import google_gmail as gmail

        rotulos = gmail.list_labels()
        self.assertTrue(rotulos, "a conta precisa expor ao menos um rótulo")
        self.assertTrue(all("id" in item for item in rotulos))

        mensagens = gmail.list_messages(max_results=1)
        if mensagens:
            info = gmail.get_message(mensagens[0]["id"])
            self.assertIn("id", info)
            self.assertIn("labels", info)


if __name__ == "__main__":
    unittest.main()
