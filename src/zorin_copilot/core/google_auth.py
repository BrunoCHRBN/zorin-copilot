# Decisão de design: OAuth 2.0 no fluxo "Desktop app" (installed-app) com PKCE é o
# único correto para um app local. Não há segredo de cliente que valha esconder
# numa máquina do usuário (em app instalado o `client_secret` não é segredo), e o
# redirect é loopback (`http://localhost:<porta>`). O consent screen fica em
# publishing "In production" para o refresh token não expirar em 7 dias — e isso
# NÃO exige verificação da Google para uso pessoal (ver docs/google-oauth-setup.md).
#
# Por que as bibliotecas do Google são importadas DENTRO das funções: elas vivem no
# extra opcional `[google]` do pyproject. Sem o extra o app sobe e todo o resto
# funciona — só as ferramentas Google ficam indisponíveis, o mesmo tratamento que
# Pillow/Piper já têm. Um import no topo quebraria o app inteiro numa instalação
# sem o extra, que é o caso desta máquina hoje.
#
# Por que `is_connected()` NÃO importa as bibliotecas: ele decide olhando o token
# guardado (tem refresh_token? a validade ainda está no futuro?). Isso é puro,
# testável sem rede e sem o extra instalado — e é ele que a UI consulta a cada
# abertura, então precisa ser barato e à prova de ausência de dependência.

"""Autenticação OAuth2 e cofre de tokens para as integrações Google."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import CopilotConfig

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Registro de serviços e escopos
# --------------------------------------------------------------------------
#
# Escopos MÍNIMOS (least-privilege), aplicados a todas as etapas por decisão do
# usuário. A regra é pedir o menor escopo que ainda faz a operação funcionar:
#   - Calendar: `calendar.events` (eventos), NÃO `calendar` (agendas/acl/sharing).
#   - Drive: `drive.file` (só arquivos criados/abertos pelo app), NÃO `drive` (tudo).
#   - Gmail: `gmail.send` + `gmail.modify` (enviar e etiquetar), NÃO
#     `https://mail.google.com/` (acesso total, escopo restrito).
# Os escopos são a fonte única aqui — NÃO os duplicar em config, senão divergem.

SERVICE_SCOPES: dict[str, tuple[str, ...]] = {
    "calendar": ("https://www.googleapis.com/auth/calendar.events",),
    "drive": ("https://www.googleapis.com/auth/drive.file",),
    "gmail": (
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
    ),
}

#: Nome da API e versão usados por `googleapiclient.discovery.build`.
SERVICE_API: dict[str, tuple[str, str]] = {
    "calendar": ("calendar", "v3"),
    "drive": ("drive", "v3"),
    "gmail": ("gmail", "v1"),
}

SERVICE_LABELS: dict[str, str] = {
    "calendar": "Google Agenda",
    "drive": "Google Drive",
    "gmail": "Gmail",
}

#: Módulos que o extra `[google]` precisa prover. Checados por `find_spec` — não
#: por `import`, para não pagar o custo de carregar o cliente inteiro só para
#: saber se ele existe.
_REQUIRED_MODULES: tuple[str, ...] = (
    "googleapiclient.discovery",
    "google_auth_oauthlib.flow",
    "google.oauth2.credentials",
)

KEYRING_SERVICE_NAME = "zorin-copilot-google"


class GoogleAuthError(Exception):
    """Erro base das integrações Google. Mensagem sempre acionável pelo usuário."""


class GoogleLibsMissing(GoogleAuthError):
    """As bibliotecas do extra `[google]` não estão instaladas."""


class GoogleClientSecretMissing(GoogleAuthError):
    """Falta o `client_secret.json` do projeto no Google Cloud."""


class GoogleNotConnected(GoogleAuthError):
    """O serviço não tem token utilizável — é preciso conectar antes."""


class GoogleNetworkError(GoogleAuthError):
    """Falha de transporte/rede ao falar com o Google.

    Deliberadamente **distinta** de `GoogleNotConnected`: aqui a credencial é boa
    e continua guardada. Tratar as duas como iguais faria um Wi-Fi ruim apagar o
    token e exigir reautorização — trocar um problema de rede por um de conta.
    """


#: Marca do OAuth que indica credencial morta (revogada ou expirada de vez).
#: É o `error` que a Google devolve no corpo do refresh falho.
_REVOKED_MARKER = "invalid_grant"


def is_revocation_error(exc: Exception) -> bool:
    """A falha de refresh indica credencial **morta**, ou apenas rede?

    Medido contra a API: ao revogar o acesso em myaccount.google.com/permissions,
    o refresh falha com `invalid_grant`. Já um erro de transporte vem sem essa
    marca. Só o primeiro justifica apagar o token.
    """
    return _REVOKED_MARKER in str(exc).lower()


#: Campo NOSSO dentro do JSON do token: quando a autorização foi feita.
#: `credentials.to_json()` só conhece campos do Google, então qualquer chave nossa
#: evapora quando o token é regravado após um refresh — por isso ela é preservada
#: explicitamente em `_merge_token`.
AUTHORIZED_AT_KEY = "authorized_at"

#: Janela do refresh token com o app em "Testing" (usuário Externo).
#:
#: ATENÇÃO — é uma propriedade da **publicação do app**, não do token: a Google
#: não devolve esse prazo em campo nenhum (medido: as chaves do token real não
#: trazem nada sobre o refresh; `expiry` é o do *access token*, ~1 h). Se o app
#: for publicado, ou o cliente for Interno, o refresh **não expira** e este prazo
#: vira falso alarme. Por isso o aviso diz a premissa em voz alta e é desligável.
TESTING_REFRESH_WINDOW_DAYS = 7


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _merge_token(existing_raw: str | None, new_json: str) -> str:
    """Regrava o token preservando os metadados nossos.

    Sem isto o primeiro refresh apagaria `authorized_at` — e o aviso de expiração
    perderia, em silêncio, a única base que tem.
    """
    try:
        merged = json.loads(new_json)
    except (TypeError, ValueError):
        return new_json
    if not isinstance(merged, dict):
        return new_json
    try:
        existing = json.loads(existing_raw) if existing_raw else {}
    except (TypeError, ValueError):
        existing = {}
    if isinstance(existing, dict) and AUTHORIZED_AT_KEY in existing:
        merged[AUTHORIZED_AT_KEY] = existing[AUTHORIZED_AT_KEY]
    return json.dumps(merged)


def authorization_age_days(service: str, store: TokenStore | None = None) -> float | None:
    """Há quantos dias a autorização foi feita. `None` = **não sabemos**.

    `None` é a resposta honesta para tokens gravados antes deste campo existir.
    Chutar "0 dias" faria o aviso nunca disparar — justamente no caso que ele
    existe para cobrir.
    """
    store = store or default_token_store()
    try:
        raw = store.load(service)
    except Exception as exc:
        logger.debug("Idade da autorização de %s indisponível: %s", service, exc)
        return None
    if not raw:
        return None
    try:
        stamp = json.loads(raw).get(AUTHORIZED_AT_KEY)
        if not stamp:
            return None
        authorized = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError, AttributeError):
        return None
    if authorized.tzinfo is None:
        authorized = authorized.replace(tzinfo=timezone.utc)
    return (_now_utc() - authorized).total_seconds() / 86400.0


def refresh_expiry_warning(
    service: str,
    store: TokenStore | None = None,
    warn_days: int = TESTING_REFRESH_WINDOW_DAYS,
    margin_days: int = 2,
) -> dict[str, Any] | None:
    """Aviso quando a autorização se aproxima do fim da janela de "Testing".

    Devolve `None` quando não há o que avisar, ou um resumo com `days_left`
    (pode ser `None`), `severity` e `message`. `warn_days=0` desliga.

    Severidades: `unknown` (não sabemos a idade), `info` (chegando perto) e
    `warning` (passou da janela).

    Sobre `unknown`: um token gravado antes deste recurso não tem a data. A
    resposta escolhida NÃO é o silêncio — idade suposta faria o aviso nunca
    disparar justamente no ciclo em que ele é necessário, que é a falha que não
    se vê. Avisar que não sabemos é visível, é verdade e se resolve sozinho na
    próxima reconexão (que passa a gravar a data).
    """
    if not warn_days or warn_days <= 0:
        return None
    if not is_connected(service, store):
        return None

    label = SERVICE_LABELS.get(service, service)
    age = authorization_age_days(service, store)

    if age is None:
        return {
            "service": service,
            "label": label,
            "age_days": None,
            "days_left": None,
            "severity": "unknown",
            "message": (
                f"Não sei quando o {label} foi autorizado (conexão anterior a este aviso). "
                f"Em modo \"Testing\" o acesso dura ~{warn_days} dias — reconecte para o app "
                "passar a avisar com data."
            ),
        }

    days_left = float(warn_days) - age
    if days_left > margin_days:
        return None

    if days_left <= 0:
        severity = "warning"
        message = (
            f"A autorização do {label} passou de {warn_days} dias. Em modo \"Testing\" a "
            "Google expira o refresh token nesse prazo — reconecte para não ser pego de surpresa."
        )
    else:
        severity = "info"
        message = (
            f"O {label} foi autorizado há {age:.0f} dias. Em modo \"Testing\" o acesso expira "
            f"em ~{days_left:.0f} dia(s) — reconecte quando for conveniente."
        )
    return {
        "service": service,
        "label": label,
        "age_days": round(age, 1),
        "days_left": round(days_left, 1),
        "severity": severity,
        "message": message,
    }


def refresh_expiry_warnings(
    store: TokenStore | None = None,
    warn_days: int = TESTING_REFRESH_WINDOW_DAYS,
    margin_days: int = 2,
) -> list[dict[str, Any]]:
    """Os avisos de todos os serviços conhecidos, na ordem canônica."""
    found = []
    for service in known_services():
        warning = refresh_expiry_warning(
            service, store, warn_days=warn_days, margin_days=margin_days
        )
        if warning is not None:
            found.append(warning)
    return found


def known_services() -> tuple[str, ...]:
    """Serviços suportados, na ordem canônica de exibição."""
    return tuple(SERVICE_SCOPES.keys())


def is_known_service(service: str) -> bool:
    return service in SERVICE_SCOPES


def scopes_for(service: str) -> tuple[str, ...]:
    """Escopos mínimos de um serviço. Levanta para serviço desconhecido.

    Falhar alto aqui é deliberado: um serviço novo com escopo não declarado deve
    aparecer no desenvolvimento, não virar um token silenciosamente sem permissão.
    """
    if service not in SERVICE_SCOPES:
        raise GoogleAuthError(f"Serviço Google desconhecido: {service!r}")
    return SERVICE_SCOPES[service]


def _module_available(name: str) -> bool:
    """`find_spec` tolerante: pacote-pai ausente levanta, e isso é "não existe"."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def google_libs_available() -> bool:
    """Se o extra `[google]` está instalado. Nunca levanta."""
    return all(_module_available(m) for m in _REQUIRED_MODULES)


def missing_google_libs() -> tuple[str, ...]:
    return tuple(m for m in _REQUIRED_MODULES if not _module_available(m))


# --------------------------------------------------------------------------
# Caminhos
# --------------------------------------------------------------------------


def client_secret_path(cfg: CopilotConfig | None = None) -> Path:
    """Onde mora o `client_secret.json` baixado do Google Cloud.

    Config vazia = `~/.config/zorin-copilot/google_client_secret.json` (respeita
    `XDG_CONFIG_HOME`, e portanto o sandbox dos testes).
    """
    cfg = cfg or CopilotConfig.load()
    configured = (getattr(cfg, "google_client_secret_path", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return CopilotConfig.config_dir() / "google_client_secret.json"


def default_token_file() -> Path:
    """Caminho do cofre em arquivo (fallback quando não há `keyring`)."""
    return CopilotConfig.config_dir() / "google_tokens.json"


# --------------------------------------------------------------------------
# Cofre de tokens
# --------------------------------------------------------------------------


class TokenStore:
    """Contrato do cofre. Implementações: arquivo (0600) e keyring do sistema."""

    name = "base"

    def save(self, service: str, token_json: str) -> None:  # pragma: no cover - contrato
        raise NotImplementedError

    def load(self, service: str) -> str | None:  # pragma: no cover - contrato
        raise NotImplementedError

    def delete(self, service: str) -> None:  # pragma: no cover - contrato
        raise NotImplementedError


class FileTokenStore(TokenStore):
    """Cofre em arquivo JSON com permissão 0600.

    Não é o mais forte — um arquivo legível pelo dono é legível por qualquer
    processo do dono —, mas é o fallback honesto quando o `keyring` não está
    instalado, e é estritamente melhor que guardar o token em plaintext no
    `config.json` (que já vai para backup, para o git, para o olho de terceiros).
    """

    name = "file"

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else default_token_file()

    def _read_all(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Arquivo corrompido não pode derrubar o app: trata como vazio e segue.
            logger.warning("Cofre de tokens Google ilegível em %s; ignorando.", self.path)
            return {}
        return data if isinstance(data, dict) else {}

    def _write_all(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def save(self, service: str, token_json: str) -> None:
        data = self._read_all()
        data[service] = token_json
        self._write_all(data)

    def load(self, service: str) -> str | None:
        return self._read_all().get(service)

    def delete(self, service: str) -> None:
        data = self._read_all()
        if service in data:
            data.pop(service, None)
            self._write_all(data)


class KeyringTokenStore(TokenStore):
    """Cofre no Secret Service do sistema, via pacote `keyring`.

    No GNOME/Zorin o `keyring` usa o gnome-keyring, então o token fica cifrado e
    destravado pela sessão — não num arquivo. Exige `keyring` instalado (vem no
    extra `[google]`). O import é tardio de propósito: o cofre pode ser construído
    só quando o usuário escolhe esse backend.
    """

    name = "keyring"

    def __init__(self) -> None:
        import keyring  # noqa: F401 - presença é o que valida o backend

        self._keyring = keyring

    def save(self, service: str, token_json: str) -> None:
        self._keyring.set_password(KEYRING_SERVICE_NAME, service, token_json)

    def load(self, service: str) -> str | None:
        return self._keyring.get_password(KEYRING_SERVICE_NAME, service)

    def delete(self, service: str) -> None:
        try:
            self._keyring.delete_password(KEYRING_SERVICE_NAME, service)
        except Exception:  # senha ausente é o estado desejado, não um erro
            pass


def keyring_available() -> bool:
    return _module_available("keyring")


def default_token_store() -> TokenStore:
    """Escolhe o melhor cofre disponível.

    Ordem: `keyring` (se instalado) → arquivo 0600. `ZORIN_GOOGLE_TOKEN_BACKEND`
    força um backend ("keyring" ou "file") — útil para teste e para quem prefere
    o arquivo mesmo com o keyring presente. Um backend forçado e indisponível cai
    no arquivo em vez de quebrar: degradar é melhor que impedir o uso.
    """
    forced = (os.environ.get("ZORIN_GOOGLE_TOKEN_BACKEND") or "").strip().lower()
    if not forced:
        # Sem env, respeita a preferência salva. Leitura de config só neste
        # ramo — o caminho comum (keyring instalado) não paga o I/O.
        try:
            forced = (getattr(CopilotConfig.load(), "google_token_backend", "") or "").strip().lower()
        except Exception:
            forced = ""
    if forced == "file":
        return FileTokenStore()
    if forced == "keyring":
        if keyring_available():
            try:
                return KeyringTokenStore()
            except Exception as exc:  # keyring presente mas sem backend utilizável
                logger.warning("keyring indisponível (%s); usando arquivo.", exc)
        return FileTokenStore()
    if keyring_available():
        try:
            return KeyringTokenStore()
        except Exception as exc:
            logger.warning("keyring indisponível (%s); usando arquivo.", exc)
    return FileTokenStore()


# --------------------------------------------------------------------------
# Regras puras do token (testáveis sem rede e sem as bibliotecas)
# --------------------------------------------------------------------------


def parse_token(token_json: str | None) -> dict[str, Any] | None:
    """Interpreta o JSON do token. Devolve `None` para entrada inválida."""
    if not token_json:
        return None
    try:
        data = json.loads(token_json)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _expiry_in_future(expiry: Any, now: datetime) -> bool:
    """A validade está no futuro? Aceita ISO com ou sem 'Z'."""
    if not isinstance(expiry, str) or not expiry:
        return False
    raw = expiry.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > now


def token_is_usable(token: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """Um token armazenado permite uso agora?

    Regra: tem `refresh_token` (renova sozinho, indefinidamente) **ou** o access
    token ainda está válido. Um token sem refresh e já vencido não serve para
    nada — nesse caso o veredito é "não conectado", que é o estado VISÍVEL (o
    usuário vê que precisa reconectar). Valor desconhecido cai no caso visível,
    nunca no caso feliz: a assimetria é deliberada, ver `MEMORY.md`.
    """
    if not isinstance(token, dict):
        return False
    if token.get("refresh_token"):
        return True
    return _expiry_in_future(token.get("expiry"), now or datetime.now(timezone.utc))


def is_connected(service: str, store: TokenStore | None = None) -> bool:
    """Há credencial utilizável para o serviço? Nunca levanta, nunca vai à rede."""
    if not is_known_service(service):
        return False
    store = store or default_token_store()
    try:
        return token_is_usable(parse_token(store.load(service)))
    except Exception as exc:  # cofre indisponível não pode derrubar a UI
        logger.warning("Falha ao ler o cofre de tokens Google: %s", exc)
        return False


def service_enabled(
    service: str,
    enabled: bool,
    allowed_services: Iterable[str] | None,
) -> bool:
    """O serviço está habilitado pelas preferências? **Fonte única da regra.**

    `enabled` é o portão mestre (opt-in). `allowed_services`, quando não-vazia, é
    uma allowlist explícita — são os toggles por serviço. Lista vazia significa
    "o portão mestre já expressou a intenção", e qualquer serviço passa.

    Mora aqui, e não na UI nem no agente, porque os dois precisam **concordar**:
    uma cópia divergiria em silêncio e a tela mostraria "ligado" enquanto o agente
    recusa — ou o inverso. Mesma regra de "importe a regra, não reimplemente" que o
    projeto já aplica em outros pontos.
    """
    if not enabled:
        return False
    allowed = [str(item) for item in (allowed_services or []) if str(item).strip()]
    if allowed and service not in allowed:
        return False
    return True


def connection_status(store: TokenStore | None = None) -> list[dict[str, Any]]:
    """Status por serviço, para o CLI e a UI. `connected` é conservador."""
    store = store or default_token_store()
    libs = google_libs_available()
    rows: list[dict[str, Any]] = []
    for service in known_services():
        token = None
        try:
            token = parse_token(store.load(service))
        except Exception as exc:
            logger.warning("Falha ao ler token de %s: %s", service, exc)
        rows.append(
            {
                "service": service,
                "label": SERVICE_LABELS.get(service, service),
                "connected": token_is_usable(token),
                "has_refresh": bool(token and token.get("refresh_token")),
                "libs_available": libs,
                "scopes": list(scopes_for(service)),
            }
        )
    return rows


# --------------------------------------------------------------------------
# Fluxo OAuth e construção do cliente
# --------------------------------------------------------------------------


def _require_libs() -> None:
    if google_libs_available():
        return
    faltando = ", ".join(missing_google_libs())
    raise GoogleLibsMissing(
        "As bibliotecas do Google não estão instaladas "
        f"(faltando: {faltando}).\n"
        "  Instale com:  pip install 'zorin-copilot[google]'\n"
        "  ou, no repositório:  uv sync --extra google"
    )


def run_connect_flow(
    service: str,
    store: TokenStore | None = None,
    *,
    port: int = 0,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Executa o fluxo OAuth interativo e guarda o token. Bloqueia até o consent.

    `port=0` deixa o SO escolher a porta de loopback. `open_browser=False` só
    imprime a URL (útil em terminal remoto). Devolve um resumo — nunca o token.
    """
    if not is_known_service(service):
        raise GoogleAuthError(f"Serviço Google desconhecido: {service!r}")
    _require_libs()

    secret_file = client_secret_path()
    if not secret_file.exists():
        raise GoogleClientSecretMissing(
            f"Arquivo de credenciais não encontrado em {secret_file}.\n"
            "  Crie um OAuth Client ID do tipo \"Desktop app\" no Google Cloud,\n"
            "  baixe o JSON e salve nesse caminho (ver docs/google-oauth-setup.md)."
        )

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), list(scopes_for(service)))
    credentials = flow.run_local_server(port=port, open_browser=open_browser)

    store = store or default_token_store()
    # Grava a data da autorização JUNTO do token: é a única base do aviso de
    # expiração, porque a Google não informa quando o refresh token expira.
    payload = json.loads(credentials.to_json())
    payload[AUTHORIZED_AT_KEY] = _now_utc().isoformat()
    store.save(service, json.dumps(payload))

    return {
        "service": service,
        "label": SERVICE_LABELS.get(service, service),
        "connected": True,
        "has_refresh": bool(getattr(credentials, "refresh_token", None)),
        "backend": store.name,
        "authorized_at": payload[AUTHORIZED_AT_KEY],
    }


def _load_credentials(service: str, store: TokenStore):
    """Monta `Credentials` do cofre, renovando se vencido. Uso interno."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    raw = store.load(service)
    token = parse_token(raw)
    if not token_is_usable(token):
        raise GoogleNotConnected(
            f"{SERVICE_LABELS.get(service, service)} não está conectado.\n"
            f"  Conecte com:  zorin-copilot-cli google connect {service}"
        )

    credentials = Credentials.from_authorized_user_info(token, list(scopes_for(service)))
    if not credentials.valid:
        if credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                if is_revocation_error(exc):
                    # Credencial morta (o usuário revogou o acesso, ou o refresh
                    # token expirou de vez). Apagar é o que faz o estado virar
                    # "Reconexão necessária" na tela, em vez de um erro críptico a
                    # cada tentativa — e é o que a doc promete ao usuário.
                    store.delete(service)
                    raise GoogleNotConnected(
                        f"{SERVICE_LABELS.get(service, service)} precisa ser reconectado: "
                        "a autorização foi revogada ou expirou.\n"
                        f"  Conecte de novo com:  zorin-copilot-cli google connect {service}"
                    ) from exc
                # Sem a marca de revogação, o token continua válido: o problema é
                # de rede. NÃO apagar — senão um Wi-Fi ruim custaria reautorização.
                raise GoogleNetworkError(
                    f"Não foi possível renovar a credencial de "
                    f"{SERVICE_LABELS.get(service, service)} agora. A credencial continua "
                    "guardada; verifique a conexão e tente de novo."
                ) from exc
            store.save(service, _merge_token(raw, credentials.to_json()))
        else:
            raise GoogleNotConnected(
                f"A credencial de {SERVICE_LABELS.get(service, service)} expirou e não há "
                "refresh token. Reconecte com `google connect`."
            )
    return credentials


def get_service(service: str, store: TokenStore | None = None):
    """Cliente autenticado pronto para uso (ex.: `service.events().list(...)`).

    Importa as bibliotecas só aqui — quem chama já garantiu `google_enabled` e
    `is_connected()`. Levanta `GoogleLibsMissing`/`GoogleNotConnected` com
    mensagem acionável em vez de estourar um `ImportError` cru.
    """
    if not is_known_service(service):
        raise GoogleAuthError(f"Serviço Google desconhecido: {service!r}")
    _require_libs()

    from googleapiclient.discovery import build

    store = store or default_token_store()
    credentials = _load_credentials(service, store)
    api_name, api_version = SERVICE_API[service]
    return build(api_name, api_version, credentials=credentials, cache_discovery=False)


def disconnect(service: str, store: TokenStore | None = None) -> bool:
    """Remove o token guardado. Devolve `True` se havia algo a remover."""
    if not is_known_service(service):
        raise GoogleAuthError(f"Serviço Google desconhecido: {service!r}")
    store = store or default_token_store()
    had = store.load(service) is not None
    store.delete(service)
    return had
