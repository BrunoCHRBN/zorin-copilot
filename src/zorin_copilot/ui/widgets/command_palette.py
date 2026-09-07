# Decisão de design: painel de comandos em overlay dentro da própria janela, não em
# um diálogo separado. Um Adw.Dialog roubaria o foco e perderia o contexto visual do
# glassmorphism; o overlay mantém a janela viva atrás do scrim, como Raycast/Spotlight.

"""Painel de comandos (Ctrl+K) com busca difusa sobre as ações da janela."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - apenas para type checking
    from ..app import CopilotWindow

MAX_RESULTS = 8


@dataclass(frozen=True)
class PaletteCommand:
    """Uma entrada do painel de comandos."""

    name: str
    title: str
    subtitle: str = ""
    icon: str = "system-run-symbolic"
    accelerator: str = ""
    keywords: tuple[str, ...] = ()

    def haystack(self) -> str:
        """Texto usado na busca: título, subtítulo, id e palavras-chave."""
        return " ".join((self.title, self.subtitle, self.name, *self.keywords)).lower()


def score_command(query: str, command: PaletteCommand) -> int | None:
    """Pontua a correspondência de um comando com a busca. ``None`` = não casa.

    A ordem importa: começo do título vale mais que substring, que vale mais que
    correspondência esparsa (subsequência). É o que faz "nconv" achar "Nova conversa".
    """
    if not query:
        return 0

    # Normaliza aqui, não em quem chama: assim a função é confiável sozinha.
    query = query.strip().lower()
    if not query:
        return 0

    title = command.title.lower()
    haystack = command.haystack()

    if title.startswith(query):
        return 100
    if query in title:
        return 80
    if query in haystack:
        return 60
    if _is_subsequence(query, haystack):
        # Quanto mais curto o texto em relação à busca, melhor a correspondência.
        return max(10, 40 - (len(haystack) - len(query)))
    return None


def _is_subsequence(needle: str, haystack: str) -> bool:
    """True se ``needle`` aparece em ordem (não contígua) dentro de ``haystack``."""
    it = iter(haystack)
    return all(char in it for char in needle)


class CommandPalette(Gtk.Overlay):
    """Overlay de busca sobre os comandos disponíveis na janela."""

    def __init__(self, ctx: "CopilotWindow"):
        super().__init__()
        self.ctx = ctx
        self._commands: list[PaletteCommand] = []
        self._rows: dict[Adw.ActionRow, PaletteCommand] = {}
        self._visible_rows: list[Adw.ActionRow] = []
        self._open = False

        self._build()
        self.set_visible(False)

    # -- construção ------------------------------------------------------
    def _build(self) -> None:
        # Scrim: fecha o painel ao clicar fora.
        self.scrim = Gtk.Box()
        self.scrim.add_css_class("palette-scrim")
        self.scrim.set_hexpand(True)
        self.scrim.set_vexpand(True)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.close())
        self.scrim.add_controller(click)
        self.set_child(self.scrim)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("command-palette")
        card.set_halign(Gtk.Align.CENTER)
        card.set_valign(Gtk.Align.START)
        card.set_margin_top(56)
        card.set_size_request(520, -1)

        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text("Buscar comandos…")
        # "search-changed" tem emissão atrasada (chega com o valor anterior);
        # "notify::text" dispara na hora, com o valor final.
        self.entry.connect("notify::text", lambda _e, _pspec: self._refilter())
        self.entry.connect("activate", lambda _e: self.activate_selected())
        card.append(self.entry)

        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", self._on_row_activated)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(340)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(self.listbox)
        card.append(scroll)

        self.add_overlay(card)
        self.card = card

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.entry.add_controller(key_ctrl)

    # -- ciclo de vida ---------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._open

    def open(self, query: str = "") -> None:
        """Abre o painel recarregando os comandos (a lista pode ter mudado)."""
        self._open = True
        self.set_visible(True)
        self.set_commands(self.ctx.palette_commands())
        self.entry.set_text(query)
        self._refilter()
        # O foco só pega depois do widget estar mapeado.
        GLib.idle_add(self._focus_entry)

    def close(self) -> None:
        self._open = False
        self.set_visible(False)
        self.entry.set_text("")
        self.ctx.palette_closed()

    def _focus_entry(self) -> bool:
        if self._open:
            self.entry.grab_focus()
        return GLib.SOURCE_REMOVE

    # -- comandos --------------------------------------------------------
    def set_commands(self, commands: list[PaletteCommand]) -> None:
        self._commands = list(commands)

    # -- filtragem -------------------------------------------------------
    def _refilter(self) -> None:
        for row in list(self._rows):
            self.listbox.remove(row)
        self._rows.clear()
        self._visible_rows.clear()

        query = self.entry.get_text().strip().lower()

        scored: list[tuple[int, PaletteCommand]] = []
        for command in self._commands:
            score = score_command(query, command)
            if score is not None:
                scored.append((score, command))
        scored.sort(key=lambda item: (-item[0], item[1].title.lower()))

        for _score, command in scored[:MAX_RESULTS]:
            row = self._make_row(command)
            self.listbox.append(row)
            self._rows[row] = command
            self._visible_rows.append(row)

        if not self._visible_rows:
            empty = Adw.ActionRow(title="Nenhum comando encontrado")
            empty.set_selectable(False)
            self.listbox.append(empty)
            self._visible_rows.append(empty)
            return

        self.listbox.select_row(self._visible_rows[0])

    def _make_row(self, command: PaletteCommand) -> Adw.ActionRow:
        row = Adw.ActionRow(title=command.title, subtitle=command.subtitle)
        row.set_subtitle_lines(1)

        icon = Gtk.Image.new_from_icon_name(command.icon)
        icon.set_pixel_size(18)
        row.add_prefix(icon)

        if command.accelerator:
            accel = Gtk.Label(label=_pretty_accelerator(command.accelerator))
            accel.add_css_class("caption")
            accel.add_css_class("dim-label")
            row.add_suffix(accel)

        return row

    # -- navegação -------------------------------------------------------
    def move_selection(self, delta: int) -> None:
        """Move a seleção, com wrap-around."""
        if not self._visible_rows:
            return
        current = self.listbox.get_selected_row()
        if current not in self._visible_rows:
            index = 0
        else:
            index = (self._visible_rows.index(current) + delta) % len(self._visible_rows)
        row = self._visible_rows[index]
        if row in self._rows:  # não seleciona a linha de "nenhum resultado"
            self.listbox.select_row(row)

    def activate_selected(self) -> None:
        row = self.listbox.get_selected_row()
        command = self._rows.get(row) if row else None
        if command is not None:
            self._run(command)

    def _on_row_activated(self, _listbox, row) -> None:
        command = self._rows.get(row)
        if command is not None:
            self._run(command)

    def _on_key_pressed(self, _ctrl, keyval, _keycode, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self.move_selection(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self.move_selection(-1)
            return True
        return False

    def _run(self, command: PaletteCommand) -> None:
        # Fecha antes de executar: o comando pode abrir diálogo ou mexer no foco.
        self._open = False
        self.set_visible(False)
        self.entry.set_text("")
        self.ctx.run_palette_command(command)
        self.ctx.palette_closed()


def _pretty_accelerator(accelerator: str) -> str:
    """``<Control><Shift>k`` -> ``Ctrl+Shift+K``."""
    parts = [p for p in accelerator.replace(">", "").split("<") if p]
    if not parts:
        return accelerator
    *mods, key = parts
    labels = {"Control": "Ctrl", "Shift": "Shift", "Alt": "Alt", "Super": "Super"}
    out = [labels.get(m, m) for m in mods]
    out.append(key.upper() if len(key) == 1 else key)
    return "+".join(out)
