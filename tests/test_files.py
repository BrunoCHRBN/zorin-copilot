"""Testes unitários para o gerenciador de arquivos e organização de diretórios."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from zorin_copilot.core.files import FileManager


class FileManagerTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="copilot_test_files_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_write_document_new_file(self):
        filename = "pesquisa_ia"
        content = "# Relatório de IA\n\nResultados encontrados na pesquisa."
        ok, msg, path = FileManager.write_document(filename, content, directory=self.test_dir)
        self.assertTrue(ok)
        self.assertTrue(path.endswith("pesquisa_ia.md"))
        self.assertTrue(os.path.exists(path))

        with open(path, "r", encoding="utf-8") as f:
            saved = f.read()
        self.assertEqual(saved, content)

    def test_write_document_append(self):
        filename = "notas.txt"
        ok1, _, path1 = FileManager.write_document(filename, "Linha 1", directory=self.test_dir)
        self.assertTrue(ok1)
        ok2, _, _ = FileManager.write_document(filename, "Linha 2", directory=self.test_dir, append=True)
        self.assertTrue(ok2)

        with open(path1, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Linha 1", content)
        self.assertIn("Linha 2", content)

    def test_read_document_success(self):
        path = os.path.join(self.test_dir, "doc.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Conteúdo para leitura de teste.")

        ok, content = FileManager.read_document(path)
        self.assertTrue(ok)
        self.assertEqual(content, "Conteúdo para leitura de teste.")

    def test_read_document_nonexistent(self):
        ok, content = FileManager.read_document(os.path.join(self.test_dir, "inexistente.txt"))
        self.assertFalse(ok)
        self.assertIn("não foi encontrado", content)

    def test_organize_directory_dry_run(self):
        # Cria arquivos fictícios de teste
        f_img = os.path.join(self.test_dir, "foto.jpg")
        f_doc = os.path.join(self.test_dir, "relatorio.pdf")
        f_code = os.path.join(self.test_dir, "script.py")
        for p in [f_img, f_doc, f_code]:
            with open(p, "w") as f:
                f.write("test")

        ok, summary, stats = FileManager.organize_directory(directory=self.test_dir, dry_run=True)
        self.assertTrue(ok)
        self.assertIn("Simulação", summary)
        self.assertEqual(stats.get("Imagens"), 1)
        self.assertEqual(stats.get("Documentos"), 1)
        self.assertEqual(stats.get("Codigo_e_Scripts"), 1)

        # Na simulação, os arquivos continuam na raiz da pasta
        self.assertTrue(os.path.exists(f_img))
        self.assertTrue(os.path.exists(f_doc))

    def test_organize_directory_real_execution(self):
        f_img = os.path.join(self.test_dir, "wallpaper.png")
        f_zip = os.path.join(self.test_dir, "arquivo.zip")
        for p in [f_img, f_zip]:
            with open(p, "w") as f:
                f.write("data")

        ok, summary, stats = FileManager.organize_directory(directory=self.test_dir, dry_run=False)
        self.assertTrue(ok)
        self.assertIn("Organização concluída", summary)

        # Arquivos devem ter sido movidos para subpastas
        self.assertFalse(os.path.exists(f_img))
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "Imagens", "wallpaper.png")))
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "Instaladores_e_Pacotes", "arquivo.zip")))

    def test_organize_directory_collision_avoidance(self):
        # Cria imagem na raiz e uma imagem com o mesmo nome na pasta Imagens
        img_subfolder = os.path.join(self.test_dir, "Imagens")
        os.makedirs(img_subfolder, exist_ok=True)
        existing = os.path.join(img_subfolder, "foto.png")
        with open(existing, "w") as f:
            f.write("original")

        root_img = os.path.join(self.test_dir, "foto.png")
        with open(root_img, "w") as f:
            f.write("nova")

        ok, _, stats = FileManager.organize_directory(directory=self.test_dir, dry_run=False)
        self.assertTrue(ok)
        # O arquivo original deve existir e o novo deve ter sido salvo como foto_1.png
        self.assertTrue(os.path.exists(existing))
        self.assertTrue(os.path.exists(os.path.join(img_subfolder, "foto_1.png")))


if __name__ == "__main__":
    unittest.main()
