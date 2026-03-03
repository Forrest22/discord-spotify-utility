""" 
database management using sqlalchemy
"""
import logging
from datetime import datetime, timezone
from os import makedirs
from pathlib import Path
from typing import Optional
from sqlalchemy import create_engine, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, DeclarativeBase, mapped_column, Mapped, relationship
from sqlalchemy.orm.session import Session

class Base(DeclarativeBase):
    """Base class for sqlalchemy"""

# --- Models ---
class Guild(Base):
    """Guild (AKA Discord Server)"""
    __tablename__ = "guilds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    channels: Mapped[list["Channel"]] = relationship(back_populates="guild")

class Channel(Base):
    """Channel in a discord server"""
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guilds.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    guild: Mapped["Guild"] = relationship(back_populates="channels")
    messages: Mapped[list["Message"]] = relationship(back_populates="channel")

class DiscordUser(Base):
    """Discord User"""
    __tablename__ = "discord_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    messages: Mapped[list["Message"]] = relationship(back_populates="author")

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

class SpotifyLink(Base):
    """_summary_

    Args:
        Base (_type_): _description_
    """
    __tablename__ = "message_spotify_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(Text)
    resource_id: Mapped[Optional[str]] = mapped_column(Text)
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
        self._session = sessionmaker(bind=self.engine)
        self._init_db()

    def _init_db(self):
        self.logger.info("Initializing database...")
        Base.metadata.create_all(self.engine)
        self.logger.info("Database ready.")

    def get_session(self) -> Session:
        """Gets the connection to the db

        Returns:
            Session: sessioned connection to the db
        """
        return self._session()
