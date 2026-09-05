"""Testes unitários para o controlador de mídia MPRIS2 (Spotify, VLC e navegadores)."""

import unittest
from unittest.mock import MagicMock, patch

from zorin_copilot.core.media import MediaPlayerManager, TrackInfo


class MediaControlTest(unittest.TestCase):
    def test_track_info_summary_playing(self):
        info = TrackInfo(
            title="Bohemian Rhapsody",
            artist="Queen",
            album="A Night at the Opera",
            playback_status="Playing",
            player_name="org.mpris.MediaPlayer2.spotify",
        )
        summary = info.summary()
        self.assertIn("Tocando", summary)
        self.assertIn("Bohemian Rhapsody", summary)
        self.assertIn("Queen", summary)
        self.assertIn("Spotify", summary)

    def test_track_info_summary_stopped(self):
        info = TrackInfo()
        self.assertIn("Nenhuma mídia em reprodução", info.summary())

    @patch("zorin_copilot.core.media.MediaPlayerManager.list_players")
    def test_resolve_player_prefers_explicit(self, mock_list):
        mock_list.return_value = [
            "org.mpris.MediaPlayer2.chromium.instance1",
            "org.mpris.MediaPlayer2.spotify",
            "org.mpris.MediaPlayer2.vlc",
        ]
        chosen = MediaPlayerManager.resolve_player("spotify")
        self.assertEqual(chosen, "org.mpris.MediaPlayer2.spotify")

    @patch("zorin_copilot.core.media.MediaPlayerManager.get_track_info")
    @patch("zorin_copilot.core.media.MediaPlayerManager.list_players")
    def test_resolve_player_prioritizes_active_playing(self, mock_list, mock_info):
        mock_list.return_value = [
            "org.mpris.MediaPlayer2.vlc",
            "org.mpris.MediaPlayer2.spotify",
        ]
        # VLC parado, Spotify tocando
        mock_info.side_effect = [
            TrackInfo(playback_status="Stopped", player_name="vlc"),
            TrackInfo(playback_status="Playing", player_name="spotify"),
        ]
        chosen = MediaPlayerManager.resolve_player()
        self.assertEqual(chosen, "org.mpris.MediaPlayer2.spotify")

    @patch("zorin_copilot.core.media.MediaPlayerManager.resolve_player")
    def test_control_status_query(self, mock_resolve):
        mock_resolve.return_value = "org.mpris.MediaPlayer2.spotify"
        with patch("zorin_copilot.core.media.MediaPlayerManager.get_track_info") as mock_track:
            mock_track.return_value = TrackInfo(
                title="Starboy",
                artist="The Weeknd",
                playback_status="Playing",
                player_name="org.mpris.MediaPlayer2.spotify",
            )
            ok, msg = MediaPlayerManager.control("status")
            self.assertTrue(ok)
            self.assertIn("Starboy", msg)
            self.assertIn("The Weeknd", msg)

    @patch("zorin_copilot.core.media.MediaPlayerManager.resolve_player")
    def test_control_no_player_no_spotify(self, mock_resolve):
        mock_resolve.return_value = None
        with patch("shutil.which", return_value=None):
            ok, msg = MediaPlayerManager.control("pause")
            self.assertFalse(ok)
            self.assertIn("Nenhum reprodutor de mídia ativo", msg)


if __name__ == "__main__":
    unittest.main()
