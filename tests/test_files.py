"""Testes unitários para o gerenciador de arquivos e organização de diretórios."""

import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from zorin_copilot.core.files import FileManager

try:
    import docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    import pptx
    HAS_PPTX = True
except ImportError:
    HAS_PPTX = False


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


class OfficeDocumentTest(unittest.TestCase):
    """Geração real de .docx / .pptx via write_document (extensão-driven)."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="copilot_test_office_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @unittest.skipUnless(HAS_DOCX, "python-docx não instalado")
    def test_write_document_docx_real(self):
        md = (
            "# Título do Relatório\n\n"
            "Parágrafo com **negrito** e *itálico* e `codigo`.\n\n"
            "- item um\n- item dois\n\n"
            "```\nlinha_de_codigo()\n```\n"
        )
        ok, msg, path = FileManager.write_document("relatorio.docx", md, directory=self.test_dir)
        self.assertTrue(ok, msg)
        self.assertTrue(path.endswith(".docx"))
        # .docx é um zip Office Open XML válido
        self.assertTrue(zipfile.is_zipfile(path))

        from docx import Document

        doc = Document(path)
        joined = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("Título do Relatório", joined)
        self.assertIn("negrito", joined)
        self.assertIn("item um", joined)
        self.assertIn("linha_de_codigo()", joined)

    @unittest.skipUnless(HAS_DOCX, "python-docx não instalado")
    def test_write_document_docx_abnt(self):
        md = (
            "# 1 Introdução\n\n"
            "Este parágrafo contextualiza o trabalho de Gestão Comercial.\n\n"
            "> Citação direta longa com mais de três linhas extraída de Kotler demonstrando o recuo de 4 cm.\n\n"
            "# 2 Referências\n\n"
            "KOTLER, Philip. Administração de Marketing. São Paulo: Pearson, 2018."
        )
        ok, msg, path = FileManager.write_document("tcc_gestao_abnt.docx", md, directory=self.test_dir)
        self.assertTrue(ok, msg)
        self.assertIn("ABNT", msg)
        self.assertTrue(zipfile.is_zipfile(path))

        from docx import Document

        doc = Document(path)
        sec = doc.sections[0]
        self.assertAlmostEqual(sec.top_margin.cm, 3.0, places=1)
        self.assertAlmostEqual(sec.left_margin.cm, 3.0, places=1)
        self.assertAlmostEqual(sec.bottom_margin.cm, 2.0, places=1)
        self.assertAlmostEqual(sec.right_margin.cm, 2.0, places=1)

        # Citação longa (> 3 linhas) com recuo de 4 cm
        quote_p = [p for p in doc.paragraphs if "Citação direta longa" in p.text][0]
        self.assertAlmostEqual(quote_p.paragraph_format.left_indent.cm, 4.0, places=1)
        self.assertAlmostEqual(quote_p.paragraph_format.line_spacing, 1.0, places=1)

    @unittest.skipUnless(HAS_PPTX, "python-pptx não instalado")
    def test_write_document_pptx_real(self):
        md = (
            "# Slide Um\n\n- ponto a\n- ponto b\n\n"
            "---\n\n"
            "# Slide Dois\n\nTexto do segundo slide."
        )
        ok, msg, path = FileManager.write_document("pitch.pptx", md, directory=self.test_dir)
        self.assertTrue(ok, msg)
        self.assertTrue(path.endswith(".pptx"))
        self.assertTrue(zipfile.is_zipfile(path))

        from pptx import Presentation

        prs = Presentation(path)
        self.assertEqual(len(prs.slides), 2)
        titles = [
            s.shapes.title.text for s in prs.slides if s.shapes.title is not None
        ]
        self.assertIn("Slide Um", titles)
        self.assertIn("Slide Dois", titles)

    def test_write_document_docx_missing_dependency(self):
        import zorin_copilot.core.document_generators as dg
        from zorin_copilot.core.document_generators import MissingOfficeDependencyError

        real = dg.generate_docx

        def _boom(*_a, **_k):
            raise MissingOfficeDependencyError("python-docx faltando")

        dg.generate_docx = _boom
        try:
            ok, msg, path = FileManager.write_document(
                "x.docx", "# oi", directory=self.test_dir
            )
        finally:
            dg.generate_docx = real

        self.assertFalse(ok)
        self.assertEqual(path, "")
        self.assertIn("python-docx faltando", msg)

    @unittest.skipUnless(HAS_PPTX, "python-pptx não instalado")
    def test_write_document_office_append_ignored(self):
        # append com .docx gera arquivo novo (não anexa ao binário)
        md = "# Apresentação\n\n- bullet"
        ok, msg, path = FileManager.write_document(
            "a.pptx", md, directory=self.test_dir, append=True
        )
        self.assertTrue(ok, msg)
        self.assertTrue(zipfile.is_zipfile(path))

    def test_resolve_destination_path_academic_senac(self):
        academic_path = FileManager.resolve_target_path("tcc_gestao_comercial.docx")
        expected_dir = os.path.expanduser("~/Documentos/Gestao_Comercial/TCC_Artigos")
        self.assertEqual(os.path.dirname(academic_path), expected_dir)

        pi_path = FileManager.resolve_target_path("pi_senac_plano_de_negocio.docx")
        self.assertEqual(os.path.dirname(pi_path), expected_dir)

    def test_resolve_destination_path_dummy_user(self):
        # /home/usuario/scripts deve ser normalizado para Path.home()/scripts
        resolved = FileManager.resolve_target_path("scan_wifi.py", directory="/home/usuario/scripts")
        expected_dir = str(Path.home() / "scripts")
        self.assertEqual(os.path.dirname(resolved), expected_dir)
        self.assertEqual(os.path.basename(resolved), "scan_wifi.py")

        # Caminho completo passado em filename
        resolved2 = FileManager.resolve_target_path("/home/alguem/Documentos/relatorio.md")
        expected_dir2 = str(Path.home() / "Documentos")
        self.assertEqual(os.path.dirname(resolved2), expected_dir2)

    def test_resolve_destination_path_script_default(self):
        resolved = FileManager.resolve_target_path("scan_wifi.py")
        expected_dir = str(Path.home() / "scripts")
        self.assertEqual(os.path.dirname(resolved), expected_dir)


if __name__ == "__main__":
    unittest.main()
