"""Wrapper Class for using spotipy"""
import spotipy
from spotipy.oauth2 import SpotifyOAuth


class SpotifyManager:
    """
    Wrapper class for using spotipy
    """
    def __init__(self, client_id, client_secret, redirect_uri):
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope="playlist-modify-public",
            )
        )

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
        track_info = self.sp.track(track_id)
        return {
            "artist_name": track_info["artists"][0]["name"],
            "track_name": track_info["name"],
            "track_id": track_info["id"],
        }

    def create_playlist(self, user_id, name, description="") -> str | None:
        """Creates a playlist, returns its ID

        Args:
            user_id (_type_): User ID
            name (_type_): Name of playlist
            description (str, optional): Description of playlist. Defaults to "".

        Returns:
            _type_: Playlist ID
        """
        print(type(user_id),type(name),type(description))
        playlist = self.sp.user_playlist_create(
            user=user_id, name=name, description=description
        )
        return playlist["id"]

    def add_tracks_to_playlist(self, playlist_id, track_ids) -> None:
        """Adds a number of track ids to a playlist

        Args:
            playlist_id (_type_): id of the playlist
            track_ids (_type_): list of track ids
        """
        uris = [f"spotify:track:{track_id}" for track_id in track_ids]
        self.sp.playlist_add_items(playlist_id, uris)
