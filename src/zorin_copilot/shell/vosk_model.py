"""Resolve e (opcionalmente) baixa o modelo de linguagem do Vosk.

O Vosk NÃO embute o modelo: ele é uma *pasta* (``am/``, ``conf/``, ``graph/``…)
baixada à parte de https://alphacephei.com/vosk/models. É exatamente essa pasta
que o campo ``wake_word_model_path`` aponta — e é por isso que, recém-instalado,
o caminho está vazio e a wake word não funciona.

Este módulo centraliza duas coisas:

* **Descobrir** um modelo já presente (caminho configurado, ou o cache padrão
  ``~/.cache/zorin-copilot/vosk-models``). Assim, depois de baixado uma vez, o
  app o acha sozinho mesmo que o campo esteja vazio.
* **Baixar** o modelo para o cache padrão quando solicitado (botão na UI), com
  callback de progresso e validação da estrutura da pasta extraída.

Tudo é offline-safe: se não houver rede, ``download_model`` devolve um erro
claro e o app degrada para o backend nulo (wake word desativada) sem quebrar.
"""

from __future__ import annotations

import logging
import os
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Idioma usado pela wake word por padrão (a frase configurada do usuário é
# "eae pantero" — português do Brasil). Extensível: basta acrescentar entradas.
DEFAULT_LANG = "pt-BR"

# Modelos conhecidos: chave de idioma -> (url, pasta esperada, rótulo).
# URLs verificadas em alphacephei.com/vosk/models. O ``folder`` é o nome da
# pasta dentro do zip (e onde a gente extrai).
KNOWN_MODELS: dict[str, dict] = {
    "pt-BR": {
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip",
        "folder": "vosk-model-small-pt-0.3",
        "label": "Português (Brasil) — pequeno (~32 MB)",
    },
}

# Callback de progresso: (fração 0..1, mensagem).
ProgressCb = Callable[[float, str], None]


def default_models_dir() -> Path:
    """Pasta de cache padrão para modelos baixados do Vosk."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return Path(base) / "zorin-copilot" / "vosk-models"


def is_valid_model_dir(path: str) -> bool:
    """True se ``path`` parece uma pasta de modelo Vosk utilizável.

    O ``vosk.Model()`` exige a pasta com os arquivos do modelo. Há dois
    formatos na wild:
      * **small** (ex.: ``vosk-model-small-pt-0.3``): ``final.mdl``, ``Gr.fst``,
        ``HCLr.fst``, ``ivector/``, ``mfcc.conf`` no nível raiz;
      * **large** (ex.: ``vosk-model-pt-fb``): ``conf/``, ``am/`` (com
        ``final.mdl``), ``graph/``.
    Validamos a presença do ``final.mdl`` (o modelo acústico, comum aos dois)
    para não passarmos um caminho que o Vosk recusaria em runtime — foi esse o
    erro silencioso que deixou a wake word parada.
    """
    if not path:
        return False
    p = Path(path)
    if not p.is_dir():
        return False
    if (p / "final.mdl").is_file():  # small: nível raiz
        return True
    if (p / "am" / "final.mdl").is_file():  # large: dentro de am/
        return True
    if (p / "conf" / "model.conf").is_file():  # large: conf presente
        return True
    return False


def find_model(configured_path: str = "", lang: str = DEFAULT_LANG) -> Optional[str]:
    """Devolve um caminho de modelo válido ou ``None``.

    Ordem de busca:
      1. caminho configurado, se for uma pasta de modelo válida;
      2. a pasta conhecida do idioma dentro do cache padrão;
      3. qualquer pasta de modelo válida no cache (fallback genérico).
    """
    if is_valid_model_dir(configured_path):
        return configured_path
    models_dir = default_models_dir()
    spec = KNOWN_MODELS.get(lang)
    if spec:
        candidate = models_dir / spec["folder"]
        if is_valid_model_dir(str(candidate)):
            return str(candidate)
    if models_dir.is_dir():
        for entry in sorted(models_dir.iterdir()):
            if entry.is_dir() and is_valid_model_dir(str(entry)):
                return str(entry)
    return None


def download_model(
    lang: str = DEFAULT_LANG,
    progress: Optional[ProgressCb] = None,
    models_dir: Optional[Path] = None,
) -> str:
    """Baixa e extrai o modelo do idioma para o cache; devolve o caminho.

    Levanta ``RuntimeError`` com mensagem clara em caso de rede/extração.
    ``progress(fraction, message)`` é chamado durante o download.
    """
    spec = KNOWN_MODELS.get(lang)
    if spec is None:
        raise RuntimeError(f"idioma de modelo desconhecido: {lang}")
    models_dir = models_dir or default_models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)
    dest = models_dir / spec["folder"]
    if is_valid_model_dir(str(dest)):
        if progress:
            progress(1.0, "Modelo já presente")
        return str(dest)

    zip_path = models_dir / (spec["folder"] + ".zip")
    _download_file(spec["url"], zip_path, progress)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(models_dir)
    except zipfile.BadZipFile as exc:
        zip_path.unlink(missing_ok=True)
        raise RuntimeError(f"arquivo de modelo corrompido: {exc}") from exc
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass

    if not is_valid_model_dir(str(dest)):
        raise RuntimeError("modelo extraído não tem a estrutura esperada do Vosk")
    logger.info("Vosk: modelo %s baixado para %s", lang, dest)
    if progress:
        progress(1.0, "Pronto")
    return str(dest)


def _download_file(url: str, dest: Path, progress: Optional[ProgressCb]) -> None:
    """Baixa ``url`` para ``dest`` em chunks, reportando progresso."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "zorin-copilot"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            got = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(1 << 16)  # 64 KB
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if progress and total:
                        progress(min(1.0, got / total), f"{got // 1024} KB / {total // 1024} KB")
                    elif progress:
                        progress(-1.0, f"{got // 1024} KB")
    except Exception as exc:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise RuntimeError(f"falha ao baixar {url}: {exc}") from exc
