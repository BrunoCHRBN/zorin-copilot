"""WS2/B1: resolução de fonte de captura de microfone processada (NS + AGC).

Objetivo: obter, quando disponível, um *source* PipeWire já filtrado
(RNNoise para redução de ruído + AGC) para alimentar o `pw-record`, em vez do
microfone cru. Tudo é *best-effort* e degrada graciosamente:

- Sem PipeWire (``pw-cli``/``pw-dump``)             -> ``None`` (usa o mic padrão)
- Módulo de filtro ausente / comando falha          -> ``None`` (usa o mic padrão)
- Criação ok                                        -> nome do source (cacheado)

Nunca levanta exceção e nunca bloqueia a inicialização da gravação. A criação do
nó de filtro depende da instalação exata do PipeWire na máquina (módulos
``rnnoise``/``compander``) — **validar em hardware real** (ver plano Entrega A, R5).
"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

_FILTER_SOURCE_DESCRIPTION = "zorin-copilot-mic-ns-agc"

# Cache de idempotência no processo: source já criado/encontrado.
_CREATED_SOURCE: str | None = None
# Resultado resolvido (source ou None), para não reconsultar o PipeWire a cada
# sessão de voz.
_UNSET = object()
_RESOLVED: object = _UNSET


def _pw_cli() -> str | None:
    return shutil.which("pw-cli")


def _pw_dump() -> str | None:
    return shutil.which("pw-dump")


def _find_existing_filtered_source() -> str | None:
    """Reusa um source filtrado já existente com nossa descrição (idempotência)."""
    dump = _pw_dump()
    if not dump:
        return None
    try:
        out = subprocess.run([dump, "Node"], capture_output=True, text=True, timeout=5)
    except Exception as exc:  # pragma: no cover - depende do binário
        logger.debug("pw-dump falhou ao listar nós: %s", exc)
        return None
    if out.returncode != 0 or _FILTER_SOURCE_DESCRIPTION not in out.stdout:
        return None
    global _CREATED_SOURCE
    _CREATED_SOURCE = _FILTER_SOURCE_DESCRIPTION
    return _CREATED_SOURCE


def _create_filtered_source(enable_ns: bool, enable_agc: bool) -> str | None:
    """Best-effort: carrega ``module-filter-chain`` (RNNoise + compander/AGC).

    Retorna o nome do source criado ou ``None`` em qualquer falha. A sintaxe exata
    do ``module-filter-chain`` e a presença dos módulos ``rnnoise``/``compander``
    variam por instalação — **validar em hardware real**. Qualquer erro -> ``None``
    (mic padrão), sem interromper a gravação.
    """
    global _CREATED_SOURCE
    cli = _pw_cli()
    if not cli:
        return None
    if _CREATED_SOURCE:
        return _CREATED_SOURCE
    if not (enable_ns or enable_agc):
        return None

    try:
        # Grão simplificado: RNNoise (NS) seguido de compander (AGC). A string
        # `filter.graph` completa (com ports/links) deve ser conferida na máquina
        # alvo; aqui só exercitamos o caminho de criação com fallback seguro.
        graph = "filter.graph=rnnoise,compander"
        args = [
            cli, "load-module", "libpipewire-module-filter-chain",
            f"node.description={_FILTER_SOURCE_DESCRIPTION}",
            graph,
        ]
        res = subprocess.run(args, capture_output=True, text=True, timeout=8)
        # IMPORTANTE: `pw-cli load-module` retorna código 0 MESMO quando falha —
        # o erro ("Could not load module" / "Error: ...") vai para o stderr. Checar
        # apenas o returncode produz falso positivo (reporta sucesso sem ter criado o
        # nó). Validamos stdout+stderr para detectar a falha de fato.
        combined = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0 or "Error" in combined or "Could not" in combined:
            logger.debug("Filtro de captura não pôde ser criado: %s", combined.strip())
            return None
        _CREATED_SOURCE = _FILTER_SOURCE_DESCRIPTION
        return _CREATED_SOURCE
    except Exception as exc:  # pragma: no cover - depende do binário
        logger.debug("Falha ao criar source filtrado (NS/AGC): %s", exc)
        return None


def get_processed_capture_source(
    enable_ns: bool = True,
    enable_agc: bool = True,
) -> str | None:
    """Devolve o nome de um source de captura filtrado (NS+AGC) ou ``None``.

    ``None`` significa "usar o microfone padrão" — chamadores devem tratar ``None``
    como 'sem ``--target``'. Idempotente e seguro: nunca levanta, resultado
    cacheado no processo.
    """
    global _RESOLVED
    if _RESOLVED is not _UNSET:
        return _RESOLVED  # type: ignore[return-value]

    result: str | None = None
    if enable_ns or enable_agc:
        if _pw_cli() or _pw_dump():
            existing = _find_existing_filtered_source()
            result = existing or _create_filtered_source(enable_ns, enable_agc)
    _RESOLVED = result
    return result


def enrich_capture_cmd(cmd: list[str], target: str | None) -> list[str]:
    """Insere o argumento de *target* de captura antes do ``-`` de saída.

    PipeWire (``pw-record``): ``--target <source>``; ALSA (``arecord``):
    ``-D <device>``. Sem ``target`` (``None``) devolve o comando inalterado.
    """
    if not target:
        return list(cmd)
    out = list(cmd)
    is_pw = bool(out) and out[0].endswith("pw-record")
    is_alsa = bool(out) and out[0].endswith("arecord")
    if out and out[-1] == "-":
        if is_pw:
            out[-1:] = ["--target", target, "-"]
        elif is_alsa:
            out[-1:] = ["-D", target, "-"]
    return out


def reset_cache() -> None:
    """Limpa o cache de resolução (uso de testes)."""
    global _CREATED_SOURCE, _RESOLVED
    _CREATED_SOURCE = None
    _RESOLVED = _UNSET
