"""Query layer for music analytics over stored track shares."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
from typing import Literal

from sqlalchemy.sql.functions import count as count_rows

from db_manager import DBManager, Artist, Album, Track, TrackShare, Message, track_artists

Period = Literal["day", "week", "month", "year", "all"]

_PERIOD_DELTAS: dict[str, timedelta] = {
    "day":   timedelta(days=1),
    "week":  timedelta(weeks=1),
    "month": timedelta(days=30),
    "year":  timedelta(days=365),
}


def period_cutoff(period: Period) -> datetime | None:
    """Return the UTC datetime at the start of the given period, or None for 'all'."""
    if period == "all":
        return None
    return datetime.now(timezone.utc) - _PERIOD_DELTAS[period]


class StatsManager:
    """Provides pre-built analytics queries over the enriched track data."""

    def __init__(self, db: DBManager):
        self.db = db
        self.logger = logging.getLogger("discord-spotify-util.stats")

    def top_tracks(self, period: Period, n: int = 10) -> list[tuple[str, int]]:
        """Return the n most-shared tracks in the given period as (name, count) pairs."""
        cutoff = period_cutoff(period)
        cnt = count_rows(TrackShare.id)
        with self.db.session() as s:
            q = (
                s.query(Track.name, cnt.label("cnt"))
                .join(TrackShare, TrackShare.track_id == Track.id)
                .join(Message, Message.id == TrackShare.message_id)
            )
            if cutoff:
                q = q.filter(Message.created_at >= cutoff)
            return list(
                q.group_by(Track.id, Track.name)
                .order_by(cnt.desc())
                .limit(n)
                .all()
            )

    def top_albums(self, period: Period, n: int = 10) -> list[tuple[str, int]]:
        """Return the n most-shared albums in the given period as (name, count) pairs."""
        cutoff = period_cutoff(period)
        cnt = count_rows(TrackShare.id)
        with self.db.session() as s:
            q = (
                s.query(Album.name, cnt.label("cnt"))
                .join(Track, Track.album_id == Album.id)
                .join(TrackShare, TrackShare.track_id == Track.id)
                .join(Message, Message.id == TrackShare.message_id)
            )
            if cutoff:
                q = q.filter(Message.created_at >= cutoff)
            return list(
                q.group_by(Album.id, Album.name)
                .order_by(cnt.desc())
                .limit(n)
                .all()
            )

    def top_artists(self, period: Period, n: int = 10) -> list[tuple[str, int]]:
        """Return the n most-shared artists in the given period as (name, count) pairs."""
        cutoff = period_cutoff(period)
        cnt = count_rows(TrackShare.id)
        with self.db.session() as s:
            q = (
                s.query(Artist.name, cnt.label("cnt"))
                .join(track_artists, track_artists.c.artist_id == Artist.id)
                .join(TrackShare, TrackShare.track_id == track_artists.c.track_id)
                .join(Message, Message.id == TrackShare.message_id)
            )
            if cutoff:
                q = q.filter(Message.created_at >= cutoff)
            return list(
                q.group_by(Artist.id, Artist.name)
                .order_by(cnt.desc())
                .limit(n)
                .all()
            )

    def genre_frequencies(self, period: Period) -> dict[str, int]:
        """Return a genre → share-count dict for all artists in the given period.

        Genres are sourced from Artist.genres (list[str] stored as JSON).
        Each genre is weighted by the number of track shares its artist appears on.
        """
        cutoff = period_cutoff(period)
        cnt = count_rows(TrackShare.id)
        with self.db.session() as s:
            q = (
                s.query(Artist.genres, cnt)
                .join(track_artists, track_artists.c.artist_id == Artist.id)
                .join(TrackShare, TrackShare.track_id == track_artists.c.track_id)
                .join(Message, Message.id == TrackShare.message_id)
            )
            if cutoff:
                q = q.filter(Message.created_at >= cutoff)
            rows = q.group_by(Artist.id, Artist.genres).all()

        freqs: Counter = Counter()
        for genres_json, share_count in rows:
            for genre in (genres_json or []):
                freqs[genre] += share_count
        return dict(freqs)
