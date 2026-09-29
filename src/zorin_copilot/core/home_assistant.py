# Decisão de design: cliente REST leve e resiliente para Home Assistant — controle de lâmpadas (dimerização, Kelvin, RGB), ar-condicionado, tomadas e cenas sem dependências externas pesadas.

"""Gerenciador de integração com Casa Inteligente e IoT via Home Assistant."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .config import CopilotConfig

logger = logging.getLogger(__name__)

# Mapa de cores básicas em português para RGB
COLOR_NAME_TO_RGB: dict[str, list[int]] = {
    "vermelho": [255, 0, 0],
    "vermelha": [255, 0, 0],
    "red": [255, 0, 0],
    "verde": [0, 255, 0],
    "green": [0, 255, 0],
    "azul": [0, 0, 255],
    "blue": [0, 0, 255],
    "amarelo": [255, 230, 0],
    "amarela": [255, 230, 0],
    "yellow": [255, 230, 0],
    "laranja": [255, 140, 0],
    "orange": [255, 140, 0],
    "roxo": [140, 0, 255],
    "roxa": [140, 0, 255],
    "purple": [140, 0, 255],
    "violeta": [180, 0, 255],
    "rosa": [255, 80, 180],
    "pink": [255, 80, 180],
    "ciano": [0, 240, 255],
    "cyan": [0, 240, 255],
    "branco": [255, 255, 255],
    "branca": [255, 255, 255],
    "white": [255, 255, 255],
}

# Temperatura de cor (Kelvin) com base em palavras-chave em português
KEYWORD_TO_KELVIN: dict[str, int] = {
    "quente": 2700,
    "quentes": 2700,
    "warm": 2700,
    "ambar": 2700,
    "âmbar": 2700,
    "relax": 2700,
    "descanso": 2700,
    "suave": 3200,
    "soft": 3200,
    "aconchegante": 3000,
    "neutro": 4000,
    "neutra": 4000,
    "neutral": 4000,
    "natural": 4000,
    "dia": 4500,
    "frio": 6500,
    "fria": 6500,
    "cold": 6500,
    "foco": 6500,
    "estudo": 6500,
    "trabalho": 6500,
}


class HomeAssistantManager:
    """Controlador de integração do Zorin Copilot com o Home Assistant."""

    _instance: HomeAssistantManager | None = None
    _instance_config_mtime: float = -1.0

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        cfg = CopilotConfig.load()
        raw_url = url if url is not None else cfg.ha_url
        self.url = (raw_url or "http://localhost:8123").rstrip("/")
        self.token = token if token is not None else (cfg.ha_token or os.environ.get("HOME_ASSISTANT_TOKEN", ""))
        # Criado sem URL/token explícitos = "apoiado na configuração": é assim que
        # o app o constrói. Nesse modo ele também respeita o liga/desliga das
        # preferências; instâncias explícitas (testes, "Testar Conexão") valem
        # por si e não dependem do config.json do usuário.
        self._from_config = url is None and token is None
        self._states_cache: list[dict[str, Any]] | None = None
        self._cache_timestamp: float = 0.0

    @classmethod
    def _config_mtime(cls) -> float:
        try:
            return CopilotConfig.config_file().stat().st_mtime
        except OSError:
            return 0.0

    @classmethod
    def get_default(cls) -> HomeAssistantManager:
        """Instância compartilhada, **relida quando a configuração muda**.

        O bug que isto corrige: o gerenciador era criado uma única vez e nunca
        mais relia o config.json. O Live o construía no arranque (token ainda
        vazio) e o processo inteiro ficava com a cópia antiga — a tela de
        preferências conectava (cria instância própria, com o token digitado)
        enquanto a voz respondia "Home Assistant não configurado". Salvar as
        preferências agora tem efeito imediato, sem reiniciar o aplicativo.
        """
        mtime = cls._config_mtime()
        if cls._instance is None or cls._instance_config_mtime != mtime:
            cls._instance = cls()
            cls._instance_config_mtime = mtime
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Descarta a instância para forçar releitura na próxima chamada.

        Usado ao salvar as preferências: garante efeito imediato mesmo quando o
        mtime do arquivo não muda (granularidade de 1s do sistema de arquivos).
        """
        cls._instance = None
        cls._instance_config_mtime = -1.0

    def is_configured(self) -> bool:
        """Indica se URL e token de acesso foram fornecidos."""
        return bool(self.url and self.token.strip())

    def is_enabled(self) -> bool:
        """Se a integração está ligada nas preferências.

        Só consulta o config.json quando a instância é apoiada na configuração;
        instâncias explícitas são consideradas habilitadas.
        """
        if not self._from_config:
            return True
        return bool(getattr(CopilotConfig.load(), "ha_enabled", True))

    def unavailable(self) -> dict[str, Any] | None:
        """Motivo pelo qual a integração não pode ser usada, ou ``None`` se pode.

        Centraliza a resposta honesta: antes, sem configuração, o controle dizia
        "Nenhuma lâmpada encontrada" (enganoso) enquanto o status dizia "não
        configurado" — mensagens contraditórias para a mesma causa.
        """
        if not self.is_enabled():
            return {
                "success": False,
                "message": (
                    "Casa Inteligente está desabilitada nas preferências. "
                    "Ative 'Habilitar Casa Inteligente' para usar o Home Assistant."
                ),
            }
        if not self.is_configured():
            return {
                "success": False,
                "message": (
                    "Home Assistant não configurado. Defina a URL e o Token de "
                    "Acesso de Longa Duração nas preferências."
                ),
            }
        return None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token.strip()}"
        return headers

    def _request_json(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        timeout: float = 5.0,
        retries: int = 1,
    ) -> dict[str, Any]:
        """Requisição HTTP com retry curto para falhas transitórias de rede.

        Devolve sempre ``{"ok", "status", "data"/"error"}`` — nunca levanta, para
        que os chamadores não repitam tratamento de exceção. **Não** repete erros
        de protocolo (HTTP 4xx/5xx): um 401 não melhora com insistência e só
        atrasaria o feedback ao usuário. Repete apenas queda de conexão/timeout,
        que é justamente o que acontece quando o container do HA reinicia.
        """
        url = f"{self.url}{path}"
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        attempt = 0
        last_err: str = ""
        while True:
            try:
                req = urllib.request.Request(url, data=payload, headers=self._headers(), method=method)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8")
                    parsed = json.loads(body) if body.strip() else None
                    return {"ok": True, "status": getattr(resp, "status", 200), "data": parsed}
            except urllib.error.HTTPError as err:
                return {
                    "ok": False,
                    "status": err.code,
                    "error": str(err.reason or err),
                    "http_error": True,
                }
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
                last_err = str(getattr(err, "reason", err))
                attempt += 1
                if attempt > retries:
                    return {"ok": False, "status": 0, "error": last_err}
                time.sleep(min(0.5 * attempt, 1.5))
            except Exception as exc:
                return {"ok": False, "status": 0, "error": str(exc)}

    def test_connection(self) -> dict[str, Any]:
        """Testa comunicação com a API do Home Assistant."""
        if not self.is_configured():
            return {
                "success": False,
                "message": "Home Assistant não configurado. Forneça a URL (ex: http://localhost:8123) e o Token de Acesso de Longa Duração.",
                "url": self.url,
            }
        res = self._request_json("GET", "/api/", timeout=4.0, retries=1)
        if res["ok"]:
            data = res.get("data") or {}
            msg = data.get("message", "API em execução") if isinstance(data, dict) else "API em execução"
            return {
                "success": True,
                "message": f"Conexão com Home Assistant estabelecida com sucesso: {msg}.",
                "details": data,
                "url": self.url,
            }
        if res.get("http_error") and res.get("status") == 401:
            return {
                "success": False,
                "message": "Erro de autenticação (HTTP 401): O Token de Acesso informado é inválido ou expirou.",
                "code": 401,
            }
        if res.get("http_error"):
            return {"success": False, "message": f"Erro HTTP {res['status']} ao conectar no Home Assistant: {res['error']}"}
        return {
            "success": False,
            "message": f"Não foi possível alcançar o Home Assistant em '{self.url}'. Verifique se o container/serviço está ativo ({res['error']}).",
        }

    def get_states(self, domain: str | None = None, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Recupera lista de entidades e seus estados atuais no Home Assistant."""
        now = time.time()
        if not force_refresh and self._states_cache and (now - self._cache_timestamp < 8.0):
            states = self._states_cache
        else:
            res = self._request_json("GET", "/api/states", timeout=5.0, retries=1)
            if res["ok"] and isinstance(res.get("data"), list):
                states = res["data"]
                self._states_cache = states
                self._cache_timestamp = now
            else:
                logger.warning("Falha ao buscar estados do Home Assistant (%s): %s", self.url, res.get("error"))
                return []

        if domain:
            dom_prefix = f"{domain}."
            return [s for s in states if str(s.get("entity_id", "")).startswith(dom_prefix)]
        return states

    def find_entity(self, query: str = "", domain: str | None = None) -> dict[str, Any] | None:
        """Localiza uma entidade pelo ID exato, nome amigável ou único dispositivo no domínio."""
        entities = self.get_states(domain=domain)
        if not entities:
            return None

        clean_q = query.strip().lower()

        # 1. Se nenhuma query foi passada, mas existe exatamente uma entidade no domínio, usa ela!
        # (Cenário perfeito para usuários com uma lâmpada ou um aparelho específico).
        if not clean_q:
            if len(entities) == 1:
                return entities[0]
            return None

        # 2. Busca exata por entity_id
        for ent in entities:
            if ent.get("entity_id", "").lower() == clean_q:
                return ent

        # 3. Busca por nome amigável exato
        for ent in entities:
            attrs = ent.get("attributes") or {}
            fname = str(attrs.get("friendly_name") or "").strip().lower()
            if fname == clean_q:
                return ent

        # 4. Busca por substring no nome amigável ou entity_id
        for ent in entities:
            attrs = ent.get("attributes") or {}
            fname = str(attrs.get("friendly_name") or "").strip().lower()
            eid = str(ent.get("entity_id") or "").lower()
            if clean_q in fname or clean_q in eid:
                return ent

        # 5. Busca por palavras parciais (ex: "quarto", "sala", "avant")
        q_words = [w for w in clean_q.split() if len(w) >= 3]
        for ent in entities:
            attrs = ent.get("attributes") or {}
            fname = str(attrs.get("friendly_name") or "").strip().lower()
            if any(w in fname for w in q_words):
                return ent

        # Se só temos uma entidade no domínio e a busca mencionou o tipo de aparelho
        if len(entities) == 1 and domain and domain in clean_q:
            return entities[0]

        return None

    def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Executa um serviço no Home Assistant (ex.: light.turn_on, switch.toggle)."""
        if not self.is_configured():
            return {
                "success": False,
                "message": "Home Assistant não configurado. Defina a URL e o Token nas configurações.",
            }

        res = self._request_json(
            "POST", f"/api/services/{domain}/{service}", service_data or {}, timeout=5.0, retries=1
        )
        if res["ok"]:
            # Limpa cache para refletir nova mudança na próxima consulta
            self._states_cache = None
            return {
                "success": True,
                "message": f"Serviço '{domain}.{service}' executado com sucesso.",
                "result": res.get("data"),
            }
        if res.get("http_error"):
            return {
                "success": False,
                "message": f"Erro do Home Assistant (HTTP {res['status']}) ao chamar '{domain}.{service}': {res['error']}",
            }
        return {
            "success": False,
            "message": f"Falha de rede ao conectar com Home Assistant: {res['error']}",
        }

    def control_light(
        self,
        entity: str = "",
        action: str = "turn_on",
        brightness: int | None = None,
        color_temp: int | str | None = None,
        color: str | list[int] | None = None,
    ) -> dict[str, Any]:
        """Controla lâmpadas inteligentes (ligar/desligar, dimerização de brilho, temperatura Kelvin e cor)."""
        bloqueio = self.unavailable()
        if bloqueio:
            return bloqueio
        target_ent = self.find_entity(query=entity, domain="light")
        if not target_ent:
            available = [e.get("entity_id") for e in self.get_states(domain="light")]
            if not available:
                return {
                    "success": False,
                    "message": "Nenhuma lâmpada encontrada no Home Assistant. Verifique se o dispositivo está integrado e online.",
                }
            return {
                "success": False,
                "message": f"Lâmpada '{entity}' não encontrada. Lâmpadas disponíveis: {', '.join(available)}.",
                "available_lights": available,
            }

        entity_id = target_ent["entity_id"]
        friendly_name = (target_ent.get("attributes") or {}).get("friendly_name", entity_id)
        act = action.strip().lower()

        if act in ("turn_off", "off", "desligar", "apagar"):
            res = self.call_service("light", "turn_off", {"entity_id": entity_id})
            if res.get("success"):
                res["message"] = f"Lâmpada '{friendly_name}' desligada."
            return res

        service_data: dict[str, Any] = {"entity_id": entity_id}
        details_str: list[str] = []

        # Brilho (1..100% ou 0..255)
        if brightness is not None:
            try:
                b_val = int(brightness)
                if 0 <= b_val <= 100:
                    service_data["brightness_pct"] = b_val
                    details_str.append(f"brilho em {b_val}%")
                else:
                    b_clamped = max(1, min(255, b_val))
                    service_data["brightness"] = b_clamped
                    details_str.append(f"brilho em {round(b_clamped / 2.55)}%")
            except (ValueError, TypeError):
                pass

        # Temperatura de cor (Kelvin)
        if color_temp is not None:
            kelvin_val: int | None = None
            if isinstance(color_temp, (int, float)):
                kelvin_val = int(color_temp)
            elif isinstance(color_temp, str):
                cleaned_ct = color_temp.strip().lower()
                # Verifica palavra-chave (ex: "quente", "frio", "neutro")
                for kw, kval in KEYWORD_TO_KELVIN.items():
                    if kw in cleaned_ct:
                        kelvin_val = kval
                        break
                if kelvin_val is None:
                    # Extrai números (ex: "2700k" -> 2700)
                    match = re.search(r"\d+", cleaned_ct)
                    if match:
                        kelvin_val = int(match.group(0))

            if kelvin_val is not None:
                # Clampa na faixa típica de iluminação (2000K a 6500K)
                kelvin_val = max(2000, min(6500, kelvin_val))
                service_data["color_temp_kelvin"] = kelvin_val
                temp_desc = "branco quente" if kelvin_val <= 3200 else ("branco frio" if kelvin_val >= 5500 else "branco neutro")
                details_str.append(f"temperatura em {kelvin_val}K ({temp_desc})")

        # Cor RGB
        if color is not None:
            rgb_val: list[int] | None = None
            if isinstance(color, list) and len(color) == 3:
                rgb_val = [int(max(0, min(255, c))) for c in color]
            elif isinstance(color, str):
                c_clean = color.strip().lower()
                rgb_val = COLOR_NAME_TO_RGB.get(c_clean)
                if rgb_val is None and c_clean.startswith("#") and len(c_clean) == 7:
                    try:
                        rgb_val = [int(c_clean[i : i + 2], 16) for i in (1, 3, 5)]
                    except ValueError:
                        pass

            if rgb_val:
                service_data["rgb_color"] = rgb_val
                details_str.append(f"cor alterada")

        ha_service = "toggle" if act in ("toggle", "alternar") else "turn_on"
        res = self.call_service("light", ha_service, service_data)
        if res.get("success"):
            details_joined = f" com {', '.join(details_str)}" if details_str else ""
            verb = "alternada" if ha_service == "toggle" else "ligada"
            res["message"] = f"Lâmpada '{friendly_name}' {verb}{details_joined}."
            res["entity_id"] = entity_id
            res["friendly_name"] = friendly_name
        return res

    def control_climate(
        self,
        entity: str = "",
        action: str = "set_temperature",
        temperature: float | None = None,
        hvac_mode: str | None = None,
    ) -> dict[str, Any]:
        """Controla climatização / ar-condicionado (temperatura e modo de operação)."""
        bloqueio = self.unavailable()
        if bloqueio:
            return bloqueio
        target_ent = self.find_entity(query=entity, domain="climate")
        if not target_ent:
            available = [e.get("entity_id") for e in self.get_states(domain="climate")]
            return {
                "success": False,
                "message": f"Ar-condicionado '{entity}' não encontrado. Dispositivos disponíveis: {', '.join(available) or 'nenhum'}.",
            }

        entity_id = target_ent["entity_id"]
        friendly_name = (target_ent.get("attributes") or {}).get("friendly_name", entity_id)
        act = action.strip().lower()

        if act in ("turn_off", "off", "desligar"):
            res = self.call_service("climate", "turn_off", {"entity_id": entity_id})
            if res.get("success"):
                res["message"] = f"Ar-condicionado '{friendly_name}' desligado."
            return res

        if act in ("turn_on", "on", "ligar"):
            res = self.call_service("climate", "turn_on", {"entity_id": entity_id})
            if res.get("success"):
                res["message"] = f"Ar-condicionado '{friendly_name}' ligado."
            return res

        service_data: dict[str, Any] = {"entity_id": entity_id}
        details_list: list[str] = []

        if temperature is not None:
            service_data["temperature"] = float(temperature)
            details_list.append(f"temperatura em {temperature}°C")

        if hvac_mode:
            # Mapeia modos em português (refrigeração, aquecimento, ventilador, auto)
            mode_map = {
                "frio": "cool",
                "refrigerar": "cool",
                "cool": "cool",
                "quente": "heat",
                "aquecer": "heat",
                "heat": "heat",
                "ventilador": "fan_only",
                "ventilar": "fan_only",
                "fan": "fan_only",
                "auto": "auto",
                "automatico": "auto",
                "desligar": "off",
                "off": "off",
            }
            clean_mode = mode_map.get(hvac_mode.strip().lower(), hvac_mode.strip().lower())
            service_data["hvac_mode"] = clean_mode
            details_list.append(f"modo '{clean_mode}'")

        service_name = "set_temperature" if "temperature" in service_data else "set_hvac_mode"
        res = self.call_service("climate", service_name, service_data)
        if res.get("success"):
            joined = f" ({', '.join(details_list)})" if details_list else ""
            res["message"] = f"Ar-condicionado '{friendly_name}' atualizado{joined}."
            res["entity_id"] = entity_id
        return res

    def control_switch(self, entity: str = "", action: str = "toggle") -> dict[str, Any]:
        """Controla tomadas, interruptores e relés inteligentes."""
        bloqueio = self.unavailable()
        if bloqueio:
            return bloqueio
        target_ent = self.find_entity(query=entity, domain="switch")
        if not target_ent:
            available = [e.get("entity_id") for e in self.get_states(domain="switch")]
            return {
                "success": False,
                "message": f"Interruptor/Tomada '{entity}' não encontrado. Disponíveis: {', '.join(available) or 'nenhum'}.",
            }

        entity_id = target_ent["entity_id"]
        friendly_name = (target_ent.get("attributes") or {}).get("friendly_name", entity_id)
        act = action.strip().lower()

        ha_service = "turn_on" if act in ("on", "ligar") else ("turn_off" if act in ("off", "desligar") else "toggle")
        res = self.call_service("switch", ha_service, {"entity_id": entity_id})
        if res.get("success"):
            verb = "ligado(a)" if ha_service == "turn_on" else ("desligado(a)" if ha_service == "turn_off" else "alternado(a)")
            res["message"] = f"Dispositivo '{friendly_name}' {verb}."
            res["entity_id"] = entity_id
        return res

    def get_status(self, entity_or_query: str = "", domain: str | None = None) -> dict[str, Any]:
        """Retorna o estado legível e completo de um dispositivo ou de toda a casa."""
        bloqueio = self.unavailable()
        if bloqueio:
            return bloqueio

        if entity_or_query.strip():
            ent = self.find_entity(query=entity_or_query, domain=domain)
            if not ent:
                return {
                    "success": False,
                    "message": f"Dispositivo '{entity_or_query}' não encontrado no Home Assistant.",
                }
            return self._format_entity_status(ent)

        # Se nenhuma consulta foi passada, gera um resumo geral dos dispositivos disponíveis
        states = self.get_states(domain=domain)
        if not states:
            return {"success": True, "message": "Nenhum dispositivo encontrado no Home Assistant.", "count": 0}

        summaries = [self._format_entity_status(e)["message"] for e in states[:15]]
        return {
            "success": True,
            "count": len(states),
            "message": "Estado dos dispositivos na casa:\n• " + "\n• ".join(summaries),
            "entities": [e.get("entity_id") for e in states],
        }

    def _format_entity_status(self, entity: dict[str, Any]) -> dict[str, Any]:
        """Gera descrição legível em linguagem natural para um dispositivo."""
        eid = entity.get("entity_id", "")
        state = entity.get("state", "desconhecido")
        attrs = entity.get("attributes") or {}
        fname = attrs.get("friendly_name", eid)
        domain = eid.split(".")[0] if "." in eid else ""

        desc_parts: list[str] = []

        if domain == "light":
            state_str = "ligada" if state == "on" else ("desligada" if state == "off" else state)
            desc = f"Lâmpada '{fname}' está {state_str}"
            if state == "on":
                if "brightness" in attrs:
                    pct = round((attrs["brightness"] / 255.0) * 100)
                    desc_parts.append(f"brilho em {pct}%")
                if "color_temp_kelvin" in attrs:
                    k = attrs["color_temp_kelvin"]
                    t_str = "branco quente" if k <= 3200 else ("branco frio" if k >= 5500 else "branco neutro")
                    desc_parts.append(f"{k}K ({t_str})")
            if desc_parts:
                desc += f" com {', '.join(desc_parts)}"
            desc += "."
            return {"success": True, "entity_id": eid, "state": state, "message": desc, "attributes": attrs}

        if domain == "climate":
            temp = attrs.get("current_temperature")
            target_temp = attrs.get("temperature")
            hvac = state
            desc = f"Ar-condicionado '{fname}' está em modo '{hvac}'"
            if temp is not None:
                desc += f", temperatura ambiente {temp}°C"
            if target_temp is not None:
                desc += f", ajustado para {target_temp}°C"
            desc += "."
            return {"success": True, "entity_id": eid, "state": state, "message": desc, "attributes": attrs}

        if domain == "switch":
            state_str = "ligada" if state == "on" else ("desligada" if state == "off" else state)
            return {
                "success": True,
                "entity_id": eid,
                "state": state,
                "message": f"Tomada/Interruptor '{fname}' está {state_str}.",
                "attributes": attrs,
            }

        if domain in ("scene", "script", "automation"):
            label = {"scene": "Cena", "script": "Script", "automation": "Automação"}[domain]
            return {
                "success": True,
                "entity_id": eid,
                "state": state,
                "message": f"{label} '{fname}' (estado '{state}').",
                "attributes": attrs,
            }

        return {
            "success": True,
            "entity_id": eid,
            "state": state,
            "message": f"Dispositivo '{fname}' está com estado '{state}'.",
            "attributes": attrs,
        }

    # ------------------------------------------------------------------------
    # Cenas, Scripts e Automações — o que o Home Assistant tem de mais útil no
    # dia a dia (uma frase aciona N dispositivos) e que a versão anterior não
    # alcançava: ela só sabia falar de light/climate/switch.
    # ------------------------------------------------------------------------
    def _list_domain(self, domain: str) -> list[dict[str, Any]]:
        """Lista entidades de um domínio com nome amigável e estado."""
        out: list[dict[str, Any]] = []
        for s in self.get_states(domain=domain):
            attrs = s.get("attributes") or {}
            eid = s.get("entity_id", "")
            out.append({
                "entity_id": eid,
                "friendly_name": attrs.get("friendly_name") or eid,
                "state": s.get("state"),
            })
        return out

    def list_scenes(self) -> list[dict[str, Any]]:
        """Cenas configuradas no Home Assistant."""
        return self._list_domain("scene")

    def list_scripts(self) -> list[dict[str, Any]]:
        """Scripts configurados no Home Assistant."""
        return self._list_domain("script")

    def list_automations(self) -> list[dict[str, Any]]:
        """Automações configuradas no Home Assistant."""
        return self._list_domain("automation")

    def _trigger_entity(self, domain: str, query: str, service: str, label: str) -> dict[str, Any]:
        """Localiza uma entidade de cena/script/automação e aciona seu serviço."""
        bloqueio = self.unavailable()
        if bloqueio:
            return bloqueio
        ent = self.find_entity(query=query, domain=domain)
        if not ent:
            available = [e.get("entity_id") for e in self.get_states(domain=domain)]
            if not available:
                return {
                    "success": False,
                    "message": f"Nenhuma {label.lower()} encontrada no Home Assistant.",
                }
            return {
                "success": False,
                "message": f"{label} '{query}' não encontrada. Disponíveis: {', '.join(available)}.",
                "available": available,
            }
        entity_id = ent["entity_id"]
        friendly_name = (ent.get("attributes") or {}).get("friendly_name", entity_id)
        res = self.call_service(domain, service, {"entity_id": entity_id})
        if res.get("success"):
            verb = "disparada" if service == "trigger" else "acionada"
            res["message"] = f"{label} '{friendly_name}' {verb}."
            res["entity_id"] = entity_id
        return res

    def trigger_scene(self, scene: str = "") -> dict[str, Any]:
        """Ativa uma cena (scene.turn_on)."""
        return self._trigger_entity("scene", scene, "turn_on", "Cena")

    def run_script(self, script: str = "") -> dict[str, Any]:
        """Executa um script do Home Assistant (script.turn_on)."""
        return self._trigger_entity("script", script, "turn_on", "Script")

    def trigger_automation(self, automation: str = "") -> dict[str, Any]:
        """Dispara uma automação (automation.trigger), ignorando suas condições."""
        return self._trigger_entity("automation", automation, "trigger", "Automação")

    # ------------------------------------------------------------------------
    # Eventos em tempo real (WebSocket) — delegação para o cliente dedicado.
    # ------------------------------------------------------------------------
    def _realtime(self):
        from .home_assistant_realtime import get_realtime

        return get_realtime(self.url, self.token)

    def start_realtime(self) -> bool:
        """Liga a escuta de eventos em tempo real (idempotente)."""
        return self._realtime().start()

    def stop_realtime(self) -> None:
        """Desliga a escuta de eventos em tempo real."""
        self._realtime().stop()

    def realtime_status(self) -> dict[str, Any]:
        """Estado do canal de eventos (rodando/conectado/nº de assinantes)."""
        rt = self._realtime()
        return {
            "running": rt.is_running(),
            "connected": rt.is_connected(),
            "url": rt.url,
        }

    def on_event(
        self,
        callback: Callable[[dict[str, Any]], None],
        *,
        entity_filter: set[str] | None = None,
        domains: set[str] | None = None,
    ) -> Callable[[], None]:
        """Assina mudanças de estado. Devolve a função de cancelamento.

        O canal precisa estar ligado (``start_realtime``) para os callbacks
        dispararem; assinar antes só enfileira a intenção.
        """
        return self._realtime().subscribe(callback, entity_filter=entity_filter, domains=domains)

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        """Últimos eventos de estado observados em tempo real."""
        return self._realtime().recent_events(limit)

    def health(self) -> dict[str, Any]:
        """Diagnóstico rápido: configurado? alcançável? canal de eventos ativo?"""
        rt_status = self.realtime_status()
        if not self.is_configured():
            return {
                "success": False,
                "configured": False,
                "reachable": False,
                "realtime": rt_status,
                "message": "Home Assistant não configurado (URL ou token ausente).",
            }
        res = self._request_json("GET", "/api/", timeout=3.0, retries=0)
        reachable = bool(res["ok"])
        if reachable:
            message = "Home Assistant acessível."
            if rt_status.get("connected"):
                message += " Eventos em tempo real ativos."
        elif res.get("http_error") and res.get("status") == 401:
            message = "Token inválido (HTTP 401)."
        else:
            message = f"Home Assistant inalcançável: {res.get('error')}."
        return {
            "success": reachable,
            "configured": True,
            "reachable": reachable,
            "realtime": rt_status,
            "message": message,
        }
