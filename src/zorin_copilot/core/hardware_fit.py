"""Adequação de modelos locais ao hardware — a linha que responde "roda na minha GPU?".

Por que este módulo existe
--------------------------
O seletor de modelos mostrava `8.0B · Q4_K_M · 4.6 GB` e parava aí. São números
corretos e inúteis: quem vai baixar um modelo quer saber se ele **roda**, e o
tamanho em disco não responde isso sozinho. A pergunta real é "cabe na minha
VRAM?", e ela depende do hardware — que o app não perguntava a lugar nenhum.

Aqui ficam a sondagem e a heurística, separadas da UI de propósito: são funções
puras (ou quase) e é por isso que dá para testá-las sem GPU e sem modelo.

Honestidade sobre o que não se sabe
-----------------------------------
Duas decisões deliberadas:

* **Nunca inventar um número.** Se a VRAM não puder ser medida (GPU Intel
  integrada, driver exótico, `nvidia-smi` ausente), o veredito é `unknown` e a
  linha diz apenas o que o modelo exige — sem afirmar que cabe ou não.
* **O veredito é estimativa, não promessa.** A carga real depende de contexto,
  camadas offloaded, outras aplicações usando a GPU. Por isso o texto fala em
  "requer ~X GB" e "cabe", não em garantias.

Como a VRAM é medida, em ordem
------------------------------
1. `nvidia-smi --query-gpu=memory.total,name` (NVIDIA proprietária);
2. `/sys/class/drm/card*/device/mem_info_vram_total` (amdgpu no kernel — é o
   caminho que funciona em AMD, inclusive nas APUs recentes);
3. nada. O nome da GPU vem de `lspci` e é opcional: sem ele a frase continua
   verdadeira, só menos específica.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Um modelo quantizado carrega o arquivo **mais** a cache de contexto (KV) e o
# overhead do runtime. Medido na prática, ~20% sobre o tamanho em disco é uma
# aproximação conservadora para contextos usuais (4k–8k tokens). O fator existe
# como constante nomeada para poder ser ajustado com dados, e não como número
# mágico espalhado.
VRAM_OVERHEAD_FACTOR = 1.2

# Abaixo disto a GPU "cabe" mas fica sem folga: outras aplicações (o
# compositor, o navegador) disputam a mesma VRAM e o modelo passa a fazer
# offload dinâmico, o que derruba a velocidade de forma perceptível.
TIGHT_HEADROOM = 0.15

# Fração da RAM total que um modelo pode ocupar sem levar a máquina a thrashing.
# Sem swap (o caso comum nesta configuração) ultrapassar isso trava o sistema.
MAX_RAM_SHARE = 0.7

_DRM_VRAM_GLOB = "/sys/class/drm/card{vram_index}/device/mem_info_vram_total"

_gpu_cache: "GpuInfo | None" = None
_gpu_cache_done = False


@dataclass(frozen=True)
class GpuInfo:
    """GPU dedicada detectada. `vram_bytes` é None quando não foi possível medir."""

    name: str = ""
    vram_bytes: int | None = None

    def label(self) -> str:
        """Como a GPU aparece na frase. Vazio quando não há nome."""
        return self.name.strip()


@dataclass(frozen=True)
class FitVerdict:
    """Resultado da comparação entre o que o modelo exige e o que a máquina tem.

    `level` é a classificação estável (testável); `line` é o texto para o
    usuário. Os dois existem porque comparar strings de UI em teste é frágil.
    """

    level: str  # gpu | tight | cpu | insufficient | unknown
    required_bytes: int
    line: str


def format_gb(num_bytes: float) -> str:
    """GB com uma casa e vírgula decimal, como a barra de status do app."""
    return f"{num_bytes / (1024 ** 3):.1f}".replace(".", ",")


def estimate_required_bytes(model_size_bytes: int) -> int:
    """Memória necessária para carregar um modelo do tamanho dado, em bytes.

    Devolve 0 para entradas inválidas — o chamador trata 0 como "não sei", que é
    diferente de "não precisa de nada".
    """
    if not model_size_bytes or model_size_bytes <= 0:
        return 0
    return int(model_size_bytes * VRAM_OVERHEAD_FACTOR)


def detect_ram_bytes() -> int | None:
    """RAM total da máquina, em bytes. None se não puder ser lida.

    Lê `/proc/meminfo` direto em vez de usar `psutil` para manter este módulo sem
    dependência obrigatória — ele é chamado no caminho de abertura do seletor.
    """
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError) as exc:
        logger.debug("Não foi possível ler MemTotal: %s", exc)
    return None


def _read_sysfs_vram() -> int | None:
    """Maior VRAM dedicada encontrada em `/sys/class/drm` (caminho do amdgpu)."""
    best: int | None = None
    try:
        entries = sorted(os.listdir("/sys/class/drm"))
    except OSError:
        return None

    for entry in entries:
        match = re.fullmatch(r"card(\d+)", entry)
        if not match:
            continue
        path = _DRM_VRAM_GLOB.format(vram_index=match.group(1))
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = int(handle.read().strip())
        except (OSError, ValueError):
            continue
        if value > 0 and (best is None or value > best):
            best = value
    return best


def _read_nvidia_vram() -> tuple[int | None, str]:
    """(VRAM, nome) via `nvidia-smi`. (None, "") quando não há NVIDIA."""
    if not shutil.which("nvidia-smi"):
        return None, ""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,name",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            timeout=3.0,
            capture_output=True,
            text=True,
            # Mesma regra do resto do projeto: processo que pode sobreviver ao
            # chamador não herda os descritores de saída.
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("nvidia-smi falhou: %s", exc)
        return None, ""

    first = (proc.stdout or "").strip().splitlines()
    if not first:
        return None, ""
    parts = [piece.strip() for piece in first[0].split(",")]
    if len(parts) < 2:
        return None, ""
    try:
        # nvidia-smi reporta MiB.
        return int(parts[0]) * 1024 * 1024, parts[1]
    except ValueError:
        return None, ""


def _short_gpu_name(description: str) -> str:
    """Reduz a linha do `lspci` ao nome comercial da GPU.

    `lspci` devolve a descrição inteira do dispositivo: fornecedor, codinome e
    **todas** as variantes de SKU que compartilham o chip —

        Advanced Micro Devices, Inc. [AMD/ATI] Navi 33 [Radeon RX 7600/7600 XT/
        7600M XT/7600S/7700S / PRO W7600] (rev cf)

    Nada disso caberia numa linha de subtítulo, e a lista de SKUs não distingue
    nada para quem só quer saber se o modelo roda. O alvo é "Radeon RX 7600".
    """
    text = description.strip()
    if not text:
        return ""

    # O código de revisão do silício ("(rev cf)") não interessa a ninguém e
    # varia dentro do mesmo modelo. Removido aqui, e não em quem chama, para a
    # função ser confiável sozinha.
    text = re.sub(r"\s*\(rev [0-9a-f]+\)\s*$", "", text)

    marketing = None
    for candidate in re.findall(r"\[([^\]]+)\]", text):
        if re.search(r"Radeon|GeForce|Arc\b|Iris|UHD|Vega|RTX|GTX|\bRX\s", candidate):
            marketing = candidate
            break

    if marketing is not None:
        text = marketing
    else:
        # Sem nome comercial: ao menos tira o fornecedor da frente.
        text = re.sub(
            r"^(?:Advanced Micro Devices,?\s*Inc\.?|NVIDIA\s+Corporation|Intel\s+Corporation)\s*",
            "",
            text,
        )
        text = re.sub(r"^\[[^\]]*\]\s*", "", text)

    # A lista de variantes separada por "/" não ajuda ninguém: fica a primeira.
    return text.split("/")[0].strip(" ,")


def _read_lspci_gpu_name() -> str:
    """Nome comercial da GPU via `lspci`. "" se indisponível."""
    if not shutil.which("lspci"):
        return ""
    try:
        proc = subprocess.run(
            ["lspci"],
            check=False,
            timeout=3.0,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    for line in (proc.stdout or "").splitlines():
        if not re.search(r"VGA compatible controller|3D controller|Display controller", line):
            continue
        _, _, description = line.partition(":")
        description = description.partition(":")[2] or description
        return _short_gpu_name(description)
    return ""


def detect_gpu(*, allow_subprocess: bool | None = None) -> GpuInfo:
    """GPU dedicada da máquina, com cache. Nunca levanta.

    `allow_subprocess=False` evita `nvidia-smi`/`lspci`. O padrão desliga isso em
    modo de teste, seguindo a regra do projeto de não subir processos durante a
    suíte — a VRAM por sysfs continua sendo lida, que é só leitura de arquivo.
    """
    global _gpu_cache, _gpu_cache_done
    if _gpu_cache_done and _gpu_cache is not None:
        return _gpu_cache

    if allow_subprocess is None:
        allow_subprocess = not os.environ.get("ZORIN_TEST_MODE")

    vram: int | None = None
    name = ""

    try:
        if allow_subprocess:
            vram, name = _read_nvidia_vram()

        if vram is None:
            vram = _read_sysfs_vram()

        if not name and allow_subprocess:
            name = _read_lspci_gpu_name()
    except Exception as exc:  # noqa: BLE001
        # Esta sondagem roda no caminho de abertura do seletor de modelos: uma
        # exceção inesperada aqui deixaria a lista de modelos sem aparecer por
        # causa de um detalhe de hardware. As funções de leitura já tratam os
        # casos previsíveis; isto é a rede para os imprevisíveis, e é o que
        # torna verdadeira a promessa de "nunca levanta" do módulo. O que já
        # foi medido até aqui é mantido.
        logger.debug("Sondagem de hardware falhou: %s", exc)

    info = GpuInfo(name=name, vram_bytes=vram)
    _gpu_cache = info
    _gpu_cache_done = True
    return info


def reset_cache() -> None:
    """Descarta o cache da sondagem. Existe para os testes, não para produção."""
    global _gpu_cache, _gpu_cache_done
    _gpu_cache = None
    _gpu_cache_done = False


# Sentinela para "não informado, sonde a máquina". Existe porque `None` já tem
# significado próprio nos parâmetros de `describe_fit`: `gpu=None` quer dizer
# "não há GPU dedicada" e `ram_bytes=None`, "não foi possível medir a RAM".
# Sobrecarregar `None` com os dois sentidos tornaria o caminho "sem dados"
# impossível de testar — e é justamente o caminho em que a frase precisa ser
# mais cuidadosa.
_AUTO = object()


def describe_fit(
    model_size_bytes: int,
    *,
    gpu: GpuInfo | None | object = _AUTO,
    ram_bytes: int | None | object = _AUTO,
) -> FitVerdict:
    """Compara o que o modelo exige com o que a máquina oferece.

    Os parâmetros são injetáveis para que o teste não dependa do hardware de
    quem roda a suíte. Omiti-los sonda a máquina; passar `None` afirma que a
    informação **não existe** (`gpu=None` = sem GPU dedicada, `ram_bytes=None` =
    RAM não medida).

    A ordem de decisão é a ordem em que o problema aparece na prática: primeiro
    "cabe na GPU?", depois "cabe na RAM?", e só então "não cabe em lugar nenhum".

    `line` é **curta de propósito** — é a segunda linha do subtítulo, e vai ao
    lado da linha de especificações. O número do requisito mora em
    `required_bytes`, para quem chama juntá-lo aos outros números em vez de
    repeti-lo em prosa.
    """
    required = estimate_required_bytes(model_size_bytes)

    if required == 0:
        return FitVerdict(
            level="unknown",
            required_bytes=0,
            line="Tamanho desconhecido — não dá para estimar a memória necessária.",
        )

    if gpu is _AUTO:
        gpu = detect_gpu()
    if ram_bytes is _AUTO:
        ram_bytes = detect_ram_bytes()

    vram = gpu.vram_bytes if gpu is not None else None
    gpu_label = gpu.label() if gpu is not None else ""
    gpu_ref = f" ({gpu_label})" if gpu_label else ""

    if vram:
        vram_gb = format_gb(vram)
        if required <= vram * (1.0 - TIGHT_HEADROOM):
            return FitVerdict(
                level="gpu",
                required_bytes=required,
                line=f"Cabe nos {vram_gb} GB de VRAM{gpu_ref}.",
            )
        if required <= vram:
            return FitVerdict(
                level="tight",
                required_bytes=required,
                line=(
                    f"Cabe justo nos {vram_gb} GB de VRAM{gpu_ref} — "
                    "sobra pouca folga para o resto."
                ),
            )
        # Não cabe na GPU: ainda pode rodar em CPU, se a RAM permitir.
        if ram_bytes and required <= ram_bytes * MAX_RAM_SHARE:
            return FitVerdict(
                level="cpu",
                required_bytes=required,
                line=(
                    f"Não cabe nos {vram_gb} GB de VRAM{gpu_ref}; "
                    "roda em CPU, bem mais lento."
                ),
            )
        return FitVerdict(
            level="insufficient",
            required_bytes=required,
            line=(
                f"Acima da VRAM ({vram_gb} GB) e da RAM livre — "
                "pode travar o sistema."
            ),
        )

    # Sem VRAM medida: só é honesto falar de RAM.
    if ram_bytes:
        ram_gb = format_gb(ram_bytes)
        if required <= ram_bytes * MAX_RAM_SHARE:
            return FitVerdict(
                level="cpu",
                required_bytes=required,
                line=(
                    f"Sem GPU dedicada detectada; roda em CPU "
                    f"(você tem {ram_gb} GB de RAM)."
                ),
            )
        return FitVerdict(
            level="insufficient",
            required_bytes=required,
            line=(
                f"Pode não caber nos {ram_gb} GB de RAM, sem GPU dedicada. "
                "Risco de travar o sistema."
            ),
        )

    return FitVerdict(
        level="unknown",
        required_bytes=required,
        line="Não foi possível medir a memória desta máquina.",
    )
