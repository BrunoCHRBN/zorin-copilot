# Decisão de design: escopos mínimos `gmail.send` + `gmail.modify`. Dá para ENVIAR
# e ETIQUETAR (ler/não lido, etc.), mas não apagar de vez nem mexer em
# configurações da conta — o escopo total (`https://mail.google.com/`) é
# **restrito** na classificação da Google e nunca é pedido.
#
# Envio e revisão são SEPARADOS de propósito: `core/email_review.py` cuida da
# correção gramatical/ortográfica antes; este módulo só fala com a API. Misturar
# os dois esconderia a etapa de revisão dentro do envio.
#
# Bcc fica de fora por ora: incluí-lo no MIME cru depende de a API removê-lo antes
# da entrega, e eu não exercitei esse comportamento. Melhor não expor um campo de
# privacidade cuja segurança eu não verifiquei.

"""Cliente do Gmail (Gmail API v1)."""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage
from typing import Any

from . import google_auth

logger = logging.getLogger(__name__)

#: Rótulos de sistema que o fluxo usa.
LABEL_UNREAD = "UNREAD"
LABEL_INBOX = "INBOX"


def scope_note() -> str:
    """O que os escopos permitem — em uma frase, para a UI e o modelo."""
    return (
        "O Gmail está autorizado com escopos mínimos (`gmail.send` + `gmail.modify`): "
        "o app pode ENVIAR e ETIQUETAR mensagens (ler/não lido), mas não apagar de vez "
        "nem alterar configurações da conta."
    )


def _service(service: Any = None) -> Any:
    return service if service is not None else google_auth.get_service("gmail")


def build_message(to: str, subject: str, body: str, cc: str | None = None) -> str:
    """Monta o MIME e devolve o `raw` em base64url — o formato que a API exige.

    Puro e testável: dá para decodificar o resultado e conferir os cabeçalhos sem
    tocar na rede.
    """
    recipient = str(to or "").strip()
    if not recipient:
        raise ValueError("Destinatário é obrigatório.")

    message = EmailMessage()
    message["To"] = recipient
    if cc:
        message["Cc"] = str(cc).strip()
    message["Subject"] = str(subject or "")
    message.set_content(str(body or ""))

    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def decode_raw(raw: str) -> str:
    """Decodifica um `raw` de volta para texto — usado nos testes e no diagnóstico."""
    return base64.urlsafe_b64decode(str(raw).encode("ascii")).decode("utf-8", errors="replace")


def send_message(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    service: Any = None,
) -> dict[str, Any]:
    """Envia a mensagem. Levanta se a API recusar — não finge sucesso."""
    raw = build_message(to, subject, body, cc=cc)
    result = (
        _service(service)
        .users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    return {
        "id": result.get("id", ""),
        "thread_id": result.get("threadId", ""),
        "labels": list(result.get("labelIds") or []),
    }


def create_draft(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    service: Any = None,
) -> dict[str, Any]:
    """Cria um rascunho (não envia)."""
    raw = build_message(to, subject, body, cc=cc)
    result = (
        _service(service)
        .users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    message = result.get("message") or {}
    return {"id": result.get("id", ""), "message_id": message.get("id", "")}


def list_messages(
    query: str | None = None,
    max_results: int = 25,
    service: Any = None,
) -> list[dict[str, Any]]:
    """Lista mensagens. `query` usa a sintaxe de busca do Gmail (`is:unread`, `from:...`)."""
    params: dict[str, Any] = {"userId": "me", "maxResults": max(1, int(max_results or 25))}
    if query:
        params["q"] = str(query)
    result = _service(service).users().messages().list(**params).execute()
    return [
        {"id": item.get("id", ""), "thread_id": item.get("threadId", "")}
        for item in result.get("messages", [])
    ]


def get_message(message_id: str, service: Any = None, fmt: str = "metadata") -> dict[str, Any]:
    """Metadados de uma mensagem (assunto, remetente, destinatário, data)."""
    message_id = str(message_id or "").strip()
    if not message_id:
        raise ValueError("ID da mensagem é obrigatório.")
    raw = (
        _service(service)
        .users()
        .messages()
        .get(userId="me", id=message_id, format=fmt)
        .execute()
    )
    headers = {
        str(h.get("name", "")).lower(): h.get("value", "")
        for h in ((raw.get("payload") or {}).get("headers") or [])
    }
    return {
        "id": raw.get("id", ""),
        "thread_id": raw.get("threadId", ""),
        "snippet": raw.get("snippet", ""),
        "subject": headers.get("subject", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "date": headers.get("date", ""),
        "labels": list(raw.get("labelIds") or []),
    }


def modify_labels(
    message_id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    service: Any = None,
) -> dict[str, Any]:
    """Adiciona/remove rótulos. Sem nada a mudar, é erro de uso."""
    message_id = str(message_id or "").strip()
    if not message_id:
        raise ValueError("ID da mensagem é obrigatório.")
    body: dict[str, Any] = {}
    if add:
        body["addLabelIds"] = list(add)
    if remove:
        body["removeLabelIds"] = list(remove)
    if not body:
        raise ValueError("Nada a modificar: informe ao menos um rótulo para adicionar ou remover.")

    result = (
        _service(service)
        .users()
        .messages()
        .modify(userId="me", id=message_id, body=body)
        .execute()
    )
    return {"id": result.get("id", ""), "labels": list(result.get("labelIds") or [])}


def mark_read(message_id: str, service: Any = None) -> dict[str, Any]:
    """Marca como lida (remove o rótulo UNREAD)."""
    return modify_labels(message_id, remove=[LABEL_UNREAD], service=service)


def mark_unread(message_id: str, service: Any = None) -> dict[str, Any]:
    """Marca como não lida (adiciona o rótulo UNREAD)."""
    return modify_labels(message_id, add=[LABEL_UNREAD], service=service)


def list_labels(service: Any = None) -> list[dict[str, Any]]:
    """Lista os rótulos da conta."""
    result = _service(service).users().labels().list(userId="me").execute()
    return [
        {"id": item.get("id", ""), "name": item.get("name", ""), "type": item.get("type", "")}
        for item in result.get("labels", [])
    ]
