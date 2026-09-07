"""Dublês compartilhados pelos testes.

Existem para tirar a suíte da dependência do ambiente: sem eles, testes como
"abrir steam" só passam na máquina de quem tem o Steam instalado.
"""

from __future__ import annotations

from unittest.mock import patch


class FakeAppInfo:
    """Stand-in de `Gio.AppInfo` com só o que `AppManager` realmente consulta."""

    def __init__(self, name: str, executable: str = "", app_id: str = ""):
        self._name = name
        self._executable = executable or name.lower()
        self._id = app_id or f"{name.lower().replace(' ', '-')}.desktop"

    def get_id(self) -> str:
        return self._id

    def get_name(self) -> str:
        return self._name

    def get_display_name(self) -> str:
        return self._name

    def get_executable(self) -> str:
        return self._executable

    def should_show(self) -> bool:
        return True


def fake_installed_apps(*names: str) -> list[FakeAppInfo]:
    return [FakeAppInfo(n) for n in names]


def installed_apps(*names: str):
    """Context manager: fixa a lista de apps instalados vista pelo AppManager.

    Continua exercitando a lógica real de casamento de nomes (apelidos, prefixo,
    substring, fuzzy) — só o inventário do sistema é controlado.
    """
    return patch(
        "zorin_copilot.core.apps.AppManager.get_all_apps",
        staticmethod(lambda: fake_installed_apps(*names)),
    )
