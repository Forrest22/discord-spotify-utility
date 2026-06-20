"""Wrapper Class for using spotipy"""
from dataclasses import dataclass
import logging
from typing import List, Any, Set
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from db_manager import DBManager, TrackData
from utils import parse_spotify_url


@dataclass
class SpotifyManagerSettings:
    """
    Different settings for initializing SpotifyManager
    """
    client_id: str
    client_secret: str
    redirect_uri: str


def _slim_track(t: dict) -> dict:
    return {"id": t["id"], "name": t["name"], "popularity": t.get("popularity")}

def _slim_artist(a: dict) -> dict:
    return {"id": a["id"], "name": a["name"], "genres": a.get("genres", [])}

def _slim_album(a: dict) -> dict:
    return {"id": a["id"], "name": a["name"], "genres": a.get("genres", []),
            "release_date": a.get("release_date")}


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
        """Creates a playlist, returns its object.

        Args:
            name (str): Name of playlist
            description (str, optional): Description of playlist.

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

    def sync_metadata(self) -> dict:
        """Resolve all stored Spotify links to tracks and persist enriched metadata.

        Returns a summary dict: links_processed, track_shares, unique_tracks,
        unique_artists, unique_albums.
        """
        links = self.db.get_all_spotify_links()
        self.logger.info("Syncing metadata for %s spotify links...", len(links))

        # Phase 1 — resolve each link to a list of (message_id, track_id, type, source_id)
        pending_shares, all_track_ids = self._resolve_links(links)
        self.logger.info("Resolved to %s track shares (%s unique tracks).",
                         len(pending_shares), len(all_track_ids))

        # Phase 2 — fetch raw track data in batches of 50
        track_data = self._fetch_track_data(all_track_ids)

        all_artist_ids = {a["id"] for t in track_data.values() for a in t.get("artists", [])}
        all_album_ids = {
            t["album"]["id"] for t in track_data.values() if t.get("album")
        }

        # Phase 3 — upsert artists first (tracks link to them)
        self._upsert_artists(all_artist_ids)

        # Phase 4 — upsert albums
        self._upsert_albums(all_album_ids)

        # Phase 5 — upsert tracks (artists + albums are in DB now)
        for t in track_data.values():
            self.db.upsert_track(TrackData(
                track_id=t["id"],
                name=t["name"],
                album_id=t["album"]["id"] if t.get("album") else None,
                artist_ids=[a["id"] for a in t.get("artists", [])],
                raw_data=_slim_track(t),
            ))

        # Phase 6 — record track shares
        for message_id, track_id, source_type, source_id in pending_shares:
            self.db.record_track_share(message_id, track_id, source_type, source_id)

        self.logger.info("Sync complete.")
        return {
            "links_processed": len(links),
            "track_shares": len(pending_shares),
            "unique_tracks": len(all_track_ids),
            "unique_artists": len(all_artist_ids),
            "unique_albums": len(all_album_ids),
        }

    # --- Music playback metadata helpers ---

    def get_search_queries(
        self, resource_type: str, resource_id: str
    ) -> list[tuple[str, str]]:
        """Return (title, primary_artist) pairs for a Spotify resource.

        Used by MusicManager to build YouTube search queries without calling yt-dlp
        up front for every track in an album or playlist.
        """
        if resource_type == "track":
            t = self.spotipy.track(resource_id)
            artist = t["artists"][0]["name"] if t.get("artists") else ""
            return [(t["name"], artist)]
        if resource_type == "album":
            return self._album_track_pairs(resource_id)
        if resource_type == "playlist":
            return self._playlist_track_pairs(resource_id)
        if resource_type == "artist":
            results = self.spotipy.artist_top_tracks(resource_id)
            return [
                (t["name"], t["artists"][0]["name"] if t.get("artists") else "")
                for t in results.get("tracks", [])
            ]
        return []

    def get_resource_name(self, resource_type: str, resource_id: str) -> str:
        """Return a human-readable display name for a Spotify resource."""
        if resource_type == "track":
            t = self.spotipy.track(resource_id)
            artist = t["artists"][0]["name"] if t.get("artists") else ""
            return f"{t['name']} — {artist}" if artist else t["name"]
        if resource_type == "album":
            return self.spotipy.album(resource_id)["name"]
        if resource_type == "playlist":
            return self.spotipy.playlist(resource_id, fields="name")["name"]
        if resource_type == "artist":
            return self.spotipy.artist(resource_id)["name"]
        return resource_id

    def _album_track_pairs(self, album_id: str) -> list[tuple[str, str]]:
        """Paginate album tracks and return (title, primary_artist) pairs."""
        pairs: list[tuple[str, str]] = []
        results = self.spotipy.album_tracks(album_id)
        while True:
            for t in results["items"]:
                artist = t["artists"][0]["name"] if t.get("artists") else ""
                pairs.append((t["name"], artist))
            if not results["next"]:
                break
            results = self.spotipy.next(results)
        return pairs

    def _playlist_track_pairs(self, playlist_id: str) -> list[tuple[str, str]]:
        """Paginate playlist items and return (title, primary_artist) pairs."""
        pairs: list[tuple[str, str]] = []
        try:
            results = self.spotipy.playlist_items(playlist_id)
            while True:
                for item in results["items"]:
                    t = item.get("track")
                    if not t or not t.get("id"):
                        continue  # skip local/deleted tracks
                    artist = t["artists"][0]["name"] if t.get("artists") else ""
                    pairs.append((t["name"], artist))
                if not results["next"]:
                    break
                results = self.spotipy.next(results)
        except spotipy.SpotifyException as e:
            if e.http_status == 404:
                self.logger.warning("Skipping inaccessible playlist %s: %s", playlist_id, e)
            else:
                raise
        return pairs

    # --- Private helpers ---

    def _resolve_links(
        self, links: list[tuple]
    ) -> tuple[list[tuple], set[str]]:
        """Resolve raw spotify link rows to (message_id, track_id, source_type, source_id).

        Returns (pending_shares list, all_track_ids set).
        """
        pending_shares = []
        all_track_ids: set[str] = set()

        for message_id, _url, resource_type, resource_id in links:
            track_ids = self._resolve_link_to_track_ids(resource_type, resource_id)
            for track_id in track_ids:
                pending_shares.append((message_id, track_id, resource_type, resource_id))
                all_track_ids.add(track_id)

        return pending_shares, all_track_ids

    def _resolve_link_to_track_ids(
        self, resource_type: str | None, resource_id: str | None
    ) -> list[str]:
        """Return track IDs for a single spotify link."""
        if not resource_type or not resource_id:
            return []
        if resource_type == "track":
            return [resource_id]
        if resource_type == "album":
            return [uri.split(":")[-1] for uri in self._collect_album_track_uris(resource_id)]
        if resource_type == "playlist":
            return [uri.split(":")[-1] for uri in self._collect_playlist_track_uris(resource_id)]
        return []

    def _fetch_track_data(self, track_ids: set[str]) -> dict[str, dict]:
        """Batch-fetch raw Spotify track objects (50 per call)."""
        track_data = {}
        id_list = list(track_ids)
        for i in range(0, len(id_list), 50):
            results = self.spotipy.tracks(id_list[i:i + 50])
            for t in results.get("tracks") or []:
                if t:
                    track_data[t["id"]] = t
        return track_data

    def _upsert_artists(self, artist_ids: set[str]) -> None:
        """Batch-fetch and upsert artist metadata (50 per call)."""
        id_list = list(artist_ids)
        for i in range(0, len(id_list), 50):
            results = self.spotipy.artists(id_list[i:i + 50])
            for a in results.get("artists") or []:
                if a:
                    self.db.upsert_artist(a["id"], a["name"], a.get("genres", []),
                                          raw_data=_slim_artist(a))

    def _upsert_albums(self, album_ids: set[str]) -> None:
        """Batch-fetch and upsert album metadata (20 per call)."""
        id_list = list(album_ids)
        for i in range(0, len(id_list), 20):
            results = self.spotipy.albums(id_list[i:i + 20])
            for a in results.get("albums") or []:
                if a:
                    self.db.upsert_album(a["id"], a["name"], a.get("genres", []),
                                         raw_data=_slim_album(a))

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
