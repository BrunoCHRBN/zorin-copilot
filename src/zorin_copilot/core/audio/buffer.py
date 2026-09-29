"""Entrega B / P0-4 (WS2): buffer circular de PCM para a captura de voz na nuvem.

Motivo
------
No modo nuvem (Gemini Live), a captura de microfone morria junto com o WebSocket a
cada queda, e o áudio do intervalo de backoff (1.5–30 s) era perdido — a "lacuna na
transcrição" descrita em `docs/PROPOSTA_COMPORTAMENTO_AO_VIVO.md` §2.4.

Este buffer desacopla captura de envio: uma *bomba* de captura persistente empurra
chunks aqui (`push`, de uma thread do executor) e o laço de envio (por tentativa)
drena (`pop`/`drain`, do loop asyncio). Assim, o áudio capturado durante a queda
fica retido e é reenviado ao reconectar.

Teto por **segundos**, não por bytes
------------------------------------
A máquina não tem swap: buffer sem limite é vetor de OOM. O teto é expresso em
segundos (`live_reconnect_buffer_sec`) e, ao estourar, descarta-se o **mais antigo**
— o contexto mais recente é o que importa para o STT. `seconds <= 0` desliga o
buffering (nada é retido).
"""

from __future__ import annotations

import collections
import threading


class PcmRingBuffer:
    """Buffer circular thread-safe de PCM s16 little-endian mono.

    Produtor: a bomba de captura (thread do executor) → `push`.
    Consumidor: o laço de envio (corrotina asyncio) → `pop`/`drain`.
    """

    def __init__(self, seconds: float, rate: int = 16000, width: int = 2) -> None:
        self.rate = int(rate)
        self.width = int(width)
        self._cap_bytes = max(0, int(max(0.0, float(seconds)) * self.rate * self.width))
        self._chunks: collections.deque[bytes] = collections.deque()
        self._total = 0
        self._lock = threading.Lock()

    def push(self, chunk: bytes) -> None:
        """Anexa um chunk; descarta os mais antigos se passar do teto.

        Com teto zero (buffering desligado) o chunk é simplesmente descartado.
        """
        if not chunk or self._cap_bytes <= 0:
            return
        with self._lock:
            self._chunks.append(chunk)
            self._total += len(chunk)
            while self._total > self._cap_bytes and len(self._chunks) > 1:
                self._total -= len(self._chunks.popleft())

    def pop(self) -> bytes | None:
        """Remove e devolve o chunk mais antigo, ou ``None`` se vazio."""
        with self._lock:
            if not self._chunks:
                return None
            chunk = self._chunks.popleft()
            self._total -= len(chunk)
            return chunk

    def drain(self) -> bytes:
        """Remove e devolve tudo como um único bloco (flush), esvaziando o buffer."""
        with self._lock:
            data = b"".join(self._chunks)
            self._chunks.clear()
            self._total = 0
            return data

    def clear(self) -> None:
        """Esvazia o buffer."""
        with self._lock:
            self._chunks.clear()
            self._total = 0

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total

    @property
    def cap_bytes(self) -> int:
        return self._cap_bytes

    def len_seconds(self) -> float:
        """Duração do áudio retido, em segundos."""
        with self._lock:
            return self._total / float(self.rate * self.width)
