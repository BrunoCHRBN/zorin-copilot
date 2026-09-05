# Decisão de design: controle de mídia desacoplado via especificação MPRIS2 D-Bus — suporta Spotify, VLC, reprodutores locais e navegadores sem dependências externas.

"""Controlador de reprodutores de mídia via protocolo MPRIS2 (D-Bus)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TrackInfo:
    title: str = ""
    artist: str = ""
    album: str = ""
    playback_status: str = "Stopped"
    player_name: str = ""

    def summary(self) -> str:
        if not self.title and self.playback_status == "Stopped":
            return "Nenhuma mídia em reprodução no momento."
        artist_str = f" por {self.artist}" if self.artist else ""
        album_str = f" ({self.album})" if self.album else ""
        status_pt = {
            "Playing": "▶ Tocando",
            "Paused": "⏸ Pausado",
            "Stopped": "⏹ Parado",
        }.get(self.playback_status, self.playback_status)
        player_str = f" no {self.player_name.replace('org.mpris.MediaPlayer2.', '').capitalize()}" if self.player_name else ""
        return f"{status_pt}{player_str}: {self.title or 'Faixa sem título'}{artist_str}{album_str}"


class MediaPlayerManager:
    """Gerencia e controla reprodutores de mídia MPRIS2 (Spotify, VLC, Chromium, Firefox, etc.)."""

    @classmethod
    def list_players(cls) -> list[str]:
        """Lista nomes de barramento dos reprodutores MPRIS2 ativos na sessão."""
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = conn.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListNames",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
            names = reply.unpack()[0]
            return [n for n in names if n.startswith("org.mpris.MediaPlayer2.")]
        except Exception as exc:
            logger.debug(f"Falha ao listar reprodutores via Gio: {exc}")

        # Fallback via dbus-send
        try:
            res = subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--dest=org.freedesktop.DBus",
                    "--type=method_call",
                    "--print-reply",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus.ListNames",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            players = []
            for line in res.stdout.splitlines():
                if "string \"org.mpris.MediaPlayer2." in line:
                    p = line.split("\"")[1]
                    players.append(p)
            return players
        except Exception:
            return []

    @classmethod
    def resolve_player(cls, preferred: str | None = None) -> str | None:
        """Resolve o barramento do reprodutor mais adequado ou o preferido."""
        players = cls.list_players()
        if not players:
            return None

        if preferred:
            pref_clean = preferred.lower().replace("org.mpris.mediaplayer2.", "")
            for p in players:
                if pref_clean in p.lower():
                    return p

        # Prioriza o reprodutor que está atualmente tocando
        for p in players:
            info = cls.get_track_info(p)
            if info.playback_status == "Playing":
                return p

        return players[0]

    @classmethod
    def get_track_info(cls, player_bus_name: str | None = None) -> TrackInfo:
        """Obtém metadados da faixa atual e estado de reprodução."""
        bus_name = player_bus_name or cls.resolve_player()
        if not bus_name:
            return TrackInfo()

        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = conn.call_sync(
                bus_name,
                "/org/mpris/MediaPlayer2",
                "org.freedesktop.DBus.Properties",
                "GetAll",
                GLib.Variant("(s)", ("org.mpris.MediaPlayer2.Player",)),
                GLib.VariantType("(a{sv})"),
                Gio.DBusCallFlags.NONE,
                1500,
                None,
            )
            props = reply.unpack()[0]
            status = str(props.get("PlaybackStatus", "Stopped"))
            meta = props.get("Metadata", {})

            title = str(meta.get("xesam:title", ""))
            raw_artist = meta.get("xesam:artist", "")
            if isinstance(raw_artist, (list, tuple)):
                artist = ", ".join(str(a) for a in raw_artist)
            else:
                artist = str(raw_artist)

            album = str(meta.get("xesam:album", ""))

            return TrackInfo(
                title=title,
                artist=artist,
                album=album,
                playback_status=status,
                player_name=bus_name,
            )
        except Exception as exc:
            logger.debug(f"Erro ao obter metadados de {bus_name}: {exc}")

        return TrackInfo(player_name=bus_name or "")

    @classmethod
    def control(cls, action: str, player_name: str | None = None) -> tuple[bool, str]:
        """Envia um comando de controle de mídia via MPRIS2."""
        act_norm = action.lower().strip()
        method_map = {
            "play": "Play",
            "tocar": "Play",
            "pause": "Pause",
            "pausar": "Pause",
            "play_pause": "PlayPause",
            "toggle": "PlayPause",
            "alternar": "PlayPause",
            "next": "Next",
            "proxima": "Next",
            "pular": "Next",
            "avancar": "Next",
            "previous": "Previous",
            "prev": "Previous",
            "anterior": "Previous",
            "voltar": "Previous",
            "stop": "Stop",
            "parar": "Stop",
        }

        # Consulta de status / faixa
        if act_norm in ("status", "get_status", "info", "faixa", "musica", "track", "current"):
            info = cls.get_track_info(cls.resolve_player(player_name))
            return (True, info.summary())

        method = method_map.get(act_norm, "PlayPause")
        target_bus = cls.resolve_player(player_name)

        # Se nenhum player estiver ativo e a intenção for tocar, tenta abrir o Spotify se instalado
        if not target_bus:
            is_spotify = player_name and "spotify" in player_name.lower()
            if act_norm in ("play", "tocar", "play_pause", "toggle") or is_spotify:
                spot_bin = shutil.which("spotify")
                if spot_bin:
                    try:
                        subprocess.Popen([spot_bin], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return (True, "Spotify iniciado. A reprodução estará disponível em instantes.")
                    except Exception as exc:
                        return (False, f"Falha ao iniciar Spotify: {exc}")
            return (False, "Nenhum reprodutor de mídia ativo (Spotify, VLC, navegador).")

        # Executa método via Gio D-Bus
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            conn.call_sync(
                target_bus,
                "/org/mpris/MediaPlayer2",
                "org.mpris.MediaPlayer2.Player",
                method,
                None,
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            clean_player = target_bus.replace("org.mpris.MediaPlayer2.", "").capitalize()
            action_desc = {
                "Play": f"Reprodução iniciada no {clean_player}.",
                "Pause": f"Reprodução pausada no {clean_player}.",
                "PlayPause": f"Reprodução alternada (play/pause) no {clean_player}.",
                "Next": f"Avançado para a próxima faixa no {clean_player}.",
                "Previous": f"Voltado para a faixa anterior no {clean_player}.",
                "Stop": f"Reprodução interrompida no {clean_player}.",
            }.get(method, f"Comando {method} executado no {clean_player}.")

            return (True, action_desc)

        except Exception as exc:
            logger.debug(f"Gio D-Bus falhou para {method}: {exc}. Tentando dbus-send.")

        # Fallback via dbus-send
        try:
            res = subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--type=method_call",
                    f"--dest={target_bus}",
                    "/org/mpris/MediaPlayer2",
                    f"org.mpris.MediaPlayer2.Player.{method}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                clean_player = target_bus.replace("org.mpris.MediaPlayer2.", "").capitalize()
                return (True, f"Comando de mídia ({method}) enviado com sucesso ao {clean_player}.")
            return (False, f"Falha ao controlar mídia: {res.stderr.strip() or 'Erro desconhecido'}")
        except Exception as exc:
            return (False, f"Erro ao controlar mídia: {exc}")
