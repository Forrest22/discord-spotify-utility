"""Discord/Spotify Bot for Discord
    Helps collect playlists that were used to play music via bot commands and
    consolidate individual songs, albums, and playlists into other playlists.
    I also have hopes for the future to do some data analysis on the music.
"""
from os import getenv
from dotenv import load_dotenv
from log_manager import setup_logging
from db_manager import DBManager
from music_manager import MusicManager, MusicManagerSettings
from spotify_manager import SpotifyManager, SpotifyManagerSettings
from stats_manager import StatsManager
from discord_manager import DiscordManager, DiscordManagerDeps, DiscordManagerSettings

# --- Load environment variables ---
load_dotenv()

SPOTIFY_CLIENT_ID = getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = getenv("SPOTIFY_REDIRECT_URI")
DISCORD_BOT_TOKEN = getenv("DISCORD_BOT_TOKEN")
TARGET_CHANNEL_NAME = getenv("TARGET_CHANNEL_NAME")
_RAW_GUILD_IDS = getenv("GUILD_IDS")
DB_URL = getenv("DB_URL")
MUSIC_DJ_ROLE_NAME = getenv("MUSIC_DJ_ROLE_NAME", "Forrest approved DJ")

# --- Validate required env vars early ---
if not SPOTIFY_CLIENT_ID:
    raise ValueError("SPOTIFY_CLIENT_ID is not set.")
if not SPOTIFY_CLIENT_SECRET:
    raise ValueError("SPOTIFY_CLIENT_SECRET is not set.")
if not SPOTIFY_REDIRECT_URI:
    raise ValueError("SPOTIFY_REDIRECT_URI is not set.")
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN is not set.")
if not TARGET_CHANNEL_NAME:
    raise ValueError("TARGET_CHANNEL_NAME is not set.")
if not _RAW_GUILD_IDS:
    raise ValueError("GUILD_IDS is not set. Add a comma-separated list of guild IDs to .env")

GUILD_IDS = [int(guild_id) for guild_id in _RAW_GUILD_IDS.split(",")]

# --- Initialize logging ---
setup_logging()

# --- Initialize Database ---
db = DBManager(db_url=DB_URL)

# --- Initialize API managers ---
spotify_manager = SpotifyManager(
    db=db,
    settings=SpotifyManagerSettings(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
    )
)

stats_manager = StatsManager(db=db)

music_manager = MusicManager(
    spotify_manager=spotify_manager,
    settings=MusicManagerSettings(dj_role_name=MUSIC_DJ_ROLE_NAME),
)

discord_manager = DiscordManager(
    deps=DiscordManagerDeps(
        db=db,
        spotify_manager=spotify_manager,
        stats_manager=stats_manager,
        music_manager=music_manager,
    ),
    discord_settings=DiscordManagerSettings(
        target_channel=TARGET_CHANNEL_NAME,
        guild_ids=GUILD_IDS,
    ),
)

# --- Run Discord bot ---
discord_manager.run(DISCORD_BOT_TOKEN, log_handler=None)
