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
    def control(
        cls,
        action: str,
        player_name: str | None = None,
        query: str | None = None,
    ) -> tuple[bool, str]:
        """Envia comandos de transporte MPRIS2 ao reprodutor alvo."""
        act_norm = (action or "").strip().lower()

        # Consulta de status / faixa
        if act_norm in ("status", "get_status", "info", "faixa", "musica", "track", "current"):
            info = cls.get_track_info(cls.resolve_player(player_name))
            return (True, info.summary())

        # Busca ou reprodução de música / artista específico no Spotify / player
        if query or act_norm in ("search", "play_song", "tocar_musica", "buscar", "pesquisar"):
            term = (query or "").strip()
            if not term and act_norm not in ("search", "play_song", "buscar", "pesquisar"):
                term = action
            if term:
                return cls.play_search(term, player_name=player_name or "spotify")

        method_map = {
            "play": "Play",
            "tocar": "Play",
            "iniciar": "Play",
            "resume": "Play",
            "despausar": "Play",
            "pause": "Pause",
            "pausar": "Pause",
            "play_pause": "PlayPause",
            "playpause": "PlayPause",
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
        except Exception:
            pass

        # Fallback 3: playerctl CLI (se disponível)
        if shutil.which("playerctl"):
            try:
                cmd_map = {
                    "Play": "play",
                    "Pause": "pause",
                    "PlayPause": "play-pause",
                    "Next": "next",
                    "Previous": "previous",
                    "Stop": "stop",
                }
                subcmd = cmd_map.get(method, "play-pause")
                pctl_cmd = ["playerctl"]
                if player_name:
                    clean_p = player_name.lower().replace("org.mpris.mediaplayer2.", "")
                    pctl_cmd.extend(["-p", clean_p])
                pctl_cmd.append(subcmd)
                pctl_res = subprocess.run(pctl_cmd, capture_output=True, text=True, timeout=1.5, check=False)
                if pctl_res.returncode == 0:
                    clean_player = (player_name or "reprodutor").capitalize()
                    return (True, f"Comando de mídia ({method}) enviado com sucesso ao {clean_player} via playerctl.")
            except Exception as exc:
                logger.debug("Fallback playerctl falhou: %s", exc)

        return (False, f"Falha ao controlar mídia: {target_bus or 'nenhum player respondendo'}")

    @classmethod
    def open_uri(cls, uri: str, player_name: str | None = None) -> tuple[bool, str]:
        """Abre uma URI de mídia ou busca (ex: 'spotify:search:queen') no player via MPRIS2 ou desktop."""
        target_bus = cls.resolve_player(player_name)

        # 1. Se houver player ativo com suporte a D-Bus MPRIS2 OpenUri
        if target_bus:
            try:
                import gi
                gi.require_version("Gio", "2.0")
                from gi.repository import Gio, GLib

                conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                conn.call_sync(
                    target_bus,
                    "/org/mpris/MediaPlayer2",
                    "org.mpris.MediaPlayer2.Player",
                    "OpenUri",
                    GLib.Variant("(s)", (uri,)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                )
                clean_player = target_bus.replace("org.mpris.MediaPlayer2.", "").capitalize()
                return (True, f"Mídia aberta com sucesso no {clean_player}.")
            except Exception as exc:
                logger.debug(f"Gio D-Bus OpenUri falhou em {target_bus}: {exc}")

        # 2. Se for URI do Spotify
        if uri.startswith("spotify:"):
            # Tenta via Gio launcher de esquema nativo do sistema
            try:
                from gi.repository import Gio
                ok = Gio.AppInfo.launch_default_for_uri(uri, None)
                if ok:
                    return (True, "Spotify aberto com a reprodução solicitada.")
            except Exception:
                pass

            # Tenta via binário executável spotify (--uri=...)
            spot_bin = shutil.which("spotify")
            if spot_bin:
                try:
                    subprocess.Popen([spot_bin, f"--uri={uri}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return (True, "Spotify iniciado com a busca solicitada.")
                except Exception as exc:
                    logger.debug(f"Falha ao iniciar spotify com --uri: {exc}")

            # Fallback xdg-open
            try:
                subprocess.Popen(["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return (True, "Comando enviado para o Spotify.")
            except Exception:
                pass

            # Fallback Web se nativo falhar
            if uri.startswith("spotify:search:"):
                search_query = uri.replace("spotify:search:", "")
                web_url = f"https://open.spotify.com/search/{search_query}"
                try:
                    subprocess.Popen(["xdg-open", web_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return (True, "Aberto no Spotify Web.")
                except Exception:
                    pass

        return (False, f"Não foi possível abrir a mídia '{uri}'.")

    @classmethod
    def play_search(cls, query: str, player_name: str = "spotify") -> tuple[bool, str]:
        """Inicia busca e reprodução de música/artista no Spotify ou reprodutor ativo."""
        clean_q = query.strip().strip('"').strip("'")
        if not clean_q:
            return (False, "Nenhum termo de música fornecido.")

        uri = f"spotify:search:{clean_q}"

        # 1. Se o Spotify não estiver rodando, inicia o app diretamente com a busca
        players = cls.list_players()
        has_spotify = any("spotify" in p.lower() for p in players)
        spot_bin = shutil.which("spotify")
        if not has_spotify and spot_bin:
            try:
                subprocess.Popen([spot_bin, f"--uri={uri}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return (True, f"Spotify iniciado buscando '{clean_q}'. A reprodução iniciará em instantes.")
            except Exception as exc:
                logger.debug(f"Falha ao iniciar Spotify com --uri: {exc}")

        # 2. Se já estiver aberto, foca na janela do Spotify para receber a navegação e enter
        if shutil.which("hyprctl"):
            try:
                subprocess.run(["hyprctl", "dispatch", "focuswindow", "class:Spotify"], capture_output=True, text=True, timeout=0.8, check=False)
            except Exception:
                pass
        elif shutil.which("swaymsg"):
            try:
                subprocess.run(["swaymsg", '[app_id="(?i)spotify"], focus'], capture_output=True, text=True, timeout=0.8, check=False)
            except Exception:
                pass
        elif shutil.which("xdotool"):
            try:
                subprocess.run(["xdotool", "search", "--class", "spotify", "windowactivate"], capture_output=True, text=True, timeout=0.8, check=False)
            except Exception:
                pass

        # 3. Navega para a busca via playerctl ou open_uri
        opened = False
        if shutil.which("playerctl"):
            try:
                res = subprocess.run(["playerctl", "-p", "spotify", "open", uri], capture_output=True, text=True, timeout=1.5, check=False)
                if res.returncode == 0:
                    opened = True
            except Exception:
                pass

        if not opened:
            opened, _ = cls.open_uri(uri, player_name=player_name)

        # 4. Pressiona Enter para reproduzir automaticamente o primeiro resultado encontrado
        import time
        time.sleep(0.4)
        if shutil.which("ydotool"):
            try:
                subprocess.run(["ydotool", "key", "28:1", "28:0"], capture_output=True, text=True, timeout=1.0, check=False)
            except Exception:
                pass
        elif shutil.which("wtype"):
            try:
                subprocess.run(["wtype", "-k", "Return"], capture_output=True, text=True, timeout=1.0, check=False)
            except Exception:
                pass

        # 5. Obtém os metadados da faixa para responder ao usuário
        time.sleep(0.3)
        info = cls.get_track_info(cls.resolve_player("spotify"))
        if info.title:
            artist_str = f" de {info.artist}" if info.artist else ""
            return (True, f"Tocando no Spotify: '{info.title}'{artist_str}.")

        return (True, f"Buscando e tocando '{clean_q}' no Spotify.")
