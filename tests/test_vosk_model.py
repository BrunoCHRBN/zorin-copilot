# Testes do resolvedor/baixador de modelo Vosk (src/zorin_copilot/shell/vosk_model.py).
# Valida a estrutura da pasta, a auto-localização de modelo já presente e o
# caminho de extração/validação do download — sem tocar na rede (o download em
# si é coberto por um _download_file mockado que escreve um zip fake).

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
)

from zorin_copilot.shell.vosk_model import (  # noqa: E402
    DEFAULT_LANG,
    KNOWN_MODELS,
    download_model,
    find_model,
    is_valid_model_dir,
)


def _make_valid_model(base: Path, name: str = "modelo") -> Path:
    """Cria uma pasta de modelo Vosk válida (formato small: final.mdl na raiz)."""
    p = base / name
    p.mkdir(parents=True)
    (p / "final.mdl").write_text("fake")
    (p / "Gr.fst").write_text("fake")
    (p / "ivector").mkdir()
    return p


class IsValidModelDirTest(unittest.TestCase):
    def test_vazio_e_inexistente(self):
        self.assertFalse(is_valid_model_dir(""))
        self.assertFalse(is_valid_model_dir("/caminho/que/nao/existe"))

    def test_falta_final_mdl(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "semfinal"
            p.mkdir()
            (p / "Gr.fst").write_text("x")
            self.assertFalse(is_valid_model_dir(str(p)))

    def test_small_final_mdl_na_raiz_valido(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _make_valid_model(Path(tmp))
            self.assertTrue(is_valid_model_dir(str(p)))

    def test_large_am_final_mdl_valido(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m"
            (p / "am").mkdir(parents=True)
            (p / "am" / "final.mdl").write_text("fake")
            self.assertTrue(is_valid_model_dir(str(p)))


class FindModelTest(unittest.TestCase):
    def test_configurado_valido_eh_retornado(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _make_valid_model(Path(tmp))
            self.assertEqual(find_model(str(p), DEFAULT_LANG), str(p))

    def test_configurado_invalido_cai_para_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            spec = KNOWN_MODELS[DEFAULT_LANG]
            cached = _make_valid_model(cache, spec["folder"])
            # DEFAULT_LANG resolve para o cache padrão; apontamos o cache via
            # default_models_dir mockado.
            with mock.patch(
                "zorin_copilot.shell.vosk_model.default_models_dir", return_value=cache
            ):
                self.assertEqual(find_model("", DEFAULT_LANG), str(cached))

    def test_nada_encontrado_retorna_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "vazio"
            cache.mkdir()
            with mock.patch(
                "zorin_copilot.shell.vosk_model.default_models_dir", return_value=cache
            ):
                self.assertIsNone(find_model("", DEFAULT_LANG))


class DownloadModelTest(unittest.TestCase):
    def _fake_download(self, url, dest, progress):
        # Escreve um zip fake com a estrutura de modelo Vosk esperada.
        folder = KNOWN_MODELS[DEFAULT_LANG]["folder"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"{folder}/final.mdl", "fake")
            zf.writestr(f"{folder}/Gr.fst", "fake")
            zf.writestr(f"{folder}/ivector/x", "fake")
        dest.write_bytes(buf.getvalue())
        if progress:
            progress(1.0, "fake 100%")

    def test_baixa_extrai_e_valida(self):
        with tempfile.TemporaryDirectory() as tmp:
            models_dir = Path(tmp) / "models"
            with mock.patch(
                "zorin_copilot.shell.vosk_model._download_file", self._fake_download
            ):
                path = download_model(DEFAULT_LANG, models_dir=models_dir)
            self.assertTrue(is_valid_model_dir(path))
            # O zip temporário deve ter sido removido.
            self.assertFalse((models_dir / (KNOWN_MODELS[DEFAULT_LANG]["folder"] + ".zip")).exists())

    def test_modelo_ja_presente_nao_rebaixa(self):
        with tempfile.TemporaryDirectory() as tmp:
            models_dir = Path(tmp) / "models"
            spec = KNOWN_MODELS[DEFAULT_LANG]
            existing = _make_valid_model(models_dir, spec["folder"])
            called = []
            path = download_model(
                DEFAULT_LANG, progress=lambda f, m: called.append((f, m)),
                models_dir=models_dir,
            )
            self.assertEqual(path, str(existing))
            self.assertTrue(called and called[-1][0] == 1.0)

    def test_idioma_desconhecido_lanca(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                download_model("xx-XX", models_dir=Path(tmp))


if __name__ == "__main__":
    unittest.main()
