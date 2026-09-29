"""Testes do núcleo de autenticação Google (`core/google_auth.py`).

Nada aqui vai à rede nem exige o extra `[google]`: o que se testa é a **decisão**
(quando um token serve, o que cada serviço pede, como o cofre se comporta),
isolada das bibliotecas do Google por construção — as regras puras não as importam.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from zorin_copilot.core import google_auth as ga


class FakeKeyring:
    """`keyring` falso em memória — testa o USO da API, não o pacote."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}
        self.deleted: list[tuple[str, str]] = []

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise KeyError("senha ausente")
        self.deleted.append((service, username))
        self.store.pop((service, username), None)


class _MemoryStore(ga.TokenStore):
    """Cofre em memória, para os testes não tocarem disco."""

    name = "memory"

    def __init__(self, data=None):
        self.data = dict(data or {})

    def save(self, service, token_json):
        self.data[service] = token_json

    def load(self, service):
        return self.data.get(service)

    def delete(self, service):
        self.data.pop(service, None)


# --------------------------------------------------------------------------
# Dobras do `google` — exercitam os caminhos que importam as bibliotecas, sem
# instalar o extra. Testam o NOSSO uso da API (refresh, persistência, ordem),
# que é justamente onde mora o defeito silencioso.
# --------------------------------------------------------------------------


class _FakeRefreshError(Exception):
    """Equivalente ao `google.auth.exceptions.RefreshError` nas dobras.

    A Google usa essa exceção tanto para credencial revogada (`invalid_grant`)
    quanto para falha de transporte — é o TEXTO que separa os dois casos.
    """


class _FakeCredentials:
    last_instance: "_FakeCredentials | None" = None
    #: Exceção que `refresh()` deve levantar. Fica na CLASSE (não no JSON do
    #: token) porque um objeto de exceção não é serializável — e o cofre de
    #: mentira guarda JSON, como o real.
    refresh_error: Exception | None = None

    def __init__(self, info, scopes=None):
        _FakeCredentials.last_instance = self
        self.refresh_token = info.get("refresh_token")
        self.token = info.get("token")
        self.expiry = info.get("expiry")
        self.valid = bool(info.get("_valid", True))
        self.expired = bool(info.get("_expired", False))
        self.refreshed = False
        self._json = info.get("_json", '{"refresh_token": "r", "token": "novo"}')

    @classmethod
    def from_authorized_user_info(cls, info, scopes=None):
        return cls(info, scopes)

    def refresh(self, request):
        self.refreshed = True
        if type(self).refresh_error is not None:
            raise type(self).refresh_error
        self.valid = True
        self.expired = False
        # O token renovado é DIFERENTE do antigo — é o que permite ao teste
        # provar que a regravação aconteceu (senão a asserção casaria com a
        # entrada e passaria mesmo sem persistir).
        self._json = '{"refresh_token": "r", "token": "renovado"}'

    def to_json(self):
        return self._json


_FAKE_NAMES = (
    "google",
    "google.auth",
    "google.auth.exceptions",
    "google.auth.transport",
    "google.auth.transport.requests",
    "google.oauth2",
    "google.oauth2.credentials",
    "googleapiclient",
    "googleapiclient.discovery",
    "google_auth_oauthlib",
    "google_auth_oauthlib.flow",
)


def _make_flow_class(creds):
    class Flow:
        called: dict = {}

        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            Flow.called["path"] = path
            Flow.called["scopes"] = list(scopes)
            return cls()

        def run_local_server(self, port=0, open_browser=True):
            Flow.called["port"] = port
            Flow.called["open_browser"] = open_browser
            return creds

    return Flow


@contextlib.contextmanager
def _fake_google(creds=None, build_result=None, refresh_error=None):
    """Instala um `google` de mentira em `sys.modules` e o remove ao sair."""
    saved = {name: sys.modules.get(name) for name in _FAKE_NAMES}
    saved_refresh_error = _FakeCredentials.refresh_error
    _FakeCredentials.refresh_error = refresh_error
    calls: dict = {}

    def _mod(name):
        return types.ModuleType(name)

    google = _mod("google")
    google_auth = _mod("google.auth")
    google_exceptions = _mod("google.auth.exceptions")
    google_transport = _mod("google.auth.transport")
    google_requests = _mod("google.auth.transport.requests")
    google_oauth2 = _mod("google.oauth2")
    google_creds_mod = _mod("google.oauth2.credentials")
    gapiclient = _mod("googleapiclient")
    gapiclient_discovery = _mod("googleapiclient.discovery")
    oauthlib = _mod("google_auth_oauthlib")
    oauthlib_flow = _mod("google_auth_oauthlib.flow")

    google_creds_mod.Credentials = _FakeCredentials
    google_requests.Request = object
    google_exceptions.RefreshError = _FakeRefreshError

    if build_result is not None:
        def build(api, version, credentials=None, cache_discovery=True):
            calls["build"] = (api, version, credentials, cache_discovery)
            return build_result

        gapiclient_discovery.build = build

    if creds is not None:
        Flow = _make_flow_class(creds)
        oauthlib_flow.InstalledAppFlow = Flow
        calls["flow"] = Flow.called

    # Liga os pais aos filhos: o `import` acha em sys.modules, mas ligar torna a
    # dobra idêntica à árvore real (e evita surpresa em `getattr`).
    google.auth = google_auth
    google.oauth2 = google_oauth2
    google_auth.exceptions = google_exceptions
    google_auth.transport = google_transport
    google_transport.requests = google_requests
    google_oauth2.credentials = google_creds_mod
    gapiclient.discovery = gapiclient_discovery
    oauthlib.flow = oauthlib_flow

    for name, module in {
        "google": google,
        "google.auth": google_auth,
        "google.auth.exceptions": google_exceptions,
        "google.auth.transport": google_transport,
        "google.auth.transport.requests": google_requests,
        "google.oauth2": google_oauth2,
        "google.oauth2.credentials": google_creds_mod,
        "googleapiclient": gapiclient,
        "googleapiclient.discovery": gapiclient_discovery,
        "google_auth_oauthlib": oauthlib,
        "google_auth_oauthlib.flow": oauthlib_flow,
    }.items():
        sys.modules[name] = module

    try:
        yield calls
    finally:
        _FakeCredentials.refresh_error = saved_refresh_error
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class ServiceRegistryTest(unittest.TestCase):
    def test_known_services_stable(self):
        self.assertEqual(ga.known_services(), ("calendar", "drive", "gmail"))

    def test_every_service_has_api_and_label(self):
        for service in ga.known_services():
            self.assertIn(service, ga.SERVICE_API, f"{service} sem API mapeada")
            self.assertIn(service, ga.SERVICE_LABELS, f"{service} sem rótulo")
            self.assertTrue(ga.scopes_for(service), f"{service} sem escopos")

    def test_least_privilege_scopes(self):
        """Os escopos são os mínimos — a decisão do usuário, travada em teste.

        Se alguém trocar por um escopo mais amplo (`drive`, `calendar`,
        `https://mail.google.com/`), isto falha e força a decisão a ser
        consciente em vez de silenciosa.
        """
        self.assertEqual(ga.scopes_for("drive"), ("https://www.googleapis.com/auth/drive.file",))
        self.assertEqual(ga.scopes_for("calendar"), ("https://www.googleapis.com/auth/calendar.events",))
        gmail = ga.scopes_for("gmail")
        self.assertIn("https://www.googleapis.com/auth/gmail.send", gmail)
        self.assertIn("https://www.googleapis.com/auth/gmail.modify", gmail)
        # O escopo de acesso TOTAL ao Gmail é restrito; nunca deve aparecer.
        for service in ga.known_services():
            self.assertNotIn("https://mail.google.com/", ga.scopes_for(service))

    def test_unknown_service_raises(self):
        with self.assertRaises(ga.GoogleAuthError):
            ga.scopes_for("bogus")
        self.assertFalse(ga.is_known_service("bogus"))
        self.assertTrue(ga.is_known_service("calendar"))


class TokenRulesTest(unittest.TestCase):
    """Regras puras: sem rede, sem bibliotecas, sem cofre."""

    def test_parse_token_invalid_returns_none(self):
        self.assertIsNone(ga.parse_token(None))
        self.assertIsNone(ga.parse_token(""))
        self.assertIsNone(ga.parse_token("{não é json"))
        self.assertIsNone(ga.parse_token("[1, 2, 3]"))  # JSON válido, não-dict
        self.assertEqual(ga.parse_token('{"refresh_token": "x"}'), {"refresh_token": "x"})

    def test_token_with_refresh_is_usable(self):
        self.assertTrue(ga.token_is_usable({"refresh_token": "r"}))

    def test_expired_without_refresh_is_not_usable(self):
        """O caso que separa 'conectado' de 'precisa reconectar'."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.assertFalse(ga.token_is_usable({"expiry": past}))

    def test_future_expiry_without_refresh_is_usable(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.assertTrue(ga.token_is_usable({"expiry": future}))

    def test_expiry_with_z_suffix_accepted(self):
        self.assertTrue(ga.token_is_usable({"expiry": "2999-01-01T00:00:00Z"}))
        self.assertFalse(ga.token_is_usable({"expiry": "2000-01-01T00:00:00Z"}))

    def test_garbage_is_not_usable(self):
        """Valor desconhecido cai no caso VISÍVEL (não conectado), nunca no feliz."""
        self.assertFalse(ga.token_is_usable(None))
        self.assertFalse(ga.token_is_usable("string"))
        self.assertFalse(ga.token_is_usable({}))
        self.assertFalse(ga.token_is_usable({"expiry": "não é data"}))


class FileTokenStoreTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "google_tokens.json"
        self.addCleanup(self._dir.cleanup)
        self.store = ga.FileTokenStore(self.path)

    def test_roundtrip(self):
        self.assertIsNone(self.store.load("calendar"))
        self.store.save("calendar", '{"refresh_token": "r"}')
        self.assertEqual(self.store.load("calendar"), '{"refresh_token": "r"}')

    def test_file_permissions_are_0600(self):
        self.store.save("calendar", '{"refresh_token": "r"}')
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600, f"permissões do cofre: {oct(mode)}")

    def test_services_are_independent(self):
        self.store.save("calendar", '{"refresh_token": "c"}')
        self.store.save("drive", '{"refresh_token": "d"}')
        self.assertEqual(self.store.load("calendar"), '{"refresh_token": "c"}')
        self.assertEqual(self.store.load("drive"), '{"refresh_token": "d"}')
        self.store.delete("calendar")
        self.assertIsNone(self.store.load("calendar"))
        self.assertEqual(self.store.load("drive"), '{"refresh_token": "d"}')

    def test_delete_missing_is_noop(self):
        self.store.delete("gmail")  # não levanta
        self.assertIsNone(self.store.load("gmail"))

    def test_corrupted_file_does_not_raise(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{lixo", encoding="utf-8")
        self.assertIsNone(self.store.load("calendar"))  # degrada, não quebra
        self.store.save("calendar", '{"refresh_token": "r"}')  # e volta a funcionar
        self.assertEqual(self.store.load("calendar"), '{"refresh_token": "r"}')


class KeyringTokenStoreTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKeyring()
        self._saved = sys.modules.get("keyring")
        sys.modules["keyring"] = self.fake  # type: ignore[assignment]
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            sys.modules.pop("keyring", None)
        else:
            sys.modules["keyring"] = self._saved

    def test_roundtrip_uses_keyring_service(self):
        store = ga.KeyringTokenStore()
        self.assertEqual(store.name, "keyring")
        store.save("gmail", '{"refresh_token": "g"}')
        self.assertEqual(store.load("gmail"), '{"refresh_token": "g"}')
        self.assertIn((ga.KEYRING_SERVICE_NAME, "gmail"), self.fake.store)

    def test_delete_swallows_missing(self):
        store = ga.KeyringTokenStore()
        store.delete("drive")  # ausente: não levanta


class DefaultStoreSelectionTest(unittest.TestCase):
    def test_env_forces_file(self):
        with patch.dict(os.environ, {"ZORIN_GOOGLE_TOKEN_BACKEND": "file"}):
            self.assertEqual(ga.default_token_store().name, "file")

    def test_env_forces_keyring_when_available(self):
        fake = FakeKeyring()
        saved = sys.modules.get("keyring")
        sys.modules["keyring"] = fake  # type: ignore[assignment]

        def restore():
            if saved is None:
                sys.modules.pop("keyring", None)
            else:
                sys.modules["keyring"] = saved

        self.addCleanup(restore)
        with patch.object(ga, "keyring_available", return_value=True):
            with patch.dict(os.environ, {"ZORIN_GOOGLE_TOKEN_BACKEND": "keyring"}):
                self.assertEqual(ga.default_token_store().name, "keyring")

    def test_falls_back_to_file_without_keyring(self):
        with patch.object(ga, "keyring_available", return_value=False):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ZORIN_GOOGLE_TOKEN_BACKEND", None)
                self.assertEqual(ga.default_token_store().name, "file")


class IsConnectedTest(unittest.TestCase):
    """`is_connected` decide pelo token guardado, sem rede e sem bibliotecas."""

    def test_true_with_refresh_token(self):
        store = _MemoryStore({"calendar": json.dumps({"refresh_token": "r"})})
        self.assertTrue(ga.is_connected("calendar", store))

    def test_false_when_absent(self):
        self.assertFalse(ga.is_connected("calendar", _MemoryStore()))

    def test_false_for_expired_without_refresh(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        store = _MemoryStore({"drive": json.dumps({"expiry": past})})
        self.assertFalse(ga.is_connected("drive", store))

    def test_false_for_unknown_service(self):
        store = _MemoryStore({"bogus": json.dumps({"refresh_token": "r"})})
        self.assertFalse(ga.is_connected("bogus", store))

    def test_store_failure_is_not_connected(self):
        class Broken(ga.TokenStore):
            name = "broken"

            def load(self, service):
                raise RuntimeError("cofre indisponível")

        # Um cofre que explode não pode derrubar a UI: o veredito é "não conectado".
        self.assertFalse(ga.is_connected("gmail", Broken()))


class RevocationAndNetworkTest(unittest.TestCase):
    """Critério de aceite da Fase 5: revogação vira "reconexão necessária"; rede não
    apaga o token.

    Validado contra o defeito: a mutação que deixa de limpar o cofre na revogação,
    ou que limpa numa falha de rede, precisa morrer aqui.
    """

    def _store(self):
        return _MemoryStore(
            {
                "calendar": json.dumps(
                    {"refresh_token": "r", "_valid": False, "_expired": True}
                )
            }
        )

    def test_revoked_token_is_cleared_and_reports_not_connected(self):
        store = self._store()
        with patch.object(ga, "_module_available", return_value=True):
            with _fake_google(
                build_result=object(),
                refresh_error=_FakeRefreshError(
                    "invalid_grant: Token has been expired or revoked."
                ),
            ):
                with self.assertRaises(ga.GoogleNotConnected) as ctx:
                    ga.get_service("calendar", store=store)
        self.assertIn("reconectado", str(ctx.exception))
        self.assertIsNone(store.load("calendar"), "o token morto precisa sair do cofre")
        # E o estado que a UI lê passa a ser "não conectado" — é o que faz a tela
        # mostrar "Reconexão necessária" em vez de falhar em silêncio.
        self.assertFalse(ga.is_connected("calendar", store))

    def test_network_failure_keeps_the_token(self):
        store = self._store()
        with patch.object(ga, "_module_available", return_value=True):
            with _fake_google(
                build_result=object(),
                refresh_error=_FakeRefreshError("transport error: connection reset by peer"),
            ):
                with self.assertRaises(ga.GoogleNetworkError):
                    ga.get_service("calendar", store=store)
        self.assertIsNotNone(
            store.load("calendar"),
            "falha de rede NÃO pode apagar o token — custaria reautorização por um Wi-Fi ruim",
        )

    def test_network_error_is_not_the_same_as_not_connected(self):
        # Uma pede reautorização; a outra pede paciência. Confundir as duas é o
        # defeito que apaga credencial boa.
        self.assertTrue(issubclass(ga.GoogleNetworkError, ga.GoogleAuthError))
        self.assertFalse(issubclass(ga.GoogleNetworkError, ga.GoogleNotConnected))


class IsRevocationErrorTest(unittest.TestCase):
    def test_invalid_grant_is_revocation(self):
        self.assertTrue(ga.is_revocation_error(Exception("invalid_grant: revoked")))

    def test_transport_error_is_not_revocation(self):
        self.assertFalse(ga.is_revocation_error(Exception("transport error")))

    def test_match_is_case_insensitive(self):
        self.assertTrue(ga.is_revocation_error(Exception("INVALID_GRANT")))


class AuthorizationAgeTest(unittest.TestCase):
    """A idade da autorização — e a recusa a inventá-la quando não há dado."""

    def test_age_is_none_without_the_field(self):
        store = _MemoryStore({"calendar": json.dumps({"refresh_token": "r"})})
        self.assertIsNone(ga.authorization_age_days("calendar", store))

    def test_age_is_none_without_token(self):
        self.assertIsNone(ga.authorization_age_days("calendar", _MemoryStore()))

    def test_age_computed_from_the_field(self):
        stamp = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        store = _MemoryStore({"calendar": json.dumps({"authorized_at": stamp})})
        self.assertAlmostEqual(ga.authorization_age_days("calendar", store), 3.0, places=1)

    def test_naive_timestamp_is_read_as_utc(self):
        stamp = (
            (datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None).isoformat()
        )
        store = _MemoryStore({"calendar": json.dumps({"authorized_at": stamp})})
        self.assertAlmostEqual(ga.authorization_age_days("calendar", store), 2.0, places=1)

    def test_garbage_timestamp_is_none(self):
        store = _MemoryStore({"calendar": json.dumps({"authorized_at": "nao-e-data"})})
        self.assertIsNone(ga.authorization_age_days("calendar", store))


class MergeTokenTest(unittest.TestCase):
    """`credentials.to_json()` não conhece os campos nossos — preservá-los é o
    que impede o primeiro refresh de apagar a data da autorização."""

    def test_preserves_authorized_at_across_refresh(self):
        antigo = json.dumps(
            {"refresh_token": "r", "authorized_at": "2026-09-01T00:00:00+00:00"}
        )
        novo = json.dumps({"refresh_token": "r", "token": "novo"})
        merged = json.loads(ga._merge_token(antigo, novo))
        self.assertEqual(merged["authorized_at"], "2026-09-01T00:00:00+00:00")
        self.assertEqual(merged["token"], "novo")

    def test_without_metadata_is_fine(self):
        merged = json.loads(ga._merge_token(None, json.dumps({"token": "x"})))
        self.assertEqual(merged, {"token": "x"})

    def test_malformed_new_json_is_returned_untouched(self):
        self.assertEqual(ga._merge_token("{}", "nao-json"), "nao-json")

    def test_malformed_old_json_does_not_break(self):
        merged = json.loads(ga._merge_token("nao-json", json.dumps({"token": "x"})))
        self.assertEqual(merged, {"token": "x"})


class RefreshExpiryWarningTest(unittest.TestCase):
    """O aviso proativo — e as três decisões de honestidade dele."""

    def _store(self, age_days=None, connected=True):
        payload = {"refresh_token": "r", "token": "t"}
        if age_days is not None:
            payload["authorized_at"] = (
                datetime.now(timezone.utc) - timedelta(days=age_days)
            ).isoformat()
        return _MemoryStore({"calendar": json.dumps(payload)} if connected else {})

    def test_fresh_authorization_has_no_warning(self):
        self.assertIsNone(ga.refresh_expiry_warning("calendar", self._store(age_days=1)))

    def test_near_the_window_warns_info(self):
        aviso = ga.refresh_expiry_warning("calendar", self._store(age_days=6))
        self.assertIsNotNone(aviso)
        self.assertEqual(aviso["severity"], "info")
        self.assertAlmostEqual(aviso["days_left"], 1.0, places=1)

    def test_past_the_window_warns_warning(self):
        aviso = ga.refresh_expiry_warning("calendar", self._store(age_days=8))
        self.assertEqual(aviso["severity"], "warning")

    def test_unknown_age_is_reported_not_hidden(self):
        """Silêncio aqui seria a falha que não se vê: o aviso nunca dispararia
        justamente no ciclo em que ele é necessário."""
        aviso = ga.refresh_expiry_warning("calendar", self._store(age_days=None))
        self.assertIsNotNone(aviso, "idade desconhecida NÃO pode virar silêncio")
        self.assertEqual(aviso["severity"], "unknown")
        self.assertIsNone(aviso["days_left"])
        self.assertIn("Não sei", aviso["message"])

    def test_disconnected_service_has_no_warning(self):
        self.assertIsNone(
            ga.refresh_expiry_warning("calendar", self._store(age_days=8, connected=False))
        )

    def test_zero_days_disables_the_warning(self):
        self.assertIsNone(
            ga.refresh_expiry_warning("calendar", self._store(age_days=8), warn_days=0)
        )

    def test_aggregate_lists_every_connected_service(self):
        avisos = ga.refresh_expiry_warnings(self._store(age_days=8))
        self.assertEqual([item["service"] for item in avisos], ["calendar"])


class ConnectRecordsAuthorizationDateTest(unittest.TestCase):
    """O connect precisa GRAVAR a data — é a única base do aviso de expiração."""

    def test_connect_records_authorized_at(self):
        handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        handle.write(b"{}")
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        creds = types.SimpleNamespace(
            refresh_token="r",
            to_json=lambda: json.dumps({"refresh_token": "r", "token": "t"}),
        )
        store = _MemoryStore()
        with patch.object(ga, "_module_available", return_value=True):
            with patch.object(ga, "client_secret_path", return_value=Path(handle.name)):
                with _fake_google(creds=creds):
                    result = ga.run_connect_flow("calendar", store=store)

        salvo = json.loads(store.load("calendar"))
        self.assertIn(ga.AUTHORIZED_AT_KEY, salvo)
        self.assertEqual(salvo["token"], "t", "o token em si não pode ser perdido")
        self.assertIn("authorized_at", result)
        # E a data gravada tem de ser legível pelo leitor da idade — senão o
        # campo existiria sem servir para nada.
        idade = ga.authorization_age_days("calendar", store)
        self.assertIsNotNone(idade)
        self.assertLess(idade, 0.01)


class ServiceEnabledTest(unittest.TestCase):
    """A regra ÚNICA de "serviço habilitado?" — importada pela UI e pelo agente."""

    def test_master_off_disables_everything(self):
        self.assertFalse(ga.service_enabled("calendar", False, ["calendar"]))

    def test_empty_allowlist_allows(self):
        self.assertTrue(ga.service_enabled("drive", True, []))

    def test_none_allowlist_allows(self):
        self.assertTrue(ga.service_enabled("drive", True, None))

    def test_allowlist_blocks_unlisted_service(self):
        self.assertTrue(ga.service_enabled("calendar", True, ["calendar"]))
        self.assertFalse(ga.service_enabled("drive", True, ["calendar"]))

    def test_blank_entries_do_not_block_everything(self):
        # Uma lista só com espaços é "vazia": bloquear tudo em silêncio seria o
        # defeito invisível — a tela diria ligado e nada funcionaria.
        self.assertTrue(ga.service_enabled("calendar", True, ["", "  "]))


class ConnectionStatusTest(unittest.TestCase):
    def test_one_row_per_service_with_conservative_default(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = ga.FileTokenStore(Path(tmp.name) / "tokens.json")
        rows = ga.connection_status(store=store)
        self.assertEqual([r["service"] for r in rows], list(ga.known_services()))
        for row in rows:
            self.assertFalse(row["connected"])  # nada guardado ⇒ não conectado
            self.assertIn("scopes", row)
            self.assertIn("libs_available", row)


class LibsAndFlowTest(unittest.TestCase):
    def test_google_libs_available_is_bool(self):
        self.assertIsInstance(ga.google_libs_available(), bool)

    def test_connect_flow_without_libs_raises_actionable_error(self):
        # `patch` em vez de `skipIf`: com o extra instalado o teste pularia, e uma
        # regra que pula é uma regra sem guarda. Foi exatamente assim que a mutação
        # da ORDEM das checagens sobreviveu quando instalamos o `[google]`.
        with patch.object(ga, "_module_available", return_value=False):
            with self.assertRaises(ga.GoogleLibsMissing) as ctx:
                ga.run_connect_flow("calendar")
        self.assertIn("google", str(ctx.exception))

    def test_get_service_without_libs_raises_actionable_error(self):
        with patch.object(ga, "_module_available", return_value=False):
            with self.assertRaises(ga.GoogleLibsMissing):
                ga.get_service("drive")

    def test_unknown_service_reported_before_libs(self):
        """Serviço inválido é erro de USO, não de instalação.

        Se a checagem de bibliotecas viesse primeiro, o erro seria "falta o
        extra [google]" — enganoso, porque o problema é o nome do serviço. O
        teste exige que o erro NÃO seja o de bibliotecas (senão passaria nos
        dois estados, já que GoogleLibsMissing herda de GoogleAuthError).

        `_module_available` é forçado a False de propósito: com o extra instalado,
        `_require_libs()` não levanta e a ORDEM deixa de ser observável — a
        mutação que troca a ordem sobreviveria sem esta linha.
        """
        with patch.object(ga, "_module_available", return_value=False):
            with self.assertRaises(ga.GoogleAuthError) as ctx:
                ga.run_connect_flow("bogus")
            self.assertNotIsInstance(ctx.exception, ga.GoogleLibsMissing)
            self.assertIn("desconhecido", str(ctx.exception))
            with self.assertRaises(ga.GoogleAuthError) as ctx2:
                ga.get_service("bogus")
            self.assertNotIsInstance(ctx2.exception, ga.GoogleLibsMissing)

    def test_client_secret_path_default_and_override(self):
        cfg = types.SimpleNamespace(google_client_secret_path="")
        default = ga.client_secret_path(cfg)
        self.assertEqual(default.name, "google_client_secret.json")
        cfg2 = types.SimpleNamespace(google_client_secret_path="~/meu/segredo.json")
        self.assertEqual(ga.client_secret_path(cfg2), Path("~/meu/segredo.json").expanduser())


class GetServiceTest(unittest.TestCase):
    """Exercita o caminho que importa as bibliotecas, com dobras injetadas."""

    def test_not_connected_without_token(self):
        with patch.object(ga, "_module_available", return_value=True):
            with _fake_google(build_result=object()):
                with self.assertRaises(ga.GoogleNotConnected):
                    ga.get_service("calendar", store=_MemoryStore())

    def test_builds_expected_api_and_disables_discovery_cache(self):
        store = _MemoryStore({"calendar": json.dumps({"refresh_token": "r", "_valid": True})})
        sentinel = object()
        with patch.object(ga, "_module_available", return_value=True):
            with _fake_google(build_result=sentinel) as calls:
                service = ga.get_service("calendar", store=store)
        self.assertIs(service, sentinel)
        api, version, _creds, cache = calls["build"]
        self.assertEqual((api, version), ("calendar", "v3"))
        self.assertFalse(cache, "cache_discovery=False evita I/O de descoberta a cada chamada")

    def test_refreshes_expired_token_and_persists(self):
        """O caminho silencioso: token vencido precisa ser renovado **e regravado**."""
        antigo = json.dumps(
            {"refresh_token": "r", "token": "antigo", "_valid": False, "_expired": True}
        )
        store = _MemoryStore({"drive": antigo})
        with patch.object(ga, "_module_available", return_value=True):
            with _fake_google(build_result=object()):
                ga.get_service("drive", store=store)
        self.assertTrue(_FakeCredentials.last_instance.refreshed)
        # Igualdade exata com o token renovado: se a regravação sumir, o cofre
        # devolve o token "antigo" e isto falha. `assertIn("renovado")` NÃO
        # serviria — a entrada poderia já conter a palavra.
        self.assertEqual(store.load("drive"), '{"refresh_token": "r", "token": "renovado"}')

    def test_expired_without_refresh_raises(self):
        store = _MemoryStore({"gmail": json.dumps({"expiry": "2000-01-01T00:00:00Z"})})
        with patch.object(ga, "_module_available", return_value=True):
            with _fake_google(build_result=object()):
                with self.assertRaises(ga.GoogleNotConnected):
                    ga.get_service("gmail", store=store)


class ConnectFlowTest(unittest.TestCase):
    def _secret(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "client_secret.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def test_connect_saves_token_and_reports_refresh(self):
        store = _MemoryStore()
        creds = _FakeCredentials({"refresh_token": "r", "_json": '{"refresh_token": "r"}'})
        with patch.object(ga, "_module_available", return_value=True):
            with patch.object(ga, "client_secret_path", return_value=self._secret()):
                with _fake_google(creds=creds) as calls:
                    result = ga.run_connect_flow("gmail", store=store, open_browser=False)
        self.assertTrue(result["connected"])
        self.assertTrue(result["has_refresh"])
        self.assertEqual(result["backend"], "memory")
        salvo = json.loads(store.load("gmail"))
        self.assertEqual(salvo["refresh_token"], "r")
        # A data da autorização entra JUNTO do token: é a única base do aviso de
        # expiração, porque a Google não informa quando o refresh expira.
        self.assertIn(ga.AUTHORIZED_AT_KEY, salvo)
        self.assertEqual(calls["flow"]["scopes"], list(ga.scopes_for("gmail")))
        self.assertFalse(calls["flow"]["open_browser"])

    def test_connect_without_secret_file_raises(self):
        with patch.object(ga, "_module_available", return_value=True):
            with patch.object(
                ga, "client_secret_path", return_value=Path("/nao/existe/segredo.json")
            ):
                with self.assertRaises(ga.GoogleClientSecretMissing):
                    ga.run_connect_flow("calendar", store=_MemoryStore())

    def test_connect_without_refresh_reports_no_refresh(self):
        creds = _FakeCredentials({"_json": '{"token": "só-access"}'})
        with patch.object(ga, "_module_available", return_value=True):
            with patch.object(ga, "client_secret_path", return_value=self._secret()):
                with _fake_google(creds=creds):
                    result = ga.run_connect_flow("calendar", store=_MemoryStore())
        self.assertFalse(result["has_refresh"])


if __name__ == "__main__":
    unittest.main()
