"""Wrapper Class for using spotipy"""
from dataclasses import dataclass
import logging
from typing import List, Any, Set
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from db_manager import DBManager
from utils import parse_spotify_url

@dataclass
class SpotifyManagerSettings:
    """
    Different settings for initializing SpotifyManager
    """
    client_id: str
    client_secret: str
    redirect_uri: str

class SpotifyManager:
    """
    Wrapper class for using spotipy
    """
    def __init__(self, db: DBManager, settings: SpotifyManagerSettings):
        self.logger = logging.getLogger("discord-spotify-util.spotify")
        self.spotipy = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=settings.client_id,
                client_secret=settings.client_secret,
                redirect_uri=settings.redirect_uri,
                scope="playlist-modify-public"
            )
        )
        self.db = db
        self.logger.info("Spotify client connected as %s",
                         self.spotipy.current_user()["display_name"])

    def create_playlist(
            self,
            name: str,
            description: str = "Created via discord-spotify-utility"
        ) -> Any | None:
        """Creates a playlist, returns its ID

        Args:
            user_id (str): User ID
            name (str): Name of playlist
            description (str, optional): Description of playlist.
                Defaults to "Created via discord-spotify-utility".

        Returns:
            Any | None: Playlist object
        """
        playlist = self.spotipy.user_playlist_create(
            user=self.spotipy.current_user()["id"],
            name=name,
            public=True,
            collaborative=False,
            description=description
        )
        return playlist

    def add_tracks_to_playlist(self, playlist_id: str, track_urls: List[str]) -> None:
        """Adds a number of track ids to a playlist, supports track, album, and playlist links.

        Args:
            playlist_id (str): id of the playlist
            track_urls (List[str]): list of track/album/playlist URLs
        """
        track_uris = self._get_deduped_track_uris_from_urls(track_urls)

        for i in range(0, len(track_uris), 100):
            self.spotipy.playlist_add_items(playlist_id, list(track_uris)[i:i + 100])

    def _get_deduped_track_uris_from_urls(self, track_urls: List[str]) -> Set[str]:
        track_uris: Set[str] = set()
        for url in track_urls:
            resource_type, resource_id = parse_spotify_url(url)
            if resource_type == "track":
                track_uris.add(f"spotify:track:{resource_id}")
            elif resource_type == "album":
                track_uris |= self._collect_album_track_uris(resource_id)
            elif resource_type == "playlist":
                track_uris |= self._collect_playlist_track_uris(resource_id)
        return track_uris

    def _collect_album_track_uris(self, album_id: str) -> Set[str]:
        track_uris: Set[str] = set()
        results = self.spotipy.album_tracks(album_id)
        while True:
            for t in results["items"]:
                if t["uri"].startswith("spotify:track:"):
                    track_uris.add(t["uri"])
            if not results["next"]:
                break
            results = self.spotipy.next(results)
        return track_uris

    def _collect_playlist_track_uris(self, playlist_id: str) -> Set[str]:
        track_uris: Set[str] = set()
        try:
            results = self.spotipy.playlist_items(playlist_id)
            while True:
                for t in results["items"]:
                    if t["track"] and t["track"]["uri"].startswith("spotify:track:"):
                        track_uris.add(t["track"]["uri"])
                if not results["next"]:
                    break
                results = self.spotipy.next(results)
        except spotipy.SpotifyException as e:
            if e.http_status == 404:
                self.logger.warning("Skipping inaccessible playlist %s: %s", playlist_id, e)
            else:
                raise
        return track_uris
