"""Testes unitários para a integração de Casa Inteligente e IoT (Home Assistant / Avant Neo)."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, Mock, patch

import pytest

from zorin_copilot.ai.agent_tools import ToolRegistry
from zorin_copilot.ai.live import GeminiLiveClient
from zorin_copilot.core.config import CopilotConfig
from zorin_copilot.core.home_assistant import HomeAssistantManager


@pytest.fixture(autouse=True)
def _reset_ha_singleton():
    """Isola o singleton entre testes (ele é global por processo)."""
    HomeAssistantManager.reset()
    yield
    HomeAssistantManager.reset()


@pytest.fixture
def fake_ha_states():
    """Mock da lista de estados retornada pela API /api/states do Home Assistant."""
    return [
        {
            "entity_id": "light.avant_neo_quarto",
            "state": "on",
            "attributes": {
                "friendly_name": "Lâmpada Avant Neo Quarto",
                "brightness": 204,  # ~80%
                "color_temp_kelvin": 2700,
                "supported_color_modes": ["color_temp", "hs"],
            },
        },
        {
            "entity_id": "light.luz_escritorio",
            "state": "off",
            "attributes": {
                "friendly_name": "Luz Escritório",
            },
        },
        {
            "entity_id": "climate.ar_condicionado_sala",
            "state": "cool",
            "attributes": {
                "friendly_name": "Ar Condicionado Sala",
                "current_temperature": 24.5,
                "temperature": 22.0,
                "hvac_modes": ["off", "cool", "heat", "fan_only"],
            },
        },
        {
            "entity_id": "switch.tomada_computador",
            "state": "on",
            "attributes": {
                "friendly_name": "Tomada Computador",
            },
        },
    ]


def test_ha_is_configured():
    ha_unconfigured = HomeAssistantManager(url="", token="")
    assert ha_unconfigured.is_configured() is False

    ha_configured = HomeAssistantManager(url="http://localhost:8123", token="fake-token-123")
    assert ha_configured.is_configured() is True


def test_ha_test_connection_success():
    ha = HomeAssistantManager(url="http://localhost:8123", token="test-token")
    mock_resp = io.BytesIO(json.dumps({"message": "API running"}).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = ha.test_connection()
        assert res["success"] is True
        assert "sucesso" in res["message"].lower()


def test_ha_test_connection_unauthorized():
    import urllib.error

    ha = HomeAssistantManager(url="http://localhost:8123", token="invalid-token")
    err = urllib.error.HTTPError("http://localhost:8123/api/", 401, "Unauthorized", {}, None)

    with patch("urllib.request.urlopen", side_effect=err):
        res = ha.test_connection()
        assert res["success"] is False
        assert "401" in res["message"]


def test_ha_get_states_and_filtering(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    mock_resp = io.BytesIO(json.dumps(fake_ha_states).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp):
        # Todos os estados
        all_states = ha.get_states()
        assert len(all_states) == 4

        # Filtrado apenas por luzes
        lights = ha.get_states(domain="light")
        assert len(lights) == 2
        assert all(l["entity_id"].startswith("light.") for l in lights)


def test_ha_find_entity(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    ha._states_cache = fake_ha_states
    ha._cache_timestamp = 9999999999.0

    # 1. Busca por entity_id exato
    ent1 = ha.find_entity("light.avant_neo_quarto")
    assert ent1 is not None
    assert ent1["entity_id"] == "light.avant_neo_quarto"

    # 2. Busca por nome amigável ou substring ("avant", "quarto")
    ent2 = ha.find_entity("avant neo")
    assert ent2 is not None
    assert ent2["entity_id"] == "light.avant_neo_quarto"

    # 3. Busca de ar-condicionado
    ent_ac = ha.find_entity("ar condicionado", domain="climate")
    assert ent_ac is not None
    assert ent_ac["entity_id"] == "climate.ar_condicionado_sala"

    # 4. Fallback com único dispositivo no domínio
    ha._states_cache = [fake_ha_states[0]]  # apenas uma lâmpada
    ent_single = ha.find_entity("", domain="light")
    assert ent_single is not None
    assert ent_single["entity_id"] == "light.avant_neo_quarto"


def test_ha_control_light_turn_on(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    ha._states_cache = fake_ha_states
    ha._cache_timestamp = 9999999999.0

    captured_payload = {}

    def fake_call(domain, service, service_data):
        nonlocal captured_payload
        captured_payload = service_data
        return {"success": True, "message": "ok"}

    ha.call_service = fake_call

    # Liga a lâmpada Avant Neo com brilho 80% e temperatura branco quente
    res = ha.control_light(
        entity="avant neo",
        action="turn_on",
        brightness=80,
        color_temp="quente",
    )

    assert res["success"] is True
    assert "Avant Neo" in res["message"]
    assert captured_payload["entity_id"] == "light.avant_neo_quarto"
    assert captured_payload["brightness_pct"] == 80
    assert captured_payload["color_temp_kelvin"] == 2700


def test_ha_control_light_rgb(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    ha._states_cache = fake_ha_states
    ha._cache_timestamp = 9999999999.0

    captured_payload = {}

    def fake_call(domain, service, service_data):
        nonlocal captured_payload
        captured_payload = service_data
        return {"success": True, "message": "ok"}

    ha.call_service = fake_call

    res = ha.control_light(entity="avant neo", color="azul")
    assert res["success"] is True
    assert captured_payload["rgb_color"] == [0, 0, 255]


def test_ha_control_light_turn_off(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    ha._states_cache = fake_ha_states
    ha._cache_timestamp = 9999999999.0

    called_service = ""

    def fake_call(domain, service, service_data):
        nonlocal called_service
        called_service = f"{domain}.{service}"
        return {"success": True}

    ha.call_service = fake_call

    res = ha.control_light(entity="avant neo", action="turn_off")
    assert res["success"] is True
    assert called_service == "light.turn_off"
    assert "desligada" in res["message"]


def test_ha_control_climate(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    ha._states_cache = fake_ha_states
    ha._cache_timestamp = 9999999999.0

    captured_payload = {}

    def fake_call(domain, service, service_data):
        nonlocal captured_payload
        captured_payload = service_data
        return {"success": True}

    ha.call_service = fake_call

    res = ha.control_climate(entity="ar", action="set_temperature", temperature=21.0, hvac_mode="refrigerar")
    assert res["success"] is True
    assert captured_payload["temperature"] == 21.0
    assert captured_payload["hvac_mode"] == "cool"


def test_ha_control_switch(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    ha._states_cache = fake_ha_states
    ha._cache_timestamp = 9999999999.0

    called_service = ""

    def fake_call(domain, service, service_data):
        nonlocal called_service
        called_service = f"{domain}.{service}"
        return {"success": True}

    ha.call_service = fake_call

    res = ha.control_switch(entity="tomada", action="toggle")
    assert res["success"] is True
    assert called_service == "switch.toggle"


def test_ha_get_status_formatted(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    ha._states_cache = fake_ha_states
    ha._cache_timestamp = 9999999999.0

    # Consulta específica da lâmpada
    res_light = ha.get_status("avant neo")
    assert res_light["success"] is True
    assert "ligada" in res_light["message"].lower()
    assert "80%" in res_light["message"]
    assert "2700k" in res_light["message"].lower()

    # Consulta de ar-condicionado
    res_ac = ha.get_status("ar condicionado")
    assert res_ac["success"] is True
    assert "24.5°c" in res_ac["message"].lower()
    assert "22.0°c" in res_ac["message"].lower()

    # Consulta de todos os dispositivos
    res_all = ha.get_status()
    assert res_all["success"] is True
    assert res_all["count"] == 4


def test_agent_tools_smart_home_integration(fake_ha_states, monkeypatch):
    registry = ToolRegistry()

    fake_ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    fake_ha._states_cache = fake_ha_states
    fake_ha._cache_timestamp = 9999999999.0
    fake_ha.call_service = lambda domain, service, data: {"success": True, "message": "Executado"}

    monkeypatch.setattr(HomeAssistantManager, "get_default", lambda: fake_ha)

    # 1. Chamada de controle de lâmpada pelo agente
    res = registry.call(
        "smart_home_control",
        {
            "action": "turn_on",
            "entity": "avant neo",
            "brightness": 50,
            "color_temp": "6500",
        },
    )
    assert res["ok"] is True
    assert "Avant Neo" in res["message"]

    # 2. Consulta de status
    res_stat = registry.call("smart_home_status", {"query": "avant neo"})
    assert res_stat["ok"] is True
    assert "Avant Neo" in res_stat["message"]

    # 3. Dry-run não executa mutações
    dry_reg = registry.for_dry_run()
    res_dry = dry_reg.call("smart_home_control", {"action": "turn_off", "entity": "avant neo"})
    assert res_dry["ok"] is True
    assert res_dry.get("dry_run") is True

    registry.close()


def test_live_dispatch_smart_home(fake_ha_states, monkeypatch):
    config = CopilotConfig(gemini_api_key="test-key")
    client = GeminiLiveClient(config=config)

    fake_ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    fake_ha._states_cache = fake_ha_states
    fake_ha._cache_timestamp = 9999999999.0
    fake_ha.call_service = lambda domain, service, data: {"success": True, "message": "Executado"}

    monkeypatch.setattr(HomeAssistantManager, "get_default", lambda: fake_ha)

    # Dispatch no Gemini Live
    res = client._dispatch_tool(
        "smart_home_control",
        {
            "action": "turn_on",
            "entity": "lâmpada",
            "brightness": 100,
            "color_temp": "frio",
        },
    )
    assert res["success"] is True
    assert "Avant Neo" in res["message"]

    # Status no Gemini Live
    res_stat = client._dispatch_tool("smart_home_status", {"query": "avant neo"})
    assert res_stat["success"] is True
    assert "ligada" in res_stat["message"].lower()


def test_preferences_smart_home_load_and_collect():
    from zorin_copilot.ui.preferences import PreferencesDialog

    dialog = PreferencesDialog(None)
    dialog.ha_switch_row.set_active(True)
    dialog.ha_url_row.set_text("http://192.168.1.100:8123")
    dialog.ha_token_row.set_text("secret_token_123")
    dialog.ha_default_light_row.set_text("light.avant_neo_quarto")
    dialog.ha_realtime_row.set_active(True)

    cfg = dialog._collect_current_config()
    assert cfg.ha_enabled is True
    assert cfg.ha_url == "http://192.168.1.100:8123"
    assert cfg.ha_token == "secret_token_123"
    assert cfg.ha_default_light == "light.avant_neo_quarto"
    assert cfg.ha_realtime_enabled is True


# ---------------------------------------------------------------------------
# Cenas, scripts e automações
# ---------------------------------------------------------------------------
def _with_automations(fake_ha_states):
    return fake_ha_states + [
        {"entity_id": "scene.boa_noite", "state": "scening", "attributes": {"friendly_name": "Boa Noite"}},
        {"entity_id": "script.rotina_cafe", "state": "off", "attributes": {"friendly_name": "Rotina Café"}},
        {"entity_id": "automation.alarme_manha", "state": "on", "attributes": {"friendly_name": "Alarme Manhã"}},
    ]


def test_ha_list_scenes_scripts_automations(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    ha._states_cache = _with_automations(fake_ha_states)
    ha._cache_timestamp = 9999999999.0

    assert [s["entity_id"] for s in ha.list_scenes()] == ["scene.boa_noite"]
    assert [s["entity_id"] for s in ha.list_scripts()] == ["script.rotina_cafe"]
    assert [s["entity_id"] for s in ha.list_automations()] == ["automation.alarme_manha"]
    assert ha.list_scenes()[0]["friendly_name"] == "Boa Noite"


def test_ha_trigger_scene_script_automation(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    ha._states_cache = _with_automations(fake_ha_states)
    ha._cache_timestamp = 9999999999.0

    chamadas: list[tuple[str, str, str]] = []

    def fake_call(domain, service, data):
        chamadas.append((domain, service, data["entity_id"]))
        return {"success": True}

    ha.call_service = fake_call

    assert ha.trigger_scene("boa noite")["success"] is True
    assert ha.run_script("cafe")["success"] is True
    assert ha.trigger_automation("alarme")["success"] is True
    assert chamadas == [
        ("scene", "turn_on", "scene.boa_noite"),
        ("script", "turn_on", "script.rotina_cafe"),
        ("automation", "trigger", "automation.alarme_manha"),
    ]


def test_ha_trigger_scene_inexistente_lista_disponiveis(fake_ha_states):
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    ha._states_cache = _with_automations(fake_ha_states)
    ha._cache_timestamp = 9999999999.0

    res = ha.trigger_scene("cinema")
    assert res["success"] is False
    assert "scene.boa_noite" in res["available"][0]


# ---------------------------------------------------------------------------
# Resiliência (retry / health)
# ---------------------------------------------------------------------------
def test_ha_call_service_repete_em_falha_de_rede():
    import urllib.error

    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    tentativas = {"n": 0}

    def flaky(req, timeout=None):
        tentativas["n"] += 1
        if tentativas["n"] == 1:
            raise urllib.error.URLError("connection refused")
        return io.BytesIO(b"[]")

    with patch("urllib.request.urlopen", side_effect=flaky):
        res = ha.call_service("light", "turn_on", {"entity_id": "light.x"})

    assert res["success"] is True
    assert tentativas["n"] == 2  # repetiu uma vez após a queda


def test_ha_call_service_nao_repete_erro_http():
    import urllib.error

    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    tentativas = {"n": 0}

    def boom(req, timeout=None):
        tentativas["n"] += 1
        raise urllib.error.HTTPError("u", 500, "Server Error", {}, None)

    with patch("urllib.request.urlopen", side_effect=boom):
        res = ha.call_service("light", "turn_on", {})

    assert res["success"] is False
    assert "500" in res["message"]
    assert tentativas["n"] == 1  # erro de protocolo não melhora insistindo


def test_ha_health_configurado_e_alcancavel():
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"message": "API running"}')):
        res = ha.health()
    assert res["success"] is True
    assert res["reachable"] is True
    assert res["configured"] is True

    sem_config = HomeAssistantManager(url="", token="")
    assert sem_config.health()["configured"] is False


# ---------------------------------------------------------------------------
# Delegação de eventos em tempo real
# ---------------------------------------------------------------------------
def test_ha_on_event_e_recent_events_delegam(monkeypatch):
    import zorin_copilot.core.home_assistant_realtime as rtmod

    ha = HomeAssistantManager(url="http://localhost:8123", token="token")

    class FakeRT:
        def __init__(self):
            self.assinantes = []

        def subscribe(self, cb, entity_filter=None, domains=None):
            self.assinantes.append(cb)
            return lambda: self.assinantes.remove(cb)

        def recent_events(self, limit=20):
            return [{"entity_id": "light.a", "state": "on"}][:limit]

        def is_running(self):
            return True

        def is_connected(self):
            return True

        url = "http://localhost:8123"

    fake = FakeRT()
    monkeypatch.setattr(rtmod, "get_realtime", lambda url=None, token=None: fake)

    recebidos: list[dict] = []
    ha.on_event(recebidos.append)
    assert fake.assinantes == [recebidos.append]
    assert ha.recent_events(5) == [{"entity_id": "light.a", "state": "on"}]
    assert ha.realtime_status()["connected"] is True


# ---------------------------------------------------------------------------
# Ferramentas do agente
# ---------------------------------------------------------------------------
def test_agent_tools_smart_home_automate(fake_ha_states, monkeypatch):
    registry = ToolRegistry()

    fake_ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    fake_ha._states_cache = _with_automations(fake_ha_states)
    fake_ha._cache_timestamp = 9999999999.0
    fake_ha.call_service = lambda domain, service, data: {"success": True}
    monkeypatch.setattr(HomeAssistantManager, "get_default", lambda: fake_ha)

    res_list = registry.call("smart_home_automate", {"action": "list_scenes"})
    assert res_list["ok"] is True
    assert res_list["count"] == 1
    assert "Boa Noite" in res_list["message"]

    res_trigger = registry.call("smart_home_automate", {"action": "trigger_scene", "name": "boa noite"})
    assert res_trigger["ok"] is True
    assert "acionada" in res_trigger["message"]

    # Dry-run não dispara nada.
    dry = registry.for_dry_run()
    res_dry = dry.call("smart_home_automate", {"action": "trigger_scene", "name": "boa noite"})
    assert res_dry["ok"] is True
    assert res_dry.get("dry_run") is True

    registry.close()


def test_agent_tools_smart_home_events(monkeypatch):
    registry = ToolRegistry()

    fake_ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    fake_ha.recent_events = lambda limit=20: [
        {
            "entity_id": "light.avant_neo_quarto",
            "domain": "light",
            "friendly_name": "Luz Quarto",
            "old_state": "off",
            "state": "on",
        }
    ]
    fake_ha.realtime_status = lambda: {"running": True, "connected": True, "url": "u"}
    monkeypatch.setattr(HomeAssistantManager, "get_default", lambda: fake_ha)

    res = registry.call("smart_home_events", {"limit": 5})
    assert res["ok"] is True
    assert res["count"] == 1
    assert "Luz Quarto" in res["message"]
    registry.close()


def test_live_dispatch_smart_home_automate_e_events(fake_ha_states, monkeypatch):
    config = CopilotConfig(gemini_api_key="test-key")
    client = GeminiLiveClient(config=config)

    fake_ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    fake_ha._states_cache = _with_automations(fake_ha_states)
    fake_ha._cache_timestamp = 9999999999.0
    fake_ha.call_service = lambda domain, service, data: {"success": True}
    monkeypatch.setattr(HomeAssistantManager, "get_default", lambda: fake_ha)

    res_list = client._dispatch_tool("smart_home_automate", {"action": "list_scenes"})
    assert res_list["success"] is True
    assert res_list["count"] == 1

    res_trigger = client._dispatch_tool("smart_home_automate", {"action": "trigger_scene", "name": "boa noite"})
    assert res_trigger["success"] is True

    fake_ha.recent_events = lambda limit=20: [
        {"domain": "light", "friendly_name": "Luz Quarto", "old_state": "off", "state": "on"}
    ]
    res_events = client._dispatch_tool("smart_home_events", {"limit": 5})
    assert res_events["success"] is True
    assert res_events["count"] == 1


# ---------------------------------------------------------------------------
# Regressão: singleton obsoleto e mensagens honestas
# ---------------------------------------------------------------------------
def test_get_default_rele_quando_config_muda(monkeypatch):
    """Salvar as preferências deve ter efeito sem reiniciar o app.

    Regressão do bug real: o gerenciador era criado uma vez e nunca relia o
    config.json — o Live ficava com o token vazio e dizia "não configurado"
    mesmo depois de a tela de preferências conectar com sucesso.
    """
    monkeypatch.setattr(HomeAssistantManager, "_config_mtime", classmethod(lambda cls: 100.0))
    primeiro = HomeAssistantManager.get_default()

    # mtime inalterado → mesma instância (não recria a cada chamada).
    assert HomeAssistantManager.get_default() is primeiro

    # Config salva (mtime muda) → instância nova, com a config relida.
    monkeypatch.setattr(HomeAssistantManager, "_config_mtime", classmethod(lambda cls: 200.0))
    segundo = HomeAssistantManager.get_default()
    assert segundo is not primeiro

    # reset() explícito também força recriação (cobre granularidade de 1s).
    HomeAssistantManager.reset()
    assert HomeAssistantManager.get_default() is not segundo


def test_unavailable_quando_nao_configurado():
    ha = HomeAssistantManager(url="", token="")
    motivo = ha.unavailable()
    assert motivo is not None
    assert "não configurado" in motivo["message"].lower()
    assert "desabilitad" not in motivo["message"].lower()  # explícito => habilitado


def test_unavailable_none_quando_configurado():
    ha = HomeAssistantManager(url="http://localhost:8123", token="token")
    assert ha.unavailable() is None
    assert ha.is_enabled() is True


def test_unavailable_quando_desabilitado(monkeypatch):
    ha = HomeAssistantManager()  # apoiado na configuração
    monkeypatch.setattr(
        CopilotConfig,
        "load",
        classmethod(lambda cls: CopilotConfig(ha_enabled=False, ha_token="token-x")),
    )
    motivo = ha.unavailable()
    assert motivo is not None
    assert "desabilitad" in motivo["message"].lower()


def test_control_light_sem_config_mensagem_honesta():
    """Sem configuração, o controle não pode dizer "nenhuma lâmpada encontrada":
    a causa é outra e a mensagem tem de dizer qual é."""
    ha = HomeAssistantManager(url="", token="")
    res = ha.control_light(entity="qualquer")
    assert res["success"] is False
    assert "não configurado" in res["message"].lower()
    assert "nenhuma lâmpada" not in res["message"].lower()


def test_get_status_sem_config_mensagem_honesta():
    ha = HomeAssistantManager(url="", token="")
    res = ha.get_status()
    assert res["success"] is False
    assert "não configurado" in res["message"].lower()

