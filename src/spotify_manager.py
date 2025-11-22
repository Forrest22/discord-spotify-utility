# spotify_manager.py
import spotipy
from spotipy.oauth2 import SpotifyOAuth

class SpotifyManager:
    def __init__(self, client_id, client_secret, redirect_uri):
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope="playlist-modify-public"
        ))

    def get_track_info(self, track_id):
        track_info = self.sp.track(track_id)
        return {
            'artist_name': track_info['artists'][0]['name'],
            'track_name': track_info['name'],
            'track_id': track_info['id']
        }

    def create_playlist(self, user_id, name, description=""):
        playlist = self.sp.user_playlist_create(
            user=user_id,
            name=name,
            description=description
        )
        return playlist['id']

    def add_tracks_to_playlist(self, playlist_id, track_ids):
        uris = [f"spotify:track:{track_id}" for track_id in track_ids]
        self.sp.playlist_add_items(playlist_id, uris)
