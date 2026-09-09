# Decisão de design: o GTK4 não implementa `backdrop-filter`, então janelas GTK não
# conseguem desfocar o que está atrás delas por conta própria. O único caminho para
# "vidro real" é delegar ao compositor — e, entre os suportados, só o Hyprland (wlroots)
# oferece isso por app_id/namespace via `hyprctl` (`windowrule blur` para janelas XDG e
# `layerrule blur` para superfícies layer-shell).
#
# Este módulo é o único ponto que fala com o `hyprctl`. Ele é deliberadamente
# autocontido (não depende do projeto-irmão hypr-look) e segue o mesmo padrão de
# subprocess usado em `core/desktop/shortcuts.py` (`HyprlandShortcutBackend.apply_runtime`).
#
# Regras de segurança/UX:
#  - Só age quando o Hyprland está de fato rodando (env var ou binário no PATH).
#  - É idempotente: reaplicar a mesma regra não dispara `hyprctl` de novo.
#  - Nunca é chamado por frame — o blur é estático por sessão; a reatividade ao áudio
#    (R3) é feita localmente em cairo, não aqui.
#  - Pode ser desligado por completo com ZORIN_COPILOT_DISABLE_COMPOSITOR_BLUR=1.

"""Aplicação de blur/rounding reais no Hyprland via `hyprctl` (wlroots layer/window rules)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from ..core.desktop.env import current_environment

logger = logging.getLogger(__name__)

#: Variável de ambiente que desliga toda a integração de blur do compositor.
_DISABLE_ENV = "ZORIN_COPILOT_DISABLE_COMPOSITOR_BLUR"


class HyprlandEffects:
    """Orquestra regras de blur/rounding do Hyprland para as janelas do Copilot.

    As regras são do app **próprio** (por ``class:`` da janela principal ou pelo
    ``namespace`` da pílula layer-shell), nunca globais, e podem ser removidas.
    """

    def __init__(self) -> None:
        self._applied: set[str] = set()
        self._available: bool | None = None

    # ------------------------------------------------------------------
    # Disponibilidade
    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """Hyprland está rodando E o `hyprctl` está acessível? (cacheado)."""
        if self._available is None:
            self._available = self._detect()
        return self._available

    def _detect(self) -> bool:
        if os.environ.get(_DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
            return False
        if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            # Sem a assinatura não há sessão Hyprland viva para aplicar regras.
            return False
        return shutil.which("hyprctl") is not None

    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------
    def _run(self, *args: str) -> bool:
        """Executa `hyprctl <args>`. Retorna True se correu sem erro de execução.

        Não propaga exceções: uma falha de IPC do compositor não deve derrubar a UI.
        """
        if not self.is_available():
            return False
        try:
            proc = subprocess.run(
                ["hyprctl", *args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("hyprctl %s falhou: %s", args, exc)
            return False
        if proc.returncode != 0:
            logger.debug("hyprctl %s retornou %s: %s", args, proc.returncode, proc.stderr.strip())
            return False
        return True

    def _ensure(self, key: str, *args: str) -> None:
        """Aplica uma regra se ainda não aplicada (idempotente)."""
        if key in self._applied:
            return
        if self._run(*args):
            self._applied.add(key)

    def _remove(self, key: str, *args: str) -> None:
        """Remove uma regra previamente aplicada (se aplicada)."""
        if key not in self._applied:
            return
        if self._run(*args):
            self._applied.discard(key)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def ensure_window_blur(self, class_: str = "io.github.bruno.ZorinCopilot") -> None:
        """Desfoca a janela principal (janela XDG comum) pelo app_id.

        O Hyprland casa `windowrule` por **título** sem prefixo; como o título é
        "Zorin Copilot" e não o app_id, é preciso o prefixo `class:` para casar
        pelo app_id correto. Sem ele a regra não casa e o compositor registra erro.
        """
        self._ensure(f"win-blur:{class_}", "keyword", "windowrule", f"blur,class:{class_}")
        self._ensure(f"win-round:{class_}", "keyword", "windowrule", f"rounding,class:{class_}")

    def remove_window_blur(self, class_: str = "io.github.bruno.ZorinCopilot") -> None:
        """Revoga o blur/rounding da janela principal."""
        self._remove(f"win-blur:{class_}", "keyword", "windowrule", f"noblur,class:{class_}")
        self._remove(f"win-round:{class_}", "keyword", "windowrule", f"norounding,class:{class_}")

    def ensure_layer_blur(self, namespace: str) -> None:
        """Desfoca a pílula de voz (superfície layer-shell) pelo namespace."""
        self._ensure(f"layer-blur:{namespace}", "keyword", "layerrule", f"blur,{namespace}")
        self._ensure(f"layer-round:{namespace}", "keyword", "layerrule", f"rounding,{namespace}")

    def remove_layer_blur(self, namespace: str) -> None:
        """Revoga o blur/rounding da pílula."""
        self._remove(f"layer-blur:{namespace}", "keyword", "layerrule", f"noblur,{namespace}")
        self._remove(f"layer-round:{namespace}", "keyword", "layerrule", f"norounding,{namespace}")

    def apply_for_app(self, class_: str = "io.github.bruno.ZorinCopilot") -> None:
        """Aplica blur+rounding para a janela principal e a pílula (chamado no realize)."""
        self.ensure_window_blur(class_)
        self.ensure_layer_blur(current_environment().blur_namespace())

    def remove_all(self, class_: str = "io.github.bruno.ZorinCopilot") -> None:
        """Remove todas as regras deste app (chamado no encerramento)."""
        self.remove_window_blur(class_)
        self.remove_layer_blur(current_environment().blur_namespace())

    def reset_cache(self) -> None:
        """Força reavaliação de disponibilidade/disponibilidade na próxima chamada."""
        self._applied.clear()
        self._available = None


#: Instância singleton usada por toda a UI.
hyprland_effects = HyprlandEffects()
