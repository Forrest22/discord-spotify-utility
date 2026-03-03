""" 
database management using sqlalchemy
"""
from contextlib import contextmanager
import logging
from datetime import datetime, timezone
from os import makedirs
from pathlib import Path
from typing import Optional
from sqlalchemy import JSON, create_engine, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, DeclarativeBase, mapped_column, Mapped, relationship

class Base(DeclarativeBase):
    """Base class for sqlalchemy"""

# --- Models ---
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
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)

class SpotifyLink(Base):
    """_summary_

    Args:
        Base (_type_): _description_
    """
    __tablename__ = "message_spotify_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(Text)  # 'track' | 'album' | 'playlist'
    resource_id: Mapped[Optional[str]] = mapped_column(Text)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON)
    message: Mapped["Message"] = relationship(back_populates="spotify_links")

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
    def _session(self):
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_or_create_guild(
        self,
        guild_id: int,
        name: str,
        raw_data: Optional[dict] = None
    ) -> Guild:
        """gets, creates, or updates the guild (discord server)

        Args:
            guild_id (int): guild id
            name (str): name of guild
            raw_data (Optional[dict], optional): raw data from the API. Defaults to None.

        Returns:
            Guild: the guild (the discord server) info
        """
        with self._session() as session:
            guild = session.get(Guild, guild_id)
            if not guild:
                guild = Guild(id=guild_id, name=name, raw_data=raw_data)
                session.add(guild)
            else:
                guild.name = name
                if raw_data is not None:
                    guild.raw_data = raw_data
            return guild

    def get_or_create_channel(
        self,
        channel_id: int,
        guild_id: int, name: str,
        raw_data: Optional[dict] = None
    ) -> Channel:
        """gets, creates, or updates the guild (discord server)

        Args:
            channel_id (int): discord channel id
            guild_id (int): discord server id
            name (str): name of the channel
            raw_data (Optional[dict], optional): raw data from the API. Defaults to None.

        Returns:
            Channel: the channel info
        """
        with self._session() as session:
            channel = session.get(Channel, channel_id)
            if not channel:
                channel = Channel(id=channel_id, guild_id=guild_id, name=name)
                session.add(channel)
            elif channel.name != name:
                channel.name = name
                if raw_data is not None:
                    channel.raw_data = raw_data
            return channel

    def get_or_create_discord_user(
        self, user_id: int,
        username: str,
        raw_data: Optional[dict] = None
    ) -> DiscordUser:
        """gets, creates, or updates the user data

        Args:
            user_id (int): discord user id
            username (str): discord username
            raw_data (Optional[dict], optional): raw data from the API. Defaults to None.

        Returns:
            DiscordUser: discord user information
        """
        with self._session() as session:
            user = session.get(DiscordUser, user_id)
            if not user:
                user = DiscordUser(id=user_id, username=username)
                session.add(user)
            elif user.username != username:
                user.username = username
                if raw_data is not None:
                    user.raw_data = raw_data
            return user

    def record_message(
        self,
        message_id: int,
        channel_id: int,
        author_id: int,
        content: Optional[str],
        created_at: datetime,
        spotify_urls: list[str] | None = None,
        raw_data: Optional[dict] = None
    ) -> Message:
        """Record a message and any spotify links found in it."""
        with self._session() as session:
            message = session.get(Message, message_id)
            if message:
                return message  # already recorded, skip

            message = Message(
                id=message_id,
                channel_id=channel_id,
                author_id=author_id,
                content=content,
                created_at=created_at,
                raw_data=raw_data
            )
            session.add(message)

            for url in (spotify_urls or []):
                resource_type, resource_id = self._parse_spotify_url(url)
                session.add(SpotifyLink(
                    message_id=message_id,
                    url=url,
                    resource_type=resource_type,
                    resource_id=resource_id,
                ))

            return message

    def get_spotify_links_for_channel(self, channel_id: int) -> list[SpotifyLink]:
        """Returns a list of spotify links for given channel_id

        Args:
            channel_id (int): channel_id

        Returns:
            list[SpotifyLink]: list of the spotify links in the db for that channel
        """
        with self._session() as session:
            return (
                session.query(SpotifyLink)
                .join(Message)
                .filter(Message.channel_id == channel_id)
                .all()
            )

    @staticmethod
    def _parse_spotify_url(url: str) -> tuple[Optional[str], Optional[str]]:
        """Extract resource type and ID from a Spotify URL.
        e.g. https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC
             -> ('track', '4uLU6hMCjMI75M1A2tKUQC')
        """
        try:
            path_parts = url.rstrip("/").split("open.spotify.com/")[-1].split("/")
            if len(path_parts) >= 2:
                return path_parts[0], path_parts[1].split("?")[0]
        except Exception:
            pass
        return None, None
