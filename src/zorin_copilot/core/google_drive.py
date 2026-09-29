# Decisão de design: escopo `drive.file`, o mais restrito que ainda permite o uso
# real. Com ele o app enxerga e mexe APENAS nos arquivos que ele mesmo criou ou
# que o usuário abriu explicitamente com ele — o resto do Drive é invisível. É a
# escolha privacy-first, e a consequência precisa estar dita: `list`/`search` NÃO
# varrem o Drive inteiro (ver `scope_note()`).
#
# As bibliotecas do Google NÃO são importadas aqui: quem as resolve é
# `google_auth.get_service()`, que degrada com mensagem acionável. O upload aceita
# `media_body` injetado, então o módulo é testável sem o extra instalado.

"""Cliente do Google Drive (Drive API v3)."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

from . import google_auth

logger = logging.getLogger(__name__)

MIME_FOLDER = "application/vnd.google-apps.folder"

#: Campos pedidos por padrão. Pedir só o necessário reduz a resposta e o tráfego.
DEFAULT_FIELDS = "id,name,mimeType,size,modifiedTime,webViewLink,parents,trashed"


def scope_note() -> str:
    """A limitação do escopo, em uma frase, para a UI e o modelo."""
    return (
        "O Drive está autorizado no escopo mínimo (`drive.file`): só os arquivos que o "
        "Zorin Copilot criou ou que você abriu com ele aparecem aqui — o restante do seu "
        "Drive fica invisível de propósito."
    )


def _service(service: Any = None) -> Any:
    return service if service is not None else google_auth.get_service("drive")


def _file_to_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Normaliza um arquivo da API para o formato interno."""
    return {
        "id": raw.get("id", ""),
        "name": raw.get("name", ""),
        "mime_type": raw.get("mimeType", ""),
        "size": raw.get("size", ""),
        "modified_time": raw.get("modifiedTime", ""),
        "web_view_link": raw.get("webViewLink", ""),
        "parents": list(raw.get("parents") or []),
        "trashed": bool(raw.get("trashed", False)),
    }


def list_files(
    query: str | None = None,
    max_results: int = 25,
    service: Any = None,
) -> list[dict[str, Any]]:
    """Lista arquivos. Sem `query`, tudo o que o escopo `drive.file` alcança."""
    params: dict[str, Any] = {
        "pageSize": max(1, int(max_results or 25)),
        "fields": f"files({DEFAULT_FIELDS})",
    }
    # Sem filtro explícito, NÃO listar a lixeira. **Medido contra a API real:** a
    # `files.list` sem `q` devolve também os arquivos na lixeira — o usuário
    # mandava um arquivo para a lixeira e ele reaparecia em "meus arquivos".
    # Nenhum teste com dobra pegaria isto; só a execução de verdade.
    params["q"] = str(query) if query else "trashed = false"
    result = _service(service).files().list(**params).execute()
    return [_file_to_dict(item) for item in result.get("files", [])]


def search(term: str, max_results: int = 25, service: Any = None) -> list[dict[str, Any]]:
    """Busca por nome. A aspas simples do termo é escapada (é a do sintaxe `q`)."""
    clean = str(term or "").strip().replace("'", "\\'")
    if not clean:
        return []
    return list_files(
        query=f"name contains '{clean}' and trashed = false",
        max_results=max_results,
        service=service,
    )


def get_file(file_id: str, service: Any = None) -> dict[str, Any]:
    """Metadados de um arquivo."""
    file_id = str(file_id or "").strip()
    if not file_id:
        raise ValueError("ID do arquivo é obrigatório.")
    raw = _service(service).files().get(fileId=file_id, fields=DEFAULT_FIELDS).execute()
    return _file_to_dict(raw)


def upload(
    local_path: str,
    name: str | None = None,
    folder_id: str | None = None,
    mime_type: str | None = None,
    service: Any = None,
    media_body: Any = None,
) -> dict[str, Any]:
    """Envia um arquivo local para o Drive.

    `media_body` existe para injetar uma dobra no teste — em produção ele é
    construído com `MediaFileUpload` (import tardio, lib opcional).
    """
    path = Path(str(local_path)).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    metadata: dict[str, Any] = {"name": (name or path.name)}
    if folder_id:
        metadata["parents"] = [folder_id]

    if media_body is None:
        from googleapiclient.http import MediaFileUpload

        guessed = mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        media_body = MediaFileUpload(str(path), mimetype=guessed, resumable=False)

    raw = (
        _service(service)
        .files()
        .create(body=metadata, media_body=media_body, fields=DEFAULT_FIELDS)
        .execute()
    )
    return _file_to_dict(raw)


def download(file_id: str, dest_path: str, service: Any = None) -> str:
    """Baixa o conteúdo para `dest_path`. Devolve o caminho gravado.

    O conteúdo é lido inteiro em memória (`execute()`); para arquivos muito
    grandes o download por partes seria o caminho — fora do escopo pessoal atual.
    """
    file_id = str(file_id or "").strip()
    if not file_id:
        raise ValueError("ID do arquivo é obrigatório.")
    dest = Path(str(dest_path)).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = _service(service).files().get_media(fileId=file_id).execute()
    if isinstance(content, str):  # a API devolve bytes; str só em dobra de teste
        content = content.encode("utf-8")
    dest.write_bytes(content)
    return str(dest)


def create_folder(
    name: str,
    parent_id: str | None = None,
    service: Any = None,
) -> dict[str, Any]:
    """Cria uma pasta (um arquivo de mimeType de pasta)."""
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("Nome da pasta é obrigatório.")
    metadata: dict[str, Any] = {"name": clean, "mimeType": MIME_FOLDER}
    if parent_id:
        metadata["parents"] = [parent_id]
    raw = (
        _service(service)
        .files()
        .create(body=metadata, fields=DEFAULT_FIELDS)
        .execute()
    )
    return _file_to_dict(raw)


def move(file_id: str, folder_id: str, service: Any = None) -> dict[str, Any]:
    """Move para outra pasta: adiciona o novo pai e remove o(s) anterior(es).

    Sem remover os pais antigos o arquivo ficaria em duas pastas ao mesmo tempo —
    a API trata `parents` como lista, não como pai único.
    """
    file_id = str(file_id or "").strip()
    folder_id = str(folder_id or "").strip()
    if not file_id or not folder_id:
        raise ValueError("ID do arquivo e da pasta são obrigatórios.")

    svc = _service(service)
    current = svc.files().get(fileId=file_id, fields="parents").execute()
    previous = ",".join(current.get("parents") or [])

    raw = (
        svc.files()
        .update(
            fileId=file_id,
            addParents=folder_id,
            removeParents=previous or None,
            fields=DEFAULT_FIELDS,
        )
        .execute()
    )
    return _file_to_dict(raw)


def rename(file_id: str, new_name: str, service: Any = None) -> dict[str, Any]:
    """Renomeia um arquivo."""
    file_id = str(file_id or "").strip()
    clean = str(new_name or "").strip()
    if not file_id or not clean:
        raise ValueError("ID do arquivo e novo nome são obrigatórios.")
    raw = (
        _service(service)
        .files()
        .update(fileId=file_id, body={"name": clean}, fields=DEFAULT_FIELDS)
        .execute()
    )
    return _file_to_dict(raw)


def trash(file_id: str, service: Any = None) -> bool:
    """Manda para a lixeira do Drive (recuperável) — não apaga de vez."""
    file_id = str(file_id or "").strip()
    if not file_id:
        raise ValueError("ID do arquivo é obrigatório.")
    _service(service).files().update(
        fileId=file_id, body={"trashed": True}, fields="id,trashed"
    ).execute()
    return True


def delete_permanently(file_id: str, service: Any = None) -> bool:
    """Apaga **definitivamente** — NÃO vai para a lixeira. Irreversível.

    Contraparte do `trash`: sem isto a lixeira do app só cresce e nunca se esvazia.
    É deliberadamente uma função separada, e não um parâmetro de `trash`, para que
    o nome diga o que faz: quem lê `delete_permanently` sabe que não há volta.
    A ferramenta marca a ação como `CONFIRM` — apagar de vez nunca dispara sem o
    usuário saber.
    """
    file_id = str(file_id or "").strip()
    if not file_id:
        raise ValueError("ID do arquivo é obrigatório.")
    _service(service).files().delete(fileId=file_id).execute()
    return True
