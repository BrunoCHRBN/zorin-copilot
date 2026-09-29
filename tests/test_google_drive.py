"""Testes do cliente do Google Drive (`core/google_drive.py`) e do roteamento da
ferramenta `google_drive`.

Sem rede: o cliente aceita um `service` injetado e o upload aceita `media_body`
injetado. Testa-se a DECISÃO e o que é enviado à API — não a API da Google.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zorin_copilot.core import google_drive as gdrive


class _FakeExec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeFiles:
    def __init__(self, calls):
        self.calls = calls

    def list(self, **kwargs):
        self.calls["list"] = kwargs
        return _FakeExec(
            {
                "files": [
                    {
                        "id": "f1",
                        "name": "notas.txt",
                        "mimeType": "text/plain",
                        "size": "10",
                        "modifiedTime": "2026-09-22T10:00:00.000Z",
                        "webViewLink": "https://drive.google.com/file/d/f1",
                        "parents": ["root"],
                    }
                ]
            }
        )

    def get(self, fileId, fields=None):
        self.calls["get"] = {"fileId": fileId, "fields": fields}
        return _FakeExec({"id": fileId, "name": "notas.txt", "parents": ["root"]})

    def create(self, body=None, media_body=None, fields=None):
        self.calls["create"] = {"body": body, "media_body": media_body, "fields": fields}
        body = body or {}
        return _FakeExec(
            {
                "id": "novo-1",
                "name": body.get("name", ""),
                "mimeType": body.get("mimeType", ""),
                "parents": body.get("parents", []),
                "webViewLink": "https://drive.google.com/file/d/novo-1",
            }
        )

    def update(self, fileId, body=None, addParents=None, removeParents=None, fields=None):
        self.calls["update"] = {
            "fileId": fileId,
            "body": body,
            "addParents": addParents,
            "removeParents": removeParents,
            "fields": fields,
        }
        body = body or {}
        return _FakeExec(
            {
                "id": fileId,
                "name": body.get("name", "antigo.txt"),
                "parents": [addParents] if addParents else [],
                "trashed": bool(body.get("trashed")),
            }
        )

    def get_media(self, fileId):
        self.calls["get_media"] = {"fileId": fileId}
        return _FakeExec(b"conteudo-binario")

    def delete(self, fileId):
        self.calls["delete"] = {"fileId": fileId}
        return _FakeExec({})


class FakeDriveService:
    def __init__(self):
        self.calls: dict = {}

    def files(self):
        return _FakeFiles(self.calls)


class ScopeNoteTest(unittest.TestCase):
    def test_mentions_the_restricted_scope(self):
        note = gdrive.scope_note()
        self.assertIn("drive.file", note)


class ListAndSearchTest(unittest.TestCase):
    def test_list_params_and_normalization(self):
        svc = FakeDriveService()
        files = gdrive.list_files(max_results=7, service=svc)
        params = svc.calls["list"]
        self.assertEqual(params["pageSize"], 7)
        self.assertIn("files(", params["fields"])
        self.assertEqual(files[0]["name"], "notas.txt")
        self.assertEqual(files[0]["mime_type"], "text/plain")
        self.assertEqual(files[0]["web_view_link"], "https://drive.google.com/file/d/f1")

    def test_list_without_query_excludes_trash(self):
        """Achado da execução ao vivo: sem filtro, a API devolvia a lixeira junto.

        Um arquivo mandado para a lixeira reaparecia em "meus arquivos". O padrão
        agora filtra `trashed = false`.
        """
        svc = FakeDriveService()
        gdrive.list_files(service=svc)
        self.assertEqual(svc.calls["list"]["q"], "trashed = false")

    def test_list_with_query_sends_q(self):
        svc = FakeDriveService()
        gdrive.list_files(query="trashed = false", service=svc)
        self.assertEqual(svc.calls["list"]["q"], "trashed = false")

    def test_list_with_explicit_trash_query_is_respected(self):
        svc = FakeDriveService()
        gdrive.list_files(query="trashed = true", service=svc)
        self.assertEqual(svc.calls["list"]["q"], "trashed = true")

    def test_search_builds_name_query(self):
        svc = FakeDriveService()
        gdrive.search("relatório", service=svc)
        self.assertEqual(svc.calls["list"]["q"], "name contains 'relatório' and trashed = false")

    def test_search_escapes_single_quote(self):
        svc = FakeDriveService()
        gdrive.search("o'brien", service=svc)
        self.assertEqual(svc.calls["list"]["q"], "name contains 'o\\'brien' and trashed = false")

    def test_search_empty_term_returns_nothing_without_calling_api(self):
        svc = FakeDriveService()
        self.assertEqual(gdrive.search("   ", service=svc), [])
        self.assertNotIn("list", svc.calls)


class GetFileTest(unittest.TestCase):
    def test_get_normalizes(self):
        svc = FakeDriveService()
        info = gdrive.get_file("f1", service=svc)
        self.assertEqual(info["id"], "f1")
        self.assertEqual(svc.calls["get"]["fileId"], "f1")

    def test_empty_id_raises(self):
        with self.assertRaises(ValueError):
            gdrive.get_file("", service=FakeDriveService())


class UploadTest(unittest.TestCase):
    def _tmp_file(self, name="notas.txt", content=b"oi"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / name
        path.write_bytes(content)
        return path

    def test_upload_uses_filename_as_default_name(self):
        svc = FakeDriveService()
        path = self._tmp_file()
        info = gdrive.upload(str(path), service=svc, media_body=object())
        self.assertEqual(svc.calls["create"]["body"]["name"], "notas.txt")
        self.assertEqual(info["name"], "notas.txt")

    def test_upload_with_folder_sets_parents(self):
        svc = FakeDriveService()
        path = self._tmp_file()
        gdrive.upload(str(path), name="custom.txt", folder_id="pasta-1", service=svc, media_body=object())
        body = svc.calls["create"]["body"]
        self.assertEqual(body["name"], "custom.txt")
        self.assertEqual(body["parents"], ["pasta-1"])

    def test_upload_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            gdrive.upload("/nao/existe/arquivo.txt", service=FakeDriveService(), media_body=object())


class DownloadTest(unittest.TestCase):
    def test_download_writes_bytes_and_creates_parents(self):
        svc = FakeDriveService()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dest = Path(tmp.name) / "sub" / "saida.bin"
        written = gdrive.download("f1", str(dest), service=svc)
        self.assertEqual(written, str(dest))
        self.assertEqual(dest.read_bytes(), b"conteudo-binario")
        self.assertEqual(svc.calls["get_media"]["fileId"], "f1")

    def test_empty_id_raises(self):
        with self.assertRaises(ValueError):
            gdrive.download("", "/tmp/x", service=FakeDriveService())


class FolderTest(unittest.TestCase):
    def test_create_folder_sets_folder_mimetype(self):
        svc = FakeDriveService()
        info = gdrive.create_folder("Projetos", service=svc)
        self.assertEqual(svc.calls["create"]["body"]["mimeType"], gdrive.MIME_FOLDER)
        self.assertEqual(info["name"], "Projetos")

    def test_create_folder_empty_name_raises(self):
        with self.assertRaises(ValueError):
            gdrive.create_folder("  ", service=FakeDriveService())


class MoveTest(unittest.TestCase):
    def test_move_adds_new_parent_and_removes_old(self):
        svc = FakeDriveService()
        gdrive.move("f1", "pasta-2", service=svc)
        call = svc.calls["update"]
        self.assertEqual(call["addParents"], "pasta-2")
        self.assertEqual(call["removeParents"], "root", "sem remover o pai antigo o arquivo ficaria em duas pastas")

    def test_move_missing_ids_raise(self):
        with self.assertRaises(ValueError):
            gdrive.move("", "pasta", service=FakeDriveService())
        with self.assertRaises(ValueError):
            gdrive.move("f1", "", service=FakeDriveService())


class RenameTest(unittest.TestCase):
    def test_rename_updates_name(self):
        svc = FakeDriveService()
        info = gdrive.rename("f1", "novo.txt", service=svc)
        self.assertEqual(svc.calls["update"]["body"], {"name": "novo.txt"})
        self.assertEqual(info["name"], "novo.txt")

    def test_rename_empty_raises(self):
        with self.assertRaises(ValueError):
            gdrive.rename("f1", "  ", service=FakeDriveService())


class TrashTest(unittest.TestCase):
    def test_trash_sets_flag(self):
        svc = FakeDriveService()
        self.assertTrue(gdrive.trash("f1", service=svc))
        self.assertEqual(svc.calls["update"]["body"], {"trashed": True})

    def test_trash_empty_id_raises(self):
        with self.assertRaises(ValueError):
            gdrive.trash("", service=FakeDriveService())


class DeletePermanentlyTest(unittest.TestCase):
    def test_delete_uses_hard_delete_not_trash(self):
        svc = FakeDriveService()
        self.assertTrue(gdrive.delete_permanently("f1", service=svc))
        self.assertEqual(svc.calls["delete"]["fileId"], "f1")
        self.assertNotIn(
            "update", svc.calls, "apagar de vez NÃO pode passar pela lixeira"
        )

    def test_delete_empty_id_raises(self):
        with self.assertRaises(ValueError):
            gdrive.delete_permanently("", service=FakeDriveService())


class DriveToolTest(unittest.TestCase):
    def _registry(self):
        from zorin_copilot.ai.agent_tools import ToolRegistry

        return ToolRegistry()

    def test_without_libs_reports_actionable_error(self):
        reg = self._registry()
        with patch("zorin_copilot.core.google_auth.google_libs_available", return_value=False):
            res = reg.call("google_drive", {"action": "list"})
        self.assertFalse(res["success"])
        self.assertIn("google", res["error"].lower())

    def test_not_connected_is_honest(self):
        reg = self._registry()
        with patch("zorin_copilot.core.google_auth.google_libs_available", return_value=True):
            with patch.object(type(reg), "_google_service_active", return_value=False):
                res = reg.call("google_drive", {"action": "list"})
        self.assertFalse(res["success"])
        self.assertIn("conectado", res["error"])

    def test_connected_lists_files(self):
        reg = self._registry()
        with patch("zorin_copilot.core.google_auth.google_libs_available", return_value=True):
            with patch.object(type(reg), "_google_service_active", return_value=True):
                with patch(
                    "zorin_copilot.core.google_drive.list_files",
                    return_value=[{"id": "f1", "name": "notas.txt"}],
                ) as mocked:
                    res = reg.call("google_drive", {"action": "list"})
        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["source"], "google")
        mocked.assert_called_once()


class DriveRiskTest(unittest.TestCase):
    def setUp(self):
        from zorin_copilot.shell.risk import RiskPolicy

        self.policy = RiskPolicy()

    def test_writes_and_download_require_confirmation(self):
        from zorin_copilot.shell.risk import RiskLevel

        for action in ("upload", "download", "mkdir", "move", "rename", "trash", "delete"):
            level, _ = self.policy.classify("google_drive", {"action": action})
            self.assertEqual(level, RiskLevel.CONFIRM, f"{action} deveria confirmar")

    def test_reads_are_safe(self):
        from zorin_copilot.shell.risk import RiskLevel

        for action in ("list", "search", "get"):
            level, _ = self.policy.classify("google_drive", {"action": action})
            self.assertEqual(level, RiskLevel.SAFE, f"{action} não deveria confirmar")


if __name__ == "__main__":
    unittest.main()
