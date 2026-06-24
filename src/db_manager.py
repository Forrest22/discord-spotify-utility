"""
database management using sqlalchemy
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
from datetime import datetime, timezone
from os import makedirs
from pathlib import Path
from typing import Optional
from sqlalchemy import (
    JSON, Table, Column, UniqueConstraint,
    create_engine, Integer, Text, DateTime, ForeignKey,
)
from sqlalchemy.orm import sessionmaker, DeclarativeBase, mapped_column, Mapped, relationship
from utils import parse_spotify_url


class Base(DeclarativeBase):
    """Base class for sqlalchemy"""


# --- Association tables ---
track_artists = Table(
    "track_artists",
    Base.metadata,
    Column("track_id", Text, ForeignKey("tracks.id"), primary_key=True),
    Column("artist_id", Text, ForeignKey("artists.id"), primary_key=True),
)

# --- Discord models ---
class Guild(Base):
    """Guild (AKA Discord Server)"""
    __tablename__ = "guilds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    channels: Mapped[list["Channel"]] = relationship(back_populates="guild")
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)

class Channel(Base):
    """Channel in a discord server"""
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guilds.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    guild: Mapped["Guild"] = relationship(back_populates="channels")
    messages: Mapped[list["Message"]] = relationship(back_populates="channel")
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)

class DiscordUser(Base):
    """Discord User"""
    __tablename__ = "discord_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    messages: Mapped[list["Message"]] = relationship(back_populates="author")
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)

class Message(Base):
    """Message"""
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("discord_users.id"), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  default=lambda: datetime.now(timezone.utc))
    channel: Mapped["Channel"] = relationship(back_populates="messages")
    author: Mapped["DiscordUser"] = relationship(back_populates="messages")
    spotify_links: Mapped[list["SpotifyLink"]] = relationship(back_populates="message")
    track_shares: Mapped[list["TrackShare"]] = relationship(back_populates="message")
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)

class SpotifyLink(Base):
    """Spotify Link"""
    __tablename__ = "message_spotify_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(Text)  # 'track' | 'album' | 'playlist'
    resource_id: Mapped[Optional[str]] = mapped_column(Text)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)
    message: Mapped["Message"] = relationship(back_populates="spotify_links")

# --- Spotify enrichment models ---
class Artist(Base):
    """Spotify Artist"""
    __tablename__ = "artists"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    genres: Mapped[Optional[list]] = mapped_column(JSON)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)
    tracks: Mapped[list["Track"]] = relationship(secondary=track_artists, back_populates="artists")

class Album(Base):
    """Spotify Album"""
    __tablename__ = "albums"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    genres: Mapped[Optional[list]] = mapped_column(JSON)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)
    tracks: Mapped[list["Track"]] = relationship(back_populates="album")

class Track(Base):
    """Spotify Track"""
    __tablename__ = "tracks"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    album_id: Mapped[Optional[str]] = mapped_column(Text, ForeignKey("albums.id"), nullable=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)
    album: Mapped[Optional["Album"]] = relationship(back_populates="tracks")
    artists: Mapped[list["Artist"]] = relationship(
        secondary=track_artists, back_populates="tracks"
    )
    shares: Mapped[list["TrackShare"]] = relationship(back_populates="track")

class TrackShare(Base):
    """Analytics fact: one row per (message, resolved track)."""
    __tablename__ = "track_shares"
    __table_args__ = (UniqueConstraint("message_id", "track_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    track_id: Mapped[str] = mapped_column(Text, ForeignKey("tracks.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)  # 'track'|'album'|'playlist'
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped["Message"] = relationship(back_populates="track_shares")
    track: Mapped["Track"] = relationship(back_populates="shares")


class TriviaScore(Base):
    """Trivia analytics fact: one row per user per completed trivia game."""
    __tablename__ = "trivia_scores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("discord_users.id"), nullable=False)
    guild_id: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

# --- Data transfer objects ---
@dataclass
class MessageRecord:
    """Data needed to record a Discord message and its Spotify links."""
    message_id: int
    channel_id: int
    author_id: int
    content: Optional[str]
    created_at: datetime
    spotify_urls: list[str] = field(default_factory=list)
    raw_data: Optional[dict] = None

@dataclass
class TrackData:
    """Data needed to upsert a Spotify track."""
    track_id: str
    name: str
    album_id: Optional[str]
    artist_ids: list[str]
    raw_data: Optional[dict] = None

# --- Manager ---
class DBManager:
    """Auto generates tables if they don't exist, and connects to the db"""
    def __init__(self, db_url: str = None):
        self.logger = logging.getLogger("discord-spotify-util.db")
        storage_dir = Path(__file__).parent.parent / "storage"
        makedirs(storage_dir, exist_ok=True)
        db_url = db_url or f"sqlite:///{storage_dir}/discord-spotify.db"
        self.logger.info("Using database at: %s", db_url)
        self.engine = create_engine(db_url)
        self._session_factory = sessionmaker(bind=self.engine)
        self._init_db()

    def _init_db(self):
        self.logger.info("Initializing database...")
        Base.metadata.create_all(self.engine)
        self.logger.info("Database ready.")

    @contextmanager
    def session(self):
        """Context manager providing a transactional DB session."""
        s = self._session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # --- Discord entity upserts ---

    def get_or_create_guild(
        self,
        guild_id: int,
        name: str,
        raw_data: Optional[dict] = None
    ) -> None:
        """gets, creates, or updates the guild (discord server)"""
        with self.session() as s:
            guild = s.get(Guild, guild_id)
            if not guild:
                s.add(Guild(id=guild_id, name=name, raw_data=raw_data))
            else:
                guild.name = name
                if raw_data is not None:
                    guild.raw_data = raw_data

    def get_or_create_channel(
        self,
        channel_id: int,
        guild_id: int,
        name: str,
        raw_data: Optional[dict] = None
    ) -> None:
        """gets, creates, or updates the channel"""
        with self.session() as s:
            channel = s.get(Channel, channel_id)
            if not channel:
                s.add(Channel(
                    id=channel_id, guild_id=guild_id, name=name, raw_data=raw_data
                ))
            else:
                channel.name = name
                if raw_data is not None:
                    channel.raw_data = raw_data

    def get_or_create_discord_user(
        self,
        user_id: int,
        username: str,
        raw_data: Optional[dict] = None
    ) -> None:
        """gets, creates, or updates the user"""
        with self.session() as s:
            user = s.get(DiscordUser, user_id)
            if not user:
                s.add(DiscordUser(id=user_id, username=username, raw_data=raw_data))
            else:
                user.username = username
                if raw_data is not None:
                    user.raw_data = raw_data

    def record_message(self, record: MessageRecord) -> None:
        """Record a message and any spotify links found in it. Idempotent."""
        with self.session() as s:
            if s.get(Message, record.message_id):
                return  # already recorded, skip

            s.add(Message(
                id=record.message_id,
                channel_id=record.channel_id,
                author_id=record.author_id,
                content=record.content,
                created_at=record.created_at,
                raw_data=record.raw_data,
            ))
            for url in record.spotify_urls:
                resource_type, resource_id = parse_spotify_url(url)
                s.add(SpotifyLink(
                    message_id=record.message_id,
                    url=url,
                    resource_type=resource_type,
                    resource_id=resource_id,
                ))

    def get_spotify_urls_for_channel(
        self,
        channel_id: int,
        cutoff: datetime | None = None,
    ) -> list[str]:
        """Return Spotify URL strings for a channel, optionally filtered by message date.

        Returns plain strings (not ORM objects) so they are safe to use after the
        session closes.
        """
        with self.session() as s:
            q = (
                s.query(SpotifyLink.url)
                .join(Message)
                .filter(Message.channel_id == channel_id)
            )
            if cutoff is not None:
                q = q.filter(Message.created_at >= cutoff)
            return [row[0] for row in q.all()]

    def get_all_spotify_links(self) -> list[tuple]:
        """Return (message_id, url, resource_type, resource_id) tuples for all spotify links."""
        with self.session() as s:
            rows = s.query(
                SpotifyLink.message_id,
                SpotifyLink.url,
                SpotifyLink.resource_type,
                SpotifyLink.resource_id,
            ).all()
            return list(rows)

    # --- Spotify enrichment upserts ---

    def upsert_artist(
        self,
        artist_id: str,
        name: str,
        genres: list[str],
        raw_data: Optional[dict] = None
    ) -> None:
        """Upsert a Spotify artist."""
        with self.session() as s:
            artist = s.get(Artist, artist_id)
            if not artist:
                s.add(Artist(id=artist_id, name=name, genres=genres, raw_data=raw_data))
            else:
                artist.name = name
                artist.genres = genres
                if raw_data is not None:
                    artist.raw_data = raw_data

    def upsert_album(
        self,
        album_id: str,
        name: str,
        genres: list[str],
        raw_data: Optional[dict] = None
    ) -> None:
        """Upsert a Spotify album."""
        with self.session() as s:
            album = s.get(Album, album_id)
            if not album:
                s.add(Album(id=album_id, name=name, genres=genres, raw_data=raw_data))
            else:
                album.name = name
                album.genres = genres
                if raw_data is not None:
                    album.raw_data = raw_data

    def upsert_track(self, data: TrackData) -> None:
        """Upsert a Spotify track and link it to its artists."""
        with self.session() as s:
            track = s.get(Track, data.track_id)
            if not track:
                track = Track(
                    id=data.track_id,
                    name=data.name,
                    album_id=data.album_id,
                    raw_data=data.raw_data,
                )
                s.add(track)
            else:
                track.name = data.name
                track.album_id = data.album_id
                if data.raw_data is not None:
                    track.raw_data = data.raw_data
            track.artists = [a for a in (s.get(Artist, aid) for aid in data.artist_ids) if a]

    def record_track_share(
        self,
        message_id: int,
        track_id: str,
        source_type: str,
        source_id: str,
    ) -> None:
        """Record that a message resolved to a given track. Idempotent."""
        with self.session() as s:
            exists = (
                s.query(TrackShare)
                .filter_by(message_id=message_id, track_id=track_id)
                .first()
            )
            if not exists:
                s.add(TrackShare(
                    message_id=message_id,
                    track_id=track_id,
                    source_type=source_type,
                    source_id=source_id,
                ))

    def record_trivia_score(
        self,
        user_id: int,
        guild_id: int,
        points: int,
        username: str,
    ) -> None:
        """Record a user's final score from a completed trivia game."""
        self.get_or_create_discord_user(user_id, username)
        with self.session() as s:
            s.add(TriviaScore(user_id=user_id, guild_id=guild_id, points=points))
