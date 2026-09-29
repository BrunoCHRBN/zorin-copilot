"""Validação por mutação do núcleo de autenticação Google (`core/google_auth.py`).

Motivo de existir: os testes de `tests/test_google_auth.py` cobrem **decisões**
(quando um token serve, o que cada serviço pede, como o cofre se comporta). Um
teste que afirma o comportamento errado passa nos dois estados — então este
harness reintroduz defeitos plausíveis e exige que a suíte os mate.

**Como rodar** (da raiz do repo):

    .venv/bin/python tests/mutacao/mutate_google_auth.py

Categorias: KILLED / SURVIVED / EQUIVALENTE / INVÁLIDA.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: Raiz derivada do próprio arquivo — o harness mora em `tests/mutacao/`.
RAIZ = Path(__file__).resolve().parents[2]
ALVO = RAIZ / "src" / "zorin_copilot" / "core" / "google_auth.py"
TESTES = ["tests/test_google_auth.py"]

#: (nome, de, para)
MUTACOES: list[tuple[str, str, str]] = [
    # -- regra do token --------------------------------------------------
    ("token: validade deixa de ser considerada (desconhecido vira usável)",
     '    return _expiry_in_future(token.get("expiry"), now or datetime.now(timezone.utc))',
     "    return True"),
    ("token: refresh deixa de ser exigido",
     '    if token.get("refresh_token"):\n        return True',
     "    if False:\n        return True"),
    ("token: data inválida passa a contar como válida",
     "    except ValueError:\n        return False",
     "    except ValueError:\n        return True"),
    ("parse: JSON inválido deixa de virar None",
     "    return data if isinstance(data, dict) else None",
     "    return data if isinstance(data, dict) else {}"),
    # -- is_connected ----------------------------------------------------
    ("conexão: serviço desconhecido passa a ser 'conectado'",
     "    if not is_known_service(service):\n        return False",
     "    if False:\n        return False"),
    ("conexão: falha do cofre passa a contar como conectado",
     '        logger.warning("Falha ao ler o cofre de tokens Google: %s", exc)\n        return False',
     '        logger.warning("Falha ao ler o cofre de tokens Google: %s", exc)\n        return True'),
    # -- cofre em arquivo ------------------------------------------------
    ("cofre: deixa de restringir a permissão a 0600",
     "            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)",
     "            pass"),
    # -- escopos (least-privilege) --------------------------------------
    ("escopo: Drive pede acesso total em vez de drive.file",
     '"drive": ("https://www.googleapis.com/auth/drive.file",),',
     '"drive": ("https://www.googleapis.com/auth/drive",),'),
    # Âncora alargada com a linha `if service not in SERVICE_SCOPES:`: o `raise` sozinho
    # aparece 4x em google_auth.py (scopes_for, run_connect_flow, get_service, disconnect)
    # e acertava `scopes_for` só por ele ser a PRIMEIRA definição do arquivo.
    ("escopo: serviço desconhecido deixa de levantar",
     "    if service not in SERVICE_SCOPES:\n"
     '        raise GoogleAuthError(f"Serviço Google desconhecido: {service!r}")',
     "    if service not in SERVICE_SCOPES:\n"
     "        return ()"),
    # -- ordem das checagens --------------------------------------------
    # Âncora alargada até `secret_file = ...`: o par de checagens aparece 2x
    # (run_connect_flow e get_service) e acertava o fluxo por ordem de definição.
    ("fluxo: exige bibliotecas antes de validar o serviço",
     "    if not is_known_service(service):\n"
     '        raise GoogleAuthError(f"Serviço Google desconhecido: {service!r}")\n'
     "    _require_libs()\n"
     "\n"
     "    secret_file = client_secret_path()",
     "    _require_libs()\n"
     "    if not is_known_service(service):\n"
     '        raise GoogleAuthError(f"Serviço Google desconhecido: {service!r}")\n'
     "\n"
     "    secret_file = client_secret_path()"),
    # -- renovação e persistência (caminhos com bibliotecas) -------------
    ("refresh: token vencido deixa de ser renovado",
     "    if not credentials.valid:",
     "    if False:"),
    ("refresh: token renovado deixa de ser regravado",
     "            store.save(service, _merge_token(raw, credentials.to_json()))",
     "            pass"),
    ("build: cache de descoberta ligado (I/O a cada chamada)",
     "    return build(api_name, api_version, credentials=credentials, cache_discovery=False)",
     "    return build(api_name, api_version, credentials=credentials, cache_discovery=True)"),
    ("connect: token autorizado deixa de ser gravado",
     "    store.save(service, json.dumps(payload))",
     "    pass"),
    # -- revogação × falha de rede (Fase 5) ------------------------------
    ("revogação: token morto deixa de ser removido do cofre",
     "                    store.delete(service)\n                    raise GoogleNotConnected(",
     "                    raise GoogleNotConnected("),
    ("rede: falha de transporte passa a ser tratada como revogação",
     "                if is_revocation_error(exc):",
     "                if True:"),
    # -- aviso de expiração (Fase 5) -------------------------------------
    ("data: refresh deixa de preservar a data da autorização",
     "    if isinstance(existing, dict) and AUTHORIZED_AT_KEY in existing:\n"
     "        merged[AUTHORIZED_AT_KEY] = existing[AUTHORIZED_AT_KEY]",
     "    if False:\n"
     "        merged[AUTHORIZED_AT_KEY] = existing[AUTHORIZED_AT_KEY]"),
    ("connect: deixa de gravar a data da autorização",
     "    payload[AUTHORIZED_AT_KEY] = _now_utc().isoformat()",
     "    pass"),
    ("aviso: idade desconhecida passa a ser rotulada como 'info'",
     '            "severity": "unknown",',
     '            "severity": "info",'),
    ("aviso: desligar com 0 dias deixa de funcionar",
     "    if not warn_days or warn_days <= 0:\n        return None",
     "    if False:\n        return None"),
    ("aviso: passa a avisar mesmo com autorização recente",
     "    if days_left > margin_days:\n        return None",
     "    if days_left > 100000:\n        return None"),
]

#: Sobreviventes que NÃO são lacuna: a mutação não muda comportamento observável
#: (medido, não presumido — ver a nota sobre a 3ª categoria no LEIAME).
EQUIVALENTES: dict[str, str] = {}


def rodar_testes() -> bool:
    r = subprocess.run(
        [str(RAIZ / ".venv" / "bin" / "python"), "-m", "pytest", "-q", *TESTES],
        cwd=str(RAIZ),
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def main() -> int:
    original = ALVO.read_text(encoding="utf-8")
    matadas: list[str] = []
    sobreviveram: list[str] = []
    invalidas: list[str] = []

    try:
        for nome, de, para in MUTACOES:
            if de not in original:
                invalidas.append(nome)
                print(f"INVÁLIDA   {nome}  (trecho não encontrado)")
                continue
            if original.count(de) > 1:
                invalidas.append(nome)
                print(f"INVÁLIDA   {nome}  (trecho ambíguo: {original.count(de)} ocorrências)")
                continue
            mutado = original.replace(de, para, 1)
            if mutado == original:
                invalidas.append(nome)
                print(f"INVÁLIDA   {nome}  (substituição não mudou nada)")
                continue
            ALVO.write_text(mutado, encoding="utf-8")
            try:
                if rodar_testes():
                    if nome in EQUIVALENTES:
                        print(f"EQUIVALENTE {nome}")
                    else:
                        sobreviveram.append(nome)
                        print(f"SOBREVIVEU {nome}")
                else:
                    matadas.append(nome)
                    print(f"KILLED     {nome}")
            finally:
                ALVO.write_text(original, encoding="utf-8")
    finally:
        ALVO.write_text(original, encoding="utf-8")

    print("\n" + "=" * 70)
    print(
        f"matadas: {len(matadas)}   sobreviveram: {len(sobreviveram)}   "
        f"equivalentes: {len(EQUIVALENTES)}   inválidas: {len(invalidas)}"
    )
    if sobreviveram:
        print("\nSOBREVIVENTES (lacunas de teste):")
        for nome in sobreviveram:
            print(f"  - {nome}")
    if invalidas:
        print("\nINVÁLIDAS (mutação não aplicada):")
        for nome in invalidas:
            print(f"  - {nome}")
    return 1 if (sobreviveram or invalidas) else 0


if __name__ == "__main__":
    sys.exit(main())
