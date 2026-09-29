"""Testes do canal de eventos em tempo real e da ponte HA→Zorin.

Cobrem a lógica de dados (normalização, filtro, backoff) e o barramento de
assinantes sem abrir socket — o laço de I/O é exercitado por `handle_message`,
que é exatamente o ponto onde a regra de negócio vive.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from zorin_copilot.core.home_assistant_realtime import (
    MAX_BACKOFF,
    HomeAssistantRealtime,
    backoff_delay,
    event_matches,
    normalize_state_changed,
)


# --------------------------------------------------------------------- puros
def test_backoff_cresce_e_limita():
    assert backoff_delay(0) == 1.0
    assert backoff_delay(1) == 2.0
    assert backoff_delay(2) == 4.0
    # Nunca ultrapassa o teto, por mais tentativas que haja.
    assert backoff_delay(10) == MAX_BACKOFF
    assert backoff_delay(100) == MAX_BACKOFF
    # Entrada negativa não explode.
    assert backoff_delay(-5) == 1.0


def test_normalize_state_changed_achata_evento():
    raw = {
        "event_type": "state_changed",
        "time_fired": "2026-09-21T12:00:00",
        "data": {
            "entity_id": "light.avant_neo_quarto",
            "old_state": {"state": "off"},
            "new_state": {
                "state": "on",
                "attributes": {"friendly_name": "Luz Quarto", "brightness": 128},
            },
        },
    }
    evt = normalize_state_changed(raw)
    assert evt is not None
    assert evt["entity_id"] == "light.avant_neo_quarto"
    assert evt["domain"] == "light"
    assert evt["state"] == "on"
    assert evt["old_state"] == "off"
    assert evt["friendly_name"] == "Luz Quarto"
    assert evt["kind"] == "state"


def test_normalize_descarta_evento_sem_entidade():
    assert normalize_state_changed({"data": {}}) is None
    assert normalize_state_changed({"data": {"entity_id": ""}}) is None
    assert normalize_state_changed("não é dicionário") is None  # type: ignore[arg-type]


def test_event_matches_filtros():
    evt = {"kind": "state", "entity_id": "light.a", "domain": "light"}
    assert event_matches(evt) is True
    assert event_matches(evt, entity_filter={"light.a"}) is True
    assert event_matches(evt, entity_filter={"light.b"}) is False
    assert event_matches(evt, domains={"switch"}) is False
    assert event_matches(evt, entity_filter={"light.a"}, domains={"light"}) is True
    # Eventos de sistema (conexão) passam mesmo com filtro de entidade: o
    # assinante precisa saber que a fonte caiu.
    system_evt = {"kind": "connection", "state": "connected"}
    assert event_matches(system_evt, entity_filter={"light.a"}) is True


# --------------------------------------------------------------- barramento
@pytest.fixture
def realtime():
    # entity_filter="" isola do config.json real do usuário.
    return HomeAssistantRealtime(url="http://localhost:8123", token="token", entity_filter="")


def test_handle_message_publica_para_assinante(realtime):
    recebidos: list[dict] = []
    realtime.subscribe(recebidos.append)
    realtime.handle_message({
        "type": "event",
        "event": {"data": {"entity_id": "light.a", "new_state": {"state": "on", "attributes": {}}}},
    })
    assert len(recebidos) == 1
    assert recebidos[0]["entity_id"] == "light.a"
    assert recebidos[0]["state"] == "on"


def test_filtro_por_dominio_entrega_apenas_o_relevante(realtime):
    recebidos: list[dict] = []
    realtime.subscribe(recebidos.append, domains={"light"})
    realtime.handle_message({
        "type": "event",
        "event": {"data": {"entity_id": "light.a", "new_state": {"state": "on", "attributes": {}}}},
    })
    realtime.handle_message({
        "type": "event",
        "event": {"data": {"entity_id": "switch.b", "new_state": {"state": "on", "attributes": {}}}},
    })
    assert [e["entity_id"] for e in recebidos] == ["light.a"]
    # Mas o buffer registra os dois — histórico é global, filtro é do assinante.
    assert len(realtime.recent_events(10)) == 2


def test_unsubscribe_interrompe_entrega(realtime):
    recebidos: list[dict] = []
    unsub = realtime.subscribe(recebidos.append)
    realtime.handle_message({
        "type": "event",
        "event": {"data": {"entity_id": "light.a", "new_state": {"state": "on", "attributes": {}}}},
    })
    unsub()
    realtime.handle_message({
        "type": "event",
        "event": {"data": {"entity_id": "light.a", "new_state": {"state": "off", "attributes": {}}}},
    })
    assert len(recebidos) == 1


def test_assinante_que_falha_nao_derruba_os_demais(realtime):
    bons: list[dict] = []

    def ruim(_evt):
        raise RuntimeError("assinante quebrado")

    realtime.subscribe(ruim)
    realtime.subscribe(bons.append)
    realtime.handle_message({
        "type": "event",
        "event": {"data": {"entity_id": "light.a", "new_state": {"state": "on", "attributes": {}}}},
    })
    assert len(bons) == 1


def test_emit_connection_chega_aos_assinantes(realtime):
    recebidos: list[dict] = []
    realtime.subscribe(recebidos.append, domains={"light"})
    realtime._emit_connection("connected", "http://localhost:8123")
    assert recebidos and recebidos[0]["kind"] == "connection"
    assert recebidos[0]["state"] == "connected"


def test_snapshot_estados_mantem_o_ultimo(realtime):
    for st in ("on", "off", "on"):
        realtime.handle_message({
            "type": "event",
            "event": {"data": {"entity_id": "light.a", "new_state": {"state": st, "attributes": {}}}},
        })
    snap = realtime.snapshot_states()
    assert snap["light.a"]["state"] == "on"


def test_start_sem_token_nao_inicia():
    rt = HomeAssistantRealtime(url="http://localhost:8123", token="", entity_filter="")
    assert rt.is_configured() is False
    assert rt.start() is False


def test_ws_url_converte_esquema():
    assert HomeAssistantRealtime(url="http://localhost:8123", token="t", entity_filter="")._ws_url() == \
        "ws://localhost:8123/api/websocket"
    assert HomeAssistantRealtime(url="https://casa.local:8123", token="t", entity_filter="")._ws_url() == \
        "wss://casa.local:8123/api/websocket"


# ------------------------------------------------- integração: caminho feliz
def test_realtime_caminho_feliz_com_servidor_falso():
    """Sobe um servidor WebSocket mínimo que finge ser o HA (auth_required →
    auth_ok → evento) e confirma que o cliente autentica, assina e entrega o
    evento ao assinante. Valida o pipeline inteiro sem depender de HA real."""
    import asyncio
    import struct
    import threading
    import time

    from zorin_copilot.core.home_assistant_realtime import _read_frame

    def server_frame(payload: bytes, opcode: int = 0x1) -> bytes:
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header += struct.pack("!H", n)
        else:
            header.append(127)
            header += struct.pack("!Q", n)
        return bytes(header) + payload

    async def handler(reader, writer):
        await reader.readline()  # linha de requisição
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
        )
        await writer.drain()
        writer.write(server_frame(json.dumps({"type": "auth_required", "ha_version": "2026.9"}).encode()))
        await writer.drain()
        await _read_frame(reader)  # recebe "auth"
        writer.write(server_frame(json.dumps({"type": "auth_ok", "ha_version": "2026.9"}).encode()))
        await writer.drain()
        await _read_frame(reader)  # recebe "subscribe_events"
        writer.write(server_frame(json.dumps({"type": "result", "id": 1, "success": True, "result": None}).encode()))
        writer.write(server_frame(json.dumps({
            "type": "event",
            "event": {"data": {"entity_id": "light.fake", "new_state": {"state": "on", "attributes": {"friendly_name": "Fake"}}}},
        }).encode()))
        await writer.drain()
        await asyncio.sleep(2.0)

    loop = asyncio.new_event_loop()
    pronto = threading.Event()
    port_box: dict[str, int] = {}

    def run() -> None:
        asyncio.set_event_loop(loop)

        async def main() -> None:
            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            port_box["port"] = server.sockets[0].getsockname()[1]
            pronto.set()
            async with server:
                await server.serve_forever()

        loop.run_until_complete(main())

    threading.Thread(target=run, daemon=True).start()
    assert pronto.wait(3.0), "servidor falso não subiu"

    rt = HomeAssistantRealtime(url=f"http://127.0.0.1:{port_box['port']}", token="qualquer", entity_filter="")
    recebidos: list[dict] = []
    rt.subscribe(recebidos.append)
    assert rt.start() is True
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and not any(e.get("entity_id") == "light.fake" for e in recebidos):
            time.sleep(0.05)
    finally:
        rt.stop()
    assert any(e.get("entity_id") == "light.fake" and e.get("state") == "on" for e in recebidos)


# -------------------------------------------------------------------- ponte
def _load_bridge():
    path = Path(__file__).resolve().parents[1] / "scripts" / "ha_bridge.py"
    spec = importlib.util.spec_from_file_location("ha_bridge", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_bridge_token_comparacao():
    bridge = _load_bridge()
    state = bridge.BridgeState(token="segredo")
    assert state.token_ok("segredo") is True
    assert state.token_ok("errado") is False
    assert state.token_ok(None) is False
    # Sem token configurado, aceita (modo aberto, útil em testes locais).
    assert bridge.BridgeState(token="").token_ok(None) is True


def test_bridge_process_registra_evento():
    bridge = _load_bridge()
    notificados: list[tuple[str, str]] = []
    state = bridge.BridgeState(
        token="segredo",
        notify=True,
        notifier=lambda event, message: notificados.append((event, message)),
    )
    res = state.process({"event": "campainha", "message": "Tocou!"})
    assert res["status"] == "ok"
    assert res["count"] == 1
    assert notificados == [("campainha", "Tocou!")]
    assert state.snapshot(5)[0]["message"] == "Tocou!"


def test_bridge_process_grava_log_jsonl(tmp_path):
    bridge = _load_bridge()
    log = tmp_path / "bridge.jsonl"
    state = bridge.BridgeState(token="", log_path=log)
    state.process({"event": "x", "message": "y"})
    linhas = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(linhas) == 1
    assert json.loads(linhas[0])["event"] == "x"


def test_bridge_http_fluxo_completo():
    bridge = _load_bridge()
    import threading
    import urllib.error
    import urllib.request

    state = bridge.BridgeState(token="SEGREDO")
    server = bridge.create_server("127.0.0.1", 0, state=state)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"

        def post(token: str | None):
            body = json.dumps({"event": "e", "message": "m"}).encode()
            req = urllib.request.Request(f"{base}/webhook", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("X-Bridge-Token", token)
            try:
                with urllib.request.urlopen(req, timeout=3) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as err:
                return err.code, json.loads(err.read())

        with urllib.request.urlopen(f"{base}/health", timeout=3) as resp:
            assert json.loads(resp.read())["status"] == "ok"

        assert post(None)[0] == 401          # sem token: recusado
        assert post("errado")[0] == 401      # token errado: recusado
        status, body = post("SEGREDO")       # token certo: aceito
        assert status == 200 and body["count"] == 1
    finally:
        server.shutdown()
        server.server_close()
