"""Wrapper Class for using spotipy"""
from dataclasses import dataclass
from logging import Logger
from typing import List, Any, Set
import spotipy
from spotipy.oauth2 import SpotifyOAuth

@dataclass
class SpotifyManagerSettings:
    """SpotifyManagerSettings
    Different settings for initializing SpotifyManager
    """
    client_id: str
    client_secret: str
    redirect_uri: str
    logger: Logger

class SpotifyManager:
    """
    Wrapper class for using spotipy
    """
    def __init__(self, settings: SpotifyManagerSettings):
        self.logger=settings.logger
        self.spotipy = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=settings.client_id,
                client_secret=settings.client_secret,
                redirect_uri=settings.redirect_uri,
                scope="playlist-modify-public"
            )
        )
        self.logger.info("Spotify client connected as "
                         + f"{self.spotipy.current_user()["display_name"]}.")

    def get_track_info(self, track_id):
        """Gets the track info from spotify

        Args:
            track_id (_type_): track to get info about

        Returns:
            {
                "artist_name": str,
                "track_name": str,
                "track_id": str,
            }
        """
        track_info = self.spotipy.track(track_id)
        return {
            "artist_name": track_info["artists"][0]["name"],
            "track_name": track_info["name"],
            "track_id": track_info["id"],
        }

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
        track_uris = set()

        for item in track_urls:
            if "track" in item:
                track_id = item.split("track/")[-1].split("?")[0]
                track_uris.add(f"spotify:track:{track_id}")

            elif "album" in item:
                album_id = item.split("album/")[-1].split("?")[0] if "album/" in item else item
                results = self.spotipy.album_tracks(album_id)
                for t in results["items"]:
                    if t["uri"].startswith("spotify:track:"):
                        track_uris.add(t["uri"])

                while results["next"]:
                    results = self.spotipy.next(results)
                    for t in results["items"]:
                        if t["uri"].startswith("spotify:track:"):
                            track_uris.add(t["uri"])

            elif "playlist" in item:
                pl_id = item.split("playlist/")[-1].split("?")[0] if "playlist/" in item else item
                try:
                    results = self.spotipy.playlist_items(pl_id)
                    for t in results["items"]:
                        if t["track"] and t["track"]["uri"].startswith("spotify:track:"):
                            track_uris.add(t["track"]["uri"])

                    while results["next"]:
                        results = self.spotipy.next(results)
                        for t in results["items"]:
                            if t["track"] and t["track"]["uri"].startswith("spotify:track:"):
                                track_uris.add(t["track"]["uri"])

                except spotipy.SpotifyException as e:
                    if e.http_status == 404:
                        self.logger.warning(f"Skipping inaccessible playlist {pl_id}: {e}")
                    else:
                        raise

        return track_uris
