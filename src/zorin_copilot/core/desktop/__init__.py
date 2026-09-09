"""Camada de abstração de desktop.

Agrupa tudo que varia entre distribuições e ambientes gráficos: atalhos globais,
captura de tela, controles de sistema e bandeja. O resto do Copilot conversa com
estes módulos e deixa de saber se está rodando em Zorin OS, EndeavourOS, GNOME,
Plasma ou Hyprland.

Importar este pacote nunca levanta exceção por biblioteca faltante — os módulos
fazem import tardio de ``gi`` e degradam com mensagem quando algo não existe.
"""

from .env import (
    DistroInfo,
    Environment,
    current_environment,
    detect_environment,
    parse_os_release,
)

__all__ = [
    "DistroInfo",
    "Environment",
    "current_environment",
    "detect_environment",
    "parse_os_release",
]
