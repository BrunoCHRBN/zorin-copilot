#!/usr/bin/env python3
# Decisão de design: o Zorin Copilot já conversa com o Home Assistant no sentido
# Copilot -> HA (controlar luzes, ar, cenas). Faltava o caminho de volta: uma
# automação do HA avisar o desktop ("alguém tocou a campainha", "a lavadora
# terminou"). Este script é essa ponte, isolado de propósito — roda como serviço
# independente, sem depender de GTK nem do processo principal do Copilot.
#
# Sem dependências externas (stdlib apenas), para poder ser executado em qualquer
# máquina que tenha Python 3 e o Home Assistant. NÃO executa comandos arbitrários
# vindos da rede: os únicos efeitos são log, notificação de desktop e fala.
#
# A fala, aliás, deixou de ser própria. A ponte **pede** a frase ao Copilot
# (`zorin-copilot --announce=<texto>`, pela instância única do GTK) e só recorre
# ao `spd-say` quando não há app rodando. Enquanto ela falava por conta própria, a
# casa tinha duas bocas de timbres diferentes e nenhuma arbitragem entre elas.
# O texto vai como um único argumento, nunca por shell — isso não abre porta
# nenhuma para comando vindo da rede.

"""Ponte HA→Zorin: recebe webhooks do Home Assistant e avisa o desktop.

Uso típico (rodando como serviço do usuário):

    ZORIN_HA_BRIDGE_TOKEN=segredo123 python3 scripts/ha_bridge.py --notify --speak

Quem fala é escolhido por `--voice`:

* `auto` (padrão) — a voz do Copilot; se ele não estiver rodando, `spd-say`;
* `copilot` — só a voz do Copilot (prefere silêncio a um segundo timbre);
* `system` — só o `spd-say`.

Depois, no Home Assistant, uma automação chama:

    rest_command.zorin_notify:
      url: http://127.0.0.1:8787/webhook
      method: POST
      headers:
        X-Bridge-Token: segredo123
      payload: '{"event":"doorbell","message":"Alguém tocou a campainha"}'

Teste rápido:

    curl -s -X POST http://127.0.0.1:8787/webhook \
      -H 'X-Bridge-Token: segredo123' -H 'Content-Type: application/json' \
      -d '{"event":"teste","message":"Olá do Home Assistant"}'
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("ha_bridge")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_BODY_BYTES = 64 * 1024  # webhooks são pequenos; recusa payloads absurdos
RECENT_LIMIT = 100

# --- voz -------------------------------------------------------------------
#
# A ponte não tem voz própria: ela pede a frase ao Copilot, que é quem tem o
# Piper, a arbitragem (`_claim_speaker`) e o timbre que o usuário já conhece.
# O `spd-say` continua existindo como plano B para o caso "app não está rodando".
#
# Ele é mesmo só um plano B. Medido nesta máquina: `spd-say` **não está
# instalado**, e antes desta mudança `--speak` simplesmente não falava nada, sem
# erro e sem log de aviso.

DEFAULT_VOICE = "auto"
VOICE_CHOICES = ("auto", "copilot", "system")
COPILOT_BIN = "zorin-copilot"
SPD_BIN = "spd-say"

#: Teto do pedido de fala. O Copilot responde assim que entrega a frase à thread
#: dele (milissegundos); 10 s é folga para uma instância primária subindo do
#: zero. Se estourar, o processo é morto e a ponte cai no plano B.
SPEAK_TIMEOUT = 10.0

#: Espelham `zorin_copilot.core.ha_announce`. A ponte é stdlib-only de propósito
#: — tem de rodar em qualquer máquina com Python 3 —, então **não** importa o
#: pacote. A paridade é travada por teste (`tests/test_ha_bridge_voice.py`), e
#: não por um comentário pedindo atenção.
EXIT_SPOKEN = 0
EXIT_NO_TARGET = 3
EXIT_EMPTY = 4


class VoiceRoute(str, Enum):
    """Por onde uma fala sai."""

    COPILOT = "copilot"
    SYSTEM = "system"
    SILENT = "silent"


def decide_voice(engine: str, *, copilot: bool, system: bool) -> VoiceRoute:
    """Escolhe a boca **antes** de tentar falar.

    `copilot`/`system` são disponibilidade medida (o binário está no PATH?), não
    intenção. A ordem das condições é a regra: escolha explícita primeiro,
    preferência depois, e silêncio só quando não sobrou nada.
    """
    if engine == "system":
        # Escolha explícita: nem se olha para o Copilot.
        return VoiceRoute.SYSTEM if system else VoiceRoute.SILENT
    if engine == "copilot":
        return VoiceRoute.COPILOT if copilot else VoiceRoute.SILENT
    # `auto`: prefere o Copilot — é a única boca que o usuário já conhece — e o
    # `spd-say` fica como plano B.
    if copilot:
        return VoiceRoute.COPILOT
    return VoiceRoute.SYSTEM if system else VoiceRoute.SILENT


def decide_after_copilot(
    engine: str, exit_code: int | None, *, system: bool
) -> VoiceRoute:
    """O que fazer quando a tentativa pelo Copilot **não** falou.

    `exit_code` é `None` quando o processo nem chegou a rodar (binário sumiu no
    meio, timeout, `OSError`).

    A primeira condição é a que mais importa: se o Copilot **falou**, não se cai
    em nada. Sem ela, o plano B repetiria a mesma frase logo depois e a casa
    diria tudo duas vezes, com dois timbres.
    """
    if exit_code == EXIT_SPOKEN:
        return VoiceRoute.COPILOT
    if engine == "copilot":
        # Modo estrito: quem pediu "só a voz do Copilot" prefere silêncio a
        # reintroduzir a segunda boca.
        return VoiceRoute.SILENT
    if exit_code == EXIT_EMPTY:
        # Não havia o que dizer; o `spd-say` também não teria o que dizer.
        return VoiceRoute.SILENT
    # Sem app rodando (`EXIT_NO_TARGET`), ou o Copilot falhou de vez: é aqui,
    # e só aqui, que o plano B ganha o direito de existir.
    return VoiceRoute.SYSTEM if system else VoiceRoute.SILENT


class BridgeState:
    """Estado compartilhado do servidor: token, log, buffer e efeitos."""

    def __init__(
        self,
        *,
        token: str = "",
        log_path: Path | None = None,
        notify: bool = False,
        speak: bool = False,
        voice: str = DEFAULT_VOICE,
        notifier: Callable[[str, str], None] | None = None,
        speaker: Callable[[str], None] | None = None,
    ) -> None:
        self.token = token
        self.log_path = log_path
        self.notify = notify
        self.speak = speak
        self.voice = voice
        self._notifier = notifier or _desktop_notify
        self._speaker = speaker or make_speaker(voice)
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT_LIMIT)
        self._lock = threading.Lock()
        self.received = 0
        self.rejected = 0

    # -- autenticação -----------------------------------------------------
    def token_ok(self, provided: str | None) -> bool:
        """Comparação em tempo constante. Sem token configurado, tudo passa."""
        if not self.token:
            return True
        return hmac.compare_digest(str(provided or ""), self.token)

    # -- processamento ----------------------------------------------------
    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Registra o evento e dispara os efeitos (notificação/fala)."""
        event = str(payload.get("event") or "ha_event").strip()
        message = str(payload.get("message") or payload.get("title") or event).strip()
        record = {
            "received_at": time.time(),
            "event": event,
            "message": message,
            "data": payload.get("data") if isinstance(payload.get("data"), dict) else {},
        }
        with self._lock:
            self.recent.append(record)
            self.received += 1

        self._append_log(record)

        if self.notify:
            try:
                self._notifier(event, message)
            except Exception as exc:  # efeito cosmético não pode derrubar a ponte
                logger.debug("Falha ao notificar: %s", exc)
        if self.speak and message:
            try:
                self._speaker(message)
            except Exception as exc:
                logger.debug("Falha ao falar: %s", exc)

        return {"status": "ok", "event": event, "message": message, "count": self.received}

    def _append_log(self, record: dict[str, Any]) -> None:
        if not self.log_path:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("Falha ao gravar log da ponte: %s", exc)

    def snapshot(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self.recent)
        return items[-limit:] if limit > 0 else items


def _desktop_notify(event: str, message: str) -> None:
    """Notificação de desktop best-effort (não falha se o utilitário faltar)."""
    notify_send = shutil.which("notify-send")
    if notify_send:
        subprocess.run(
            [notify_send, "-a", "Zorin Copilot", "-i", "home", "Casa Inteligente", message],
            check=False,
        )
        return
    logger.info("[Casa Inteligente] %s: %s", event, message)


def _speak_via_copilot(text: str) -> int | None:
    """Pede a fala à instância do Copilot. Devolve o código de saída, ou `None`.

    `None` = nem chegamos a rodar (binário ausente, timeout, `OSError`).

    O texto vai **colado** à opção (`--announce=<texto>`), e isso é medido, não
    estético: vira um único argumento — sem shell, logo sem injeção — e um texto
    começando por `-` não é confundido com outra opção. Verificado com o parser
    real do GTK: `--announce=--toggle` chega ao app como a string `--toggle`, e
    `--toggle` nem aparece na lista de argumentos.
    """
    binary = shutil.which(COPILOT_BIN)
    if not binary:
        return None
    try:
        done = subprocess.run(
            [binary, f"--announce={text}"],
            check=False,
            capture_output=True,
            timeout=SPEAK_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Não consegui pedir a fala ao Copilot: %s", exc)
        return None
    if done.stderr:
        # O app loga em stderr; guardar em debug ajuda a diagnosticar sem
        # sujar a saída da ponte.
        logger.debug(
            "Copilot (saída %s): %s",
            done.returncode,
            done.stderr.decode("utf-8", "replace").strip(),
        )
    return done.returncode


def _speak_text(text: str) -> None:
    """Fala via `spd-say` (voz do sistema). Silêncio é relatado, não engolido."""
    spd_say = shutil.which(SPD_BIN)
    if spd_say:
        subprocess.run([spd_say, "-w", text], check=False)
        return
    logger.info("[Fala sem voz] %s", text)


def speak(text: str, *, engine: str = DEFAULT_VOICE) -> None:
    """Fala pela boca escolhida, com plano B quando — e só quando — faz sentido.

    Bloqueia até a fala terminar apenas no caminho do `spd-say` (por causa do
    `-w`). Pelo Copilot o pedido é entregue e a resposta volta na hora: quem
    fala é a thread do app, não o webhook do Home Assistant, que segue livre.
    """
    system_ok = shutil.which(SPD_BIN) is not None
    route = decide_voice(
        engine,
        copilot=shutil.which(COPILOT_BIN) is not None,
        system=system_ok,
    )
    if route is VoiceRoute.COPILOT:
        code = _speak_via_copilot(text)
        if code == EXIT_SPOKEN:
            # Registrar o sucesso importa tanto quanto registrar a falha: sem
            # esta linha, "não caiu no plano B" seria indistinguível de "não
            # tentou nada" ao se ler o log.
            logger.debug("Fala entregue ao Copilot.")
            return
        route = decide_after_copilot(engine, code, system=system_ok)
        logger.debug(
            "Copilot não falou (saída %s) — rota agora: %s", code, route.value
        )
    if route is VoiceRoute.SYSTEM:
        _speak_text(text)
        return
    # Sem boca: o log é o último lugar onde a mensagem ainda pode ser lida.
    logger.info("[Fala sem voz] %s", text)


def make_speaker(engine: str = DEFAULT_VOICE) -> Callable[[str], None]:
    """Falante injetável para o `BridgeState` (os testes passam o próprio)."""

    def _speaker(text: str) -> None:
        speak(text, engine=engine)

    return _speaker


def warn_if_mute(engine: str = DEFAULT_VOICE) -> bool:
    """Avisa no arranque quando `--speak` não vai sair som nenhum. Devolve se está mudo.

    Sem isto o sintoma é o pior possível: a ponte aceita o webhook, responde
    200, grava o evento no log — e a casa fica muda, sem uma linha explicando.
    Foi exatamente o que aconteceu enquanto o caminho era só o `spd-say`.
    """
    copilot_ok = shutil.which(COPILOT_BIN) is not None
    system_ok = shutil.which(SPD_BIN) is not None
    if decide_voice(engine, copilot=copilot_ok, system=system_ok) is not VoiceRoute.SILENT:
        return False
    faltando = []
    if not copilot_ok:
        faltando.append(f"'{COPILOT_BIN}' não está no PATH")
    if not system_ok:
        faltando.append(f"'{SPD_BIN}' não está instalado")
    logger.warning(
        "Fala pedida (--speak --voice %s), mas não há boca disponível: %s. "
        "As mensagens serão registradas no log sem serem faladas.",
        engine,
        "; ".join(faltando),
    )
    return True


def make_handler(state: BridgeState) -> type[BaseHTTPRequestHandler]:
    """Cria a classe de handler ligada a um estado (facilita testes)."""

    class _BridgeHandler(BaseHTTPRequestHandler):
        server_version = "ZorinHABridge/1.0"

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _provided_token(self, query: dict[str, list[str]]) -> str | None:
            header = self.headers.get("X-Bridge-Token")
            if header:
                return header
            values = query.get("token")
            return values[0] if values else None

        def do_GET(self) -> None:  # noqa: N802 (nome exigido pela stdlib)
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send(200, {
                    "status": "ok",
                    "received": state.received,
                    "rejected": state.rejected,
                    "auth": bool(state.token),
                })
                return
            if parsed.path == "/events":
                query = parse_qs(parsed.query)
                if not state.token_ok(self._provided_token(query)):
                    state.rejected += 1
                    self._send(401, {"status": "error", "error": "token inválido"})
                    return
                limit = int((query.get("limit") or ["20"])[0] or 20)
                self._send(200, {"status": "ok", "events": state.snapshot(limit)})
                return
            self._send(404, {"status": "error", "error": "rota não encontrada"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path not in ("/webhook", "/ha/event"):
                self._send(404, {"status": "error", "error": "rota não encontrada"})
                return
            query = parse_qs(parsed.query)
            if not state.token_ok(self._provided_token(query)):
                state.rejected += 1
                self._send(401, {"status": "error", "error": "token inválido"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send(400, {"status": "error", "error": "corpo ausente ou grande demais"})
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("payload precisa ser um objeto JSON")
            except (ValueError, UnicodeDecodeError) as exc:
                self._send(400, {"status": "error", "error": f"JSON inválido: {exc}"})
                return
            try:
                result = state.process(payload)
            except Exception as exc:  # nunca deixar o HA receber 500 silencioso
                logger.exception("Falha ao processar webhook: %s", exc)
                self._send(500, {"status": "error", "error": str(exc)})
                return
            self._send(200, result)

        def log_message(self, fmt: str, *args: Any) -> None:  # silencia o access log padrão
            logger.debug("%s - %s", self.address_string(), fmt % args)

    return _BridgeHandler


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    state: BridgeState | None = None,
    token: str = "",
    log_path: Path | None = None,
    notify: bool = False,
    speak: bool = False,
    voice: str = DEFAULT_VOICE,
) -> ThreadingHTTPServer:
    """Cria (mas não inicia) o servidor HTTP da ponte."""
    state = state or BridgeState(
        token=token, log_path=log_path, notify=notify, speak=speak, voice=voice
    )
    handler = make_handler(state)
    server = ThreadingHTTPServer((host, port), handler)
    server.bridge_state = state  # type: ignore[attr-defined]
    return server


def _default_log_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "zorin-copilot" / "ha_bridge.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ponte HA→Zorin: webhooks do Home Assistant para o desktop.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Endereço de escuta (padrão {DEFAULT_HOST}).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Porta (padrão {DEFAULT_PORT}).")
    parser.add_argument("--token", default=os.environ.get("ZORIN_HA_BRIDGE_TOKEN", ""),
                        help="Segredo compartilhado (ou use a env ZORIN_HA_BRIDGE_TOKEN).")
    parser.add_argument("--log", default=str(_default_log_path()), help="Arquivo JSONL de eventos.")
    parser.add_argument("--notify", action="store_true", help="Mostra notificação de desktop.")
    parser.add_argument("--speak", action="store_true", help="Fala a mensagem (ver --voice).")
    parser.add_argument(
        "--voice",
        choices=VOICE_CHOICES,
        default=DEFAULT_VOICE,
        help=(
            "Quem fala: 'auto' (voz do Copilot, caindo em spd-say se ele não estiver "
            "rodando), 'copilot' (só a voz do Copilot) ou 'system' (só spd-say)."
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="Log detalhado.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    server = create_server(
        args.host,
        args.port,
        token=args.token,
        log_path=Path(args.log),
        notify=args.notify,
        speak=args.speak,
        voice=args.voice,
    )
    if not args.token:
        logger.warning("Ponte sem token: qualquer processo local pode publicar eventos. Defina --token.")
    if args.speak:
        warn_if_mute(args.voice)
    logger.info("Ponte HA→Zorin ouvindo em http://%s:%d (log: %s)", args.host, args.port, args.log)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Encerrando ponte HA→Zorin.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
