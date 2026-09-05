# Decisão de design: despachante de e-mails universal para Zorin OS e Linux.
# Suporta tanto clientes de e-mail nativos (Thunderbird, Evolution, Geary via xdg-email e mailto)
# quanto webmail direto no navegador (Gmail e Outlook Web com deep links de composição),
# com validação estrita de destinatário via regex e integração direta com a memória de contatos.

"""Gerenciador de composição e integração de e-mails para o Zorin Copilot."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import urllib.parse
from typing import Any

from .clipboard import ClipboardService
from .memory import MemoryManager

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class EmailManager:
    """Gerencia a resolução de contatos e a abertura de rascunhos de e-mail no desktop."""

    def __init__(self, memory: MemoryManager | None = None):
        self.memory = memory or MemoryManager()

    def resolve_recipient(self, query: str) -> tuple[str, str]:
        """
        Resolve o destinatário a partir de um e-mail direto ou do nome/apelido na base de contatos.
        Retorna (nome_resolvido, email_resolvido).
        """
        q = query.strip()
        # 1. Se já for um e-mail válido direto
        if EMAIL_REGEX.match(q):
            # Verifica se temos nome salvo para esse e-mail
            saved = self.memory.get_contact_by_email(q)
            name = saved["name"] if saved else q.split("@")[0]
            return name, q

        # 2. Busca na base de contatos pelo nome ou apelido
        matches = self.memory.find_contact(q)
        if matches:
            best = matches[0]
            return best["name"], best["email"]

        # 3. Não encontrado e não é e-mail
        return "", ""

    def compose(
        self,
        recipient: str,
        subject: str = "",
        body: str = "",
        client: str = "auto",
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        Abre o cliente de e-mail configurado pronto para edição.
        client: 'auto', 'native', 'gmail', 'outlook'.
        """
        name, to_email = self.resolve_recipient(recipient)
        if not to_email:
            msg = (
                f"Contato '{recipient}' não encontrado na sua base e não é um e-mail válido. "
                f"Deseja que eu cadastre esse contato primeiro?"
            )
            return False, msg, {}

        # Registra atualização do último contato na memória
        self.memory.update_contact_last_used(to_email)

        # Se houver corpo de texto, copia para a área de transferência por conveniência
        if body:
            ClipboardService.set_text(body)

        client_clean = client.lower().strip()

        # Cenário 1: Webmail Gmail
        if client_clean == "gmail":
            params = {"view": "cm", "fs": "1", "to": to_email}
            if subject:
                params["su"] = subject
            if body:
                params["body"] = body
            url = f"https://mail.google.com/mail/?{urllib.parse.urlencode(params)}"
            self._open_uri(url)
            msg = f"Rascunho aberto no Gmail Web para {name} <{to_email}>."
            return True, msg, {"email": to_email, "name": name, "client": "gmail"}

        # Cenário 2: Webmail Outlook
        if client_clean == "outlook":
            params = {"to": to_email}
            if subject:
                params["subject"] = subject
            if body:
                params["body"] = body
            url = f"https://outlook.live.com/mail/0/deeplink/compose?{urllib.parse.urlencode(params)}"
            self._open_uri(url)
            msg = f"Rascunho aberto no Outlook Web para {name} <{to_email}>."
            return True, msg, {"email": to_email, "name": name, "client": "outlook"}

        # Cenário 3: Cliente Nativo via xdg-email ou mailto (Padrão Zorin OS)
        if shutil.which("xdg-email"):
            cmd = ["xdg-email", "--to", to_email]
            if subject:
                cmd.extend(["--subject", subject])
            if body:
                cmd.extend(["--body", body])
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = f"Cliente de e-mail padrão aberto com rascunho para {name} <{to_email}>."
                return True, msg, {"email": to_email, "name": name, "client": "xdg-email"}
            except Exception as exc:
                logger.warning(f"xdg-email falhou, tentando fallback mailto: {exc}")

        # Fallback universal: protocolo mailto via gio open
        query_params = {}
        if subject:
            query_params["subject"] = subject
        if body:
            query_params["body"] = body
        qs = f"?{urllib.parse.urlencode(query_params)}" if query_params else ""
        mailto_url = f"mailto:{to_email}{qs}"
        self._open_uri(mailto_url)
        msg = f"Compositor de e-mail iniciado para {name} <{to_email}>."
        return True, msg, {"email": to_email, "name": name, "client": "mailto"}

    def _open_uri(self, uri: str) -> None:
        """Abre URL ou URI usando gio open ou xdg-open."""
        for opener in ("gio", "xdg-open"):
            if shutil.which(opener):
                try:
                    subprocess.Popen(
                        [opener, "open" if opener == "gio" else "", uri],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
                except Exception:
                    pass
